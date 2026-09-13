use async_trait::async_trait;
use coset::iana;
use native_vault_core::{
    FixedError, RegistrationPersister, StoredCredential, authenticate, prepare_registration,
};
use passkey_authenticator::{UiHint, UserCheck, UserValidationMethod};
use passkey_types::{
    ctap2::{Ctap2Error, Flags, get_assertion, make_credential},
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
struct Persist;
#[async_trait]
impl RegistrationPersister for Persist {
    async fn persist(&mut self, _: &[u8]) -> Result<(), FixedError> {
        Ok(())
    }
}
fn make() -> make_credential::Request {
    make_credential::Request {
        client_data_hash: vec![1; 32].into(),
        rp: make_credential::PublicKeyCredentialRpEntity {
            id: "example.com".into(),
            name: Some("E".into()),
        },
        user: webauthn::PublicKeyCredentialUserEntity {
            id: b"user".to_vec().into(),
            name: "u".into(),
            display_name: "u".into(),
        },
        pub_key_cred_params: vec![webauthn::PublicKeyCredentialParameters {
            ty: webauthn::PublicKeyCredentialType::PublicKey,
            alg: iana::Algorithm::ES256,
        }],
        exclude_list: None,
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
fn get(rk: bool) -> get_assertion::Request {
    get_assertion::Request {
        rp_id: "example.com".into(),
        client_data_hash: vec![2; 32].into(),
        allow_list: None,
        extensions: None,
        options: get_assertion::Options {
            rk,
            up: true,
            uv: true,
        },
        pin_auth: None,
        pin_protocol: None,
    }
}
#[tokio::test]
async fn public_factory_refuses_rk_get() {
    let p = prepare_registration(make(), Uv, &[], 4096).await.unwrap();
    let s = p.canonical_source_bytes().to_vec();
    assert!(matches!(
        authenticate(&s, get(true), Uv, 4096).await,
        Err(FixedError::InvalidRequest)
    ));
}
#[tokio::test]
async fn public_factory_refuses_unknown_descriptors() {
    let mut m = make();
    m.exclude_list = Some(vec![webauthn::PublicKeyCredentialDescriptor {
        ty: webauthn::PublicKeyCredentialType::Unknown,
        id: vec![1; 16].into(),
        transports: None,
    }]);
    assert!(matches!(
        prepare_registration(m, Uv, &[], 4096).await,
        Err(FixedError::InvalidRequest)
    ));
}
#[tokio::test]
async fn public_factory_registers_and_authenticates_discoverably() {
    let prepared = prepare_registration(make(), Uv, &[], 4096).await.unwrap();
    let source = prepared.canonical_source_bytes().to_vec();
    let make_response = prepared
        .commit_with(&mut Persist)
        .await
        .unwrap()
        .into_response();
    assert!(
        make_response
            .auth_data
            .flags
            .contains(Flags::UP | Flags::UV | Flags::BE | Flags::BS)
    );
    for allow in [None, Some(vec![])] {
        let mut request = get(false);
        request.allow_list = allow;
        let assertion = authenticate(&source, request, Uv, 4096).await.unwrap();
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
async fn public_factory_refuses_unknown_allow_descriptor() {
    let p = prepare_registration(make(), Uv, &[], 4096).await.unwrap();
    let s = p.canonical_source_bytes().to_vec();
    let mut r = get(false);
    r.allow_list = Some(vec![webauthn::PublicKeyCredentialDescriptor {
        ty: webauthn::PublicKeyCredentialType::Unknown,
        id: vec![1; 16].into(),
        transports: None,
    }]);
    assert!(matches!(
        authenticate(&s, r, Uv, 4096).await,
        Err(FixedError::InvalidRequest)
    ));
}
