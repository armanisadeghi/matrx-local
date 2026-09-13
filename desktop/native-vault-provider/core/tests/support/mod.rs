use async_trait::async_trait;
use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
use native_vault_core::{
    FixedError, RegistrationPersister, StoredCredential, authenticate, prepare_registration,
};
use passkey_authenticator::{UiHint, UserCheck, UserValidationMethod};
use passkey_types::{
    ctap2::{Ctap2Error, get_assertion, make_credential},
    webauthn::{self, ClientDataType, CollectedClientData},
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::io::{self, Read};

pub const MAX_FRAME_BYTES: usize = 1024 * 1024;

#[derive(Debug, PartialEq, Eq)]
pub enum WireError {
    Invalid,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CanonicalB64(pub Vec<u8>);
impl Serialize for CanonicalB64 {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&URL_SAFE_NO_PAD.encode(&self.0))
    }
}
impl<'de> Deserialize<'de> for CanonicalB64 {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        let value = String::deserialize(d)?;
        if value.contains('=') {
            return Err(serde::de::Error::custom("noncanonical base64url"));
        }
        let decoded = URL_SAFE_NO_PAD
            .decode(&value)
            .map_err(serde::de::Error::custom)?;
        if URL_SAFE_NO_PAD.encode(&decoded) != value {
            return Err(serde::de::Error::custom("noncanonical base64url"));
        }
        Ok(Self(decoded))
    }
}

#[derive(Debug, Serialize)]
pub struct Optional<T>(pub Option<T>);
impl<T> Optional<T> {
    fn is_none(value: &Self) -> bool {
        value.0.is_none()
    }
}
impl<T> Default for Optional<T> {
    fn default() -> Self {
        Self(None)
    }
}
impl<'de, T: Deserialize<'de>> Deserialize<'de> for Optional<T> {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        T::deserialize(d).map(|v| Self(Some(v)))
    }
}
#[derive(Debug, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Empty {}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Rp {
    pub id: String,
    pub name: String,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct User {
    pub id: CanonicalB64,
    pub name: String,
    #[serde(rename = "displayName")]
    pub display_name: String,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Param {
    #[serde(rename = "type")]
    pub ty: String,
    pub alg: i64,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Selection {
    #[serde(rename = "residentKey")]
    pub resident_key: String,
    #[serde(rename = "userVerification")]
    pub user_verification: String,
    #[serde(
        rename = "requireResidentKey",
        default,
        skip_serializing_if = "Optional::is_none"
    )]
    pub require_resident_key: Optional<bool>,
    #[serde(
        rename = "authenticatorAttachment",
        default,
        skip_serializing_if = "Optional::is_none"
    )]
    pub authenticator_attachment: Optional<String>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Descriptor {
    #[serde(rename = "type")]
    pub ty: String,
    pub id: CanonicalB64,
    #[serde(default, skip_serializing_if = "Optional::is_none")]
    pub transports: Optional<Vec<String>>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateOptions {
    pub rp: Rp,
    pub user: User,
    pub challenge: CanonicalB64,
    #[serde(rename = "pubKeyCredParams")]
    pub params: Vec<Param>,
    #[serde(default, skip_serializing_if = "Optional::is_none")]
    pub timeout: Optional<u32>,
    #[serde(
        rename = "excludeCredentials",
        default,
        skip_serializing_if = "Optional::is_none"
    )]
    pub exclude: Optional<Vec<Descriptor>>,
    #[serde(rename = "authenticatorSelection")]
    pub selection: Selection,
    pub attestation: String,
    #[serde(default, skip_serializing_if = "Optional::is_none")]
    pub extensions: Optional<Empty>,
    #[serde(default, skip_serializing_if = "Optional::is_none")]
    pub hints: Optional<Vec<String>>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GetOptions {
    pub challenge: CanonicalB64,
    #[serde(rename = "rpId")]
    pub rp_id: String,
    #[serde(
        rename = "allowCredentials",
        default,
        skip_serializing_if = "Optional::is_none"
    )]
    pub allow: Optional<Vec<Descriptor>>,
    #[serde(rename = "userVerification")]
    pub user_verification: String,
    #[serde(default, skip_serializing_if = "Optional::is_none")]
    pub timeout: Optional<u32>,
    #[serde(default, skip_serializing_if = "Optional::is_none")]
    pub extensions: Optional<Empty>,
    #[serde(default)]
    pub hints: Optional<Vec<String>>,
}

