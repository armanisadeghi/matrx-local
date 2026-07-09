use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tauri::{AppHandle, Emitter, Manager, State};
use tokio::sync::Mutex;

use super::config::LlmConfig;
use super::model_selector;
use super::server::{find_free_port, LlmServer, LlmServerStatus};
use crate::transcription::hardware::HardwareProfile;

/// Async-safe state for the LLM server. Uses tokio::sync::Mutex so async
/// tauri commands can hold the lock across .await points (start, stop, health).
pub type LlmServerState = Arc<Mutex<LlmServer>>;

/// Sync-accessible handle to the llama-server child process.
///
/// This is a SEPARATE lock from LlmServerState. It holds only the
/// CommandChild handle and uses std::sync::Mutex so graceful_shutdown_sync()
/// — a synchronous function called from the Quit menu event — can kill the
/// child process without needing an async runtime or tokio::sync::Mutex.
///
/// LlmServer also keeps its own process field for lifecycle management.
/// When the server stops normally, both are cleared. At shutdown, we grab
/// this handle directly and kill it even if the tokio mutex is locked.
/// Holds the PID (not the CommandChild — that lives in LlmServer and is not
/// shareable) so graceful_shutdown_sync can kill llama-server even while the
/// tokio LlmServerState mutex is held (e.g. mid health-wait during start).
/// Previously this handle was NEVER populated, making shutdown prong (b)
/// dead code.
pub type LlmProcessHandle = Arc<std::sync::Mutex<Option<u32>>>;

/// Shared atomic flag used to request cancellation of an in-flight download.
/// Set to true by `cancel_llm_download`; reset to false at the start of each
/// new `download_llm_model` invocation.
/// Newtype wrapper (not a type alias) so Tauri can manage it as a distinct
/// state type from WhisperDownloadCancelState, which is also Arc<AtomicBool>.
/// Tauri keys managed state by TypeId — type aliases share a TypeId, newtypes don't.
pub struct LlmDownloadCancelState(pub Arc<AtomicBool>);

// ── Server Lifecycle ──────────────────────────────────────────────────────

/// Start or restart llama-server with the selected model.
///
/// OWNERSHIP: Rust owns the llama-server lifecycle. This command is the
/// canonical entry point for any caller (frontend `useLlm.startServer`,
/// the auto-start path in `lib.rs setup()`, etc.) — every llama-server
/// spawn in production funnels through here. The Python engine MUST NOT
/// invoke this command, scan for llama-server processes, or kill them
/// (see `app/preflight.py` for the registry-level enforcement).
///
/// Every invocation logs `[llm-cmd] start_llm_server` with the model and
/// stop reason so the operator can grep the unified log to confirm who
/// triggered the spawn — UI button, auto-start, modal auto-open, etc.
#[tauri::command]
pub async fn start_llm_server(
    app: AppHandle,
    state: State<'_, LlmServerState>,
    model_filename: String,
    gpu_layers: i32,
    context_length: Option<u32>,
    mmproj_filename: Option<String>,
) -> Result<LlmServerStatus, String> {
    println!(
        "[llm-cmd] start_llm_server invoked: model={} gpu_layers={} ctx={:?}",
        model_filename, gpu_layers, context_length
    );

    let model_path = resolve_model_path(&app, &model_filename)?;

    if !std::path::Path::new(&model_path).exists() {
        return Err(format!(
            "Model file not found: {}. Download it first.",
            model_filename
        ));
    }

    // Prefer an explicit mmproj from the caller; otherwise look it up in the
    // catalog so vision models (Gemma 4 / Qwen 3.5) auto-attach their projector.
    let resolved_mmproj = mmproj_filename
        .filter(|f| !f.is_empty())
        .or_else(|| {
            model_selector::LLM_MODELS
                .iter()
                .find(|m| m.filename == model_filename)
                .filter(|m| !m.mmproj_filename.is_empty())
                .map(|m| m.mmproj_filename.to_string())
                .or_else(|| {
                    // Variant filenames (e.g. Q8_0) share the parent entry's mmproj.
                    model_selector::LLM_MODELS.iter().find_map(|m| {
                        m.variants
                            .iter()
                            .find(|v| v.filename == model_filename)
                            .and_then(|v| {
                                if !v.mmproj_filename.is_empty() {
                                    Some(v.mmproj_filename.to_string())
                                } else if !m.mmproj_filename.is_empty() {
                                    Some(m.mmproj_filename.to_string())
                                } else {
                                    None
                                }
                            })
                    })
                })
        });

    let mmproj_path = resolved_mmproj
        .as_ref()
        .map(|f| resolve_model_path(&app, f))
        .transpose()?
        .filter(|p| std::path::Path::new(p).exists());

    if let Some(ref name) = resolved_mmproj {
        if mmproj_path.is_none() {
            eprintln!(
                "[llm-cmd] mmproj '{}' listed for '{}' but not on disk — starting text-only",
                name, model_filename
            );
        }
    }

    let port = find_free_port(11434)?;
    let ctx = context_length.unwrap_or(8192);

    // Notify UI that server is starting (with model name for progress display)
    let _ = app.emit(
        "llm-server-starting",
        serde_json::json!({
            "model_filename": &model_filename,
            "port": port,
        }),
    );

    let mut server = state.lock().await;
    server
        .start(&app, &model_path, gpu_layers, ctx, port, mmproj_path.as_deref())
        .await?;

    // Persist config — reload first to preserve hf_token and any future fields,
    // then update only the fields this command owns.
    if let Ok(config_dir) = app.path().app_data_dir() {
        let mut config = LlmConfig::load(&config_dir);
        config.selected_model = Some(model_filename);
        config.setup_complete = true;
        config.last_port = Some(port);
        let _ = config.save(&config_dir);
    }

    let _ = app.emit("llm-server-ready", &server.status);
    Ok(server.status.clone())
}

/// Stop the running llama-server.
#[tauri::command]
pub async fn stop_llm_server(
    app: AppHandle,
    state: State<'_, LlmServerState>,
) -> Result<(), String> {
    let mut server = state.lock().await;
    server.stop().await;
    if let Some(handle) = app.try_state::<LlmProcessHandle>() {
        if let Ok(mut g) = handle.lock() {
            *g = None;
        }
    }
    let _ = app.emit("llm-server-stopped", ());
    Ok(())
}

/// Get the current server status (running, port, model name).
#[tauri::command]
pub async fn get_llm_server_status(
    state: State<'_, LlmServerState>,
) -> Result<LlmServerStatus, String> {
    let server = state.lock().await;
    Ok(server.status.clone())
}

/// Run a health check against the running server.
#[tauri::command]
pub async fn check_llm_server_health(state: State<'_, LlmServerState>) -> Result<bool, String> {
    let server = state.lock().await;
    Ok(server.health_check().await)
}

