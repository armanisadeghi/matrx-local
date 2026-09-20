use async_trait::async_trait;
use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
use coset::{CborSerializable, Label};
use native_vault_core::{FixedError, StoredCredential, canonical_source, prepare_registration};
use passkey_authenticator::{UiHint, UserCheck, UserValidationMethod};
use passkey_types::{
    ctap2::{Ctap2Error, make_credential},
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

async fn valid_source() -> Vec<u8> {
    let request = make_credential::Request {
        client_data_hash: vec![1; 32].into(),
        rp: make_credential::PublicKeyCredentialRpEntity {
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
            alg: coset::iana::Algorithm::ES256,
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
    };
    prepare_registration(request, Uv, &[], 4096)
        .await
        .unwrap()
        .canonical_source_bytes()
        .to_vec()
}

fn object(source: &[u8]) -> serde_json::Map<String, serde_json::Value> {
    serde_json::from_slice(source).unwrap()
}

fn with_cose(mut source: serde_json::Map<String, serde_json::Value>, bytes: Vec<u8>) -> Vec<u8> {
    source.insert(
        "private_cose_key".into(),
        serde_json::Value::String(URL_SAFE_NO_PAD.encode(bytes)),
    );
    serde_json::to_vec(&source).unwrap()
}

#[tokio::test]
async fn canonical_valid_source_rejects_each_json_and_bound_mutation() {
    let source = valid_source().await;
    assert_eq!(canonical_source(&source, 4096).unwrap().as_slice(), source);
    assert_eq!(canonical_source(b"{", 4096), Err(FixedError::InvalidSource));
    assert_eq!(
        canonical_source(&[source.as_slice(), b"x"].concat(), 4096),
        Err(FixedError::InvalidSource)
    );
    assert_eq!(
        canonical_source(&[b" ", source.as_slice()].concat(), 4096),
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
    for missing in ["version", "username", "display_name", "backup_state"] {
        let mut value = object(&source);
        value.remove(missing);
        assert_eq!(
            canonical_source(&serde_json::to_vec(&value).unwrap(), 4096),
            Err(FixedError::InvalidSource),
            "{missing}"
        );
    }
    let mut short_id = object(&source);
    short_id.insert(
        "credential_id".into(),
        serde_json::Value::String(URL_SAFE_NO_PAD.encode([1_u8; 15])),
    );
    assert_eq!(
        canonical_source(&serde_json::to_vec(&short_id).unwrap(), 4096),
        Err(FixedError::InvalidSource)
    );
    let mut empty_handle = object(&source);
    empty_handle.insert(
        "user_handle".into(),
        serde_json::Value::String(String::new()),
    );
    assert_eq!(
        canonical_source(&serde_json::to_vec(&empty_handle).unwrap(), 4096),
        Err(FixedError::InvalidSource)
    );
    let mut oversized_id = object(&source);
    oversized_id.insert(
        "credential_id".into(),
        serde_json::Value::String(URL_SAFE_NO_PAD.encode([2_u8; 1025])),
    );
    assert_eq!(
        canonical_source(&serde_json::to_vec(&oversized_id).unwrap(), 4096),
        Err(FixedError::InvalidSource)
    );
    let mut oversized_handle = object(&source);
    oversized_handle.insert(
        "user_handle".into(),
        serde_json::Value::String(URL_SAFE_NO_PAD.encode([3_u8; 65])),
    );
    assert_eq!(
        canonical_source(&serde_json::to_vec(&oversized_handle).unwrap(), 4096),
        Err(FixedError::InvalidSource)
    );
    assert_eq!(
        canonical_source(&source, source.len() - 1),
        Err(FixedError::InvalidSource)
    );
}

#[tokio::test]
async fn canonical_valid_source_rejects_each_cose_mutation() {
    let source = valid_source().await;
    let value = object(&source);
    let cose_b64 = value["private_cose_key"].as_str().unwrap();
    let cose = URL_SAFE_NO_PAD.decode(cose_b64).unwrap();
    assert_eq!(
        canonical_source(
            &with_cose(value.clone(), [cose.as_slice(), b"\0"].concat()),
            4096
        ),
        Err(FixedError::InvalidSource)
    );
    let mut duplicate = cose.clone();
    assert!((0xa0..=0xb7).contains(&duplicate[0]));
    duplicate[0] += 1;
    duplicate.extend_from_slice(&[0x03, 0x26]);
    assert_eq!(
        canonical_source(&with_cose(value.clone(), duplicate), 4096),
        Err(FixedError::InvalidSource)
    );
    assert_eq!(
        canonical_source(&with_cose(value.clone(), vec![0xff]), 4096),
        Err(FixedError::InvalidSource)
    );

    let mut key = coset::CoseKey::from_slice(&cose).unwrap();
    for (label, entry) in &mut key.params {
        if *label == Label::Int(-2) {
            *entry = coset::cbor::value::Value::Bytes(vec![0; 32]);
        }
    }
    assert_eq!(
        canonical_source(&with_cose(value.clone(), key.to_vec().unwrap()), 4096),
        Err(FixedError::InvalidSource)
    );
    let mut key = coset::CoseKey::from_slice(&cose).unwrap();
    key.key_id = vec![1];
    assert_eq!(
        canonical_source(&with_cose(value.clone(), key.to_vec().unwrap()), 4096),
        Err(FixedError::InvalidSource)
    );
    let mut key = coset::CoseKey::from_slice(&cose).unwrap();
    key.base_iv = vec![1];
    assert_eq!(
        canonical_source(&with_cose(value.clone(), key.to_vec().unwrap()), 4096),
        Err(FixedError::InvalidSource)
    );
    let mut key = coset::CoseKey::from_slice(&cose).unwrap();
    key.key_ops
        .insert(coset::RegisteredLabel::Text("forbidden".into()));
    assert_eq!(
        canonical_source(&with_cose(value, key.to_vec().unwrap()), 4096),
        Err(FixedError::InvalidSource)
    );
}
