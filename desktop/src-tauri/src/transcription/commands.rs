use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use tauri::{AppHandle, Emitter, Manager, State};
use whisper_cpp_plus::TranscriptionParams;

use super::{
    audio_capture,
    config::TranscriptionConfig,
    downloader,
    hardware::HardwareProfile,
    manager::TranscriptionManager,
    model_selector,
    wake_word::{WakeWordMode, WakeWordState},
};

// Default model used for wake word detection (fastest whisper model).
const WAKE_WORD_DEFAULT_MODEL: &str = "ggml-tiny.en.bin";

/// Tauri-managed state holding the active transcription context.
pub struct TranscriptionState(pub Mutex<Option<TranscriptionManager>>);

/// Tauri-managed state for active recording session.
///
/// The `Arc<Mutex<bool>>` is the stop flag shared with the audio thread.
/// The `Mutex<Option<JoinHandle<()>>>` stores the thread handle so
/// `graceful_shutdown_sync` can join it on quit, guaranteeing the GGML
/// WhisperContext on the thread stack is fully dropped before process exit.
pub struct RecordingState {
    pub flag: Arc<Mutex<bool>>,
    pub thread_handle: Mutex<Option<JoinHandle<()>>>,
}

impl RecordingState {
    pub fn new() -> Self {
        RecordingState {
            flag: Arc::new(Mutex::new(false)),
            thread_handle: Mutex::new(None),
        }
    }
}

/// Shared atomic flag for cancelling in-flight whisper model downloads.
/// Newtype wrapper (not a type alias) so Tauri can manage it as a distinct
/// state type from LlmDownloadCancelState, which is also Arc<AtomicBool>.
/// Tauri keys managed state by TypeId — type aliases share a TypeId, newtypes don't.
pub struct WhisperDownloadCancelState(pub Arc<AtomicBool>);

/// Tauri-managed state for the wake-word subsystem.
pub struct WakeWordAppState(pub Arc<WakeWordState>);

// ── Hardware Detection ─────────────────────────────────────────────────────

/// Detect hardware capabilities and return a model recommendation.
#[tauri::command]
pub async fn detect_hardware() -> Result<serde_json::Value, String> {
    let hw = HardwareProfile::detect();
    let selection = model_selector::select_model(&hw, None);
    let can_upgrade = model_selector::should_offer_upgrade(&hw, &selection.tier);

    // Get info for all tiers so the frontend can display options
    let all_models: Vec<serde_json::Value> = model_selector::MODELS
        .iter()
        .map(|m| {
            serde_json::json!({
                "tier": m.tier,
                "filename": m.filename,
                "download_size_mb": m.download_size_mb,
                "ram_required_mb": m.ram_required_mb,
                "relative_speed": m.relative_speed,
                "accuracy": m.accuracy,
                "description": m.description,
            })
        })
        .collect();

    Ok(serde_json::json!({
        "hardware": hw,
        "recommended_tier": selection.tier,
        "recommended_filename": selection.filename,
        "recommended_size_mb": selection.download_size_mb,
        "reason": selection.reason,
        "can_upgrade": can_upgrade,
        "all_models": all_models,
    }))
}

// ── Model Download ─────────────────────────────────────────────────────────