#[derive(Serialize, Deserialize)]
#[serde(tag = "command", rename_all = "snake_case", deny_unknown_fields)]
pub enum Frame {
    Register {
        id: String,
        options: CreateOptions,
        origin: String,
        uv: bool,
        persistence: String,
    },
    Authenticate {
        id: String,
        options: GetOptions,
        origin: String,
        uv: bool,
    },
    CancelRegister {
        id: String,
        options: CreateOptions,
        origin: String,
        uv: bool,
    },
}

fn id_ok(id: &str) -> bool {
    !id.is_empty() && id.len() <= 64 && id.is_ascii()
}
fn descriptors_ok(list: &Optional<Vec<Descriptor>>) -> bool {
    list.0.as_ref().is_none_or(|items| {
        items.iter().all(|d| {
            (16..=1023).contains(&d.id.0.len())
                && d.ty == "public-key"
                && d.transports.0.as_ref().is_none_or(|ts| {
                    let mut seen = std::collections::BTreeSet::new();
                    ts.iter()
                        .all(|t| (t == "internal" || t == "hybrid") && seen.insert(t))
                })
        })
    })
}
fn origin_ok(rp: &str, origin: &str) -> bool {
    origin == format!("https://{rp}") || (rp == "localhost" && origin == "http://localhost")
}
pub fn parse(frame: &[u8]) -> Result<Frame, WireError> {
    let parsed: Frame = serde_json::from_slice(frame).map_err(|_| WireError::Invalid)?;
    let valid = match &parsed {
        Frame::Register {
            id,
            options,
            origin,
            persistence,
            ..
        } => {
            id_ok(id)
                && origin_ok(&options.rp.id, origin)
                && !options.challenge.0.is_empty()
                && options.challenge.0.len() <= 1024
                && (1..=64).contains(&options.user.id.0.len())
                && !options.params.is_empty()
                && options
                    .params
                    .iter()
                    .all(|p| p.ty == "public-key" && p.alg == -7)
                && options.selection.resident_key == "required"
                && options.selection.user_verification == "required"
                && options.selection.require_resident_key.0.is_none_or(|v| v)
                && options
                    .selection
                    .authenticator_attachment
                    .0
                    .as_deref()
                    .is_none_or(|v| v == "platform")
                && options.attestation == "none"
                && options.timeout.0.is_none_or(|v| v > 0)
                && descriptors_ok(&options.exclude)
                && options.hints.0.as_ref().is_none_or(Vec::is_empty)
                && (persistence == "success" || persistence == "failure")
        }
        Frame::CancelRegister {
            id,
            options,
            origin,
            ..
        } => {
            id_ok(id)
                && origin_ok(&options.rp.id, origin)
                && !options.challenge.0.is_empty()
                && options.challenge.0.len() <= 1024
                && (1..=64).contains(&options.user.id.0.len())
                && !options.params.is_empty()
                && options
                    .params
                    .iter()
                    .all(|p| p.ty == "public-key" && p.alg == -7)
                && options.selection.resident_key == "required"
                && options.selection.user_verification == "required"
                && options.selection.require_resident_key.0.is_none_or(|v| v)
                && options
                    .selection
                    .authenticator_attachment
                    .0
                    .as_deref()
                    .is_none_or(|v| v == "platform")
                && options.attestation == "none"
                && options.timeout.0.is_none_or(|v| v > 0)
                && descriptors_ok(&options.exclude)
                && options.hints.0.as_ref().is_none_or(Vec::is_empty)
        }
        Frame::Authenticate {
            id,
            options,
            origin,
            ..
        } => {
            id_ok(id)
                && origin_ok(&options.rp_id, origin)
                && !options.challenge.0.is_empty()
                && options.challenge.0.len() <= 1024
                && options.user_verification == "required"
                && options.timeout.0.is_none_or(|v| v > 0)
                && descriptors_ok(&options.allow)
                && options.hints.0.as_ref().is_none_or(Vec::is_empty)
        }
    };
    valid.then_some(parsed).ok_or(WireError::Invalid)
}

/// Extract only an otherwise-valid public request id after the strict full parse fails.
/// This stays typed so duplicate ids are still rejected by serde rather than silently chosen.
pub fn valid_request_id(frame: &[u8]) -> Option<String> {
    #[derive(Deserialize)]
    struct Header {
        id: String,
    }
    serde_json::from_slice::<Header>(frame)
        .ok()
        .and_then(|header| id_ok(&header.id).then_some(header.id))
}

