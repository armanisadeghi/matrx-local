//! Wake-word detection using a dedicated whisper.cpp (tiny.en) inference loop.
//!
//! Architecture
//! ────────────
//! Rather than adding a new ML dependency, we reuse the whisper.cpp engine that
//! is already in the codebase.  A dedicated "wake word thread" runs whisper-tiny
//! on short 2-second audio windows at ~2 Hz, checks whether the decoded text
//! contains the configured keyword phrase, and fires an event on match.
//!
//! This is different from the main transcription thread:
//!   - It uses the SMALLEST model (ggml-tiny.en.bin, 75 MB)
//!   - It runs on 2-second windows (fast, low latency)
//!   - It has its own AudioCapture instance (does NOT share with whisper)
//!   - It is paused when the main transcription is active
//!
//! Why not sherpa-onnx?
//! The sherpa-onnx 0.1 Rust crate does not yet expose a KeywordSpotter type —
//! only OnlineRecognizer/OfflineRecognizer (ASR), VAD, and TTS.  The C API for
//! keyword spotting exists but the Rust bindings have not been generated yet.
//! We will migrate to sherpa-onnx KWS when those bindings land.  Until then,
//! whisper-tiny gives excellent accuracy at reasonable cost.
//!
//! State machine
//! ─────────────
//!   IDLE       — thread not started
//!   LISTENING  — actively detecting
//!   MUTED      — thread running, audio ignored
//!   DISMISSED  — false trigger; auto-reverts after DISMISS_PAUSE_SECS
//!
//! Cross-platform
//! ─────────────
//! Uses only cpal, rubato, and whisper-cpp-plus — all already in Cargo.toml.
//! No additional dependencies.

use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};
use whisper_cpp_plus::TranscriptionParams;

// ── Public state types ────────────────────────────────────────────────────────

/// Operational mode of the wake-word subsystem.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum WakeWordMode {
    Listening,
    Muted,
    Dismissed,
}

/// Thread-safe shared state for the wake-word thread.
pub struct WakeWordState {
    pub mode: Mutex<WakeWordMode>,
    pub running: Mutex<bool>,
    /// Path to the models directory (same dir the main engine uses).
    pub models_dir: Mutex<Option<PathBuf>>,
    /// The whisper model filename to use for wake word detection.
    /// Defaults to ggml-tiny.en.bin for speed; can be changed at runtime.
    pub model_filename: Mutex<String>,
    /// Keyword phrase to match (case-insensitive substring check on decoded text).
    pub keyword: Mutex<String>,
    /// Handle to the background thread so shutdown can join it and guarantee the
    /// GGML context on the thread stack is fully dropped before the process exits.
    /// Without joining, GGML's C atexit handler calls ggml_abort() → SIGABRT →
    /// macOS crash report even on intentional quit.
    pub thread_handle: Mutex<Option<JoinHandle<()>>>,
}

impl WakeWordState {
    pub fn new() -> Self {
        WakeWordState {
            mode: Mutex::new(WakeWordMode::Listening),
            running: Mutex::new(false),
            models_dir: Mutex::new(None),
            model_filename: Mutex::new("ggml-tiny.en.bin".to_string()),
            // Whisper transcribes "matrx" as "matrix" — use the common spelling
            // so the substring match actually fires. Will be replaced by trained
            // phonemes when sherpa-onnx KWS bindings land.
            keyword: Mutex::new("hey matrix".to_string()),
            thread_handle: Mutex::new(None),
        }
    }
}

// ── RMS helper ───────────────────────────────────────────────────────────────

fn rms(samples: &[f32]) -> f32 {
    if samples.is_empty() {
        return 0.0;
    }
    let sq: f32 = samples.iter().map(|s| s * s).sum();
    (sq / samples.len() as f32).sqrt()
}

// ── Thread entry point ────────────────────────────────────────────────────────

/// Spawn the wake-word background thread and store the JoinHandle in state.
///
/// The thread runs until `state.running` is set to false.
/// The JoinHandle is stored in `state.thread_handle` so `graceful_shutdown_sync`
/// can `.join()` it, guaranteeing the GGML context is fully dropped before the
/// process exits (prevents SIGABRT / macOS crash reports on quit).
///
/// It emits the following Tauri events:
///   "wake-word-detected" — `{ keyword: String }` when the phrase is heard
///   "wake-word-rms"      — `f32` live microphone level (0–1), ~5 Hz
///   "wake-word-mode"     — `WakeWordMode` on every state transition
///   "wake-word-error"    — `String` (non-fatal; thread keeps running)
pub fn spawn_wake_word_thread(
    app: tauri::AppHandle,
    state: Arc<WakeWordState>,
    device_name: Option<String>,
    startup_tx: std::sync::mpsc::SyncSender<Result<(), String>>,
) {
    let state_for_thread = state.clone();
    let handle = std::thread::spawn(move || {
        wake_word_loop(app, state_for_thread, device_name, startup_tx);
    });
    *state.thread_handle.lock().unwrap() = Some(handle);
}

