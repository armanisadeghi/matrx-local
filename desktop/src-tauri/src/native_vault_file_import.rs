//! Closed metadata-only native CXF file-import command.
use serde::{Deserialize, Serialize};
use tauri::Manager;

const MAX_REQUEST_BYTES: usize = 96 * 1024;
const MAX_RESPONSE_BYTES: usize = 64 * 1024;

#[derive(Deserialize, Serialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum Request {
    BeginFileImport {
        organization_id: String,
    },
    Status {
        operation_id: String,
    },
    Preview {
        operation_id: String,
        offset: u16,
    },
    ChooseScope {
        operation_id: String,
        organization_id: String,
    },
    Confirm {
        operation_id: String,
        preview_digest: String,
    },
    Cancel {
        operation_id: String,
    },
    Recover,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Authorizing,
    Preview,
    AwaitingConfirmation,
    Importing,
    Partial,
    Completed,
    Cancelled,
    Failed,
    Unavailable,
}
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Disposition {
    Eligible,
    Unsupported,
    Committed,
    Failed,
    Uncertain,
    NotAttempted,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Slot {
    slot_id: String,
    title: Option<String>,
    disposition: Disposition,
    reason: Option<String>,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Status {
    operation_id: String,
    phase: Phase,
    total: u16,
    committed: u16,
    already_present: u16,
    unsupported: u16,
    failed: u16,
    uncertain: u16,
    not_attempted: u16,
    message: String,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Preview {
    operation_id: String,
    preview_digest: String,
    offset: u16,
    total: u16,
    slots: Vec<Slot>,
}

#[derive(Deserialize, Serialize)]
#[serde(untagged)]
pub enum Response {
    Status(Status),
    Preview(Preview),
}

impl Request {
    fn bounded(&self) -> bool {
        match self {
            Self::BeginFileImport { organization_id } => uuid(organization_id),
            Self::Recover => true,
            Self::Status { operation_id } | Self::Cancel { operation_id } => uuid(operation_id),
            Self::Preview {
                operation_id,
                offset,
            } => uuid(operation_id) && *offset <= 2_000,
            Self::ChooseScope {
                operation_id,
                organization_id,
            } => uuid(operation_id) && uuid(organization_id),
            Self::Confirm {
                operation_id,
                preview_digest,
            } => {
                uuid(operation_id)
                    && preview_digest.len() == 64
                    && preview_digest
                        .bytes()
                        .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
            }
        }
    }
    fn action(&self) -> &'static str {
        match self {
            Self::BeginFileImport { .. } => "import_begin",
            Self::Status { .. } => "import_status",
            Self::Preview { .. } => "import_preview",
            Self::ChooseScope { .. } => "import_choose_scope",
            Self::Confirm { .. } => "import_confirm",
            Self::Cancel { .. } => "import_cancel",
            Self::Recover => "import_recover",
        }
    }
    fn host_json(&self) -> Result<Vec<u8>, &'static str> {
        let mut value = serde_json::to_value(self).map_err(|_| "The import request is invalid.")?;
        value["action"] = serde_json::Value::String(self.action().into());
        let bytes = serde_json::to_vec(&value).map_err(|_| "The import request is invalid.")?;
        if bytes.len() > MAX_REQUEST_BYTES {
            return Err("The import request is invalid.");
        }
        Ok(bytes)
    }
}
fn uuid(value: &str) -> bool {
    value.len() == 36
        && value == value.to_ascii_lowercase()
        && value.bytes().enumerate().all(|(index, byte)| match index {
            8 | 13 | 18 | 23 => byte == b'-',
            _ => byte.is_ascii_hexdigit(),
        })
}

#[cfg(target_os = "macos")]
async fn dispatch(
    app: &tauri::AppHandle,
    window: Option<tauri::WebviewWindow>,
    input: Vec<u8>,
) -> Result<Vec<u8>, &'static str> {
    use std::{
        ffi::c_void,
        sync::{
            atomic::{AtomicBool, Ordering},
            Arc,
        },
        time::Duration,
    };
    unsafe extern "C" {
        fn matrx_vault_exchange_dispatch(
            input: *const u8,
            length: isize,
            window: *mut c_void,
            output: *mut u8,
            capacity: isize,
        ) -> isize;
    }
    let (sender, receiver) = tokio::sync::oneshot::channel();
    let cancelled = Arc::new(AtomicBool::new(false));
    let pending = cancelled.clone();
    app.run_on_main_thread(move || {
        if pending.load(Ordering::Acquire) {
            return;
        }
        let handle = window
            .and_then(|window| window.ns_window().ok())
            .unwrap_or(std::ptr::null_mut());
        let mut output = vec![0_u8; MAX_RESPONSE_BYTES];
        let length = unsafe {
            matrx_vault_exchange_dispatch(
                input.as_ptr(),
                input.len() as isize,
                handle,
                output.as_mut_ptr(),
                output.len() as isize,
            )
        };
        let result = if (0..=MAX_RESPONSE_BYTES as isize).contains(&length) {
            output.truncate(length as usize);
            Some(output)
        } else {
            None
        };
        let _ = sender.send(result);
    })
    .map_err(|_| "Native passkey import could not start.")?;
    let result = tokio::time::timeout(Duration::from_secs(5), receiver).await;
    cancelled.store(true, Ordering::Release);
    result
        .ok()
        .and_then(Result::ok)
        .flatten()
        .ok_or("Native passkey import is unavailable.")
}