// ── Model Management ──────────────────────────────────────────────────────

/// Import a local GGUF file into the models directory by copying it.
///
/// `source_path` — absolute path to the .gguf file on the user's machine.
/// `dest_filename` — the filename to store it as (e.g. "my-model.gguf").
///   If empty, the source file's own name is used.
///
/// Returns the final filename that was saved.
#[tauri::command]
pub async fn import_local_llm_model(
    app: AppHandle,
    source_path: String,
    dest_filename: String,
) -> Result<String, String> {
    let src = std::path::Path::new(&source_path);

    if !src.exists() {
        return Err(format!("File not found: {}", source_path));
    }

    // Determine destination filename
    let filename = if dest_filename.trim().is_empty() {
        src.file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("custom-model.gguf")
            .to_string()
    } else {
        let mut n = dest_filename.trim().to_string();
        if !n.ends_with(".gguf") {
            n.push_str(".gguf");
        }
        n
    };

    let models_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join("models");

    std::fs::create_dir_all(&models_dir)
        .map_err(|e| format!("Failed to create models dir: {}", e))?;

    let dest = models_dir.join(&filename);

    // Quick GGUF magic check on the source before copying
    {
        use std::io::Read;
        let mut f = std::fs::File::open(src).map_err(|e| e.to_string())?;
        let mut magic = [0u8; 4];
        f.read_exact(&mut magic)
            .map_err(|_| "File is too small to be a valid GGUF model".to_string())?;
        if magic != [0x47, 0x47, 0x55, 0x46] {
            return Err(
                "Not a valid GGUF file (wrong magic bytes). Only .gguf models are supported."
                    .to_string(),
            );
        }
    }

    tokio::fs::copy(src, &dest)
        .await
        .map_err(|e| format!("Failed to copy model: {}", e))?;

    Ok(filename)
}

/// Check if a model (and all its parts for split models) exists and is fully valid.
#[tauri::command]
pub fn check_llm_model_exists(app: AppHandle, filename: String) -> bool {
    let models_dir = match app.path().app_data_dir() {
        Ok(d) => d.join("models"),
        Err(_) => return false,
    };

    if let Some(catalog) = model_selector::LLM_MODELS
        .iter()
        .find(|m| m.filename == filename)
    {
        // For split models, all parts must be present and valid
        let part_filenames = catalog.all_part_filenames();
        let part_sizes: Vec<Option<u64>> = (0..part_filenames.len())
            .map(|i| {
                if i == 0 {
                    Some(catalog.expected_size_bytes)
                } else {
                    catalog
                        .hf_part_sizes
                        .get(i - 1)
                        .copied()
                        .map(Some)
                        .flatten()
                }
            })
            .collect();

        part_filenames
            .iter()
            .zip(part_sizes.iter())
            .all(|(pf, &expected)| is_valid_gguf(&models_dir.join(pf), expected))
    } else {
        // Custom model — just check the single file
        let path = models_dir.join(&filename);
        is_valid_gguf(&path, None)
    }
}

/// Cancel an in-flight download. Safe to call at any time; no-op if nothing is downloading.
#[tauri::command]
pub fn cancel_llm_download(
    app: AppHandle,
    cancel: State<'_, LlmDownloadCancelState>,
) -> Result<(), String> {
    cancel.0.store(true, Ordering::SeqCst);
    let _ = app.emit(
        "llm-download-cancelled",
        serde_json::json!({ "reason": "user_cancelled" }),
    );
    Ok(())
}

// ── HuggingFace Token Management ─────────────────────────────────────────

/// Save a HuggingFace access token (hf_…) to llm.json (legacy fallback only).
/// Canonical storage is the engine SQLite API key `huggingface`; the UI migrates
/// tokens there. Pass an empty string to clear llm.json.
#[tauri::command]
pub async fn save_hf_token(app: AppHandle, token: String) -> Result<(), String> {
    let config_dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
    let mut config = LlmConfig::load(&config_dir);
    config.hf_token = if token.trim().is_empty() {
        None
    } else {
        Some(token.trim().to_string())
    };
    config.save(&config_dir)
}

/// Return the stored HuggingFace token, or null if not set.
#[tauri::command]
pub async fn get_hf_token(app: AppHandle) -> Result<Option<String>, String> {
    let config_dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
    let config = LlmConfig::load(&config_dir);
    Ok(config.hf_token)
}

// ── Download ──────────────────────────────────────────────────────────────