/// Download a Whisper model with live progress events.
/// Emits "whisper-download-progress" (legacy) and "dm-progress" (universal) events.
#[tauri::command]
pub async fn download_whisper_model(
    app: AppHandle,
    filename: String,
    cancel: State<'_, WhisperDownloadCancelState>,
) -> Result<String, String> {
    use crate::downloads::commands::DownloadManagerState;

    cancel.0.store(false, Ordering::SeqCst);

    let models_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join("models");

    let dl_id = format!("whisper-{}", filename);
    let whisper_url = format!(
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{}",
        filename
    );
    if let Some(dm) = app.try_state::<DownloadManagerState>() {
        dm.register_external(
            &app,
            dl_id.clone(),
            "whisper".to_string(),
            filename.clone(),
            format!("Whisper: {}", filename),
            vec![whisper_url],
        ).await;
    }

    let app_clone = app.clone();
    let dl_id_clone = dl_id.clone();
    let fname = filename.clone();
    let cancel_flag = Arc::clone(&cancel.0);
    let dest = downloader::download_model(&fname, &models_dir, move |progress| {
        let _ = app_clone.emit(
            "whisper-download-progress",
            serde_json::json!({
                "filename": progress.filename,
                "bytes_downloaded": progress.bytes_downloaded,
                "total_bytes": progress.total_bytes,
                "percent": progress.percent,
            }),
        );
        let _ = app_clone.emit(
            "dm-progress",
            serde_json::json!({
                "id": dl_id_clone,
                "category": "whisper",
                "filename": progress.filename,
                "display_name": format!("Whisper: {}", progress.filename),
                "status": "active",
                "bytes_done": progress.bytes_downloaded,
                "total_bytes": progress.total_bytes,
                "percent": progress.percent,
                "part_current": 1,
                "part_total": 1,
                "speed_bps": 0.0,
                "eta_seconds": null,
                "error_msg": null,
            }),
        );
    }, cancel_flag)
    .await;

    match dest {
        Ok(path) => {
            if let Some(dm) = app.try_state::<DownloadManagerState>() {
                dm.mark_external_completed(&app, &dl_id, 0).await;
            }
            Ok(path.to_string_lossy().to_string())
        }
        Err(e) => {
            if let Some(dm) = app.try_state::<DownloadManagerState>() {
                dm.mark_external_failed(&app, &dl_id, &e).await;
            }
            Err(e)
        }
    }
}

/// Download the VAD model required for streaming transcription.
#[tauri::command]
pub async fn download_vad_model(
    app: AppHandle,
    cancel: State<'_, WhisperDownloadCancelState>,
) -> Result<String, String> {
    use crate::downloads::commands::DownloadManagerState;

    let models_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join("models");

    let vad_filename = downloader::VAD_MODEL_FILENAME;
    let dl_id = format!("whisper-{}", vad_filename);

    // Reset the shared cancel flag (download_whisper_model does this too) —
    // a leftover true from a previous cancel made every VAD download abort
    // instantly with "Download cancelled".
    cancel.0.store(false, Ordering::SeqCst);

    if let Some(dm) = app.try_state::<DownloadManagerState>() {
        let vad_url = format!(
            "https://huggingface.co/ggml-org/whisper-vad/resolve/main/{}",
            vad_filename
        );
        dm.register_external(
            &app,
            dl_id.clone(),
            "whisper".to_string(),
            vad_filename.to_string(),
            format!("VAD: {}", vad_filename),
            vec![vad_url],
        ).await;
    }

    let app_clone = app.clone();
    let dl_id_clone = dl_id.clone();
    let cancel_flag = Arc::clone(&cancel.0);
    let dest = downloader::download_model(
        vad_filename,
        &models_dir,
        move |progress| {
            let _ = app_clone.emit(
                "whisper-download-progress",
                serde_json::json!({
                    "filename": progress.filename,
                    "bytes_downloaded": progress.bytes_downloaded,
                    "total_bytes": progress.total_bytes,
                    "percent": progress.percent,
                }),
            );
            let _ = app_clone.emit(
                "dm-progress",
                serde_json::json!({
                    "id": dl_id_clone,
                    "category": "whisper",
                    "filename": progress.filename,
                    "display_name": format!("VAD: {}", progress.filename),
                    "status": "active",
                    "bytes_done": progress.bytes_downloaded,
                    "total_bytes": progress.total_bytes,
                    "percent": progress.percent,
                    "part_current": 1,
                    "part_total": 1,
                    "speed_bps": 0.0,
                    "eta_seconds": null,
                    "error_msg": null,
                }),
            );
        },
        cancel_flag,
    )
    .await;

    match dest {
        Ok(path) => {
            if let Some(dm) = app.try_state::<DownloadManagerState>() {
                dm.mark_external_completed(&app, &dl_id, 0).await;
            }
            Ok(path.to_string_lossy().to_string())
        }
        Err(e) => {
            if let Some(dm) = app.try_state::<DownloadManagerState>() {
                dm.mark_external_failed(&app, &dl_id, &e).await;
            }
            Err(e)
        }
    }
}

