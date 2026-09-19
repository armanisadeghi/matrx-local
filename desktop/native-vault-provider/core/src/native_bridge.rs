//! UniFFI-only provider-private bridge.  It never creates client data, derives
//! origins, exposes canonical sources, or invokes Apple completion APIs.
use super::*;
use passkey_authenticator::{UiHint, UserCheck, UserValidationMethod};
use passkey_types::{
    ctap2::{Ctap2Error, get_assertion, make_credential},
    webauthn::{
        self, PublicKeyCredentialDescriptor, PublicKeyCredentialParameters, PublicKeyCredentialType,
    },
};
use std::sync::{
    Arc,
    atomic::{AtomicU8, Ordering},
};
use tokio::sync::Notify;

const FRESH: u8 = 0;
const RUNNING: u8 = 1;
const CANCELLED: u8 = 2;
const FINISHED: u8 = 3;
const MAX_SOURCES: usize = 128;
const MAX_SOURCE_BYTES: u32 = 65_536;

#[derive(uniffi::Error, Debug, Clone, Copy, PartialEq, Eq)]
pub enum VerificationCallbackError {
    Denied,
    Failed,
    Unexpected,
}
#[derive(uniffi::Error, Debug, Clone, Copy, PartialEq, Eq)]
pub enum PersistenceCallbackError {
    Refused,
    Failed,
    Unexpected,
}
#[derive(uniffi::Error, Debug, Clone, Copy, PartialEq, Eq)]
pub enum BridgeError {
    InvalidRequest,
    InvalidSource,
    VerificationDenied,
    CredentialExcluded,
    NoCredentials,
    PersistenceFailed,
    Cancelled,
    AlreadyUsed,
    OperationFailed,
}
impl std::fmt::Display for BridgeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "native operation failed")
    }
}
impl std::error::Error for BridgeError {}
impl From<FixedError> for BridgeError {
    fn from(value: FixedError) -> Self {
        match value {
            FixedError::InvalidRequest => Self::InvalidRequest,
            FixedError::InvalidSource => Self::InvalidSource,
            FixedError::VerificationDenied => Self::VerificationDenied,
            FixedError::CredentialExcluded => Self::CredentialExcluded,
            FixedError::NoCredentials => Self::NoCredentials,
            FixedError::PersistenceFailed => Self::PersistenceFailed,
            FixedError::Cancelled => Self::Cancelled,
            FixedError::OperationFailed => Self::OperationFailed,
        }
    }
}

#[derive(uniffi::Record)]
pub struct NativeRegistrationInput {
    pub rp_id: String,
    pub user_handle: Vec<u8>,
    pub username: String,
    pub display_name: Option<String>,
    pub client_data_hash: Vec<u8>,
    pub supported_algorithms: Vec<i32>,
    pub excluded_credential_ids: Vec<Vec<u8>>,
}
#[derive(uniffi::Record)]
pub struct NativeAssertionInput {
    pub rp_id: String,
    pub client_data_hash: Vec<u8>,
    pub allowed_credential_ids: Vec<Vec<u8>>,
}
#[derive(uniffi::Record)]
pub struct NativeRegistrationResult {
    pub credential_id: Vec<u8>,
    pub attestation_object: Vec<u8>,
}
#[derive(uniffi::Record)]
pub struct NativeAssertionResult {
    pub credential_id: Vec<u8>,
    pub authenticator_data: Vec<u8>,
    pub signature: Vec<u8>,
    pub user_handle: Vec<u8>,
}

#[uniffi::export(callback_interface)]
#[async_trait]
pub trait NativeCeremony: Send + Sync {
    async fn verify_user(&self) -> Result<(), VerificationCallbackError>;
    async fn persist_registration(
        &self,
        canonical_source: Vec<u8>,
    ) -> Result<(), PersistenceCallbackError>;
}