/// Download an LLM model from HuggingFace with progress events.
///
/// For single-file models, pass `urls` with a single entry and `filename` as the
/// destination filename.
///
/// For split models (e.g. Qwen2.5-14B), pass all part URLs in order. Each part is
/// downloaded with its original filename (extracted from the URL). llama-server can
/// load multi-part GGUF natively when given the first part's path — we do NOT
/// concatenate parts. `filename` is the first-part filename in this case.
///
/// The desktop passes `hf_token` from engine storage when available; otherwise
/// llm.json is used as a legacy fallback. The token is injected as
/// `Authorization: Bearer <token>` on every request. This is required for:
///   - Repos using XET storage (newer HF repos) — plain GET returns binary chunks,
///     not the file, without authentication.
///   - Gated / private models.
///
/// Features:
///   - XET storage detection: if HF redirects to cas-bridge.xethub.hf.co without
///     a token, a clear actionable error is returned instead of a corrupt file.
///   - Per-chunk 60-second idle timeout to detect stalled connections
///   - Cancellation via the `LlmDownloadCancelState` atomic flag
///   - Expected-size validation to reject partial/corrupted files
///   - 3-attempt retry with exponential backoff per part
///
/// Emits "llm-download-progress" events:
///   { filename, part, total_parts, bytes_downloaded, total_bytes, percent }
/// Emits "llm-download-cancelled" on cancellation.
#[tauri::command]
pub async fn download_llm_model(
    app: AppHandle,
    filename: String,
    urls: Vec<String>,
    hf_token: Option<String>,
    cancel: State<'_, LlmDownloadCancelState>,
) -> Result<String, String> {
    use crate::downloads::commands::DownloadManagerState;

    if urls.is_empty() {
        eprintln!("[llm] download_llm_model ERROR: no URLs provided for '{}'", filename);
        return Err("No download URLs provided".to_string());
    }

    eprintln!(
        "[llm] download_llm_model START: '{}' — {} part(s), HF token: {}",
        filename,
        urls.len(),
        if hf_token.as_ref().map(|t| !t.trim().is_empty()).unwrap_or(false) { "provided" } else { "not set (public repo assumed)" }
    );

    // Reset cancel flag at the start of every new download
    cancel.0.store(false, Ordering::SeqCst);

    // Register with the universal download manager for tracking/persistence.
    // The actual HTTP is still done by the code below (preserves XET detection,
    // GGUF validation, etc.). We just emit dm-progress alongside llm-download-progress.
    let dm_id = format!("llm-{}", filename);
    let catalog_display_name = {
        model_selector::LLM_MODELS
            .iter()
            .find(|m| m.filename == filename)
            .map(|m| m.name.to_string())
            .unwrap_or_else(|| filename.clone())
    };
    let dm_arc = app.try_state::<DownloadManagerState>().map(|s| s.inner().clone());
    if let Some(ref dm) = dm_arc {
        dm.register_external(
            &app,
            dm_id.clone(),
            "llm".to_string(),
            filename.clone(),
            catalog_display_name,
            urls.clone(),
        ).await;
    }

    let dest_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join("models");

    std::fs::create_dir_all(&dest_dir)
        .map_err(|e| format!("Failed to create models dir: {}", e))?;

    // Engine API key first, then legacy llm.json (may be None — fine for public repos)
    let hf_token: Option<String> = hf_token
        .filter(|s| !s.trim().is_empty())
        .map(|s| s.trim().to_string())
        .or_else(|| {
            let config_dir = app.path().app_data_dir().ok()?;
            LlmConfig::load(&config_dir).hf_token
        });

    // NOTE: HuggingFace now routes ALL public model downloads through XET storage
    // (cas-bridge.xethub.hf.co). Despite the domain name, this endpoint is a
    // standard HTTPS/S3 URL that reqwest follows automatically — no special SDK
    // needed. The XET early-exit check has been removed because it was blocking
    // every download. HTTP 401/403 errors from auth failures surface naturally.

    // Look up catalog entry so we can get per-part expected sizes
    let catalog_entry = model_selector::LLM_MODELS
        .iter()
        .find(|m| m.filename == filename);

    let cancel_ref = cancel.inner().0.clone();

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(7200))
        .build()
        .map_err(|e| e.to_string())?;

    let total_parts = urls.len();

    if total_parts == 1 {
        // ── Single-file model ──────────────────────────────────────────────
        let dest_path = dest_dir.join(&filename);
        let expected_size = catalog_entry.map(|m| m.expected_size_bytes);

        eprintln!(
            "[llm] download '{}': single-file, dest='{}', expected_size={:?}",
            filename,
            dest_path.display(),
            expected_size
        );

        // Skip if already fully downloaded
        if is_valid_gguf(&dest_path, expected_size) {
            eprintln!("[llm] download '{}': already complete (valid GGUF on disk), skipping", filename);
            let _ = app.emit(
                "llm-download-progress",
                serde_json::json!({
                    "filename": filename,
                    "part": 1,
                    "total_parts": 1,
                    "part_bytes_downloaded": expected_size.unwrap_or(0),
                    "part_total_bytes": expected_size.unwrap_or(0),
                    "bytes_downloaded": expected_size.unwrap_or(0),
                    "total_bytes": expected_size.unwrap_or(0),
                    "percent": 100.0,
                    "status": "already_complete",
                }),
            );
            // The DM entry was registered Active above — close it out, or it
            // stays "active" forever and resume_incomplete flags it as
            // "Interrupted: app was closed mid-download" on the next launch.
            if let Some(dm) = app.try_state::<DownloadManagerState>() {
                dm.mark_external_completed(&app, &dm_id, expected_size.unwrap_or(0))
                    .await;
            }
            let _ = ensure_mmproj_downloaded(
                &client,
                &app,
                &dest_dir,
                &filename,
                &cancel_ref,
                hf_token.as_deref(),
            )
            .await;
            return Ok(dest_path.to_string_lossy().to_string());
        }

        if dest_path.exists() {
            let _ = tokio::fs::remove_file(&dest_path).await;
        }

        let url = &urls[0];
        let mut last_error = String::new();
        for attempt in 0..3u32 {
            if cancel_ref.load(Ordering::SeqCst) {
                eprintln!("[llm] download '{}': cancelled by user before attempt {}", filename, attempt + 1);
                return Err("Download cancelled by user".to_string());
            }
            if attempt > 0 {
                let wait = 2u64.pow(attempt);
                eprintln!("[llm] download '{}': retrying in {}s (attempt {}/3)", filename, wait, attempt + 1);
                tokio::time::sleep(std::time::Duration::from_secs(wait)).await;
            } else {
                eprintln!("[llm] download '{}': starting attempt 1/3 from '{}'", filename, url);
            }
            match try_download_part(
                &client,
                url,
                &dest_path,
                &filename,
                1,
                1,
                0,
                0,
                &app,
                &cancel_ref,
                hf_token.as_deref(),
                Some(&dm_id),
            )
            .await
            {
                Ok(_) => {
                    eprintln!("[llm] download '{}': attempt {} HTTP stream complete, validating…", filename, attempt + 1);
                    if is_valid_gguf(&dest_path, expected_size) {
                        eprintln!("[llm] download '{}': SUCCESS — validation passed", filename);
                        if let Some(ref dm) = dm_arc {
                            dm.mark_external_completed(&app, &dm_id, expected_size.unwrap_or(0)).await;
                        }
                        let _ = ensure_mmproj_downloaded(
                            &client,
                            &app,
                            &dest_dir,
                            &filename,
                            &cancel_ref,
                            hf_token.as_deref(),
                        )
                        .await;
                        return Ok(dest_path.to_string_lossy().to_string());
                    }
                    // is_valid_gguf already logged the specific reason
                    last_error =
                        "Downloaded file failed validation (size mismatch or bad magic bytes)"
                            .to_string();
                    eprintln!("[llm] download '{}': attempt {} validation FAILED — deleting partial file", filename, attempt + 1);
                    let _ = tokio::fs::remove_file(&dest_path).await;
                }
                Err(e) if e.contains("cancelled") => {
                    eprintln!("[llm] download '{}': cancelled during download", filename);
                    let _ = tokio::fs::remove_file(&dest_path).await;
                    return Err("Download cancelled by user".to_string());
                }
                Err(e) => {
                    last_error = format!("Attempt {}: {}", attempt + 1, e);
                    eprintln!("[llm] download '{}': attempt {} FAILED — {}", filename, attempt + 1, e);
                    let _ = tokio::fs::remove_file(&dest_path).await;
                }
            }
        }
        let err_msg = format!("Download failed after 3 attempts. Last error: {}", last_error);
        eprintln!("[llm] download '{}': FINAL FAILURE — {}", filename, err_msg);
        if let Some(ref dm) = dm_arc {
            dm.mark_external_failed(&app, &dm_id, &err_msg).await;
        }
        return Err(err_msg);
    }

    // ── Split model: download each part with its original filename ─────────
    // llama.cpp loads split GGUF files natively when given the first part path.
    // We must preserve the `-00001-of-N` filenames — DO NOT concatenate.

    eprintln!("[llm] download '{}': split model — {} parts", filename, total_parts);

    // Build per-part expected sizes from catalog (index 0 = first part)
    let part_expected_sizes: Vec<Option<u64>> = (0..total_parts)
        .map(|i| {
            if i == 0 {
                catalog_entry.map(|m| m.expected_size_bytes)
            } else {
                catalog_entry.and_then(|m| m.hf_part_sizes.get(i - 1).copied())
            }
        })
        .collect();

    // Probe sizes for accurate overall progress
    let mut part_sizes: Vec<u64> = Vec::with_capacity(total_parts);
    for (i, url) in urls.iter().enumerate() {
        if cancel_ref.load(Ordering::SeqCst) {
            return Err("Download cancelled by user".to_string());
        }
        let size = if let Some(known) = part_expected_sizes[i] {
            known
        } else {
            probe_content_length(&client, url, hf_token.as_deref())
                .await
                .unwrap_or(0)
        };
        part_sizes.push(size);
    }
    let grand_total: u64 = part_sizes.iter().sum();

    let mut bytes_before_this_part: u64 = 0;
    let first_part_path = dest_dir.join(&filename);

    for (i, url) in urls.iter().enumerate() {
        if cancel_ref.load(Ordering::SeqCst) {
            return Err("Download cancelled by user".to_string());
        }

        let part_num = i + 1;

        // Extract original filename from the URL (preserves -00001-of-N suffix)
        let part_filename: String = url.rsplit('/').next().unwrap_or("unknown.gguf").to_string();
        let part_path = dest_dir.join(&part_filename);
        let expected = part_expected_sizes[i];

        eprintln!(
            "[llm] download '{}': part {}/{} — file='{}', expected_size={:?}",
            filename, part_num, total_parts, part_filename, expected
        );

        // Skip if already fully downloaded
        if is_valid_gguf(&part_path, expected) {
            eprintln!("[llm] download '{}': part {}/{} already complete, skipping", filename, part_num, total_parts);
            let already = part_sizes[i];
            let overall = bytes_before_this_part + already;
            let pct = if grand_total > 0 {
                (overall as f64 / grand_total as f64) * 100.0
            } else {
                100.0
            };
            let _ = app.emit(
                "llm-download-progress",
                serde_json::json!({
                    "filename": filename,
                    "part": part_num,
                    "total_parts": total_parts,
                    "part_bytes_downloaded": already,
                    "part_total_bytes": already,
                    "bytes_downloaded": overall,
                    "total_bytes": grand_total,
                    "percent": pct,
                    "status": "already_complete",
                }),
            );
            bytes_before_this_part += part_sizes[i];
            continue;
        }

        // Remove any partial file
        if part_path.exists() {
            let _ = tokio::fs::remove_file(&part_path).await;
        }

        let mut last_error = String::new();
        let mut success = false;

        for attempt in 0..3u32 {
            if cancel_ref.load(Ordering::SeqCst) {
                eprintln!("[llm] download '{}': cancelled before part {}/{} attempt {}", filename, part_num, total_parts, attempt + 1);
                let _ = tokio::fs::remove_file(&part_path).await;
                return Err("Download cancelled by user".to_string());
            }
            if attempt > 0 {
                let wait = 2u64.pow(attempt);
                eprintln!("[llm] download '{}': retrying part {}/{} in {}s (attempt {}/3)", filename, part_num, total_parts, wait, attempt + 1);
                tokio::time::sleep(std::time::Duration::from_secs(wait)).await;
            } else {
                eprintln!("[llm] download '{}': starting part {}/{} attempt 1/3", filename, part_num, total_parts);
            }

            match try_download_part(
                &client,
                url,
                &part_path,
                &filename,
                part_num,
                total_parts,
                bytes_before_this_part,
                grand_total,
                &app,
                &cancel_ref,
                hf_token.as_deref(),
                Some(&dm_id),
            )
            .await
            {
                Ok(_) => {
                    eprintln!("[llm] download '{}': part {}/{} attempt {} HTTP stream complete, validating…", filename, part_num, total_parts, attempt + 1);
                    if is_valid_gguf(&part_path, expected) {
                        eprintln!("[llm] download '{}': part {}/{} SUCCESS", filename, part_num, total_parts);
                        success = true;
                        break;
                    }
                    // is_valid_gguf already logged the specific reason
                    last_error = format!("Part {} failed validation after download", part_num);
                    eprintln!("[llm] download '{}': part {}/{} attempt {} validation FAILED — deleting partial file", filename, part_num, total_parts, attempt + 1);
                    let _ = tokio::fs::remove_file(&part_path).await;
                }
                Err(e) if e.contains("cancelled") => {
                    eprintln!("[llm] download '{}': cancelled during part {}/{}", filename, part_num, total_parts);
                    let _ = tokio::fs::remove_file(&part_path).await;
                    return Err("Download cancelled by user".to_string());
                }
                Err(e) => {
                    last_error = format!("Part {} attempt {}: {}", part_num, attempt + 1, e);
                    eprintln!("[llm] download '{}': part {}/{} attempt {} FAILED — {}", filename, part_num, total_parts, attempt + 1, e);
                    let _ = tokio::fs::remove_file(&part_path).await;
                }
            }
        }

        if !success {
            let err_msg = format!("Failed to download part {}/{}: {}", part_num, total_parts, last_error);
            eprintln!("[llm] download '{}': FINAL FAILURE at part {}/{} — {}", filename, part_num, total_parts, err_msg);
            if let Some(ref dm) = dm_arc {
                dm.mark_external_failed(&app, &dm_id, &err_msg).await;
            }
            return Err(err_msg);
        }

        bytes_before_this_part += part_sizes[i];
    }

    // All parts downloaded — update DM state and return the first part path
    eprintln!("[llm] download '{}': ALL {} part(s) complete — SUCCESS", filename, total_parts);
    if let Some(ref dm) = dm_arc {
        dm.mark_external_completed(&app, &dm_id, grand_total).await;
    }
    let _ = ensure_mmproj_downloaded(
        &client,
        &app,
        &dest_dir,
        &filename,
        &cancel_ref,
        hf_token.as_deref(),
    )
    .await;
    Ok(first_part_path.to_string_lossy().to_string())
}