fn wake_word_loop(
    app: tauri::AppHandle,
    state: Arc<WakeWordState>,
    device_name: Option<String>,
    startup_tx: std::sync::mpsc::SyncSender<Result<(), String>>,
) {
    use super::manager::TranscriptionManager;
    use tauri::Emitter;

    // ── Resolve model path ───────────────────────────────────────────────
    let (models_dir, model_filename) = {
        let dir = state.models_dir.lock().unwrap().clone();
        let fname = state.model_filename.lock().unwrap().clone();
        match dir {
            Some(d) => (d, fname),
            None => {
                let message = "Wake-word models directory is unavailable.".to_string();
                let _ = startup_tx.send(Err(message));
                *state.running.lock().unwrap() = false;
                return;
            }
        }
    };

    let model_path = models_dir.join(&model_filename);
    if !model_path.exists() {
        // Model not downloaded yet — emit a helpful message but don't crash
        let message = format!(
            "Wake word model not found: {}. Complete voice setup first.",
            model_filename
        );
        let _ = startup_tx.send(Err(message));
        *state.running.lock().unwrap() = false;
        return;
    }

    // Load the model (runs on this thread — blocking is fine here)
    let manager = match TranscriptionManager::load(model_path) {
        Ok(m) => m,
        Err(e) => {
            let message = format!("Failed to load wake word model: {}", e);
            let _ = startup_tx.send(Err(message));
            *state.running.lock().unwrap() = false;
            return;
        }
    };

    let ctx = manager.context().clone();

    // ── Open audio capture ───────────────────────────────────────────────
    let capture =
        match super::audio_capture::AudioCapture::start_with_device(device_name.as_deref()) {
            Ok(c) => c,
            Err(e) => {
                let message = format!("Audio capture failed: {}", e);
                let _ = startup_tx.send(Err(message));
                *state.running.lock().unwrap() = false;
                return;
            }
        };
    let _ = startup_tx.send(Ok(()));

    let sample_rate = capture.sample_rate(); // always 16_000
    let window_samples = sample_rate as usize * 2; // 2-second window

    // After a trigger, wait COOLDOWN_S before re-arming.
    const COOLDOWN_S: u64 = 3;
    // After dismiss, wait DISMISS_PAUSE_S before re-arming.
    const DISMISS_PAUSE_S: u64 = 10;

    let mut last_trigger = Instant::now() - Duration::from_secs(60); // pre-cooled
    let mut dismiss_until: Option<Instant> = None;

    let mut accumulated = Vec::<f32>::with_capacity(window_samples + 4096);
    let mut tick: u32 = 0;

    loop {
        if !*state.running.lock().unwrap() {
            break;
        }

        let mode = state.mode.lock().unwrap().clone();
        let samples = match capture.drain() {
            Ok(samples) => samples,
            Err(message) => {
                let _ = app.emit("wake-word-error", &message);
                *state.mode.lock().unwrap() = WakeWordMode::Muted;
                let _ = app.emit("wake-word-mode", WakeWordMode::Muted);
                crate::lifecycle_log::log(
                    "[native-audio] wake-word capture failed after startup. Remedy: reconnect or choose another microphone.",
                );
                let diagnostics_app = app.clone();
                tauri::async_runtime::spawn(async move {
                    if let Err(error) = crate::error_outbox::enqueue_native_event(
                        diagnostics_app,
                        "error",
                        "native.audio.wake-word-runtime",
                        message,
                    )
                    .await
                    {
                        crate::lifecycle_log::log(&format!(
                            "[native-audio] could not persist wake-word runtime diagnostic: {error}"
                        ));
                    }
                });
                *state.running.lock().unwrap() = false;
                break;
            }
        };

        if !samples.is_empty() {
            // Always emit RMS so the UI audio meter stays alive
            if tick % 5 == 0 {
                let level = rms(&samples).min(1.0);
                let _ = app.emit("wake-word-rms", level);
            }

            // Manage dismissed → listening transition
            let should_detect = match &mode {
                WakeWordMode::Muted => false,
                WakeWordMode::Dismissed => {
                    if let Some(until) = dismiss_until {
                        if Instant::now() >= until {
                            dismiss_until = None;
                            *state.mode.lock().unwrap() = WakeWordMode::Listening;
                            let _ = app.emit("wake-word-mode", WakeWordMode::Listening);
                            true
                        } else {
                            false
                        }
                    } else {
                        // No timer set — apply it now
                        dismiss_until = Some(Instant::now() + Duration::from_secs(DISMISS_PAUSE_S));
                        false
                    }
                }
                WakeWordMode::Listening => last_trigger.elapsed().as_secs() >= COOLDOWN_S,
            };

            if should_detect {
                accumulated.extend_from_slice(&samples);
            }
        }

        // When we have a full 2-second window, run whisper on it
        if accumulated.len() >= window_samples {
            let window: Vec<f32> = accumulated.drain(0..window_samples).collect();

            // Energy gate — skip silent windows
            if rms(&window) > 0.00005 {
                let params = TranscriptionParams::builder()
                    .language("en")
                    .n_threads(2)
                    .build();

                if let Ok(result) = ctx.transcribe_with_params(&window, params) {
                    let full_text: String = result
                        .segments
                        .iter()
                        .map(|s| s.text.trim())
                        .collect::<Vec<_>>()
                        .join(" ")
                        .to_lowercase();

                    let keyword_lower = state.keyword.lock().unwrap().to_lowercase();
                    if full_text.contains(&keyword_lower) {
                        last_trigger = Instant::now();
                        accumulated.clear(); // discard any buffered audio after trigger

                        let _ = app.emit(
                            "wake-word-detected",
                            serde_json::json!({ "keyword": keyword_lower }),
                        );
                    }
                }
            }
        }

        // Cap accumulated buffer at 6 seconds to avoid unbounded growth when
        // detect is off (muted/dismissed) and samples keep coming in
        if accumulated.len() > sample_rate as usize * 6 {
            let excess = accumulated.len() - sample_rate as usize * 6;
            accumulated.drain(0..excess);
        }

        tick = tick.wrapping_add(1);
        std::thread::sleep(Duration::from_millis(50));
    }

    *state.running.lock().unwrap() = false;
}

// ── Dismiss helper ────────────────────────────────────────────────────────────

/// Set mode to Dismissed. The loop sets the dismiss_until timer on next iteration.
pub fn apply_dismiss(state: &WakeWordState) {
    *state.mode.lock().unwrap() = WakeWordMode::Dismissed;
}