#[derive(uniffi::Object)]
pub struct NativeOperation {
    state: AtomicU8,
    cancelled: Notify,
}
#[uniffi::export]
impl NativeOperation {
    #[uniffi::constructor]
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            state: AtomicU8::new(FRESH),
            cancelled: Notify::new(),
        })
    }
    pub fn cancel(&self) {
        loop {
            let state = self.state.load(Ordering::Acquire);
            if state == CANCELLED || state == FINISHED {
                return;
            }
            if self
                .state
                .compare_exchange(state, CANCELLED, Ordering::AcqRel, Ordering::Acquire)
                .is_ok()
            {
                self.cancelled.notify_waiters();
                return;
            }
        }
    }
    pub async fn register(
        self: Arc<Self>,
        input: NativeRegistrationInput,
        existing_sources: Vec<Vec<u8>>,
        max_source_bytes: u32,
        ceremony: Box<dyn NativeCeremony>,
    ) -> Result<NativeRegistrationResult, BridgeError> {
        self.begin()?;
        let result = self
            .register_inner(input, existing_sources, max_source_bytes, ceremony)
            .await;
        self.publish(result)
    }
    pub async fn authenticate(
        self: Arc<Self>,
        input: NativeAssertionInput,
        canonical_source: Vec<u8>,
        max_source_bytes: u32,
        ceremony: Box<dyn NativeCeremony>,
    ) -> Result<NativeAssertionResult, BridgeError> {
        self.begin()?;
        let result = self
            .authenticate_inner(input, canonical_source, max_source_bytes, ceremony)
            .await;
        self.publish(result)
    }
}
impl NativeOperation {
    fn begin(&self) -> Result<(), BridgeError> {
        self.state
            .compare_exchange(FRESH, RUNNING, Ordering::AcqRel, Ordering::Acquire)
            .map(|_| ())
            .map_err(|state| {
                if state == CANCELLED {
                    BridgeError::Cancelled
                } else {
                    BridgeError::AlreadyUsed
                }
            })
    }
    fn cancellation(&self) -> Result<(), BridgeError> {
        (self.state.load(Ordering::Acquire) == CANCELLED)
            .then_some(BridgeError::Cancelled)
            .map_or(Ok(()), Err)
    }
    async fn race<T>(
        &self,
        future: impl std::future::Future<Output = T>,
    ) -> Result<T, BridgeError> {
        let notified = self.cancelled.notified();
        tokio::pin!(notified);
        // Register before reading state: notify_waiters only wakes registered
        // waiters, so the former read-then-poll ordering could lose a cancel.
        notified.as_mut().enable();
        self.cancellation()?;
        tokio::select! {
            value = future => { self.cancellation()?; Ok(value) },
            _ = &mut notified => Err(BridgeError::Cancelled)
        }
    }
    fn publish<T>(&self, value: Result<T, BridgeError>) -> Result<T, BridgeError> {
        match self
            .state
            .compare_exchange(RUNNING, FINISHED, Ordering::AcqRel, Ordering::Acquire)
        {
            Ok(_) => value,
            Err(CANCELLED) => Err(BridgeError::Cancelled),
            Err(_) => Err(BridgeError::OperationFailed),
        }
    }
    fn source_limit(max: u32) -> Result<usize, BridgeError> {
        if max == 0 || max > MAX_SOURCE_BYTES {
            return Err(BridgeError::InvalidRequest);
        }
        Ok(max as usize)
    }
    async fn verify(&self, ceremony: &Box<dyn NativeCeremony>) -> Result<(), BridgeError> {
        match self.race(ceremony.verify_user()).await? {
            Ok(()) => Ok(()),
            Err(VerificationCallbackError::Denied) => Err(BridgeError::VerificationDenied),
            Err(VerificationCallbackError::Failed) => Err(BridgeError::OperationFailed),
            Err(VerificationCallbackError::Unexpected) => Err(BridgeError::OperationFailed),
        }
    }
    async fn register_inner(
        &self,
        input: NativeRegistrationInput,
        existing_sources: Vec<Vec<u8>>,
        max: u32,
        ceremony: Box<dyn NativeCeremony>,
    ) -> Result<NativeRegistrationResult, BridgeError> {
        let max = Self::source_limit(max)?;
        if existing_sources.len() > MAX_SOURCES
            || existing_sources.iter().map(Vec::len).sum::<usize>() > 1024 * 1024
        {
            return Err(BridgeError::InvalidSource);
        }
        let existing_sources: Vec<Zeroizing<Vec<u8>>> =
            existing_sources.into_iter().map(Zeroizing::new).collect();
        self.verify(&ceremony).await?;
        validate_registration(&input)?;
        let refs: Vec<&[u8]> = existing_sources.iter().map(|source| &source[..]).collect();
        let request = make_request(&input)?;
        let prepared = prepare_registration_with_display_name(
            request,
            VerifiedUser,
            &refs,
            max,
            Some(input.display_name.clone()),
        )
        .await
        .map_err(BridgeError::from)?;
        self.cancellation()?;
        let mut persister = CallbackPersister {
            operation: self,
            ceremony: &ceremony,
        };
        let committed = prepared
            .commit_with(&mut persister)
            .await
            .map_err(BridgeError::from)?;
        self.cancellation()?;
        let response = committed.into_response();
        self.cancellation()?;
        let credential_id = response
            .auth_data
            .attested_credential_data
            .as_ref()
            .ok_or(BridgeError::OperationFailed)?
            .credential_id()
            .to_vec();
        Ok(NativeRegistrationResult {
            credential_id,
            attestation_object: response.as_webauthn_bytes().to_vec(),
        })
    }
    async fn authenticate_inner(
        &self,
        input: NativeAssertionInput,
        canonical_source: Vec<u8>,
        max: u32,
        ceremony: Box<dyn NativeCeremony>,
    ) -> Result<NativeAssertionResult, BridgeError> {
        let max = Self::source_limit(max)?;
        let canonical_source = Zeroizing::new(canonical_source);
        self.verify(&ceremony).await?;
        validate_assertion(&input)?;
        let request = assertion_request(&input)?;
        let response = authenticate(&canonical_source, request, VerifiedUser, max)
            .await
            .map_err(BridgeError::from)?;
        self.cancellation()?;
        let credential_id = response
            .credential
            .ok_or(BridgeError::OperationFailed)?
            .id
            .to_vec();
        let user_handle = response
            .user
            .ok_or(BridgeError::OperationFailed)?
            .id
            .to_vec();
        Ok(NativeAssertionResult {
            credential_id,
            authenticator_data: response.auth_data.to_vec(),
            signature: response.signature.to_vec(),
            user_handle,
        })
    }
}

