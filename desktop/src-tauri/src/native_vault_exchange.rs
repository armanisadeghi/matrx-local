//! Closed, value-free commands for the native Apple transfer controller.
use serde::{Deserialize, Serialize};

#[derive(Deserialize, Serialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum Request {
    BeginExport { item_ids: Vec<String>, organization_id: String },
    Status { operation_id: String },
    Cancel { operation_id: String },
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Idle, Authorizing, Preflighting, ChoosingDestination, Exporting,
    HandedToDestination, Cancelled, Failed, Unavailable,
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
            Self::BeginExport { item_ids, organization_id } => !item_ids.is_empty()
                && item_ids.len() <= 2_000 && organization_id.len() == 36
                && item_ids.iter().all(|id| id.len() == 36),
            Self::Status { operation_id } | Self::Cancel { operation_id } => operation_id.len() == 36,
        }
    }
}

#[cfg(target_os = "macos")]
async fn dispatch(window: tauri::WebviewWindow, input: Vec<u8>) -> Result<Vec<u8>, &'static str> {
    use std::ffi::{c_char, c_void, CStr};
    unsafe extern "C" {
        fn matrx_vault_exchange_dispatch(input: *const u8, length: isize, window: *mut c_void) -> *mut c_char;
        fn matrx_vault_exchange_free(response: *mut c_char);
    }
    let (sender, receiver) = tokio::sync::oneshot::channel();
    let anchor = window.clone();
    window.run_on_main_thread(move || {
        let result = anchor.ns_window().ok().and_then(|handle| unsafe {
            let response = matrx_vault_exchange_dispatch(input.as_ptr(), input.len() as isize, handle);
            if response.is_null() { return None; }
            // Swift owns and bounds this allocation to 2048 bytes.
            let value = CStr::from_ptr(response).to_bytes().to_vec();
            matrx_vault_exchange_free(response);
            Some(value)
        });
        let _ = sender.send(result);
    }).map_err(|_| "Native passkey transfer could not start.")?;
    receiver.await.ok().flatten().ok_or("Native passkey transfer is unavailable. Check AutoFill setup in Settings.")
}

#[tauri::command]
pub async fn native_vault_exchange(window: tauri::WebviewWindow, request: Request) -> Result<Status, &'static str> {
    if !request.bounded() { return Err("The passkey selection is invalid."); }
    #[cfg(target_os = "macos")]
    {
        let input = serde_json::to_vec(&request).map_err(|_| "The passkey selection is invalid.")?;
        let output = dispatch(window, input).await?;
        serde_json::from_slice(&output).map_err(|_| "Native passkey transfer returned an invalid status.")
    }
    #[cfg(not(target_os = "macos"))]
    { let _ = window; Err("Apple passkey transfer requires macOS 26 or later.") }
}

pub async fn invalidate(app: &tauri::AppHandle) {
    #[cfg(target_os = "macos")]
    {
        use tauri::Manager;
        if let Some(window) = app.webview_windows().into_values().next() {
            let _ = dispatch(window, br#"{"action":"invalidate"}"#.to_vec()).await;
        }
    }
    #[cfg(not(target_os = "macos"))]
    let _ = app;
}