#[tauri::command]
pub async fn native_vault_file_import(
    window: tauri::WebviewWindow,
    request: Request,
) -> Result<serde_json::Value, &'static str> {
    if window.label() != "main" {
        return Err("Open passkey import in the main window.");
    }
    if !request.bounded() {
        return Err("The import request is invalid.");
    }
    #[cfg(target_os = "macos")]
    {
        let output = dispatch(
            window.clone().app_handle(),
            Some(window),
            request.host_json()?,
        )
        .await?;
        let response: Response = serde_json::from_slice(&output)
            .map_err(|_| "Native passkey import returned an invalid status.")?;
        match (&request, &response) {
            (Request::Preview { .. }, Response::Preview(preview))
                if uuid(&preview.operation_id)
                    && preview.preview_digest.len() == 64
                    && preview
                        .preview_digest
                        .bytes()
                        .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
                    && preview.offset <= preview.total
                    && preview.slots.len() <= 8 => {}
            (Request::Preview { .. }, _) => {
                return Err("Native passkey import returned an invalid preview.")
            }
            (_, Response::Status(status))
                if uuid(&status.operation_id)
                    && status.total
                        == status.committed
                            + status.already_present
                            + status.unsupported
                            + status.failed
                            + status.uncertain
                            + status.not_attempted => {}
            (_, Response::Status(_)) => {
                return Err("Native passkey import returned an invalid status.")
            }
            _ => return Err("Native passkey import returned an invalid status."),
        }
        serde_json::to_value(response)
            .map_err(|_| "Native passkey import returned an invalid status.")
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = window;
        Err("Apple passkey import requires macOS 26 or later.")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const OPERATION: &str = "123e4567-e89b-12d3-a456-426614174000";
    const ORGANIZATION: &str = "123e4567-e89b-42d3-a456-426614174001";

    #[test]
    fn imports_use_namespaced_host_actions_without_private_payloads() {
        let cases = [
            (
                Request::BeginFileImport {
                    organization_id: ORGANIZATION.into(),
                },
                "import_begin",
            ),
            (
                Request::Status {
                    operation_id: OPERATION.into(),
                },
                "import_status",
            ),
            (
                Request::Preview {
                    operation_id: OPERATION.into(),
                    offset: 8,
                },
                "import_preview",
            ),
            (
                Request::ChooseScope {
                    operation_id: OPERATION.into(),
                    organization_id: ORGANIZATION.into(),
                },
                "import_choose_scope",
            ),
            (
                Request::Confirm {
                    operation_id: OPERATION.into(),
                    preview_digest: "a".repeat(64),
                },
                "import_confirm",
            ),
            (
                Request::Cancel {
                    operation_id: OPERATION.into(),
                },
                "import_cancel",
            ),
            (Request::Recover, "import_recover"),
        ];
        for (request, action) in cases {
            let encoded =
                String::from_utf8(request.host_json().expect("bounded request")).expect("utf8");
            assert!(encoded.contains(&format!("\"action\":\"{action}\"")));
            assert!(!encoded.contains("source"));
            assert!(!encoded.contains("path"));
        }
    }

    #[test]
    fn rejects_noncanonical_public_identifiers_and_digest() {
        assert!(!Request::BeginFileImport {
            organization_id: "not-a-uuid".into()
        }
        .bounded());
        assert!(!Request::Status {
            operation_id: OPERATION.to_uppercase()
        }
        .bounded());
        assert!(!Request::Preview {
            operation_id: OPERATION.into(),
            offset: 2_001
        }
        .bounded());
        assert!(!Request::Confirm {
            operation_id: OPERATION.into(),
            preview_digest: "A".repeat(64)
        }
        .bounded());
        assert!(!Request::ChooseScope {
            operation_id: OPERATION.into(),
            organization_id: "not-a-uuid".into()
        }
        .bounded());
    }
}