/// Cancel an in-flight whisper model download. Safe to call at any time.
#[tauri::command]
pub fn cancel_whisper_download(
    app: AppHandle,
    cancel: State<'_, WhisperDownloadCancelState>,
) -> Result<(), String> {
    cancel.0.store(true, Ordering::SeqCst);
    let _ = app.emit(
        "whisper-download-cancelled",
        serde_json::json!({ "reason": "user_cancelled" }),
    );
    Ok(())
}

// ── Model Management ───────────────────────────────────────────────────────

/// Initialize the transcription context with a specific model.
/// Called after download or on app startup with a cached model.
#[tauri::command]
pub async fn init_transcription(
    app: AppHandle,
    state: State<'_, TranscriptionState>,
    filename: String,
) -> Result<(), String> {
    let model_path = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join("models")
        .join(&filename);

    if !model_path.exists() {
        return Err(format!("Model not found: {:?}", model_path));
    }

    let manager = tokio::task::spawn_blocking(move || TranscriptionManager::load(model_path))
        .await
        .map_err(|e| e.to_string())??;

    *state.0.lock().unwrap() = Some(manager);

    // Save selection to config
    let config_dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
    let mut config = TranscriptionConfig::load(&config_dir);
    config.selected_model = Some(filename);
    config.setup_complete = true;
    config.save(&config_dir)?;

    Ok(())
}

/// Check if a model file exists and is valid.
#[tauri::command]
pub fn check_model_exists(app: AppHandle, filename: String) -> bool {
    app.path()
        .app_data_dir()
        .ok()
        .map(|d| d.join("models").join(&filename))
        .map(|p| downloader::is_valid_model(&p))
        .unwrap_or(false)
}

/// Get the currently active model filename.
#[tauri::command]
pub fn get_active_model(state: State<'_, TranscriptionState>) -> Option<String> {
    state.0.lock().unwrap().as_ref().map(|m| {
        m.model_path()
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .to_string()
    })
}

/// List all downloaded models in the models directory.
#[tauri::command]
pub fn list_downloaded_models(app: AppHandle) -> Vec<String> {
    app.path()
        .app_data_dir()
        .ok()
        .map(|d| downloader::list_downloaded_models(&d.join("models")))
        .unwrap_or_default()
}

/// Delete a downloaded model file.
#[tauri::command]
pub async fn delete_model(app: AppHandle, filename: String) -> Result<(), String> {
    let path = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join("models")
        .join(&filename);

    if path.exists() {
        std::fs::remove_file(&path).map_err(|e| format!("Failed to delete model: {}", e))?;
    }
    Ok(())
}

// ── Transcription ──────────────────────────────────────────────────────────

