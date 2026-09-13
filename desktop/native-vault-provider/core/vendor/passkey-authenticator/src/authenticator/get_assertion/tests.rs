use std::borrow::Cow;

use passkey_types::{
    Passkey, StoredHmacSecret,
    ctap2::{
        Aaguid, Ctap2Error, StatusCode,
        get_assertion::{ExtensionInputs, Options, Request},
        make_credential::{PublicKeyCredentialRpEntity, PublicKeyCredentialUserEntity},
    },
    rand::random_vec,
};

use crate::{
    Authenticator, BackupFlags, CredentialStore, MockUserValidationMethod, PasskeyAccessor, UiHint,
    UserCheck, UserValidationMethod,
    extensions::{self, prf_eval_request},
    user_validation::MockUiHint,
};

#[derive(Clone)]
struct BackedUpPasskey {
    passkey: Passkey,
    backup_flags: BackupFlags,
}

impl PasskeyAccessor for BackedUpPasskey {
    fn backup_flags(&self) -> BackupFlags {
        self.backup_flags
    }
    fn key(&self) -> Cow<'_, coset::CoseKey> {
        Cow::Borrowed(&self.passkey.key)
    }
    fn credential_id(&self) -> &[u8] {
        &self.passkey.credential_id
    }
    fn rp_id(&self) -> &str {
        &self.passkey.rp_id
    }
    fn user_handle(&self) -> Option<&[u8]> {
        self.passkey.user_handle.as_deref().map(|value| &**value)
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
    fn extensions(&self) -> Cow<'_, passkey_types::CredentialExtensions> {
        Cow::Borrowed(&self.passkey.extensions)
    }
}

struct OneCredentialStore(BackedUpPasskey);

#[async_trait::async_trait]
impl CredentialStore for OneCredentialStore {
    type PasskeyItem = BackedUpPasskey;
    async fn find_credentials(
        &self,
        _: Option<&[passkey_types::webauthn::PublicKeyCredentialDescriptor]>,
        _: &str,
        _: Option<&[u8]>,
    ) -> Result<Vec<Self::PasskeyItem>, StatusCode> {
        Ok(vec![self.0.clone()])
    }
    async fn save_credential(
        &mut self,
        _: Passkey,
        _: PublicKeyCredentialUserEntity,
        _: PublicKeyCredentialRpEntity,
        _: Options,
    ) -> Result<(), StatusCode> {
        Ok(())
    }
    async fn update_credential(&mut self, _: &Self::PasskeyItem) -> Result<(), StatusCode> {
        Ok(())
    }
    async fn get_info(&self) -> crate::StoreInfo {
        crate::StoreInfo {
            discoverability: crate::credential_store::DiscoverabilitySupport::Full,
        }
    }
}

struct AlwaysVerified;

