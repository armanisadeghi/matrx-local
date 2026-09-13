//! Provider-private passkey operations and registration persistence gate.
use async_trait::async_trait;
use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
use coset::{CborSerializable, CoseKey, Label, RegisteredLabel, RegisteredLabelWithPrivate, iana};
use p256::elliptic_curve::sec1::ToEncodedPoint;
use passkey_authenticator::{
    Authenticator, BackupFlags, CredentialIdLength, CredentialStore, DiscoverabilitySupport,
    PasskeyAccessor, StoreInfo, UserValidationMethod,
};
use passkey_types::{
    CredentialExtensions, Passkey,
    ctap2::make_credential::{
        Options as MakeOptions, PublicKeyCredentialRpEntity, PublicKeyCredentialUserEntity,
    },
    ctap2::{Aaguid, Ctap2Code, Ctap2Error, StatusCode},
    webauthn::PublicKeyCredentialDescriptor,
};
use serde::de::{Error as _, IgnoredAny, MapAccess, Visitor};
use serde::{Deserialize, Serialize};
use std::borrow::Cow;
use zeroize::Zeroizing;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FixedError {
    PersistenceFailed,
    InvalidSource,
    InvalidRequest,
    VerificationDenied,
    CredentialExcluded,
    NoCredentials,
    OperationFailed,
}

fn map_status(status: StatusCode) -> FixedError {
    use Ctap2Error::*;
    match status {
        StatusCode::Ctap2(Ctap2Code::Known(CredentialExcluded)) => FixedError::CredentialExcluded,
        StatusCode::Ctap2(Ctap2Code::Known(NoCredentials)) => FixedError::NoCredentials,
        StatusCode::Ctap2(Ctap2Code::Known(
            OperationDenied
            | KeepAliveCancel
            | UserActionTimeout
            | ActionTimeout
            | UserPresenceRequired
            | UserVerificationBlocked
            | UserVerificationInvalid,
        )) => FixedError::VerificationDenied,
        StatusCode::Ctap2(Ctap2Code::Known(
            CborUnexpectedType | InvalidCbor | MissingParameter | LimitExceeded
            | UnsupportedAlgorithm | UnsupportedOption | InvalidOption | RequestTooLarge
            | InvalidSubcommand,
        )) => FixedError::InvalidRequest,
        _ => FixedError::OperationFailed,
    }
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceV1 {
    version: u8,
    credential_id: String,
    rp_id: String,
    user_handle: String,
    #[serde(deserialize_with = "required_option")]
    username: Option<String>,
    #[serde(deserialize_with = "required_option")]
    display_name: Option<String>,
    private_cose_key: String,
    counter: Option<u32>,
    extensions: serde_json::Map<String, serde_json::Value>,
    backup_eligible: bool,
    backup_state: bool,
}
fn required_option<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer)
}