/// Look up the multimodal projector for a catalog model (or one of its variants).
fn catalog_mmproj_for(filename: &str) -> Option<(&'static str, &'static str, u64)> {
    for m in model_selector::LLM_MODELS {
        if m.filename == filename {
            if m.mmproj_filename.is_empty() {
                return None;
            }
            return Some((m.mmproj_filename, m.mmproj_url, m.mmproj_expected_size_bytes));
        }
        for v in m.variants {
            if v.filename == filename {
                if !v.mmproj_filename.is_empty() {
                    return Some((
                        v.mmproj_filename,
                        v.mmproj_url,
                        v.mmproj_expected_size_bytes,
                    ));
                }
                if !m.mmproj_filename.is_empty() {
                    return Some((
                        m.mmproj_filename,
                        m.mmproj_url,
                        m.mmproj_expected_size_bytes,
                    ));
                }
                return None;
            }
        }
    }
    None
}

/// Download the catalog mmproj for a model if one is listed and missing on disk.
/// Saves under the catalog's local `mmproj_filename` (not the HF URL basename),
/// so Gemma/Qwen projectors don't collide as bare `mmproj-F16.gguf`.
async fn ensure_mmproj_downloaded(
    client: &reqwest::Client,
    app: &AppHandle,
    dest_dir: &std::path::Path,
    model_filename: &str,
    cancel_ref: &Arc<AtomicBool>,
    hf_token: Option<&str>,
) -> Result<(), String> {
    use crate::downloads::commands::DownloadManagerState;

    let Some((mmproj_filename, mmproj_url, expected_size)) = catalog_mmproj_for(model_filename)
    else {
        return Ok(());
    };
    if mmproj_url.is_empty() {
        return Ok(());
    }

    let dest_path = dest_dir.join(mmproj_filename);
    let expected = if expected_size > 0 {
        Some(expected_size)
    } else {
        None
    };

    if is_valid_gguf(&dest_path, expected) {
        eprintln!(
            "[llm] mmproj '{}' for '{}' already on disk — skipping",
            mmproj_filename, model_filename
        );
        return Ok(());
    }

    eprintln!(
        "[llm] downloading mmproj '{}' for '{}' from '{}'",
        mmproj_filename, model_filename, mmproj_url
    );

    if dest_path.exists() {
        let _ = tokio::fs::remove_file(&dest_path).await;
    }

    // A 1-2 GB projector download must be visible in the Downloads UI, not
    // invisible. Register a distinct DM entry (id namespaced with `mmproj`
    // so it never collides with the parent model's `llm-<file>` entry).
    let dm_id = format!("llm-mmproj-{}", mmproj_filename);
    let dm_arc = app
        .try_state::<DownloadManagerState>()
        .map(|s| s.inner().clone());
    if let Some(ref dm) = dm_arc {
        dm.register_external(
            app,
            dm_id.clone(),
            "llm".to_string(),
            mmproj_filename.to_string(),
            format!("Projector: {}", mmproj_filename),
            vec![mmproj_url.to_string()],
        )
        .await;
    }

    let mut last_error = String::new();
    for attempt in 0..3u32 {
        if cancel_ref.load(Ordering::SeqCst) {
            if let Some(ref dm) = dm_arc {
                dm.mark_external_failed(app, &dm_id, "Download cancelled by user").await;
            }
            return Err("Download cancelled by user".to_string());
        }
        if attempt > 0 {
            let wait = 2u64.pow(attempt);
            tokio::time::sleep(std::time::Duration::from_secs(wait)).await;
        }

        match try_download_part(
            client,
            mmproj_url,
            &dest_path,
            mmproj_filename,
            1,
            1,
            0,
            0,
            app,
            cancel_ref,
            hf_token,
            Some(&dm_id),
        )
        .await
        {
            Ok(_) => {
                if is_valid_gguf(&dest_path, expected) {
                    eprintln!(
                        "[llm] mmproj '{}' for '{}' downloaded successfully",
                        mmproj_filename, model_filename
                    );
                    if let Some(ref dm) = dm_arc {
                        dm.mark_external_completed(app, &dm_id, expected_size).await;
                    }
                    return Ok(());
                }
                last_error = "mmproj failed GGUF validation".to_string();
                let _ = tokio::fs::remove_file(&dest_path).await;
            }
            Err(e) if e.contains("cancelled") => {
                let _ = tokio::fs::remove_file(&dest_path).await;
                if let Some(ref dm) = dm_arc {
                    dm.mark_external_failed(app, &dm_id, "Download cancelled by user").await;
                }
                return Err("Download cancelled by user".to_string());
            }
            Err(e) => {
                last_error = e;
                let _ = tokio::fs::remove_file(&dest_path).await;
            }
        }
    }

    let err_msg = format!(
        "Failed to download mmproj '{}' after 3 attempts: {}",
        mmproj_filename, last_error
    );
    if let Some(ref dm) = dm_arc {
        dm.mark_external_failed(app, &dm_id, &err_msg).await;
    }
    Err(err_msg)
}