#[async_trait::async_trait]
impl UserValidationMethod for AlwaysVerified {
    type PasskeyItem = BackedUpPasskey;
    async fn check_user<'a>(
        &self,
        _: UiHint<'a, Self::PasskeyItem>,
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

fn create_passkey(hmac_secret: Option<Vec<u8>>) -> Passkey {
    let builder = Passkey::mock("example.com".into());

    if let Some(hs) = hmac_secret {
        builder.hmac_secret(StoredHmacSecret {
            cred_with_uv: hs,
            cred_without_uv: None,
        })
    } else {
        builder
    }
    .build()
}

fn good_request() -> Request {
    Request {
        rp_id: "example.com".into(),
        client_data_hash: vec![0; 32].into(),
        allow_list: None,
        extensions: None,
        pin_auth: None,
        pin_protocol: None,
        options: Options {
            up: true,
            uv: true,
            rk: false,
        },
    }
}

#[tokio::test]
async fn get_assertion_replaces_backup_flags_from_immutable_credential() {
    for (eligible, backed_up, expected) in [
        (false, false, passkey_types::ctap2::Flags::empty()),
        (true, false, passkey_types::ctap2::Flags::BE),
        (
            true,
            true,
            passkey_types::ctap2::Flags::BE | passkey_types::ctap2::Flags::BS,
        ),
    ] {
        let passkey = BackedUpPasskey {
            passkey: create_passkey(None),
            backup_flags: BackupFlags::new(eligible, backed_up).unwrap(),
        };
        let mut authenticator = Authenticator::new(
            Aaguid::new_empty(),
            OneCredentialStore(passkey),
            AlwaysVerified,
        );
        let response = authenticator
            .get_assertion(good_request())
            .await
            .expect("assertion should succeed");
        assert_eq!(
            response.auth_data.flags
                & (passkey_types::ctap2::Flags::BE | passkey_types::ctap2::Flags::BS),
            expected,
        );
        assert!(
            response
                .auth_data
                .flags
                .contains(passkey_types::ctap2::Flags::UP | passkey_types::ctap2::Flags::UV)
        );
    }
}

#[tokio::test]
async fn get_assertion_returns_no_credentials_found() {
    // Arrange
    let request = good_request();
    let store = None;
    let mut authenticator = Authenticator::new(
        Aaguid::new_empty(),
        store,
        MockUserValidationMethod::verified_user_with_hint(1, MockUiHint::InformNoCredentialsFound),
    );

    // Act
    let response = authenticator.get_assertion(request).await;

    // Assert
    assert_eq!(response.unwrap_err(), Ctap2Error::NoCredentials.into(),);
}

#[tokio::test]
async fn get_assertion_increments_signature_counter_when_counter_is_some() {
    // Arrange
    let request = good_request();
    let passkey = Passkey {
        counter: Some(9000),
        ..create_passkey(None)
    };
    let store = Some(passkey.clone());
    let mut authenticator = Authenticator::new(
        Aaguid::new_empty(),
        store,
        MockUserValidationMethod::verified_user_with_hint(
            1,
            MockUiHint::RequestExistingCredential(passkey),
        ),
    );

    // Act
    let response = authenticator.get_assertion(request).await.unwrap();

    // Assert
    assert_eq!(response.auth_data.counter.unwrap(), 9001);
    assert_eq!(
        authenticator
            .store()
            .as_ref()
            .and_then(|c| c.counter)
            .unwrap(),
        9001
    );
}

#[tokio::test]
async fn unsupported_extension_with_request_gives_no_ext_output() {
    let shared_store = Some(create_passkey(None));
    let user_mock = MockUserValidationMethod::verified_user(1);

    let mut authenticator =
        Authenticator::new(Aaguid::new_empty(), shared_store.clone(), user_mock);

    let request = Request {
        extensions: Some(ExtensionInputs {
            prf: Some(prf_eval_request(Some(random_vec(32)))),
            ..Default::default()
        }),
        ..good_request()
    };

    let res = authenticator
        .get_assertion(request)
        .await
        .expect("error happened while trying to authenticate a credential");

    assert!(res.auth_data.extensions.is_none());
    assert!(res.unsigned_extension_outputs.is_none());
}

#[tokio::test]
async fn unsupported_extension_with_empty_request_gives_no_ext_output() {
    let shared_store = Some(create_passkey(None));
    let user_mock = MockUserValidationMethod::verified_user(1);

    let mut authenticator =
        Authenticator::new(Aaguid::new_empty(), shared_store.clone(), user_mock);

    let request = Request {
        extensions: Some(ExtensionInputs::default()),
        ..good_request()
    };

    let res = authenticator
        .get_assertion(request)
        .await
        .expect("error happened while trying to authenticate a credential");

    assert!(res.auth_data.extensions.is_none());
    assert!(res.unsigned_extension_outputs.is_none());
}

#[tokio::test]
async fn supported_extension_with_empty_request_gives_no_ext_output() {
    let shared_store = Some(create_passkey(Some(random_vec(32))));
    let user_mock = MockUserValidationMethod::verified_user(1);

    let mut authenticator =
        Authenticator::new(Aaguid::new_empty(), shared_store.clone(), user_mock)
            .hmac_secret(extensions::HmacSecretConfig::new_with_uv_only());

    let request = Request {
        extensions: Some(ExtensionInputs::default()),
        ..good_request()
    };

    let res = authenticator
        .get_assertion(request)
        .await
        .expect("error happened while trying to authenticate a credential");

    assert!(res.auth_data.extensions.is_none());
    assert!(res.unsigned_extension_outputs.is_none());
}

#[tokio::test]
async fn supported_extension_without_extension_request_gives_no_ext_output() {
    let shared_store = Some(create_passkey(Some(random_vec(32))));
    let user_mock = MockUserValidationMethod::verified_user(1);

    let mut authenticator =
        Authenticator::new(Aaguid::new_empty(), shared_store.clone(), user_mock)
            .hmac_secret(extensions::HmacSecretConfig::new_with_uv_only());

    let request = good_request();

    let res = authenticator
        .get_assertion(request)
        .await
        .expect("error happened while trying to authenticate a credential");

    assert!(res.auth_data.extensions.is_none());
    assert!(res.unsigned_extension_outputs.is_none());
}

#[tokio::test]
async fn supported_extension_with_request_gives_output() {
    let shared_store = Some(create_passkey(Some(random_vec(32))));
    let user_mock = MockUserValidationMethod::verified_user(1);

    let mut authenticator =
        Authenticator::new(Aaguid::new_empty(), shared_store.clone(), user_mock)
            .hmac_secret(extensions::HmacSecretConfig::new_with_uv_only());

    let request = Request {
        extensions: Some(ExtensionInputs {
            prf: Some(prf_eval_request(Some(random_vec(32)))),
            ..Default::default()
        }),
        ..good_request()
    };

    let res = authenticator
        .get_assertion(request)
        .await
        .expect("error happened while trying to authenticate a credential");

    assert!(res.auth_data.extensions.is_none());
    assert!(res.unsigned_extension_outputs.is_some());
    assert!(res.unsigned_extension_outputs.unwrap().prf.is_some());
}