pub fn canonical_source(
    bytes: &[u8],
    max_source_bytes: usize,
) -> Result<Zeroizing<Vec<u8>>, FixedError> {
    if max_source_bytes == 0 || bytes.len() > max_source_bytes {
        return Err(FixedError::InvalidSource);
    }
    reject_duplicate_source_keys(bytes)?;
    let value: SourceV1 = serde_json::from_slice(bytes).map_err(|_| FixedError::InvalidSource)?;
    validate_source(&value)?;
    let canonical = serde_json::to_vec(&value).map_err(|_| FixedError::InvalidSource)?;
    if canonical != bytes {
        return Err(FixedError::InvalidSource);
    }
    Ok(Zeroizing::new(canonical))
}
fn validate_source(v: &SourceV1) -> Result<(), FixedError> {
    if v.version != 1
        || v.counter.is_some()
        || !v.extensions.is_empty()
        || !v.backup_eligible
        || !v.backup_state
        || !valid_rp(&v.rp_id)
    {
        return Err(FixedError::InvalidSource);
    }
    if !(16..=1023).contains(&decode(&v.credential_id)?.len())
        || !(1..=64).contains(&decode(&v.user_handle)?.len())
    {
        return Err(FixedError::InvalidSource);
    }
    validate_cose_key(&decode(&v.private_cose_key)?)
}
fn reject_duplicate_source_keys(bytes: &[u8]) -> Result<(), FixedError> {
    struct UniqueKeys;
    impl<'de> Visitor<'de> for UniqueKeys {
        type Value = ();
        fn expecting(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            f.write_str("a source object")
        }
        fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<(), A::Error> {
            let mut seen = std::collections::BTreeSet::new();
            while let Some(key) = map.next_key::<String>()? {
                if !seen.insert(key) {
                    return Err(A::Error::custom("duplicate source key"));
                }
                map.next_value::<IgnoredAny>()?;
            }
            Ok(())
        }
    }
    let mut d = serde_json::Deserializer::from_slice(bytes);
    serde::de::Deserializer::deserialize_map(&mut d, UniqueKeys)
        .map_err(|_| FixedError::InvalidSource)?;
    d.end().map_err(|_| FixedError::InvalidSource)
}
fn validate_cose_key(bytes: &[u8]) -> Result<(), FixedError> {
    let key = CoseKey::from_slice(bytes).map_err(|_| FixedError::InvalidSource)?;
    if key
        .clone()
        .to_vec()
        .map_err(|_| FixedError::InvalidSource)?
        != bytes
        || !matches!(key.kty, RegisteredLabel::Assigned(iana::KeyType::EC2))
        || !matches!(
            key.alg,
            Some(RegisteredLabelWithPrivate::Assigned(iana::Algorithm::ES256))
        )
        || !key.key_id.is_empty()
        || !key.key_ops.is_empty()
        || !key.base_iv.is_empty()
        || key.params.len() != 4
    {
        return Err(FixedError::InvalidSource);
    }
    let (mut x, mut y, mut d, mut curve) = (None, None, None, false);
    for (label, value) in &key.params {
        match label {
            Label::Int(-1) => {
                curve = matches!(value, coset::cbor::value::Value::Integer(v) if i64::try_from(*v).ok() == Some(1))
            }
            Label::Int(-2) => x = value.as_bytes(),
            Label::Int(-3) => y = value.as_bytes(),
            Label::Int(-4) => d = value.as_bytes(),
            _ => return Err(FixedError::InvalidSource),
        }
    }
    let (Some(x), Some(y), Some(d)) = (x, y, d) else {
        return Err(FixedError::InvalidSource);
    };
    if !curve || x.len() != 32 || y.len() != 32 || d.len() != 32 {
        return Err(FixedError::InvalidSource);
    }
    let public = p256::SecretKey::from_slice(d)
        .map_err(|_| FixedError::InvalidSource)?
        .public_key()
        .to_encoded_point(false);
    if public.x().map(AsRef::<[u8]>::as_ref) != Some(x)
        || public.y().map(AsRef::<[u8]>::as_ref) != Some(y)
    {
        return Err(FixedError::InvalidSource);
    }
    Ok(())
}
fn decode(v: &str) -> Result<Vec<u8>, FixedError> {
    if v.contains('=') {
        return Err(FixedError::InvalidSource);
    }
    let d = URL_SAFE_NO_PAD
        .decode(v)
        .map_err(|_| FixedError::InvalidSource)?;
    (URL_SAFE_NO_PAD.encode(&d) == v)
        .then_some(d)
        .ok_or(FixedError::InvalidSource)
}
fn valid_rp(rp: &str) -> bool {
    if rp == "localhost" {
        return true;
    }
    if rp.parse::<std::net::IpAddr>().is_ok()
        || rp.len() > 253
        || !rp.is_ascii()
        || !rp.contains('.')
    {
        return false;
    }
    rp.split('.').all(|l| {
        !l.is_empty()
            && l.len() <= 63
            && l.as_bytes().first().is_some_and(u8::is_ascii_alphanumeric)
            && l.as_bytes().last().is_some_and(u8::is_ascii_alphanumeric)
            && l.bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
    })
}

