//! Tauri IPC commands for the universal download manager.

use std::sync::Arc;
use tauri::{AppHandle, State};

use super::manager::DownloadManager;

/// Shared state type managed by Tauri.
pub type DownloadManagerState = Arc<DownloadManager>;

/// Enqueue a new download.
///
/// Returns the DownloadEntry (or the existing one if already queued/active/completed).
#[tauri::command]
pub async fn dm_enqueue(
    app: AppHandle,
    state: State<'_, DownloadManagerState>,
    id: String,
    category: String,
    filename: String,
    display_name: String,
    urls: Vec<String>,
    priority: Option<i32>,
    metadata: Option<String>,
    hf_token: Option<String>,
) -> Result<serde_json::Value, String> {
    let entry = state
        .enqueue(
            &app,
            id,
            category,
            filename,
            display_name,
            urls,
            priority.unwrap_or(0),
            metadata,
            hf_token,
        )
        .await?;
    serde_json::to_value(&entry).map_err(|e| e.to_string())
}

/// Cancel a download by ID.
#[tauri::command]
pub async fn dm_cancel(
    app: AppHandle,
    state: State<'_, DownloadManagerState>,
    id: String,
) -> Result<bool, String> {
    Ok(state.cancel(&app, &id).await)
}

/// List all downloads.
#[tauri::command]
pub async fn dm_list(state: State<'_, DownloadManagerState>) -> Result<serde_json::Value, String> {
    let entries = state.list().await;
    serde_json::to_value(&entries).map_err(|e| e.to_string())
}

/// Get a single download entry by ID.
#[tauri::command]
pub async fn dm_get(
    state: State<'_, DownloadManagerState>,
    id: String,
) -> Result<Option<serde_json::Value>, String> {
    let entries = state.list().await;
    for entry in entries {
        if entry.id == id {
            return Ok(Some(serde_json::to_value(&entry).map_err(|e| e.to_string())?));
        }
    }
    Ok(None)
}
