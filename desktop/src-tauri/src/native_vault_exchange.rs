//! Closed, value-free commands for the native Apple transfer controller.
use serde::{Deserialize, Serialize};

#[derive(Deserialize, Serialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum Request {
    BeginExport {
        item_ids: Vec<String>,
        organization_id: String,
    },
    Status {
        operation_id: String,
    },
    Cancel {
        operation_id: String,
    },
    Confirm {
        operation_id: String,
    },
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Idle,
    Authorizing,
    Preflighting,
    AwaitingConfirmation,
    ChoosingDestination,
    Exporting,
    HandedToDestination,
    Cancelled,
    Failed,
    Unavailable,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Status {
    operation_id: String,
    phase: Phase,
    total: u32,
    eligible: u32,
    unsupported: u32,
    handed_off: u32,
    message: String,
}

impl Request {
    fn bounded(&self) -> bool {
        match self {
            Self::BeginExport {
                item_ids,
                organization_id,
            } => {
                !item_ids.is_empty()
                    && item_ids.len() <= 2_000
                    && organization_id.len() == 36
                    && item_ids.iter().all(|id| id.len() == 36)
            }
            Self::Status { operation_id }
            | Self::Cancel { operation_id }
            | Self::Confirm { operation_id } => operation_id.len() == 36,
        }
    }
}

#[cfg(target_os = "macos")]
async fn dispatch(
    app: &tauri::AppHandle,
    anchor: Option<tauri::WebviewWindow>,
    input: Vec<u8>,
) -> Result<Vec<u8>, &'static str> {
    use std::{
        sync::{
            atomic::{AtomicBool, Ordering},
            Arc,
        },
        time::Duration,
    };
    let (sender, receiver) = tokio::sync::oneshot::channel();
    let cancelled = Arc::new(AtomicBool::new(false));
    let pending = cancelled.clone();
    app.run_on_main_thread(move || {
        if pending.load(Ordering::Acquire) {
            return;
        }
        let handle = anchor
            .and_then(|window| window.ns_window().ok())
            .unwrap_or(std::ptr::null_mut());
        let result = crate::native_vault_exchange_bridge::dispatch(&input, handle);
        let _ = sender.send(result);
    })
    .map_err(|_| "Native passkey transfer could not start.")?;
    let result = tokio::time::timeout(Duration::from_secs(5), receiver).await;
    cancelled.store(true, Ordering::Release);
    result
        .ok()
        .and_then(Result::ok)
        .flatten()
        .ok_or("Native passkey transfer is unavailable. Check AutoFill setup in Settings.")
}

#[tauri::command]
pub async fn native_vault_exchange(
    window: tauri::WebviewWindow,
    request: Request,
) -> Result<Status, &'static str> {
    if window.label() != "main" {
        return Err("Open passkey transfer in the main window.");
    }
    if !request.bounded() {
        return Err("The passkey selection is invalid.");
    }
    #[cfg(target_os = "macos")]
    {
        use tauri::Manager;
        let input =
            serde_json::to_vec(&request).map_err(|_| "The passkey selection is invalid.")?;
        let output = dispatch(window.app_handle(), Some(window.clone()), input).await?;
        serde_json::from_slice(&output)
            .map_err(|_| "Native passkey transfer returned an invalid status.")
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = window;
        Err("Apple passkey transfer requires macOS 26 or later.")
    }
}

pub async fn invalidate(app: &tauri::AppHandle) -> Result<(), &'static str> {
    #[cfg(target_os = "macos")]
    {
        dispatch(app, None, br#"{"action":"invalidate"}"#.to_vec())
            .await
            .map(|_| ())
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = app;
        Ok(())
    }
}