/// List all downloaded LLM models (GGUF files) in the models directory.
///
/// For split models (e.g. Qwen2.5-14B), only the first part is listed as a
/// "model" entry (with `is_split: true`). The other parts are present on disk
/// but are not surfaced individually — llama-server finds them automatically.
#[tauri::command]
pub fn list_llm_models(app: AppHandle) -> Vec<serde_json::Value> {
    let models_dir = match app.path().app_data_dir() {
        Ok(d) => d.join("models"),
        Err(_) => return Vec::new(),
    };

    if !models_dir.exists() {
        return Vec::new();
    }

    // Collect all .gguf filenames present on disk
    let on_disk: std::collections::HashSet<String> = std::fs::read_dir(&models_dir)
        .map(|entries| {
            entries
                .filter_map(|e| e.ok())
                .filter(|e| {
                    e.path()
                        .extension()
                        .map(|ext| ext == "gguf")
                        .unwrap_or(false)
                })
                .filter_map(|e| e.file_name().into_string().ok())
                .collect()
        })
        .unwrap_or_default();

    let mut results: Vec<serde_json::Value> = Vec::new();
    // Track filenames we've already emitted to avoid duplicates
    let mut emitted: std::collections::HashSet<String> = std::collections::HashSet::new();

    // First pass: catalog models (default filename per entry) —
    // emit one entry per catalog model if its first part is present and valid.
    for catalog in model_selector::LLM_MODELS {
        if !on_disk.contains(catalog.filename) {
            continue;
        }
        let part_path = models_dir.join(catalog.filename);
        let expected_first = Some(catalog.expected_size_bytes);
        if !is_valid_gguf(&part_path, expected_first) {
            continue;
        }

        // For split models, check that ALL parts are present
        let all_parts_present = if catalog.is_split() {
            catalog
                .all_part_filenames()
                .iter()
                .all(|pf| on_disk.contains(pf.as_str()))
        } else {
            true
        };

        // Calculate total size across all parts
        let total_size_bytes: u64 = catalog
            .all_part_filenames()
            .iter()
            .filter_map(|pf| std::fs::metadata(models_dir.join(pf)).ok().map(|m| m.len()))
            .sum();

        let size_gb = total_size_bytes as f64 / (1024.0 * 1024.0 * 1024.0);

        results.push(serde_json::json!({
            "filename": catalog.filename,
            "size_bytes": total_size_bytes,
            "size_gb": format!("{:.1}", size_gb),
            "name": catalog.name,
            "tier": catalog.tier,
            "is_custom": false,
            "is_split": catalog.is_split(),
            "all_parts_present": all_parts_present,
            "total_parts": catalog.all_part_filenames().len(),
        }));
        emitted.insert(catalog.filename.to_string());
    }

    // Second pass: catalog variant filenames.
    // Models with multiple quant variants (e.g. Qwen3.5-27B) ship as separate
    // single files. A user may download the non-default variant (e.g. Q4_K_M
    // instead of IQ3_XXS). These variant files must be listed as proper catalog
    // entries, NOT as "Custom Models".
    for catalog in model_selector::LLM_MODELS {
        for variant in catalog.variants {
            if emitted.contains(variant.filename) {
                continue; // already listed (unlikely but safe)
            }
            if !on_disk.contains(variant.filename) {
                continue;
            }
            let part_path = models_dir.join(variant.filename);
            if !is_valid_gguf(&part_path, Some(variant.expected_size_bytes)) {
                continue;
            }

            // For split variants (rare), check all parts
            let part_filenames = variant.all_part_filenames();
            let all_parts_present = part_filenames.iter().all(|pf| on_disk.contains(pf.as_str()));

            let total_size_bytes: u64 = part_filenames
                .iter()
                .filter_map(|pf| std::fs::metadata(models_dir.join(pf)).ok().map(|m| m.len()))
                .sum();
            let size_gb = total_size_bytes as f64 / (1024.0 * 1024.0 * 1024.0);

            let display_name = format!("{} ({})", catalog.name, variant.label);
            results.push(serde_json::json!({
                "filename": variant.filename,
                "size_bytes": total_size_bytes,
                "size_gb": format!("{:.1}", size_gb),
                "name": display_name,
                "tier": catalog.tier,
                "is_custom": false,
                "is_split": variant.is_split(),
                "all_parts_present": all_parts_present,
                "total_parts": part_filenames.len(),
            }));
            emitted.insert(variant.filename.to_string());
        }
    }

    // Build the complete set of all catalog-known filenames (default + all variants + all parts)
    // so the custom-model pass can correctly exclude them.
    let catalog_part_filenames: std::collections::HashSet<String> = {
        let mut set = std::collections::HashSet::new();
        for catalog in model_selector::LLM_MODELS {
            // Default filename and all split parts
            for pf in catalog.all_part_filenames() {
                set.insert(pf);
            }
            // Variant filenames and their split parts
            for variant in catalog.variants {
                for pf in variant.all_part_filenames() {
                    set.insert(pf);
                }
            }
        }
        set
    };

    // Third pass: genuinely custom models — GGUF files not known to the catalog at all.
    for fname in &on_disk {
        if catalog_part_filenames.contains(fname) {
            continue; // known catalog file — handled above
        }
        if emitted.contains(fname) {
            continue;
        }
        let path = models_dir.join(fname);
        if !is_valid_gguf(&path, None) {
            continue;
        }
        let size_bytes = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
        let size_gb = size_bytes as f64 / (1024.0 * 1024.0 * 1024.0);
        results.push(serde_json::json!({
            "filename": fname,
            "size_bytes": size_bytes,
            "size_gb": format!("{:.1}", size_gb),
            "name": "Custom Model",
            "tier": null,
            "is_custom": true,
            "is_split": false,
            "all_parts_present": true,
            "total_parts": 1,
        }));
    }

    results
}