/// Opaque provider-owned credential view used only by the upstream validation trait.
#[derive(Clone)]
pub struct StoredCredential {
    passkey: Passkey,
}
impl StoredCredential {
    fn from_source(bytes: &[u8], max: usize) -> Result<Self, FixedError> {
        let canonical = canonical_source(bytes, max)?;
        let s: SourceV1 =
            serde_json::from_slice(&canonical).map_err(|_| FixedError::InvalidSource)?;
        Ok(Self {
            passkey: Passkey {
                key: CoseKey::from_slice(&decode(&s.private_cose_key)?)
                    .map_err(|_| FixedError::InvalidSource)?,
                credential_id: decode(&s.credential_id)?.into(),
                rp_id: s.rp_id,
                user_handle: Some(decode(&s.user_handle)?.into()),
                username: s.username,
                user_display_name: s.display_name,
                counter: None,
                extensions: Default::default(),
            },
        })
    }
    fn encode(&self) -> Result<Zeroizing<Vec<u8>>, FixedError> {
        let s = SourceV1 {
            version: 1,
            credential_id: URL_SAFE_NO_PAD.encode(&*self.passkey.credential_id),
            rp_id: self.passkey.rp_id.clone(),
            user_handle: URL_SAFE_NO_PAD.encode(
                self.passkey
                    .user_handle
                    .as_deref()
                    .ok_or(FixedError::InvalidSource)?,
            ),
            username: self.passkey.username.clone(),
            display_name: self.passkey.user_display_name.clone(),
            private_cose_key: URL_SAFE_NO_PAD.encode(
                self.passkey
                    .key
                    .clone()
                    .to_vec()
                    .map_err(|_| FixedError::InvalidSource)?,
            ),
            counter: None,
            extensions: Default::default(),
            backup_eligible: true,
            backup_state: true,
        };
        let b = serde_json::to_vec(&s).map_err(|_| FixedError::InvalidSource)?;
        canonical_source(&b, b.len())
    }
}
impl PasskeyAccessor for StoredCredential {
    fn backup_flags(&self) -> BackupFlags {
        BackupFlags::new(true, true).expect("validated")
    }
    fn key(&self) -> Cow<'_, CoseKey> {
        Cow::Borrowed(&self.passkey.key)
    }
    fn credential_id(&self) -> &[u8] {
        &self.passkey.credential_id
    }
    fn rp_id(&self) -> &str {
        &self.passkey.rp_id
    }
    fn user_handle(&self) -> Option<&[u8]> {
        self.passkey.user_handle.as_deref().map(|v| &**v)
    }
    fn username(&self) -> Option<&str> {
        self.passkey.username.as_deref()
    }
    fn user_display_name(&self) -> Option<&str> {
        self.passkey.user_display_name.as_deref()
    }
    fn counter(&self) -> Option<u32> {
        None
    }
    fn set_counter(&mut self, _: u32) {}
    fn extensions(&self) -> Cow<'_, CredentialExtensions> {
        Cow::Borrowed(&self.passkey.extensions)
    }
}

struct CaptureStore {
    existing: Vec<StoredCredential>,
    captured: Option<StoredCredential>,
}
#[async_trait]
impl CredentialStore for CaptureStore {
    type PasskeyItem = StoredCredential;
    async fn find_credentials(
        &self,
        ids: Option<&[PublicKeyCredentialDescriptor]>,
        rp: &str,
        _: Option<&[u8]>,
    ) -> Result<Vec<Self::PasskeyItem>, StatusCode> {
        let out: Vec<_> = self
            .existing
            .iter()
            .filter(|c| {
                c.rp_id() == rp
                    && ids.map_or(false, |ids| {
                        ids.iter().any(|id| {
                            id.ty == passkey_types::webauthn::PublicKeyCredentialType::PublicKey
                                && id.id.as_slice() == c.credential_id()
                        })
                    })
            })
            .cloned()
            .collect();
        if out.is_empty() {
            Err(Ctap2Error::NoCredentials.into())
        } else {
            Ok(out)
        }
    }
    async fn save_credential(
        &mut self,
        cred: Passkey,
        _: PublicKeyCredentialUserEntity,
        _: PublicKeyCredentialRpEntity,
        _: MakeOptions,
    ) -> Result<(), StatusCode> {
        self.captured = Some(StoredCredential { passkey: cred });
        Ok(())
    }
    async fn update_credential(&mut self, _: &Self::PasskeyItem) -> Result<(), StatusCode> {
        Ok(())
    }
    async fn get_info(&self) -> StoreInfo {
        StoreInfo {
            discoverability: DiscoverabilitySupport::ForcedDiscoverable,
        }
    }
}
struct SingleSourceStore(StoredCredential);
#[async_trait]
impl CredentialStore for SingleSourceStore {
    type PasskeyItem = StoredCredential;
    async fn find_credentials(
        &self,
        ids: Option<&[PublicKeyCredentialDescriptor]>,
        rp: &str,
        _: Option<&[u8]>,
    ) -> Result<Vec<Self::PasskeyItem>, StatusCode> {
        if self.0.rp_id() != rp
            || ids.is_some_and(|ids| {
                !ids.is_empty()
                    && !ids.iter().any(|id| {
                        id.ty == passkey_types::webauthn::PublicKeyCredentialType::PublicKey
                            && id.id.as_slice() == self.0.credential_id()
                    })
            })
        {
            Err(Ctap2Error::NoCredentials.into())
        } else {
            Ok(vec![self.0.clone()])
        }
    }
    async fn save_credential(
        &mut self,
        _: Passkey,
        _: PublicKeyCredentialUserEntity,
        _: PublicKeyCredentialRpEntity,
        _: MakeOptions,
    ) -> Result<(), StatusCode> {
        Err(Ctap2Error::OperationDenied.into())
    }
    async fn update_credential(&mut self, _: &Self::PasskeyItem) -> Result<(), StatusCode> {
        Ok(())
    }
    async fn get_info(&self) -> StoreInfo {
        StoreInfo {
            discoverability: DiscoverabilitySupport::ForcedDiscoverable,
        }
    }
}