/// Start real-time microphone transcription.
/// Emits "whisper-segment" events with transcription results.
/// Emits "whisper-error" on failure.
///
/// `device_name`: optional CoreAudio/CPAL device name to use. When omitted the
/// system default input device is used.
#[tauri::command]
pub async fn start_transcription(
    app: AppHandle,
    state: State<'_, TranscriptionState>,
    recording: State<'_, RecordingState>,
    device_name: Option<String>,
) -> Result<(), String> {
    // Check if already recording
    {
        let is_recording = recording.flag.lock().unwrap();
        if *is_recording {
            return Err("Already recording".to_string());
        }
    }

    // Reap any previous capture thread that hasn't fully exited yet (stop
    // flips the flag but the loop may be mid-inference) — starting now would
    // otherwise revive it via the shared flag.
    let leftover = recording.thread_handle.lock().unwrap().take();
    if let Some(handle) = leftover {
        let _ = tauri::async_runtime::spawn_blocking(move || {
            let _ = handle.join();
        })
        .await;
    }

    // On macOS, verify microphone TCC permission before opening the audio
    // stream. CPAL silently delivers silence when permission is denied on
    // signed builds, making it impossible to surface the error from the audio
    // callback. Checking here gives us a clear, actionable error up front.
    #[cfg(target_os = "macos")]
    {
        let mic_granted = tauri_plugin_macos_permissions::check_microphone_permission().await;
        if !mic_granted {
            let msg = "Microphone permission not granted. Open System Settings → Privacy & Security → Microphone and enable access for Matrx Local.";
            let _ = app.emit("whisper-error", msg);
            return Err("microphone_permission_denied".to_string());
        }
    }

    // Get thread count from hardware — use up to half the CPUs, capped at 8
    let hw = HardwareProfile::detect();
    let n_threads = (hw.cpu_threads / 2).max(1).min(8) as i32;

    // Clone the context arc before entering the spawned thread
    let manager_guard = state.0.lock().unwrap();
    let manager = manager_guard
        .as_ref()
        .ok_or("Transcription not initialized — call init_transcription first")?;
    let ctx = manager.context().clone();

    // Mark as recording before spawning so the flag is set synchronously
    *recording.flag.lock().unwrap() = true;

    let app_events = app.clone();
    let recording_flag = app.state::<RecordingState>().flag.clone();

    // AudioCapture holds a cpal::Stream which is !Send, so we run the entire
    // capture + inference loop on a dedicated OS thread.
    // The JoinHandle is stored in RecordingState so graceful_shutdown_sync can
    // join it on quit, guaranteeing the GGML WhisperContext is fully dropped.
    let handle = std::thread::spawn(move || {
        // Start audio capture — always returns 16kHz mono after internal resampling
        let capture = match audio_capture::AudioCapture::start_with_device(device_name.as_deref()) {
            Ok(c) => c,
            Err(e) => {
                let _ = app_events.emit("whisper-error", e);
                *recording_flag.lock().unwrap() = false;
                return;
            }
        };

        // Whisper works best on 5-second chunks — long enough for sentence context,
        // short enough for responsive output.
        let sample_rate = capture.sample_rate(); // always 16000 after resampling
        let target_samples = sample_rate as usize * 5; // 5 seconds

        // Silence gate: skip Whisper on truly silent chunks to avoid hallucinations.
        //
        // The threshold is adaptive: we measure the first 2 seconds of audio (the
        // "floor") and set the gate at 10× that floor.  This handles the wide range
        // of device gain levels — AirPods at 24kHz deliver ~0.0001 floor, while a
        // USB desk mic at 44.1kHz delivers ~0.001 floor.  10× gives headroom without
        // letting clear-silence chunks through.
        //
        // We also keep a hard minimum (0.00001) so the gate fires on dead-silent
        // streams (permission denied / muted) rather than transcribing noise.
        let mut silence_threshold: f32 = 0.00010; // starting value; adapted below
        let mut floor_samples: Vec<f32> = Vec::with_capacity(sample_rate as usize * 2);
        let mut floor_calibrated = false;
        let mut loop_ticks: u32 = 0;

        let mut accumulated = Vec::<f32>::with_capacity(target_samples + 4096);

        loop {
            let still_recording = *recording_flag.lock().unwrap();

            // Drain whatever the CPAL callback has produced since the last tick.
            // When mic is stopped we do one final drain to capture any in-flight
            // samples that arrived between the flag flip and this tick.
            let samples = capture.drain();
            if !samples.is_empty() {
                accumulated.extend_from_slice(&samples);

                // Calibrate silence threshold from the first 2s of audio.
                // We collect raw samples before speech starts, compute their RMS,
                // and use 10× that as the gate.  This fires once on first startup.
                if !floor_calibrated {
                    floor_samples.extend_from_slice(&samples);
                    if floor_samples.len() >= sample_rate as usize * 2 {
                        let floor_rms = rms_energy(&floor_samples);
                        // 10× floor, clamped to [0.00001, 0.001]
                        silence_threshold = (floor_rms * 10.0).clamp(0.00001, 0.001);
                        floor_calibrated = true;
                        let _ = app_events.emit(
                            "whisper-calibrated",
                            serde_json::json!({
                                "floor_rms": floor_rms,
                                "threshold": silence_threshold,
                            }),
                        );
                    }
                }

                // Emit live RMS every ~250ms for the UI audio meter (only while actively recording).
                if still_recording {
                    let recent = accumulated.len().min(sample_rate as usize / 4);
                    if recent > 0 && loop_ticks % 5 == 0 {
                        let rms_live = rms_energy(&accumulated[accumulated.len() - recent..]);
                        let _ = app_events.emit("whisper-rms", rms_live);
                    }
                }
            }

            loop_ticks += 1;

            // Transcription is fully decoupled from microphone state.
            //
            // While recording: flush full 5-second chunks as they accumulate.
            // After recording stops: keep flushing — in chunks up to target_samples —
            // until accumulated is completely empty. Only then signal the frontend.
            //
            // This guarantees no audio is ever lost when the mic is stopped, even
            // if many seconds of data are still buffered.
            if accumulated.len() >= target_samples {
                // Full 5-second chunk — consume exactly target_samples to keep alignment.
                let audio_chunk: Vec<f32> = accumulated.drain(0..target_samples).collect();
                transcribe_chunk(
                    &audio_chunk,
                    &ctx,
                    n_threads,
                    silence_threshold,
                    &app_events,
                );
            } else if !still_recording && !accumulated.is_empty() {
                // Mic is off and we have a sub-5-second tail — flush it all at once.
                let audio_chunk: Vec<f32> = accumulated.drain(..).collect();
                transcribe_chunk(
                    &audio_chunk,
                    &ctx,
                    n_threads,
                    silence_threshold,
                    &app_events,
                );
            }

            // Only exit when the mic is stopped AND all buffered audio has been
            // processed. The transcription system is intentionally unaware of
            // microphone state beyond this single exit condition.
            if !still_recording && accumulated.is_empty() {
                let _ = app_events.emit("whisper-stopped", serde_json::Value::Null);
                break;
            }

            std::thread::sleep(std::time::Duration::from_millis(50));
        }

        *recording_flag.lock().unwrap() = false;
    });

    // Store the JoinHandle so graceful_shutdown_sync can join it on quit.
    *recording.thread_handle.lock().unwrap() = Some(handle);

    Ok(())
}