/// Delete a downloaded LLM model file (and all its split parts for split models).
#[tauri::command]
pub async fn delete_llm_model(
    app: AppHandle,
    state: State<'_, LlmServerState>,
    filename: String,
) -> Result<(), String> {
    // If this model is currently loaded, stop the server first.
    {
        let mut server = state.lock().await;
        if server.status.running && server.status.model_name == extract_stem(&filename) {
            server.stop().await;
        }
    }

    let models_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join("models");

    // Look up all part filenames (handles split models)
    let parts_to_delete: Vec<String> = if let Some(catalog) = model_selector::LLM_MODELS
        .iter()
        .find(|m| m.filename == filename)
    {
        catalog.all_part_filenames()
    } else {
        vec![filename.clone()]
    };

    for part_file in &parts_to_delete {
        let path = models_dir.join(part_file);
        if path.exists() {
            std::fs::remove_file(&path)
                .map_err(|e| format!("Failed to delete {}: {}", part_file, e))?;
        }
    }

    Ok(())
}

// ── Hardware & Recommendations ────────────────────────────────────────────

/// Detect hardware and return LLM model recommendation.
/// Reuses HardwareProfile from the transcription module.
#[tauri::command]
pub async fn detect_llm_hardware() -> Result<serde_json::Value, String> {
    let hw = HardwareProfile::detect();
    let selection = model_selector::select_llm_model(&hw);

    let all_models: Vec<serde_json::Value> = model_selector::LLM_MODELS
        .iter()
        .map(|m| {
            let variants: Vec<serde_json::Value> = m.variants.iter().map(|v| {
                serde_json::json!({
                    "label": v.label,
                    "quant": v.quant,
                    "filename": v.filename,
                    "disk_size_gb": v.disk_size_gb,
                    "ram_required_gb": v.ram_required_gb,
                    "hf_url": v.hf_url,
                    "hf_parts": v.hf_parts,
                    "is_split": v.is_split(),
                    "all_part_urls": v.all_part_urls(),
                    "expected_size_bytes": v.expected_size_bytes,
                    "mmproj_filename": v.mmproj_filename,
                    "mmproj_url": v.mmproj_url,
                    "mmproj_expected_size_bytes": v.mmproj_expected_size_bytes,
                })
            }).collect();

            serde_json::json!({
                "tier": m.tier,
                "name": m.name,
                "provider": m.provider,
                "filename": m.filename,
                "disk_size_gb": m.disk_size_gb,
                "ram_required_gb": m.ram_required_gb,
                "text_rating": m.text_rating,
                "code_rating": m.code_rating,
                "vision_rating": m.vision_rating,
                "tool_calling_rating": m.tool_calling_rating,
                "speed": m.speed,
                "description": m.description,
                "knowledge_cutoff": m.knowledge_cutoff,
                "hf_model_card_url": m.hf_model_card_url,
                "is_uncensored": m.is_uncensored,
                "is_server_grade": m.is_server_grade,
                "hf_url": m.hf_url,
                "hf_parts": m.hf_parts,
                "is_split": m.is_split(),
                "all_part_urls": m.all_part_urls(),
                "context_length": m.context_length,
                "expected_size_bytes": m.expected_size_bytes,
                "mmproj_filename": m.mmproj_filename,
                "mmproj_url": m.mmproj_url,
                "mmproj_expected_size_bytes": m.mmproj_expected_size_bytes,
                "variants": variants,
            })
        })
        .collect();

    Ok(serde_json::json!({
        "hardware": hw,
        "recommended_tier": selection.tier,
        "recommended_filename": selection.filename,
        "recommended_name": selection.name,
        "recommended_size_gb": selection.disk_size_gb,
        "recommended_gpu_layers": selection.gpu_layers,
        "reason": selection.reason,
        "can_upgrade": selection.can_upgrade,
        "all_models": all_models,
    }))
}

