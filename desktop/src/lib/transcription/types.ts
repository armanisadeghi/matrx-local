export type WhisperModelTier = "Low" | "Default" | "High";

export interface HardwareInfo {
  total_ram_mb: number;
  cpu_threads: number;
  gpu_vram_mb: number | null;
  supports_cuda: boolean;
  supports_metal: boolean;
  is_apple_silicon: boolean;
}

export interface ModelInfo {
  tier: WhisperModelTier;
  filename: string;
  download_size_mb: number;
  ram_required_mb: number;
  relative_speed: string;
  accuracy: string;
  description: string;
}

export interface HardwareDetectionResult {
  hardware: HardwareInfo;
  recommended_tier: WhisperModelTier;
  recommended_filename: string;
  recommended_size_mb: number;
  reason: string;
  can_upgrade: boolean;
  all_models: ModelInfo[];
  /**
   * The compiled Rust model consts as originally returned by
   * `detect_hardware`, preserved by `overlayWhisperCatalog` when it replaces
   * `all_models` with the remote catalog. Recommendation lookups
   * (`findWhisperModelInfo`) fall back here when the Rust-recommended
   * filename is missing from the remote-overlaid list. Absent when the
   * result never went through the overlay.
   */
  compiled_all_models?: ModelInfo[];
}

export interface WhisperSegment {
  text: string;
  start_sec: number;
  end_sec: number;
}

export interface DownloadProgress {
  filename: string;
  bytes_downloaded: number;
  total_bytes: number;
  percent: number;
}

export interface VoiceSetupStatus {
  setup_complete: boolean;
  selected_model: string | null;
  downloaded_models: string[];
}

export interface AudioDeviceInfo {
  name: string;
  is_default: boolean;
  sample_rates: number[];
  channels: number[];
}

// ── Wake Word ─────────────────────────────────────────────────────────────────

/** Operational mode of the wake-word subsystem. */
export type WakeWordMode = "listening" | "muted" | "dismissed";

/** Which backend engine handles wake word detection. */
export type WakeWordEngine = "whisper" | "oww";

/** Payload of the "wake-word-detected" event. */
export interface WakeWordDetectedEvent {
  /** The keyword string returned by the KWS model, or "MANUAL" for manual trigger. */
  keyword: string;
  /** Confidence score 0–1 (OWW engine); always 1.0 for whisper engine and MANUAL. */
  score?: number;
}

/** Persisted user preferences for the wake word system (stored in SQLite). */
export interface WakeWordSettings {
  engine: WakeWordEngine;
  owwModel: string;
  owwThreshold: number;
  customKeyword: string;
}

/** Status of the wake word system. */
export interface WakeWordStatus {
  mode: WakeWordMode;
  isRunning: boolean;
  kmsModelReady: boolean;
}

/** An OWW model entry from GET /wake-word/models. */
export interface OwwModelInfo {
  name: string;
  filename: string;
  downloaded: boolean;
  size_mb: number;
  description: string;
  is_built_in: boolean;
  is_custom: boolean;
}

/** Response from GET /wake-word/models. */
export interface OwwModelsResponse {
  models: OwwModelInfo[];
}

/** Response from GET /wake-word/status. */
export interface OwwStatus {
  running: boolean;
  mode: WakeWordMode;
  model_name: string;
  threshold: number;
}

/** Progress event from /wake-word/models/download-stream. */
export interface OwwDownloadProgress {
  bytes_done: number;
  total_bytes: number;
  percent: number;
}

/** A persisted transcription recording session. */
export interface TranscriptionSession {
  id: string;
  /** User-supplied title, or null if untitled */
  title: string | null;
  /** ISO timestamp of when recording started */
  createdAt: string;
  /** ISO timestamp of last update (segments appended, title changed, etc.) */
  updatedAt: string;
  /** Duration in seconds (populated when recording stops) */
  durationSecs: number;
  /** Total characters in the full transcript */
  charCount: number;
  /** The whisper model used */
  modelUsed: string | null;
  /** Audio device used (null = system default) */
  deviceUsed: string | null;
  /** All transcribed segments */
  segments: WhisperSegment[];
  /** Current (possibly AI-polished) transcript text */
  fullText: string;

  // ── AI-polish fields (optional — absent on sessions that haven't been processed) ──

  /** Original raw transcript, preserved when AI polishing is applied. */
  rawText?: string;
  /** AI-generated title (distinct from user-supplied title). Applied to title on success. */
  aiTitle?: string | null;
  /** AI-generated one-sentence description / summary of the recording. */
  aiDescription?: string | null;
  /** AI-generated topic tags (2–5 short strings). */
  aiTags?: string[];
  /** ISO timestamp of the last successful AI-polish run. */
  aiProcessedAt?: string | null;
}