struct VerifiedUser;
#[async_trait]
impl UserValidationMethod for VerifiedUser {
    type PasskeyItem = StoredCredential;
    async fn check_user<'a>(
        &self,
        _: UiHint<'a, StoredCredential>,
        _: bool,
        _: bool,
    ) -> Result<UserCheck, Ctap2Error> {
        Ok(UserCheck {
            presence: true,
            verification: true,
        })
    }
    fn is_presence_enabled(&self) -> bool {
        true
    }
    fn is_verification_enabled(&self) -> Option<bool> {
        Some(true)
    }
}
struct CallbackPersister<'a> {
    operation: &'a NativeOperation,
    ceremony: &'a Box<dyn NativeCeremony>,
}
#[async_trait]
impl RegistrationPersister for CallbackPersister<'_> {
    async fn persist(&mut self, source: &[u8]) -> Result<(), FixedError> {
        self.operation
            .cancellation()
            .map_err(|_| FixedError::Cancelled)?;
        let result = self
            .operation
            .race(self.ceremony.persist_registration(source.to_vec()))
            .await
            .map_err(|_| FixedError::Cancelled)?;
        match result {
            Ok(()) => self
                .operation
                .cancellation()
                .map_err(|_| FixedError::Cancelled),
            Err(PersistenceCallbackError::Refused) => Err(FixedError::PersistenceFailed),
            Err(PersistenceCallbackError::Failed) => Err(FixedError::OperationFailed),
            Err(PersistenceCallbackError::Unexpected) => Err(FixedError::OperationFailed),
        }
    }
}
fn valid_name(value: &str) -> bool {
    value.as_bytes().len() <= 1024
}
fn valid_ids(ids: &[Vec<u8>]) -> bool {
    ids.len() <= 128 && ids.iter().all(|id| (16..=1023).contains(&id.len()))
}
fn validate_registration(input: &NativeRegistrationInput) -> Result<(), BridgeError> {
    if !valid_rp(&input.rp_id)
        || !(1..=64).contains(&input.user_handle.len())
        || !valid_name(&input.username)
        || input
            .display_name
            .as_deref()
            .is_some_and(|v| !valid_name(v))
        || input.client_data_hash.len() != 32
        || !(1..=64).contains(&input.supported_algorithms.len())
        || !input.supported_algorithms.contains(&-7)
        || !valid_ids(&input.excluded_credential_ids)
    {
        return Err(BridgeError::InvalidRequest);
    }
    Ok(())
}
fn validate_assertion(input: &NativeAssertionInput) -> Result<(), BridgeError> {
    if !valid_rp(&input.rp_id)
        || input.client_data_hash.len() != 32
        || !valid_ids(&input.allowed_credential_ids)
    {
        Err(BridgeError::InvalidRequest)
    } else {
        Ok(())
    }
}
fn descriptor(id: Vec<u8>) -> PublicKeyCredentialDescriptor {
    PublicKeyCredentialDescriptor {
        ty: PublicKeyCredentialType::PublicKey,
        id: id.into(),
        transports: None,
    }
}
fn make_request(input: &NativeRegistrationInput) -> Result<make_credential::Request, BridgeError> {
    let display = input.display_name.clone().unwrap_or_default();
    Ok(make_credential::Request {
        client_data_hash: input.client_data_hash.clone().into(),
        rp: make_credential::PublicKeyCredentialRpEntity {
            id: input.rp_id.clone(),
            name: None,
        },
        user: webauthn::PublicKeyCredentialUserEntity {
            id: input.user_handle.clone().into(),
            name: input.username.clone(),
            display_name: display,
        }
        .into(),
        pub_key_cred_params: vec![PublicKeyCredentialParameters {
            ty: PublicKeyCredentialType::PublicKey,
            alg: iana::Algorithm::ES256,
        }],
        exclude_list: Some(
            input
                .excluded_credential_ids
                .clone()
                .into_iter()
                .map(descriptor)
                .collect(),
        ),
        extensions: None,
        options: make_credential::Options {
            rk: true,
            up: true,
            uv: true,
        },
        pin_auth: None,
        pin_protocol: None,
    })
}
fn assertion_request(input: &NativeAssertionInput) -> Result<get_assertion::Request, BridgeError> {
    Ok(get_assertion::Request {
        rp_id: input.rp_id.clone(),
        client_data_hash: input.client_data_hash.clone().into(),
        allow_list: Some(
            input
                .allowed_credential_ids
                .clone()
                .into_iter()
                .map(descriptor)
                .collect(),
        ),
        extensions: None,
        options: get_assertion::Options {
            rk: false,
            up: true,
            uv: true,
        },
        pin_auth: None,
        pin_protocol: None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct Accept {
        source: Arc<std::sync::Mutex<Option<Vec<u8>>>>,
        checks: Arc<AtomicUsize>,
    }
    #[async_trait]
    impl NativeCeremony for Accept {
        async fn verify_user(&self) -> Result<(), VerificationCallbackError> {
            self.checks.fetch_add(1, Ordering::SeqCst);
            Ok(())
        }
        async fn persist_registration(
            &self,
            source: Vec<u8>,
        ) -> Result<(), PersistenceCallbackError> {
            *self.source.lock().unwrap() = Some(source);
            Ok(())
        }
    }
    struct Deny;
    #[async_trait]
    impl NativeCeremony for Deny {
        async fn verify_user(&self) -> Result<(), VerificationCallbackError> {
            Err(VerificationCallbackError::Denied)
        }
        async fn persist_registration(&self, _: Vec<u8>) -> Result<(), PersistenceCallbackError> {
            Ok(())
        }
    }
    struct Pending {
        started: Arc<Notify>,
        release: Arc<Notify>,
    }
    #[async_trait]
    impl NativeCeremony for Pending {
        async fn verify_user(&self) -> Result<(), VerificationCallbackError> {
            Ok(())
        }
        async fn persist_registration(&self, _: Vec<u8>) -> Result<(), PersistenceCallbackError> {
            self.started.notify_waiters();
            self.release.notified().await;
            Ok(())
        }
    }
    fn registration() -> NativeRegistrationInput {
        NativeRegistrationInput {
            rp_id: "example.com".into(),
            user_handle: b"bridge-test-user".to_vec(),
            username: "bridge-test-user".into(),
            display_name: None,
            client_data_hash: vec![9; 32],
            supported_algorithms: vec![-7],
            excluded_credential_ids: vec![],
        }
    }

    #[tokio::test]
    async fn bridge_commits_before_exposing_registration_and_preserves_none_display_name() {
        let checks = Arc::new(AtomicUsize::new(0));
        let source = Arc::new(std::sync::Mutex::new(None));
        let callback = Box::new(Accept {
            source: source.clone(),
            checks: checks.clone(),
        });
        let result = NativeOperation::new()
            .register(registration(), vec![], 4096, callback)
            .await
            .unwrap();
        assert_eq!(checks.load(Ordering::SeqCst), 1);
        assert!(!result.credential_id.is_empty() && !result.attestation_object.is_empty());
        let stored: SourceV1 =
            serde_json::from_slice(source.lock().unwrap().as_deref().unwrap()).unwrap();
        assert_eq!(stored.display_name, None);
    }
    #[tokio::test]
    async fn bridge_refuses_denied_validation_and_one_shot_reuse() {
        assert!(matches!(
            NativeOperation::new()
                .register(registration(), vec![], 4096, Box::new(Deny))
                .await,
            Err(BridgeError::VerificationDenied)
        ));
        let operation = NativeOperation::new();
        let callback = Box::new(Accept {
            source: Arc::new(std::sync::Mutex::new(None)),
            checks: Arc::new(AtomicUsize::new(0)),
        });
        operation
            .clone()
            .register(registration(), vec![], 4096, callback)
            .await
            .unwrap();
        assert!(matches!(
            operation
                .register(registration(), vec![], 4096, Box::new(Deny))
                .await,
            Err(BridgeError::AlreadyUsed)
        ));
    }
    #[tokio::test]
    async fn bridge_requires_adapter_source_limit_and_rejects_unsupported_input() {
        let operation = NativeOperation::new();
        assert!(matches!(
            operation
                .register(registration(), vec![], 0, Box::new(Deny))
                .await,
            Err(BridgeError::InvalidRequest)
        ));
        let mut request = registration();
        request.supported_algorithms = vec![-8];
        assert!(matches!(
            NativeOperation::new()
                .register(
                    request,
                    vec![],
                    4096,
                    Box::new(Accept {
                        source: Arc::new(std::sync::Mutex::new(None)),
                        checks: Arc::new(AtomicUsize::new(0))
                    })
                )
                .await,
            Err(BridgeError::InvalidRequest)
        ));
    }
    #[tokio::test]
    async fn bridge_cancellation_wins_after_persistence_dispatch_without_lost_wakeup() {
        let operation = NativeOperation::new();
        let started = Arc::new(Notify::new());
        let release = Arc::new(Notify::new());
        let mut wait_started = std::pin::pin!(started.notified());
        wait_started.as_mut().enable();
        let task = tokio::spawn(operation.clone().register(
            registration(),
            vec![],
            4096,
            Box::new(Pending {
                started: started.clone(),
                release: release.clone(),
            }),
        ));
        wait_started.await;
        operation.cancel();
        release.notify_waiters();
        assert!(matches!(task.await.unwrap(), Err(BridgeError::Cancelled)));
    }
    #[tokio::test]
    async fn bridge_completion_wins_when_persistence_releases_before_cancel() {
        let operation = NativeOperation::new();
        let started = Arc::new(Notify::new());
        let release = Arc::new(Notify::new());
        let mut wait_started = std::pin::pin!(started.notified());
        wait_started.as_mut().enable();
        let task = tokio::spawn(operation.clone().register(
            registration(),
            vec![],
            4096,
            Box::new(Pending {
                started: started.clone(),
                release: release.clone(),
            }),
        ));
        wait_started.await;
        release.notify_waiters();
        assert!(task.await.unwrap().is_ok());
        operation.cancel();
        assert!(matches!(
            operation
                .register(registration(), vec![], 4096, Box::new(Deny))
                .await,
            Err(BridgeError::AlreadyUsed)
        ));
    }
}
