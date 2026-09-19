#[path = "../../tests/support/mod.rs"]
mod support;

use async_trait::async_trait;
use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
use native_vault_core::{FixedError, RegistrationPersister};
use serde::Serialize;
use std::io::{self, Write};
use support::{Frame, WireError};

#[derive(Default)]
struct Memory {
    source: Option<Vec<u8>>,
    credential_id: Option<Vec<u8>>,
}
#[async_trait]
impl RegistrationPersister for Memory {
    async fn persist(&mut self, source: &[u8]) -> Result<(), FixedError> {
        self.source = Some(source.to_vec());
        Ok(())
    }
}
struct Fail;
#[async_trait]
impl RegistrationPersister for Fail {
    async fn persist(&mut self, _: &[u8]) -> Result<(), FixedError> {
        Err(FixedError::PersistenceFailed)
    }
}
#[derive(Serialize)]
struct Failure<'a> {
    id: Option<&'a str>,
    ok: bool,
    code: &'a str,
}
fn code(error: FixedError) -> &'static str {
    match error {
        FixedError::PersistenceFailed => "PersistenceFailed",
        FixedError::InvalidSource => "InvalidSource",
        FixedError::InvalidRequest => "InvalidRequest",
        FixedError::VerificationDenied => "VerificationDenied",
        FixedError::CredentialExcluded => "CredentialExcluded",
        FixedError::NoCredentials => "NoCredentials",
        FixedError::OperationFailed => "OperationFailed",
        FixedError::Cancelled => "Cancelled",
    }
}
fn emit<T: Serialize>(out: &mut impl Write, value: &T) {
    // Serialization is from typed public envelopes only; failures never include parser text or source bytes.
    let _ = serde_json::to_writer(&mut *out, value);
    let _ = out.write_all(b"\n");
    let _ = out.flush();
}
#[tokio::main(flavor = "current_thread")]
async fn main() {
    let mut frames = support::FrameReader::new(io::stdin().lock());
    let mut memory = Memory::default();
    let mut out = io::stdout().lock();
    while let Some(frame_result) = frames.next_frame() {
        let bytes = match frame_result {
            Ok(bytes) => bytes,
            Err(WireError::Invalid) => {
                emit(
                    &mut out,
                    &Failure {
                        id: None,
                        ok: false,
                        code: "InvalidRequest",
                    },
                );
                continue;
            }
        };
        let frame = match support::parse(&bytes[..bytes.len().saturating_sub(1)]) {
            Ok(frame) => frame,
            Err(_) => {
                emit(
                    &mut out,
                    &Failure {
                        id: support::valid_request_id(&bytes[..bytes.len().saturating_sub(1)])
                            .as_deref(),
                        ok: false,
                        code: "InvalidRequest",
                    },
                );
                continue;
            }
        };
        match frame {
            Frame::Register {
                id,
                persistence,
                options,
                origin,
                uv,
            } => {
                let request = Frame::Register {
                    id: id.clone(),
                    persistence: persistence.clone(),
                    options,
                    origin,
                    uv,
                };
                let result = support::prepare_wire_registration(request).await;
                match result {
                    Ok(pending) if persistence == "success" => {
                        match pending.commit(&mut memory).await {
                            Ok(success) => {
                                memory.credential_id =
                                    URL_SAFE_NO_PAD.decode(&success.credential.raw_id).ok();
                                emit(&mut out, &success);
                            }
                            Err(e) => emit(
                                &mut out,
                                &Failure {
                                    id: Some(&id),
                                    ok: false,
                                    code: code(e),
                                },
                            ),
                        }
                    }
                    Ok(pending) => {
                        let mut fail = Fail;
                        let e = pending
                            .commit(&mut fail)
                            .await
                            .err()
                            .unwrap_or(FixedError::OperationFailed);
                        emit(
                            &mut out,
                            &Failure {
                                id: Some(&id),
                                ok: false,
                                code: code(e),
                            },
                        );
                    }
                    Err(e) => emit(
                        &mut out,
                        &Failure {
                            id: Some(&id),
                            ok: false,
                            code: code(e),
                        },
                    ),
                }
            }
            Frame::Authenticate {
                id,
                options,
                origin,
                uv,
            } => {
                let result = match (&memory.source, &memory.credential_id) {
                    (Some(source), Some(credential_id)) => {
                        support::wire_assertion(
                            Frame::Authenticate {
                                id: id.clone(),
                                options,
                                origin,
                                uv,
                            },
                            source,
                            credential_id,
                        )
                        .await
                    }
                    _ => Err(FixedError::NoCredentials),
                };
                match result {
                    Ok(success) => emit(&mut out, &success),
                    Err(e) => emit(
                        &mut out,
                        &Failure {
                            id: Some(&id),
                            ok: false,
                            code: code(e),
                        },
                    ),
                }
            }
            Frame::CancelRegister {
                id,
                options,
                origin,
                uv,
            } => {
                let result = support::cancel_wire_registration(Frame::CancelRegister {
                    id: id.clone(),
                    options,
                    origin,
                    uv,
                })
                .await;
                let failure = match result {
                    Ok(()) => Failure {
                        id: Some(&id),
                        ok: false,
                        code: "VerificationDenied",
                    },
                    Err(e) => Failure {
                        id: Some(&id),
                        ok: false,
                        code: code(e),
                    },
                };
                emit(&mut out, &failure);
            }
        }
    }
}