/// Get the LLM setup status (config + what's downloaded).
///
/// Also performs a one-time migration: the old assembled Qwen2.5-14B file
/// (`qwen2.5-14b-instruct-q4_k_m.gguf`) was created by concatenating split
/// parts, but newer llama.cpp rejects it because the embedded split metadata
/// doesn't match the filename. We detect and remove it automatically so the
/// user is prompted to re-download the native split parts.
#[tauri::command]
pub async fn get_llm_setup_status(
    app: AppHandle,
    state: State<'_, LlmServerState>,
) -> Result<serde_json::Value, String> {
    let config_dir = app.path().app_data_dir().map_err(|e| e.to_string())?;

    // Migrate: remove old assembled Qwen2.5-14B file that llama.cpp b8281+ rejects
    let models_dir = config_dir.join("models");
    let old_assembled = models_dir.join("qwen2.5-14b-instruct-q4_k_m.gguf");
    if old_assembled.exists() {
        // This is the old concatenated file — remove it so the UI shows
        // "not downloaded" and offers to fetch the native split parts instead
        let _ = std::fs::remove_file(&old_assembled);
        eprintln!("[llm] Removed legacy assembled Qwen2.5-14B file (incompatible with llama.cpp b8281+). Please re-download.");
    }

    let config = LlmConfig::load(&config_dir);

    let server = state.lock().await;
    let server_status = server.status.clone();
    drop(server);

    let downloaded = list_downloaded_model_filenames(&app);

    Ok(serde_json::json!({
        "setup_complete": config.setup_complete,
        "selected_model": config.selected_model,
        "server_running": server_status.running,
        "server_port": server_status.port,
        "server_model": server_status.model_name,
        "downloaded_models": downloaded,
    }))
}

// ── Internal Helpers ──────────────────────────────────────────────────────

fn resolve_model_path(app: &AppHandle, filename: &str) -> Result<String, String> {
    let path = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join("models")
        .join(filename);
    Ok(path.to_string_lossy().to_string())
}

/// Validate that a file is a fully-downloaded, valid GGUF model.
///
/// Checks:
///   1. File exists and is at least 10 MB
///   2. Magic bytes match "GGUF" (0x47 0x47 0x55 0x46)
///   3. If `expected_bytes` is provided, actual size must be ≥ 90% of expected
///      (10% tolerance — HuggingFace occasionally updates quantizations with slightly
///      different byte counts and our catalog values are estimates, not live HEAD queries)
fn is_valid_gguf(path: &std::path::Path, expected_bytes: Option<u64>) -> bool {
    if !path.exists() {
        return false;
    }
    let meta = match std::fs::metadata(path) {
        Ok(m) => m,
        Err(_) => return false,
    };
    let actual_size = meta.len();

    // Minimum sanity check — all real models are much larger than this
    if actual_size < 10_000_000 {
        eprintln!(
            "[llm] is_valid_gguf FAIL (too small): {} — actual {} bytes < 10 MB minimum",
            path.display(),
            actual_size
        );
        return false;
    }

    // Size validation against expected — catches partial assemblies.
    // Tolerance widened to 10% (was 5%) because catalog sizes are estimates and HF
    // sometimes updates model files. We log a warning when size diverges so it's
    // visible in the sidecar IPC log / issue report without blocking a valid download.
    if let Some(expected) = expected_bytes {
        if expected > 0 {
            let min_acceptable = (expected as f64 * 0.90) as u64;
            if actual_size < min_acceptable {
                eprintln!(
                    "[llm] is_valid_gguf FAIL (size mismatch): {} — actual {} bytes is < 90% of expected {} bytes",
                    path.display(),
                    actual_size,
                    expected
                );
                return false;
            }
            // Log divergence even when within tolerance so we can update catalog values
            let divergence_pct = ((actual_size as f64 - expected as f64) / expected as f64 * 100.0).abs();
            if divergence_pct > 2.0 {
                eprintln!(
                    "[llm] is_valid_gguf WARNING (size divergence {:.1}%): {} — actual {} vs expected {} bytes (within tolerance, file accepted)",
                    divergence_pct,
                    path.display(),
                    actual_size,
                    expected
                );
            }
        }
    }

    // Magic bytes check — hard failure, always logged
    let mut file = match std::fs::File::open(path) {
        Ok(f) => f,
        Err(e) => {
            eprintln!("[llm] is_valid_gguf FAIL (cannot open): {} — {}", path.display(), e);
            return false;
        }
    };
    let mut magic = [0u8; 4];
    use std::io::Read;
    if file.read_exact(&mut magic).is_err() {
        eprintln!("[llm] is_valid_gguf FAIL (cannot read magic bytes): {}", path.display());
        return false;
    }
    let valid = magic == [0x47, 0x47, 0x55, 0x46];
    if !valid {
        eprintln!(
            "[llm] is_valid_gguf FAIL (bad magic bytes): {} — got {:02X} {:02X} {:02X} {:02X}, expected 47 47 55 46",
            path.display(),
            magic[0], magic[1], magic[2], magic[3]
        );
    }
    valid
}

fn extract_stem(filename: &str) -> String {
    std::path::Path::new(filename)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("unknown")
        .to_string()
}

/// Probe the content-length of a URL without downloading it.
async fn probe_content_length(
    client: &reqwest::Client,
    url: &str,
    hf_token: Option<&str>,
) -> Option<u64> {
    let mut req = client.head(url).header("User-Agent", "matrx-local/1.0");
    if let Some(token) = hf_token {
        req = req.header("Authorization", format!("Bearer {}", token));
    }
    let resp = req.send().await.ok()?;
    if let Some(len) = resp.content_length() {
        if len > 0 {
            return Some(len);
        }
    }
    // HuggingFace puts the real LFS size in x-linked-size
    resp.headers()
        .get("x-linked-size")
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.parse::<u64>().ok())
}

