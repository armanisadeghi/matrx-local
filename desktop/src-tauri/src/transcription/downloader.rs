use futures_util::StreamExt;
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tokio::io::AsyncWriteExt;

const HF_WHISPER_BASE: &str = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main";
const HF_VAD_BASE: &str = "https://huggingface.co/ggml-org/whisper-vad/resolve/main";

/// All known valid magic byte sequences for whisper.cpp model files.
/// - "ggml" (0x67 0x67 0x6D 0x6C) — classic GGML format
/// - "GGUF" (0x47 0x47 0x55 0x46) — GGUF format (whisper.cpp ≥ 1.5.x)
/// - LE ggml (0x6C 0x6D 0x67 0x67) — little-endian GGML magic in some builds
const VALID_WHISPER_MAGIC: &[[u8; 4]] = &[*b"ggml", *b"GGUF", [0x6c, 0x6d, 0x67, 0x67]];

/// VAD model required for streaming transcription.
pub const VAD_MODEL_FILENAME: &str = "ggml-silero-v6.2.0.bin";

// ── Wake word model helpers ──────────────────────────────────────────────────
//
// Wake word detection reuses the existing whisper models.  The default model
// is ggml-tiny.en.bin (75 MB) which is fast enough for 2-second windows.
// No additional download is needed beyond what voice setup already fetches.

/// Returns true if the given whisper model file exists and is valid.
/// Used by the wake word system to verify the tiny model is present.
pub fn wake_word_model_exists(models_dir: &Path, filename: &str) -> bool {
    is_valid_model(&models_dir.join(filename))
}

#[derive(Debug, Clone, Serialize)]
pub struct DownloadProgress {
    pub filename: String,
    pub bytes_downloaded: u64,
    pub total_bytes: u64,
    pub percent: f32,
}

/// Downloads a Whisper model file with progress reporting and cancellation support.
/// Tries HuggingFace (canonical source).
pub async fn download_model(
    filename: &str,
    dest_dir: &Path,
    on_progress: impl Fn(DownloadProgress),
    cancel: Arc<AtomicBool>,
) -> Result<PathBuf, String> {
    let dest_path = dest_dir.join(filename);

    // Skip if already present and valid
    if is_valid_model(&dest_path) {
        return Ok(dest_path);
    }

    std::fs::create_dir_all(dest_dir).map_err(|e| format!("Failed to create models dir: {}", e))?;

    // Determine the correct base URL
    let base_url = if filename.contains("silero") {
        HF_VAD_BASE
    } else {
        HF_WHISPER_BASE
    };

    let url = format!("{}/{}", base_url, filename);

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(600))
        .build()
        .map_err(|e| e.to_string())?;

    // Retry up to 3 times with exponential backoff
    let mut last_error = String::new();
    for attempt in 0..3 {
        if attempt > 0 {
            let delay = std::time::Duration::from_secs(2u64.pow(attempt));
            tokio::time::sleep(delay).await;
        }

        if cancel.load(Ordering::SeqCst) {
            let _ = tokio::fs::remove_file(&dest_path).await;
            return Err("Download cancelled".to_string());
        }

        match try_download(&client, &url, &dest_path, filename, &on_progress, &cancel).await {
            Ok(_) => {
                // Validate the downloaded file
                if is_valid_model(&dest_path) {
                    return Ok(dest_path);
                } else {
                    last_error = "Downloaded file failed validation".to_string();
                    let _ = tokio::fs::remove_file(&dest_path).await;
                }
            }
            Err(e) => {
                last_error = format!("Attempt {}: {}", attempt + 1, e);
                let _ = tokio::fs::remove_file(&dest_path).await;
            }
        }
    }

    Err(format!(
        "Download failed after 3 attempts. Last error: {}",
        last_error
    ))
}

