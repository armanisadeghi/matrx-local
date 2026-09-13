use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
use serde::Deserialize;
use std::io::{self, Read};

pub const MAX_FRAME_BYTES: usize = 1024 * 1024;

#[derive(Debug, PartialEq, Eq)]
pub enum WireError {
    Invalid,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CanonicalB64(pub Vec<u8>);
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

#[derive(Debug)]
pub struct Optional<T>(pub Option<T>);
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
#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Empty {}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Rp {
    pub id: String,
    pub name: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct User {
    pub id: CanonicalB64,
    pub name: String,
    #[serde(rename = "displayName")]
    pub display_name: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Param {
    #[serde(rename = "type")]
    pub ty: String,
    pub alg: i64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Selection {
    #[serde(rename = "residentKey")]
    pub resident_key: String,
    #[serde(rename = "userVerification")]
    pub user_verification: String,
    #[serde(rename = "requireResidentKey", default)]
    pub require_resident_key: Optional<bool>,
    #[serde(rename = "authenticatorAttachment", default)]
    pub authenticator_attachment: Optional<String>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Descriptor {
    #[serde(rename = "type")]
    pub ty: String,
    pub id: CanonicalB64,
    #[serde(default)]
    pub transports: Optional<Vec<String>>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateOptions {
    pub rp: Rp,
    pub user: User,
    pub challenge: CanonicalB64,
    #[serde(rename = "pubKeyCredParams")]
    pub params: Vec<Param>,
    #[serde(default)]
    pub timeout: Optional<u32>,
    #[serde(rename = "excludeCredentials", default)]
    pub exclude: Optional<Vec<Descriptor>>,
    #[serde(rename = "authenticatorSelection")]
    pub selection: Selection,
    pub attestation: String,
    #[serde(default)]
    pub extensions: Optional<Empty>,
    #[serde(default)]
    pub hints: Optional<Vec<String>>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GetOptions {
    pub challenge: CanonicalB64,
    #[serde(rename = "rpId")]
    pub rp_id: String,
    #[serde(rename = "allowCredentials", default)]
    pub allow: Optional<Vec<Descriptor>>,
    #[serde(rename = "userVerification")]
    pub user_verification: String,
    #[serde(default)]
    pub timeout: Optional<u32>,
    #[serde(default)]
    pub extensions: Optional<Empty>,
    #[serde(default)]
    pub hints: Optional<Vec<String>>,
}

#[derive(Deserialize)]
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

pub fn read_frames(mut input: impl Read) -> Result<Vec<Vec<u8>>, WireError> {
    let mut frames = Vec::new();
    let mut current = Vec::new();
    let mut byte = [0_u8; 1];
    loop {
        match input.read(&mut byte) {
            Ok(0) => break,
            Ok(_) => {
                if current.len() == MAX_FRAME_BYTES {
                    return Err(WireError::Invalid);
                }
                current.push(byte[0]);
                if byte[0] == b'\n' {
                    frames.push(std::mem::take(&mut current));
                }
            }
            Err(ref e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(_) => return Err(WireError::Invalid),
        }
    }
    if !current.is_empty() {
        return Err(WireError::Invalid);
    }
    Ok(frames)
}
