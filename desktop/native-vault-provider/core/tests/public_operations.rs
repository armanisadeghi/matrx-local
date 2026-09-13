use async_trait::async_trait;
use coset::iana;
use native_vault_core::{FixedError, StoredCredential, authenticate, prepare_registration};
use passkey_authenticator::{UiHint, UserCheck, UserValidationMethod};
use passkey_types::{
    ctap2::{Ctap2Error, get_assertion, make_credential},
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
        ty: webauthn::PublicKeyCredentialType::Unknown("x".into()),
        id: vec![1; 16].into(),
        transports: None,
    }]);
    assert!(matches!(
        prepare_registration(m, Uv, &[], 4096).await,
        Err(FixedError::InvalidRequest)
    ));
}