async fn try_download(
    client: &reqwest::Client,
    url: &str,
    dest: &Path,
    filename: &str,
    on_progress: &impl Fn(DownloadProgress),
    cancel: &AtomicBool,
) -> Result<(), String> {
    let response = client.get(url).send().await.map_err(|e| e.to_string())?;

    if !response.status().is_success() {
        return Err(format!("HTTP {}", response.status()));
    }

    let total = response.content_length().unwrap_or(0);
    let mut downloaded: u64 = 0;
    let mut file = tokio::fs::File::create(dest)
        .await
        .map_err(|e| e.to_string())?;
    let mut stream = response.bytes_stream();

    while let Some(chunk) = stream.next().await {
        if cancel.load(Ordering::SeqCst) {
            drop(file);
            let _ = tokio::fs::remove_file(dest).await;
            return Err("Download cancelled".to_string());
        }
        let chunk = chunk.map_err(|e| e.to_string())?;
        file.write_all(&chunk).await.map_err(|e| e.to_string())?;
        downloaded += chunk.len() as u64;

        on_progress(DownloadProgress {
            filename: filename.to_string(),
            bytes_downloaded: downloaded,
            total_bytes: total,
            percent: if total > 0 {
                (downloaded as f32 / total as f32) * 100.0
            } else {
                0.0
            },
        });
    }

    file.flush().await.map_err(|e| e.to_string())?;
    // sync_all guarantees the OS flushes kernel buffers to the storage device
    // before we return. Without this, is_valid_model's synchronous File::open
    // may read stale/empty data on macOS, causing a spurious validation failure.
    file.sync_all().await.map_err(|e| e.to_string())?;
    Ok(())
}

/// Check if a model file exists and appears valid.
///
/// For Whisper models: validates size > 1MB and checks first 4 bytes against all
/// known GGML/GGUF magic sequences. Logs the actual bytes on failure to aid diagnosis.
///
/// For Silero VAD: the binary format has no GGML header — validated by size only (> 50KB).
pub fn is_valid_model(path: &Path) -> bool {
    if !path.exists() {
        return false;
    }
    let meta = match std::fs::metadata(path) {
        Ok(m) => m,
        Err(e) => {
            eprintln!(
                "[downloader] is_valid_model: metadata error for {:?}: {}",
                path, e
            );
            return false;
        }
    };

    let is_vad = path.to_string_lossy().contains("silero");

    if is_vad {
        // Silero VAD is an ONNX-derived binary — no GGML header, validate by size only.
        let valid = meta.len() > 50_000;
        if !valid {
            eprintln!(
                "[downloader] is_valid_model: VAD file too small — got {} bytes, need > 50000",
                meta.len()
            );
        }
        return valid;
    }

    // Whisper models must be at least 1MB
    if meta.len() < 1_000_000 {
        eprintln!(
            "[downloader] is_valid_model: file too small — got {} bytes, need > 1000000",
            meta.len()
        );
        return false;
    }

    // Read and check magic bytes
    let mut file = match std::fs::File::open(path) {
        Ok(f) => f,
        Err(e) => {
            eprintln!(
                "[downloader] is_valid_model: open error for {:?}: {}",
                path, e
            );
            return false;
        }
    };
    let mut magic = [0u8; 4];
    use std::io::Read;
    if let Err(e) = file.read_exact(&mut magic) {
        eprintln!(
            "[downloader] is_valid_model: read error for {:?}: {}",
            path, e
        );
        return false;
    }

    // Accept any known whisper.cpp magic — exact 4-byte matches or "gg" prefix
    let is_valid_magic =
        VALID_WHISPER_MAGIC.iter().any(|m| &magic == m) || magic[0..2] == [0x67, 0x67];

    if !is_valid_magic {
        eprintln!(
            "[downloader] is_valid_model: unrecognized magic bytes for {:?}: [{:#04x}, {:#04x}, {:#04x}, {:#04x}]",
            path, magic[0], magic[1], magic[2], magic[3]
        );
    }

    is_valid_magic
}

/// List all downloaded model files in the models directory.
pub fn list_downloaded_models(models_dir: &Path) -> Vec<String> {
    if !models_dir.exists() {
        return Vec::new();
    }
    std::fs::read_dir(models_dir)
        .map(|entries| {
            entries
                .filter_map(|e| e.ok())
                .filter(|e| {
                    e.path()
                        .extension()
                        .map(|ext| ext == "bin")
                        .unwrap_or(false)
                })
                .filter(|e| is_valid_model(&e.path()))
                .filter_map(|e| e.file_name().into_string().ok())
                .collect()
        })
        .unwrap_or_default()
}