pub struct FrameReader<R> {
    input: R,
    current: Vec<u8>,
}
impl<R: Read> FrameReader<R> {
    pub fn new(input: R) -> Self {
        Self {
            input,
            current: Vec::new(),
        }
    }
    /// Returns one completed frame at a time. An oversize frame is drained through its newline
    /// before its fixed error is returned, allowing the following frame to be processed.
    pub fn next_frame(&mut self) -> Option<Result<Vec<u8>, WireError>> {
        let mut byte = [0_u8; 1];
        loop {
            match self.input.read(&mut byte) {
                Ok(0) => {
                    if self.current.is_empty() {
                        return None;
                    }
                    self.current.clear();
                    return Some(Err(WireError::Invalid));
                }
                Ok(_) if byte[0] == b'\n' => {
                    self.current.push(byte[0]);
                    return Some(Ok(std::mem::take(&mut self.current)));
                }
                Ok(_) if self.current.len() + 1 >= MAX_FRAME_BYTES => loop {
                    match self.input.read(&mut byte) {
                        Ok(0) => {
                            self.current.clear();
                            return Some(Err(WireError::Invalid));
                        }
                        Ok(_) if byte[0] == b'\n' => {
                            self.current.clear();
                            return Some(Err(WireError::Invalid));
                        }
                        Ok(_) => continue,
                        Err(ref e) if e.kind() == io::ErrorKind::Interrupted => continue,
                        Err(_) => {
                            self.current.clear();
                            return Some(Err(WireError::Invalid));
                        }
                    }
                },
                Ok(_) => self.current.push(byte[0]),
                Err(ref e) if e.kind() == io::ErrorKind::Interrupted => continue,
                Err(_) => return Some(Err(WireError::Invalid)),
            }
        }
    }
}

/// Test-only user validation. It exercises the maintained authenticator's refusal path.
pub struct TestUv(pub bool);
#[async_trait]
impl UserValidationMethod for TestUv {
    type PasskeyItem = StoredCredential;
    async fn check_user<'a>(
        &self,
        _: UiHint<'a, StoredCredential>,
        _: bool,
        _: bool,
    ) -> Result<UserCheck, Ctap2Error> {
        Ok(UserCheck {
            presence: self.0,
            verification: self.0,
        })
    }
    fn is_presence_enabled(&self) -> bool {
        true
    }
    fn is_verification_enabled(&self) -> Option<bool> {
        Some(true)
    }
}