/// Root-mean-square energy of an audio buffer.
fn rms_energy(samples: &[f32]) -> f32 {
    if samples.is_empty() {
        return 0.0;
    }
    let sum_sq: f32 = samples.iter().map(|s| s * s).sum();
    (sum_sq / samples.len() as f32).sqrt()
}

/// Run Whisper on a single audio chunk and emit whisper-segment events.
///
/// Skips the chunk silently if its RMS is below `silence_threshold` (avoids
/// hallucinations on quiet/silent segments). Emits `whisper-error` on failure.
fn transcribe_chunk(
    audio_chunk: &[f32],
    ctx: &whisper_cpp_plus::WhisperContext,
    n_threads: i32,
    silence_threshold: f32,
    app_events: &AppHandle,
) {
    let rms = rms_energy(audio_chunk);
    if rms < silence_threshold {
        return;
    }

    let params = TranscriptionParams::builder()
        .language("en")
        .n_threads(n_threads)
        .build();

    match ctx.transcribe_with_params(audio_chunk, params) {
        Ok(transcription) => {
            for seg in &transcription.segments {
                let text = seg.text.trim().to_string();
                if text.is_empty() || is_hallucination(&text) {
                    continue;
                }
                let _ = app_events.emit(
                    "whisper-segment",
                    serde_json::json!({
                        "text": text,
                        "start_sec": seg.start_seconds(),
                        "end_sec": seg.end_seconds(),
                    }),
                );
            }
        }
        Err(e) => {
            let _ = app_events.emit("whisper-error", format!("Transcription error: {}", e));
        }
    }
}

/// Common Whisper hallucination strings emitted when fed silence or noise.
fn is_hallucination(text: &str) -> bool {
    const HALLUCINATIONS: &[&str] = &[
        "thanks for watching",
        "thank you for watching",
        "please subscribe",
        "like and subscribe",
        "you",
        ".",
        "...",
        "[music]",
        "[applause]",
        "(music)",
    ];
    let lower = text.to_lowercase();
    HALLUCINATIONS.iter().any(|h| lower == *h)
}

