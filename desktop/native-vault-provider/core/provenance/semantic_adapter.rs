use async_trait::async_trait;
use coset::{CoseKey, iana};
#[cfg(feature = "patched-adapter")]
use passkey_authenticator::BackupFlags;
use passkey_authenticator::{
    Authenticator, CredentialStore, DiscoverabilitySupport, PasskeyAccessor, StoreInfo, UiHint,
    UserCheck, UserValidationMethod,
};
use passkey_types::{
    CredentialExtensions, Passkey,
    ctap2::{Aaguid, Ctap2Error, Flags, StatusCode, get_assertion, make_credential},
    webauthn,
};
use std::borrow::Cow;
const RP_ID: &str = "example.com";
const ORIGINAL_USER: &[u8] = b"immutable-user-a";
const DIFFERENT_USER: &[u8] = b"immutable-user-b";
#[derive(Clone)]
struct ImmutableFixture {
    passkey: Passkey,
    backup_eligible: bool,
    backup_state: bool,
}
impl PasskeyAccessor for ImmutableFixture {
    #[cfg(feature = "patched-adapter")]
    fn backup_flags(&self) -> BackupFlags {
        BackupFlags::new(self.backup_eligible, self.backup_state)
            .expect("immutable BE/BS fixture is valid")
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
        self.passkey.user_handle.as_deref().map(Vec::as_slice)
    }
    fn username(&self) -> Option<&str> {
        self.passkey.username.as_deref()
    }
    fn user_display_name(&self) -> Option<&str> {
        self.passkey.user_display_name.as_deref()
    }
    fn counter(&self) -> Option<u32> {
        self.passkey.counter
    }
    fn set_counter(&mut self, counter: u32) {
        self.passkey.counter = Some(counter);
    }
    fn extensions(&self) -> Cow<'_, CredentialExtensions> {
        Cow::Borrowed(&self.passkey.extensions)
    }
}
struct FixtureStore {
    credentials: Vec<ImmutableFixture>,
    backup_eligible: bool,
    backup_state: bool,
}
impl FixtureStore {
    fn new(backup_eligible: bool, backup_state: bool) -> Self {
        Self {
            credentials: Vec::new(),
            backup_eligible,
            backup_state,
        }
    }
}
#[async_trait]
impl CredentialStore for FixtureStore {
    type PasskeyItem = ImmutableFixture;
    async fn find_credentials(
        &self,
        ids: Option<&[webauthn::PublicKeyCredentialDescriptor]>,
        rp_id: &str,
        _user_handle: Option<&[u8]>,
    ) -> Result<Vec<Self::PasskeyItem>, StatusCode> {
        let found = self
            .credentials
            .iter()
            .filter(|f| {
                f.rp_id() == rp_id
                    && ids.map_or(true, |list| {
                        list.iter().any(|id| id.id.as_slice() == f.credential_id())
                    })
            })
            .cloned()
            .collect::<Vec<_>>();
        if found.is_empty() {
            Err(Ctap2Error::NoCredentials.into())
        } else {
            Ok(found)
        }
    }
    async fn save_credential(
        &mut self,
        credential: Passkey,
        _user: make_credential::PublicKeyCredentialUserEntity,
        _rp: make_credential::PublicKeyCredentialRpEntity,
        _options: make_credential::Options,
    ) -> Result<(), StatusCode> {
        self.credentials.push(ImmutableFixture {
            passkey: credential,
            backup_eligible: self.backup_eligible,
            backup_state: self.backup_state,
        });
        Ok(())
    }
    async fn update_credential(
        &mut self,
        credential: &Self::PasskeyItem,
    ) -> Result<(), StatusCode> {
        let slot = self
            .credentials
            .iter_mut()
            .find(|stored| stored.credential_id() == credential.credential_id())
            .ok_or(Ctap2Error::NoCredentials)?;
        *slot = credential.clone();
        Ok(())
    }
    async fn get_info(&self) -> StoreInfo {
        StoreInfo {
            discoverability: DiscoverabilitySupport::Full,
        }
    }
}
struct AlwaysUv;
#[async_trait]
impl UserValidationMethod for AlwaysUv {
    type PasskeyItem = ImmutableFixture;
    async fn check_user<'a>(
        &self,
        _: UiHint<'a, ImmutableFixture>,
        up: bool,
        uv: bool,
    ) -> Result<UserCheck, Ctap2Error> {
        Ok(UserCheck {
            presence: up,
            verification: uv,
        })
    }
    fn is_presence_enabled(&self) -> bool {
        true
    }
    fn is_verification_enabled(&self) -> Option<bool> {
        Some(true)
    }
}
fn make_request(
    user: &[u8],
    exclude_list: Option<Vec<webauthn::PublicKeyCredentialDescriptor>>,
) -> make_credential::Request {
    make_credential::Request {
        client_data_hash: vec![7; 32].into(),
        rp: make_credential::PublicKeyCredentialRpEntity {
            id: RP_ID.into(),
            name: Some("Example".into()),
        },
        user: webauthn::PublicKeyCredentialUserEntity {
            id: user.to_vec().into(),
            name: "user".into(),
            display_name: "User".into(),
        },
        pub_key_cred_params: vec![webauthn::PublicKeyCredentialParameters {
            ty: webauthn::PublicKeyCredentialType::PublicKey,
            alg: iana::Algorithm::ES256,
        }],
        exclude_list,
        extensions: None,
        options: make_credential::Options {
            rk: true,
            up: true,
            uv: true,
        },
        pin_auth: None,
        pin_protocol: None,
    }
}
fn get_request() -> get_assertion::Request {
    get_assertion::Request {
        rp_id: RP_ID.into(),
        client_data_hash: vec![9; 32].into(),
        allow_list: None,
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
async fn make(
    auth: &mut Authenticator<FixtureStore, AlwaysUv>,
    request: make_credential::Request,
    backup_eligible: bool,
    backup_state: bool,
) -> Result<make_credential::Response, StatusCode> {
    #[cfg(feature = "patched-adapter")]
    {
        auth.make_credential_with_backup_flags(
            request,
            BackupFlags::new(backup_eligible, backup_state).expect("valid immutable fixture"),
        )
        .await
    }
    #[cfg(not(feature = "patched-adapter"))]
    {
        let _ = (backup_eligible, backup_state);
        auth.make_credential(request).await
    }
}
fn authenticator(
    backup_eligible: bool,
    backup_state: bool,
) -> Authenticator<FixtureStore, AlwaysUv> {
    Authenticator::new(
        Aaguid::new_empty(),
        FixtureStore::new(backup_eligible, backup_state),
        AlwaysUv,
    )
}
async fn assert_exact_backup_flags(backup_eligible: bool, backup_state: bool, expected: Flags) {
    let mut auth = authenticator(backup_eligible, backup_state);
    let make_response = make(
        &mut auth,
        make_request(ORIGINAL_USER, None),
        backup_eligible,
        backup_state,
    )
    .await
    .expect("registration");
    let get_response = auth
        .get_assertion(get_request())
        .await
        .expect("discoverable assertion");
    let mask = Flags::BE | Flags::BS;
    for (operation, flags) in [
        ("make", make_response.auth_data.flags),
        ("get", get_response.auth_data.flags),
    ] {
        assert_eq!(
            flags & mask,
            expected,
            "{operation} must derive immutable backup flags"
        );
        assert!(
            flags.contains(Flags::UP | Flags::UV),
            "{operation} must preserve UP/UV"
        );
    }
}
#[tokio::test]
async fn device_bound_backup_flags_are_absent_on_make_and_get() {
    assert_exact_backup_flags(false, false, Flags::empty()).await;
}
#[tokio::test]
async fn eligible_not_backed_up_has_only_be_on_make_and_get() {
    assert_exact_backup_flags(true, false, Flags::BE).await;
}
#[tokio::test]
async fn eligible_backed_up_is_intentional_control() {
    assert_exact_backup_flags(true, true, Flags::BE | Flags::BS).await;
}
#[tokio::test]
async fn cross_user_handle_exclusion_requires_credential_excluded() {
    let mut auth = authenticator(true, true);
    make(&mut auth, make_request(ORIGINAL_USER, None), true, true)
        .await
        .expect("first registration");
    let credential_id = auth.store().credentials[0].credential_id().to_vec();
    let exclude = webauthn::PublicKeyCredentialDescriptor {
        ty: webauthn::PublicKeyCredentialType::PublicKey,
        id: credential_id.into(),
        transports: None,
    };
    let result = make(
        &mut auth,
        make_request(DIFFERENT_USER, Some(vec![exclude])),
        true,
        true,
    )
    .await;
    assert!(
        matches!(result,Err(status) if status == Ctap2Error::CredentialExcluded.into()),
        "same RP and credential id must exclude despite a different user handle"
    );
}