#[async_trait]
pub trait RegistrationPersister {
    async fn persist(&mut self, canonical_source: &[u8]) -> Result<(), FixedError>;
}
pub struct PreparedRegistration {
    canonical_source: Zeroizing<Vec<u8>>,
    response: passkey_types::ctap2::make_credential::Response,
}
impl PreparedRegistration {
    pub fn canonical_source_bytes(&self) -> &[u8] {
        &self.canonical_source
    }
    pub async fn commit_with<P: RegistrationPersister>(
        self,
        p: &mut P,
    ) -> Result<CommittedRegistration, FixedError> {
        p.persist(&self.canonical_source).await?;
        Ok(CommittedRegistration {
            response: self.response,
        })
    }
}
pub struct CommittedRegistration {
    response: passkey_types::ctap2::make_credential::Response,
}
impl CommittedRegistration {
    pub fn into_response(self) -> passkey_types::ctap2::make_credential::Response {
        self.response
    }
}

fn valid_make(r: &passkey_types::ctap2::make_credential::Request) -> bool {
    valid_rp(&r.rp.id)
        && r.client_data_hash.len() == 32
        && (1..=64).contains(&r.user.id.len())
        && r.options.rk
        && r.options.up
        && r.options.uv
        && r.extensions.is_none()
        && r.pin_auth.is_none()
        && r.pin_protocol.is_none()
        && r.exclude_list.as_ref().is_none_or(|ids| {
            ids.iter()
                .all(|id| id.ty == passkey_types::webauthn::PublicKeyCredentialType::PublicKey)
        })
        && !r.pub_key_cred_params.is_empty()
        && r.pub_key_cred_params.iter().all(|param| {
            param.ty == passkey_types::webauthn::PublicKeyCredentialType::PublicKey
                && param.alg == iana::Algorithm::ES256
        })
}
fn valid_get(r: &passkey_types::ctap2::get_assertion::Request) -> bool {
    valid_rp(&r.rp_id)
        && r.client_data_hash.len() == 32
        && r.options.up
        && r.options.uv
        && !r.options.rk
        && r.extensions.is_none()
        && r.pin_auth.is_none()
        && r.pin_protocol.is_none()
        && r.allow_list.as_ref().is_none_or(|ids| {
            ids.iter()
                .all(|id| id.ty == passkey_types::webauthn::PublicKeyCredentialType::PublicKey)
        })
}
pub async fn prepare_registration<U>(
    request: passkey_types::ctap2::make_credential::Request,
    user_validation: U,
    existing_sources: &[&[u8]],
    max_source_bytes: usize,
) -> Result<PreparedRegistration, FixedError>
where
    U: UserValidationMethod<PasskeyItem = StoredCredential> + Sync,
{
    if !valid_make(&request) || max_source_bytes == 0 {
        return Err(FixedError::InvalidRequest);
    }
    let existing = existing_sources
        .iter()
        .map(|s| StoredCredential::from_source(s, max_source_bytes))
        .collect::<Result<_, _>>()?;
    let mut a = Authenticator::new(
        Aaguid::new_empty(),
        CaptureStore {
            existing,
            captured: None,
        },
        user_validation,
    );
    a.set_make_credential_id_length(CredentialIdLength::from(32));
    let response = a
        .make_credential_with_backup_flags(request, BackupFlags::new(true, true).expect("valid"))
        .await
        .map_err(map_status)?;
    let source = a
        .store()
        .captured
        .as_ref()
        .ok_or(FixedError::OperationFailed)?
        .encode()?;
    if source.len() > max_source_bytes {
        return Err(FixedError::InvalidSource);
    }
    Ok(PreparedRegistration {
        canonical_source: source,
        response,
    })
}
pub async fn authenticate<U>(
    canonical_source: &[u8],
    request: passkey_types::ctap2::get_assertion::Request,
    user_validation: U,
    max_source_bytes: usize,
) -> Result<passkey_types::ctap2::get_assertion::Response, FixedError>
where
    U: UserValidationMethod<PasskeyItem = StoredCredential> + Sync,
{
    if !valid_get(&request) {
        return Err(FixedError::InvalidRequest);
    }
    let source = StoredCredential::from_source(canonical_source, max_source_bytes)?;
    let mut a = Authenticator::new(
        Aaguid::new_empty(),
        SingleSourceStore(source),
        user_validation,
    );
    a.get_assertion(request).await.map_err(map_status)
}