/// Stop the active transcription session.
#[tauri::command]
pub async fn stop_transcription(recording: State<'_, RecordingState>) -> Result<(), String> {
    *recording.flag.lock().unwrap() = false;
    // Join the capture thread: the loop observes the flag only every ~50ms
    // (or after the current Whisper inference). Without joining, an immediate
    // restart flipped the SHARED flag back to true and revived this thread —
    // two concurrent capture loops on one WhisperContext, and the overwritten
    // JoinHandle could never be reaped at quit (the GGML SIGABRT hole).
    let handle = recording.thread_handle.lock().unwrap().take();
    if let Some(handle) = handle {
        let _ = tauri::async_runtime::spawn_blocking(move || {
            let _ = handle.join();
        })
        .await;
    }
    Ok(())
}

// ── Audio Devices ──────────────────────────────────────────────────────────

/// List available audio input devices.
#[tauri::command]
pub fn list_audio_input_devices() -> Vec<audio_capture::AudioDeviceInfo> {
    audio_capture::list_input_devices()
}

// ── Setup Status ───────────────────────────────────────────────────────────

/// Check if voice setup has been completed (model downloaded and configured).
#[tauri::command]
pub fn get_voice_setup_status(app: AppHandle) -> serde_json::Value {
    let config_dir = match app.path().app_data_dir() {
        Ok(d) => d,
        Err(_) => {
            return serde_json::json!({
                "setup_complete": false,
                "selected_model": null,
                "downloaded_models": [],
            })
        }
    };

    let config = TranscriptionConfig::load(&config_dir);
    let downloaded = downloader::list_downloaded_models(&config_dir.join("models"));

    serde_json::json!({
        "setup_complete": config.setup_complete,
        "selected_model": config.selected_model,
        "downloaded_models": downloaded,
    })
}

// ── Wake Word Commands ──────────────────────────────────────────────────────

/// Check whether the default wake word model (ggml-tiny.en.bin) is present.
/// The wake word system reuses the existing whisper models — no separate download.
#[tauri::command]
pub fn check_kws_model_exists(app: AppHandle) -> bool {
    app.path()
        .app_data_dir()
        .ok()
        .map(|d| downloader::wake_word_model_exists(&d.join("models"), WAKE_WORD_DEFAULT_MODEL))
        .unwrap_or(false)
}

/// Start wake-word listening mode.
///
/// Requires voice setup to have been completed (ggml-tiny.en.bin downloaded).
/// If already running, un-mutes without restarting the thread.
#[tauri::command]
pub async fn start_wake_word(
    app: AppHandle,
    ww_state: State<'_, WakeWordAppState>,
    device_name: Option<String>,
) -> Result<(), String> {
    // Microphone permission check (macOS TCC)
    #[cfg(target_os = "macos")]
    {
        let granted = tauri_plugin_macos_permissions::check_microphone_permission().await;
        if !granted {
            return Err("microphone_permission_denied".to_string());
        }
    }

    let models_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join("models");

    // Verify the tiny model exists
    if !downloader::wake_word_model_exists(&models_dir, WAKE_WORD_DEFAULT_MODEL) {
        return Err(format!(
            "Wake word model not found ({}). Complete voice setup first.",
            WAKE_WORD_DEFAULT_MODEL
        ));
    }

    // If already running, just un-mute
    if *ww_state.0.running.lock().unwrap() {
        *ww_state.0.mode.lock().unwrap() = WakeWordMode::Listening;
        let _ = app.emit("wake-word-mode", WakeWordMode::Listening);
        return Ok(());
    }

    // Reap a previous thread that hasn't observed running=false yet —
    // setting the SHARED flag true below would otherwise revive it and the
    // overwritten JoinHandle could never be joined at quit.
    let leftover = ww_state.0.thread_handle.lock().unwrap().take();
    if let Some(handle) = leftover {
        let _ = tauri::async_runtime::spawn_blocking(move || {
            let _ = handle.join();
        })
        .await;
    }

    // Configure and start the thread
    *ww_state.0.models_dir.lock().unwrap() = Some(models_dir);
    *ww_state.0.mode.lock().unwrap() = WakeWordMode::Listening;
    *ww_state.0.running.lock().unwrap() = true;

    let _ = app.emit("wake-word-mode", WakeWordMode::Listening);

    super::wake_word::spawn_wake_word_thread(app, ww_state.0.clone(), device_name);

    Ok(())
}