/// Download a single URL into `dest` with:
///   - A 60-second per-chunk idle timeout (stall detection)
///   - Cancellation checks on every progress event
///   - Accurate overall-progress reporting for multi-part downloads
///   - Optional HF token injected as Authorization: Bearer header
#[allow(clippy::too_many_arguments)]
async fn try_download_part(
    client: &reqwest::Client,
    url: &str,
    dest: &std::path::Path,
    filename: &str,
    part: usize,
    total_parts: usize,
    bytes_before: u64,
    grand_total: u64,
    app: &AppHandle,
    cancel: &Arc<AtomicBool>,
    hf_token: Option<&str>,
    // When Some, also feed live progress to the universal Download Manager under
    // this id (the same id register_external used) so the Downloads UI tracks
    // the transfer instead of sitting at 0% for a multi-GB LLM/mmproj download.
    dm_id: Option<&str>,
) -> Result<(), String> {
    use futures_util::StreamExt;
    use tokio::io::AsyncWriteExt;

    let mut req = client.get(url).header("User-Agent", "matrx-local/1.0");
    if let Some(token) = hf_token {
        req = req.header("Authorization", format!("Bearer {}", token));
    }
    let response = req.send().await.map_err(|e| {
        eprintln!("[llm] try_download_part '{}' part {}/{}: connection error — {}", filename, part, total_parts, e);
        e.to_string()
    })?;

    if !response.status().is_success() {
        let status = response.status();
        eprintln!(
            "[llm] try_download_part '{}' part {}/{}: HTTP {} for {}",
            filename, part, total_parts, status, url
        );
        return Err(format!("HTTP {} for {}", status, url));
    }

    let part_total = response.content_length().unwrap_or(0);
    eprintln!(
        "[llm] try_download_part '{}' part {}/{}: HTTP 200, content-length={} bytes",
        filename, part, total_parts, part_total
    );
    let mut part_downloaded: u64 = 0;

    let mut file = tokio::fs::File::create(dest)
        .await
        .map_err(|e| e.to_string())?;
    let mut stream = response.bytes_stream();

    // 60-second idle timeout: if no chunk arrives in this window, treat as stall
    let idle_timeout = std::time::Duration::from_secs(60);

    // dm-progress throttle — mirror the Download Manager's own cadence
    // (256 KB or 1s) so multi-GB transfers don't flood the IPC bus. The
    // legacy `llm-download-progress` emission below stays per-chunk (the
    // Model Repo Analyzer inline progress relies on it).
    let dm_throttle_bytes: u64 = 256 * 1024;
    let mut dm_last_bytes: u64 = 0;
    let mut dm_last_emit = std::time::Instant::now();

    loop {
        if cancel.load(Ordering::SeqCst) {
            let _ = file.flush().await;
            return Err("cancelled: user requested cancellation".to_string());
        }

        let chunk_result = tokio::time::timeout(idle_timeout, stream.next()).await;

        match chunk_result {
            Err(_) => {
                // Timed out — no data received in 60s, treat as stall
                let msg = format!(
                    "Stalled: no data received for {}s on part {}/{}",
                    idle_timeout.as_secs(),
                    part,
                    total_parts
                );
                eprintln!("[llm] try_download_part '{}': {}", filename, msg);
                return Err(msg);
            }
            Ok(None) => {
                // Stream ended cleanly
                eprintln!(
                    "[llm] try_download_part '{}' part {}/{}: stream complete, {} bytes written",
                    filename, part, total_parts, part_downloaded
                );
                break;
            }
            Ok(Some(Err(e))) => {
                eprintln!("[llm] try_download_part '{}' part {}/{}: stream error — {}", filename, part, total_parts, e);
                return Err(format!("Stream error: {}", e));
            }
            Ok(Some(Ok(chunk))) => {
                file.write_all(&chunk).await.map_err(|e| e.to_string())?;
                part_downloaded += chunk.len() as u64;

                let overall_downloaded = bytes_before + part_downloaded;
                let overall_percent = if grand_total > 0 {
                    (overall_downloaded as f64 / grand_total as f64) * 100.0
                } else if part_total > 0 {
                    (part_downloaded as f64 / part_total as f64) * 100.0
                } else {
                    0.0
                };

                let _ = app.emit(
                    "llm-download-progress",
                    serde_json::json!({
                        "filename": filename,
                        "part": part,
                        "total_parts": total_parts,
                        "part_bytes_downloaded": part_downloaded,
                        "part_total_bytes": part_total,
                        "bytes_downloaded": overall_downloaded,
                        "total_bytes": grand_total,
                        "percent": overall_percent,
                        "status": "downloading",
                    }),
                );

                // Universal Download Manager feed (throttled). Payload shape
                // mirrors the Whisper path exactly so DownloadManagerContext
                // merges it into the entry register_external created.
                if let Some(id) = dm_id {
                    let now = std::time::Instant::now();
                    if overall_downloaded.saturating_sub(dm_last_bytes) >= dm_throttle_bytes
                        || now.duration_since(dm_last_emit).as_secs() >= 1
                    {
                        dm_last_bytes = overall_downloaded;
                        dm_last_emit = now;
                        let dm_total = if grand_total > 0 { grand_total } else { part_total };
                        let _ = app.emit(
                            "dm-progress",
                            serde_json::json!({
                                // display_name intentionally omitted — the entry
                                // registered via register_external already holds the
                                // catalog name; mergeEntry preserves it when omitted.
                                "id": id,
                                "category": "llm",
                                "filename": filename,
                                "status": "active",
                                "bytes_done": overall_downloaded,
                                "total_bytes": dm_total,
                                "percent": overall_percent,
                                "part_current": part,
                                "part_total": total_parts,
                                "speed_bps": 0.0,
                                "eta_seconds": null,
                                "error_msg": null,
                            }),
                        );
                    }
                }
            }
        }
    }

    file.flush().await.map_err(|e| e.to_string())?;
    Ok(())
}

fn list_downloaded_model_filenames(app: &AppHandle) -> Vec<String> {
    let models_dir = match app.path().app_data_dir() {
        Ok(d) => d.join("models"),
        Err(_) => return Vec::new(),
    };
    if !models_dir.exists() {
        return Vec::new();
    }

    let on_disk: std::collections::HashSet<String> = std::fs::read_dir(&models_dir)
        .map(|entries| {
            entries
                .filter_map(|e| e.ok())
                .filter(|e| {
                    e.path()
                        .extension()
                        .map(|ext| ext == "gguf")
                        .unwrap_or(false)
                })
                .filter_map(|e| e.file_name().into_string().ok())
                .collect()
        })
        .unwrap_or_default();

    // Return the first-part filename for each catalog model whose first part is present,
    // plus any custom models present on disk.
    let mut result = Vec::new();

    let catalog_part_filenames: std::collections::HashSet<String> = model_selector::LLM_MODELS
        .iter()
        .flat_map(|m| m.all_part_filenames())
        .collect();

    for catalog in model_selector::LLM_MODELS {
        if on_disk.contains(catalog.filename)
            && is_valid_gguf(
                &models_dir.join(catalog.filename),
                Some(catalog.expected_size_bytes),
            )
        {
            result.push(catalog.filename.to_string());
        }
    }

    for fname in &on_disk {
        if !catalog_part_filenames.contains(fname) {
            let path = models_dir.join(fname);
            if is_valid_gguf(&path, None) {
                result.push(fname.clone());
            }
        }
    }

    result
}