pub struct PendingRegistration {
    pub id: String,
    pub client_data_json: Vec<u8>,
    pub source: Vec<u8>,
    prepared: native_vault_core::PreparedRegistration,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CredentialEnvelope<R> {
    pub id: String,
    pub raw_id: String,
    #[serde(rename = "type")]
    pub ty: &'static str,
    pub response: R,
    pub client_extension_results: Empty,
}
#[derive(Debug, Serialize)]
pub struct SuccessEnvelope<R> {
    pub id: String,
    pub ok: bool,
    pub credential: CredentialEnvelope<R>,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MakeResponse {
    #[serde(rename = "clientDataJSON")]
    pub client_data_json: String,
    pub attestation_object: String,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GetResponse {
    #[serde(rename = "clientDataJSON")]
    pub client_data_json: String,
    pub authenticator_data: String,
    pub signature: String,
    pub user_handle: String,
}

fn client_data(
    ty: ClientDataType,
    challenge: &[u8],
    origin: &str,
) -> Result<(Vec<u8>, Vec<u8>), WireError> {
    let data = CollectedClientData {
        ty,
        challenge: URL_SAFE_NO_PAD.encode(challenge),
        origin: origin.into(),
        cross_origin: Some(false),
        extra_data: (),
        unknown_keys: Default::default(),
    };
    // These exact bytes are both hashed and emitted, so serialization has one source of truth.
    let bytes = serde_json::to_vec(&data).map_err(|_| WireError::Invalid)?;
    Ok((Sha256::digest(&bytes).to_vec(), bytes))
}

fn creation_options(
    options: &CreateOptions,
) -> Result<webauthn::PublicKeyCredentialCreationOptions, WireError> {
    serde_json::from_slice(&serde_json::to_vec(options).map_err(|_| WireError::Invalid)?)
        .map_err(|_| WireError::Invalid)
}
fn assertion_options(
    options: &GetOptions,
) -> Result<webauthn::PublicKeyCredentialRequestOptions, WireError> {
    serde_json::from_slice(&serde_json::to_vec(options).map_err(|_| WireError::Invalid)?)
        .map_err(|_| WireError::Invalid)
}

async fn prepare_create(
    id: String,
    options: CreateOptions,
    origin: String,
    uv: bool,
) -> Result<PendingRegistration, FixedError> {
    let options = creation_options(&options).map_err(|_| FixedError::InvalidRequest)?;
    let (hash, client_data_json) = client_data(ClientDataType::Create, &options.challenge, &origin)
        .map_err(|_| FixedError::InvalidRequest)?;
    let request = make_credential::Request {
        client_data_hash: hash.into(),
        rp: options
            .rp
            .try_into()
            .map_err(|_| FixedError::InvalidRequest)?,
        user: options
            .user
            .try_into()
            .map_err(|_| FixedError::InvalidRequest)?,
        pub_key_cred_params: options.pub_key_cred_params,
        exclude_list: options.exclude_credentials,
        extensions: None,
        options: make_credential::Options {
            rk: true,
            up: true,
            uv: true,
        },
        pin_auth: None,
        pin_protocol: None,
    };
    let prepared = prepare_registration(request, TestUv(uv), &[], 4096).await?;
    let source = prepared.canonical_source_bytes().to_vec();
    Ok(PendingRegistration {
        id,
        client_data_json,
        source,
        prepared,
    })
}
pub async fn prepare_wire_registration(frame: Frame) -> Result<PendingRegistration, FixedError> {
    let Frame::Register {
        id,
        options,
        origin,
        uv,
        ..
    } = frame
    else {
        return Err(FixedError::InvalidRequest);
    };
    prepare_create(id, options, origin, uv).await
}
pub async fn cancel_wire_registration(frame: Frame) -> Result<(), FixedError> {
    let Frame::CancelRegister {
        id,
        options,
        origin,
        uv,
    } = frame
    else {
        return Err(FixedError::InvalidRequest);
    };
    // The prepared operation is deliberately dropped without a persister, so it cannot expose a credential.
    drop(prepare_create(id, options, origin, uv).await?);
    Ok(())
}
impl PendingRegistration {
    pub async fn commit<P: RegistrationPersister>(
        self,
        persister: &mut P,
    ) -> Result<SuccessEnvelope<MakeResponse>, FixedError> {
        let response = self.prepared.commit_with(persister).await?.into_response();
        let id = response
            .auth_data
            .attested_credential_data
            .as_ref()
            .ok_or(FixedError::OperationFailed)?
            .credential_id();
        let encoded = URL_SAFE_NO_PAD.encode(id);
        Ok(SuccessEnvelope {
            id: self.id,
            ok: true,
            credential: CredentialEnvelope {
                id: encoded.clone(),
                raw_id: encoded,
                ty: "public-key",
                response: MakeResponse {
                    client_data_json: URL_SAFE_NO_PAD.encode(self.client_data_json),
                    attestation_object: URL_SAFE_NO_PAD
                        .encode(response.as_webauthn_bytes().to_vec()),
                },
                client_extension_results: Empty {},
            },
        })
    }
}

pub async fn wire_assertion(
    frame: Frame,
    source: &[u8],
    credential_id: &[u8],
) -> Result<SuccessEnvelope<GetResponse>, FixedError> {
    let Frame::Authenticate {
        id,
        options,
        origin,
        uv,
    } = frame
    else {
        return Err(FixedError::InvalidRequest);
    };
    let options = assertion_options(&options).map_err(|_| FixedError::InvalidRequest)?;
    let (hash, client_data_json) = client_data(ClientDataType::Get, &options.challenge, &origin)
        .map_err(|_| FixedError::InvalidRequest)?;
    let request = get_assertion::Request {
        rp_id: options.rp_id.ok_or(FixedError::InvalidRequest)?,
        client_data_hash: hash.into(),
        allow_list: options.allow_credentials,
        extensions: None,
        options: get_assertion::Options {
            rk: false,
            up: true,
            uv: true,
        },
        pin_auth: None,
        pin_protocol: None,
    };
    let response = authenticate(source, request, TestUv(uv), 4096).await?;
    let encoded = URL_SAFE_NO_PAD.encode(credential_id);
    let user = response.user.ok_or(FixedError::OperationFailed)?;
    Ok(SuccessEnvelope {
        id,
        ok: true,
        credential: CredentialEnvelope {
            id: encoded.clone(),
            raw_id: encoded,
            ty: "public-key",
            response: GetResponse {
                client_data_json: URL_SAFE_NO_PAD.encode(client_data_json),
                authenticator_data: URL_SAFE_NO_PAD.encode(response.auth_data.to_vec()),
                signature: URL_SAFE_NO_PAD.encode(response.signature.to_vec()),
                user_handle: URL_SAFE_NO_PAD.encode(user.id.to_vec()),
            },
            client_extension_results: Empty {},
        },
    })
}