/// Stop wake-word detection entirely (tears down the background thread).
#[tauri::command]
pub fn stop_wake_word(app: AppHandle, ww_state: State<'_, WakeWordAppState>) -> Result<(), String> {
    *ww_state.0.running.lock().unwrap() = false;
    *ww_state.0.mode.lock().unwrap() = WakeWordMode::Muted;
    // Reap the thread off the command path (sync command — can't await).
    // start_wake_word also reaps as a fallback, so the handle is joined
    // exactly once whichever runs first.
    let handle = ww_state.0.thread_handle.lock().unwrap().take();
    if let Some(handle) = handle {
        tauri::async_runtime::spawn_blocking(move || {
            let _ = handle.join();
        });
    }
    let _ = app.emit("wake-word-mode", WakeWordMode::Muted);
    Ok(())
}

/// Mute wake-word detection (thread keeps running but ignores audio).
#[tauri::command]
pub fn mute_wake_word(app: AppHandle, ww_state: State<'_, WakeWordAppState>) -> Result<(), String> {
    *ww_state.0.mode.lock().unwrap() = WakeWordMode::Muted;
    let _ = app.emit("wake-word-mode", WakeWordMode::Muted);
    Ok(())
}

/// Resume listening after mute.
#[tauri::command]
pub fn unmute_wake_word(
    app: AppHandle,
    ww_state: State<'_, WakeWordAppState>,
) -> Result<(), String> {
    *ww_state.0.mode.lock().unwrap() = WakeWordMode::Listening;
    let _ = app.emit("wake-word-mode", WakeWordMode::Listening);
    Ok(())
}

/// Dismiss: user heard a false trigger — pause for 10 s then resume.
#[tauri::command]
pub fn dismiss_wake_word(
    app: AppHandle,
    ww_state: State<'_, WakeWordAppState>,
) -> Result<(), String> {
    super::wake_word::apply_dismiss(&ww_state.0);
    let _ = app.emit("wake-word-mode", WakeWordMode::Dismissed);
    Ok(())
}

/// Manually trigger as though the wake word was spoken (the "Wake up" button).
/// This fires a "wake-word-detected" event directly without any audio check.
#[tauri::command]
pub fn trigger_wake_word(app: AppHandle) -> Result<(), String> {
    let _ = app.emit(
        "wake-word-detected",
        serde_json::json!({ "keyword": "MANUAL", "score": 1.0 }),
    );
    Ok(())
}

/// Update the keyword phrase at runtime and persist to config.
/// Takes effect on the next detection window.
#[tauri::command]
pub fn configure_wake_word(
    app: tauri::AppHandle,
    ww_state: State<'_, WakeWordAppState>,
    keyword: Option<String>,
    model_filename: Option<String>,
) -> Result<(), String> {
    if let Some(kw) = &keyword {
        *ww_state.0.keyword.lock().unwrap() = kw.to_lowercase();
    }
    if let Some(fname) = model_filename {
        *ww_state.0.model_filename.lock().unwrap() = fname;
    }
    if let Some(kw) = keyword {
        if let Ok(dir) = app.path().app_data_dir() {
            let mut config = super::config::TranscriptionConfig::load(&dir);
            config.wake_keyword = kw.to_lowercase();
            let _ = config.save(&dir);
        }
    }
    Ok(())
}

/// Get the current wake-word mode.
#[tauri::command]
pub fn get_wake_word_mode(ww_state: State<'_, WakeWordAppState>) -> WakeWordMode {
    ww_state.0.mode.lock().unwrap().clone()
}

/// Get whether the KWS background thread is running.
#[tauri::command]
pub fn is_wake_word_running(ww_state: State<'_, WakeWordAppState>) -> bool {
    *ww_state.0.running.lock().unwrap()
}
