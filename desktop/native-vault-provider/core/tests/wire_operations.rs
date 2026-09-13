mod support;
use async_trait::async_trait;
use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
use native_vault_core::{FixedError, RegistrationPersister};
use passkey_types::webauthn::{ClientDataType, CollectedClientData};
use sha2::{Digest, Sha256};
use support::*;

fn register() -> String {
    r#"{"command":"register","id":"make-1","options":{"rp":{"id":"example.com","name":"Example"},"user":{"id":"dXNlcg","name":"user","displayName":"User"},"challenge":"Y2hhbGxlbmdl","pubKeyCredParams":[{"type":"public-key","alg":-7}],"authenticatorSelection":{"residentKey":"required","userVerification":"required"},"attestation":"none"},"origin":"https://example.com","uv":true,"persistence":"success"}"#.into()
}
fn get() -> String {
    r#"{"command":"authenticate","id":"get-1","options":{"challenge":"Z2V0LWNoYWxsZW5nZQ","rpId":"example.com","userVerification":"required"},"origin":"https://example.com","uv":true}"#.into()
}
struct Capture(Option<Vec<u8>>);
#[async_trait]
impl RegistrationPersister for Capture {
    async fn persist(&mut self, source: &[u8]) -> Result<(), FixedError> {
        self.0 = Some(source.to_vec());
        Ok(())
    }
}

#[tokio::test]
async fn maintained_mapping_runs_public_registration_then_assertion_and_emits_exact_client_data() {
    let pending = prepare_wire_registration(parse(register().as_bytes()).unwrap())
        .await
        .unwrap();
    let source = pending.source.clone();
    let expected_client_data = pending.client_data_json.clone();
    let mut persisted = Capture(None);
    let created = pending.commit(&mut persisted).await.unwrap();
    assert_eq!(created.id, "make-1");
    assert!(created.ok);
    let created_json = serde_json::to_string(&created).unwrap();
    assert!(created_json.contains("\"clientDataJSON\""));
    assert!(!created_json.contains("\"clientDataJson\""));
    assert_eq!(persisted.0.as_deref(), Some(source.as_slice()));
    let client_data: CollectedClientData = serde_json::from_slice(
        &URL_SAFE_NO_PAD
            .decode(&created.credential.response.client_data_json)
            .unwrap(),
    )
    .unwrap();
    assert_eq!(client_data.ty, ClientDataType::Create);
    assert_eq!(client_data.challenge, "Y2hhbGxlbmdl");
    assert_eq!(client_data.origin, "https://example.com");
    assert_eq!(
        URL_SAFE_NO_PAD
            .decode(&created.credential.response.client_data_json)
            .unwrap(),
        expected_client_data
    );
    let credential_id = URL_SAFE_NO_PAD.decode(&created.credential.raw_id).unwrap();
    let asserted = wire_assertion(parse(get().as_bytes()).unwrap(), &source, &credential_id)
        .await
        .unwrap();
    assert_eq!(asserted.id, "get-1");
    assert!(asserted.ok);
    let asserted_json = serde_json::to_string(&asserted).unwrap();
    assert!(asserted_json.contains("\"clientDataJSON\""));
    let get_client: CollectedClientData = serde_json::from_slice(
        &URL_SAFE_NO_PAD
            .decode(&asserted.credential.response.client_data_json)
            .unwrap(),
    )
    .unwrap();
    assert_eq!(get_client.ty, ClientDataType::Get);
    assert_eq!(get_client.challenge, "Z2V0LWNoYWxsZW5nZQ");
    assert_eq!(get_client.origin, "https://example.com");
    assert_eq!(
        Sha256::digest(
            &URL_SAFE_NO_PAD
                .decode(&asserted.credential.response.client_data_json)
                .unwrap()
        )
        .len(),
        32
    );
    assert_eq!(asserted.credential.id, created.credential.id);
    assert!(!asserted.credential.response.authenticator_data.is_empty());
    assert!(!asserted.credential.response.signature.is_empty());
}

#[tokio::test]
async fn actual_maintained_user_verification_refusal_and_commit_gate_are_preserved() {
    let denied = register().replace("\"uv\":true", "\"uv\":false");
    assert!(matches!(
        prepare_wire_registration(parse(denied.as_bytes()).unwrap()).await,
        Err(FixedError::VerificationDenied)
    ));
    let pending = prepare_wire_registration(parse(register().as_bytes()).unwrap())
        .await
        .unwrap();
    struct Fail;
    #[async_trait]
    impl RegistrationPersister for Fail {
        async fn persist(&mut self, _: &[u8]) -> Result<(), FixedError> {
            Err(FixedError::PersistenceFailed)
        }
    }
    assert!(matches!(
        pending.commit(&mut Fail).await,
        Err(FixedError::PersistenceFailed)
    ));
}
