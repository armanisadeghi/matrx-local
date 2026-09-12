//! Provider-private registration persistence gate.
//!
//! This crate deliberately has no FFI, disk, network, or host-provider API.

use async_trait::async_trait;
use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
use coset::{CborSerializable, CoseKey, Label, RegisteredLabel, RegisteredLabelWithPrivate, iana};
use p256::elliptic_curve::sec1::ToEncodedPoint;
use serde::{Deserialize, Serialize};
use zeroize::Zeroizing;

/// Bounded errors safe for the provider boundary.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FixedError {
    PersistenceFailed,
    InvalidSource,
}

/// Closed v1 provider-private source record. It is deliberately not serializable
/// outside this module; accepted input must be byte-for-byte canonical.
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceV1 {
    version: u8,
    credential_id: String,
    rp_id: String,
    user_handle: String,
    username: Option<String>,
    display_name: Option<String>,
    private_cose_key: String,
    counter: Option<u32>,
    extensions: serde_json::Map<String, serde_json::Value>,
    backup_eligible: bool,
    backup_state: bool,
}

/// Validate and retain the exact canonical source bytes supplied by the future provider adapter.
pub fn canonical_source(
    bytes: &[u8],
    max_source_bytes: usize,
) -> Result<Zeroizing<Vec<u8>>, FixedError> {
    if max_source_bytes == 0 || bytes.len() > max_source_bytes {
        return Err(FixedError::InvalidSource);
    }
    let value: SourceV1 = serde_json::from_slice(bytes).map_err(|_| FixedError::InvalidSource)?;
    if value.version != 1
        || value.counter.is_some()
        || !value.extensions.is_empty()
        || !value.backup_eligible
        || !value.backup_state
        || !valid_rp(&value.rp_id)
    {
        return Err(FixedError::InvalidSource);
    }
    let credential_id = decode(&value.credential_id)?;
    if !(16..=1023).contains(&credential_id.len()) {
        return Err(FixedError::InvalidSource);
    }
    let handle = decode(&value.user_handle)?;
    if !(1..=64).contains(&handle.len()) {
        return Err(FixedError::InvalidSource);
    }
    let cose = decode(&value.private_cose_key)?;
    validate_cose_key(&cose)?;
    let canonical = serde_json::to_vec(&value).map_err(|_| FixedError::InvalidSource)?;
    if canonical != bytes {
        return Err(FixedError::InvalidSource);
    }
    Ok(Zeroizing::new(canonical))
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
                curve = matches!(value, coset::cbor::value::Value::Integer(value) if i64::try_from(*value).ok() == Some(1))
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
    let secret = p256::SecretKey::from_slice(d).map_err(|_| FixedError::InvalidSource)?;
    let public = secret.public_key().to_encoded_point(false);
    if public.x().map(AsRef::<[u8]>::as_ref) != Some(x.as_slice())
        || public.y().map(AsRef::<[u8]>::as_ref) != Some(y.as_slice())
    {
        return Err(FixedError::InvalidSource);
    }
    Ok(())
}

fn decode(value: &str) -> Result<Vec<u8>, FixedError> {
    if value.contains('=') {
        return Err(FixedError::InvalidSource);
    }
    let decoded = URL_SAFE_NO_PAD
        .decode(value)
        .map_err(|_| FixedError::InvalidSource)?;
    if URL_SAFE_NO_PAD.encode(&decoded) != value {
        return Err(FixedError::InvalidSource);
    }
    Ok(decoded)
}

fn valid_rp(rp: &str) -> bool {
    if rp == "localhost" {
        return true;
    }
    if rp.len() > 253
        || !rp.is_ascii()
        || !rp.contains('.')
        || !rp.bytes().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'.' || byte == b'-'
        })
    {
        return false;
    }
    rp.split('.').all(|label| {
        !label.is_empty()
            && label.len() <= 63
            && label
                .as_bytes()
                .first()
                .is_some_and(u8::is_ascii_alphanumeric)
            && label
                .as_bytes()
                .last()
                .is_some_and(u8::is_ascii_alphanumeric)
            && label
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
    })
}

/// The only future persistence seam permitted to release a registration response.
#[async_trait]
pub trait RegistrationPersister {
    async fn persist(&mut self, canonical_source: &[u8]) -> Result<(), FixedError>;
}

/// Opaque registration that cannot reveal its protocol response before persistence.
pub struct PreparedRegistration<R> {
    canonical_source: Zeroizing<Vec<u8>>,
    response: R,
}

impl<R> PreparedRegistration<R> {
    pub fn new(canonical_source: Vec<u8>, response: R) -> Self {
        Self {
            canonical_source: Zeroizing::new(canonical_source),
            response,
        }
    }
    pub fn canonical_source_bytes(&self) -> &[u8] {
        &self.canonical_source
    }
    pub async fn commit_with<P: RegistrationPersister>(
        self,
        persister: &mut P,
    ) -> Result<CommittedRegistration<R>, FixedError> {
        persister.persist(&self.canonical_source).await?;
        Ok(CommittedRegistration {
            response: self.response,
        })
    }
}

/// A registration response released only after awaited persistence succeeds.
pub struct CommittedRegistration<R> {
    response: R,
}
impl<R> CommittedRegistration<R> {
    pub fn into_response(self) -> R {
        self.response
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use coset::{CoseKeyBuilder, iana};
    struct Fails;
    #[async_trait]
    impl RegistrationPersister for Fails {
        async fn persist(&mut self, _: &[u8]) -> Result<(), FixedError> {
            Err(FixedError::PersistenceFailed)
        }
    }
    #[tokio::test]
    async fn response_is_withheld_when_persistence_fails() {
        let prepared = PreparedRegistration::new(vec![1, 2], "response");
        assert!(matches!(
            prepared.commit_with(&mut Fails).await,
            Err(FixedError::PersistenceFailed)
        ));
    }

    #[test]
    fn rejects_noncanonical_and_invalid_rp_source() {
        let source = br#"{\"version\":1,\"credential_id\":\"MDEyMzQ1Njc4OWFiY2RlZg\",\"rp_id\":\"Example.com\",\"user_handle\":\"dXNlcg\",\"username\":null,\"display_name\":null,\"private_cose_key\":\"AQ\",\"counter\":null,\"extensions\":{},\"backup_eligible\":true,\"backup_state\":true}"#;
        assert_eq!(
            canonical_source(source, 4096),
            Err(FixedError::InvalidSource)
        );
    }

    #[test]
    fn accepts_only_canonical_matching_es256_source() {
        let secret = p256::SecretKey::from_slice(&[7; 32]).unwrap();
        let public = secret.public_key().to_encoded_point(false);
        let key = CoseKeyBuilder::new_ec2_priv_key(
            iana::EllipticCurve::P_256,
            public.x().unwrap().to_vec(),
            public.y().unwrap().to_vec(),
            secret.to_bytes().to_vec(),
        )
        .algorithm(iana::Algorithm::ES256)
        .build()
        .to_vec()
        .unwrap();
        let source = serde_json::json!({
            "version": 1,
            "credential_id": URL_SAFE_NO_PAD.encode([1_u8; 16]),
            "rp_id": "example.com",
            "user_handle": URL_SAFE_NO_PAD.encode(b"user"),
            "username": null,
            "display_name": null,
            "private_cose_key": URL_SAFE_NO_PAD.encode(key),
            "counter": null,
            "extensions": {},
            "backup_eligible": true,
            "backup_state": true
        });
        let bytes = serde_json::to_vec(&source).unwrap();
        assert_eq!(canonical_source(&bytes, 4096).unwrap().as_slice(), bytes);
    }
}