#[cfg(test)]
mod tests {
    use super::*;
    use passkey_authenticator::{UiHint, UserCheck};
    use passkey_types::{
        ctap2::{Flags, get_assertion, make_credential},
        webauthn,
    };
    struct Uv;
    #[async_trait]
    impl UserValidationMethod for Uv {
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
    struct DeniedUv;
    #[async_trait]
    impl UserValidationMethod for DeniedUv {
        type PasskeyItem = StoredCredential;
        async fn check_user<'a>(
            &self,
            _: UiHint<'a, StoredCredential>,
            _: bool,
            _: bool,
        ) -> Result<UserCheck, Ctap2Error> {
            Ok(UserCheck {
                presence: true,
                verification: false,
            })
        }
        fn is_presence_enabled(&self) -> bool {
            true
        }
        fn is_verification_enabled(&self) -> Option<bool> {
            Some(true)
        }
    }
    struct DeniedUp;
    #[async_trait]
    impl UserValidationMethod for DeniedUp {
        type PasskeyItem = StoredCredential;
        async fn check_user<'a>(
            &self,
            _: UiHint<'a, StoredCredential>,
            _: bool,
            _: bool,
        ) -> Result<UserCheck, Ctap2Error> {
            Ok(UserCheck {
                presence: false,
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
    struct OkPersist;
    #[async_trait]
    impl RegistrationPersister for OkPersist {
        async fn persist(&mut self, _: &[u8]) -> Result<(), FixedError> {
            Ok(())
        }
    }
    fn make_request(
        exclude_list: Option<Vec<webauthn::PublicKeyCredentialDescriptor>>,
    ) -> make_credential::Request {
        make_credential::Request {
            client_data_hash: vec![1; 32].into(),
            rp: PublicKeyCredentialRpEntity {
                id: "example.com".into(),
                name: Some("Example".into()),
            },
            user: webauthn::PublicKeyCredentialUserEntity {
                id: b"user".to_vec().into(),
                name: "user".into(),
                display_name: "User".into(),
            },
            pub_key_cred_params: vec![webauthn::PublicKeyCredentialParameters {
                ty: webauthn::PublicKeyCredentialType::PublicKey,
                alg: iana::Algorithm::ES256,
            }],
            exclude_list,
            extensions: None,
            options: MakeOptions {
                rk: true,
                up: true,
                uv: true,
            },
            pin_auth: None,
            pin_protocol: None,
        }
    }
    fn get_request(
        allow_list: Option<Vec<webauthn::PublicKeyCredentialDescriptor>>,
        rp: &str,
    ) -> get_assertion::Request {
        get_assertion::Request {
            rp_id: rp.into(),
            client_data_hash: vec![2; 32].into(),
            allow_list,
            extensions: None,
            options: get_assertion::Options {
                rk: false,
                up: true,
                uv: true,
            },
            pin_auth: None,
            pin_protocol: None,
        }
    }
    fn valid_source() -> Vec<u8> {
        let secret = p256::SecretKey::from_slice(&[7; 32]).unwrap();
        let p = secret.public_key().to_encoded_point(false);
        let key = coset::CoseKeyBuilder::new_ec2_priv_key(
            iana::EllipticCurve::P_256,
            p.x().unwrap().to_vec(),
            p.y().unwrap().to_vec(),
            secret.to_bytes().to_vec(),
        )
        .algorithm(iana::Algorithm::ES256)
        .build()
        .to_vec()
        .unwrap();
        serde_json::to_vec(&SourceV1 {
            version: 1,
            credential_id: URL_SAFE_NO_PAD.encode([1_u8; 16]),
            rp_id: "example.com".into(),
            user_handle: URL_SAFE_NO_PAD.encode(b"user"),
            username: None,
            display_name: None,
            private_cose_key: URL_SAFE_NO_PAD.encode(key),
            counter: None,
            extensions: Default::default(),
            backup_eligible: true,
            backup_state: true,
        })
        .unwrap()
    }
    #[test]
    fn canonical_fixture_and_numeric_ip_refusal() {
        let source = valid_source();
        assert_eq!(canonical_source(&source, 4096).unwrap().as_slice(), source);
        let mut ip: SourceV1 = serde_json::from_slice(&source).unwrap();
        ip.rp_id = "127.0.0.1".into();
        assert_eq!(
            canonical_source(&serde_json::to_vec(&ip).unwrap(), 4096),
            Err(FixedError::InvalidSource)
        );
    }
    #[tokio::test]
    async fn maintained_make_and_get_emit_required_flags_and_discoverable_paths() {
        let prepared = prepare_registration(make_request(None), Uv, &[], 4096)
            .await
            .unwrap();
        let source = prepared.canonical_source_bytes().to_vec();
        let committed = prepared.commit_with(&mut OkPersist).await.unwrap();
        let make = committed.into_response();
        assert!(
            make.auth_data
                .flags
                .contains(Flags::UP | Flags::UV | Flags::BE | Flags::BS)
        );
        for allow in [None, Some(vec![])] {
            let assertion = authenticate(&source, get_request(allow, "example.com"), Uv, 4096)
                .await
                .unwrap();
            assert!(
                assertion
                    .auth_data
                    .flags
                    .contains(Flags::UP | Flags::UV | Flags::BE | Flags::BS)
            );
            assert_eq!(assertion.auth_data.counter, None);
        }
    }
    #[tokio::test]
    async fn assertion_refuses_wrong_rp_and_nonmatching_allowlist() {
        let source = valid_source();
        assert!(matches!(
            authenticate(&source, get_request(None, "other.example"), Uv, 4096).await,
            Err(FixedError::NoCredentials)
        ));
        let descriptor = webauthn::PublicKeyCredentialDescriptor {
            ty: webauthn::PublicKeyCredentialType::PublicKey,
            id: vec![9; 16].into(),
            transports: None,
        };
        assert!(matches!(
            authenticate(
                &source,
                get_request(Some(vec![descriptor]), "example.com"),
                Uv,
                4096
            )
            .await,
            Err(FixedError::NoCredentials)
        ));
    }
    #[tokio::test]
    async fn exclusion_matches_existing_source_across_user_handles() {
        let source = valid_source();
        let existing = [&source[..]];
        let descriptor = webauthn::PublicKeyCredentialDescriptor {
            ty: webauthn::PublicKeyCredentialType::PublicKey,
            id: vec![1; 16].into(),
            transports: None,
        };
        let mut request = make_request(Some(vec![descriptor]));
        let source_record: SourceV1 = serde_json::from_slice(&source).unwrap();
        let old_handle = decode(&source_record.user_handle).unwrap();
        request.user.id = b"different-new-user".to_vec().into();
        assert_ne!(&*request.user.id, old_handle.as_slice());
        assert!(matches!(
            prepare_registration(request, Uv, &existing, 4096).await,
            Err(FixedError::CredentialExcluded)
        ));
    }

    #[tokio::test]
    async fn actual_user_presence_and_verification_denials_are_refused() {
        assert!(matches!(
            prepare_registration(make_request(None), DeniedUv, &[], 4096).await,
            Err(FixedError::VerificationDenied)
        ));
        assert!(matches!(
            prepare_registration(make_request(None), DeniedUp, &[], 4096).await,
            Err(FixedError::VerificationDenied)
        ));
    }

    struct FailingPersist;
    #[async_trait]
    impl RegistrationPersister for FailingPersist {
        async fn persist(&mut self, _: &[u8]) -> Result<(), FixedError> {
            Err(FixedError::PersistenceFailed)
        }
    }
    struct PendingPersist(std::sync::Arc<std::sync::atomic::AtomicBool>);
    #[async_trait]
    impl RegistrationPersister for PendingPersist {
        async fn persist(&mut self, _: &[u8]) -> Result<(), FixedError> {
            self.0.store(true, std::sync::atomic::Ordering::SeqCst);
            std::future::pending().await
        }
    }
    #[tokio::test]
    async fn failed_or_cancelled_commit_never_releases_registration_response() {
        let prepared = prepare_registration(make_request(None), Uv, &[], 4096)
            .await
            .unwrap();
        assert!(matches!(
            prepared.commit_with(&mut FailingPersist).await,
            Err(FixedError::PersistenceFailed)
        ));
        let prepared = prepare_registration(make_request(None), Uv, &[], 4096)
            .await
            .unwrap();
        let entered = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let mut persister = PendingPersist(entered.clone());
        let mut pending = Box::pin(prepared.commit_with(&mut persister));
        std::future::poll_fn(|cx| {
            assert!(matches!(
                std::future::Future::poll(pending.as_mut(), cx),
                std::task::Poll::Pending
            ));
            std::task::Poll::Ready(())
        })
        .await;
        drop(pending);
        assert!(entered.load(std::sync::atomic::Ordering::SeqCst));
    }
    #[test]
    fn total_status_mapping_covers_all_bytes_and_fixed_named_groups() {
        for byte in 0_u8..=u8::MAX {
            let expected = match byte {
                0x19 => FixedError::CredentialExcluded,
                0x2e => FixedError::NoCredentials,
                0x27 | 0x2d | 0x2f | 0x3a | 0x3b | 0x3c | 0x3f => FixedError::VerificationDenied,
                0x11 | 0x12 | 0x14 | 0x15 | 0x26 | 0x2b | 0x2c | 0x39 | 0x3e => {
                    FixedError::InvalidRequest
                }
                _ => FixedError::OperationFailed,
            };
            assert_eq!(map_status(StatusCode::from(byte)), expected, "0x{byte:02x}");
        }
        assert_eq!(
            map_status(Ctap2Error::CredentialExcluded.into()),
            FixedError::CredentialExcluded
        );
        assert_eq!(
            map_status(Ctap2Error::NoCredentials.into()),
            FixedError::NoCredentials
        );
        assert_eq!(
            map_status(Ctap2Error::OperationDenied.into()),
            FixedError::VerificationDenied
        );
        assert_eq!(
            map_status(Ctap2Error::InvalidCbor.into()),
            FixedError::InvalidRequest
        );
        assert_eq!(
            map_status(Ctap2Error::Ok.into()),
            FixedError::OperationFailed
        );
    }
    #[test]
    fn valid_fixture_one_mutation_codec_rejections() {
        let source = valid_source();
        let mut missing: serde_json::Map<String, serde_json::Value> =
            serde_json::from_slice(&source).unwrap();
        missing.remove("user_handle");
        assert_eq!(
            canonical_source(&serde_json::to_vec(&missing).unwrap(), 4096),
            Err(FixedError::InvalidSource)
        );
        let duplicate = String::from_utf8(source.clone()).unwrap().replacen(
            "\"version\":1",
            "\"version\":1,\"version\":1",
            1,
        );
        assert_eq!(
            canonical_source(duplicate.as_bytes(), 4096),
            Err(FixedError::InvalidSource)
        );
        let whitespace = [b" ".as_slice(), source.as_slice()].concat();
        assert_eq!(
            canonical_source(&whitespace, 4096),
            Err(FixedError::InvalidSource)
        );
    }
}
