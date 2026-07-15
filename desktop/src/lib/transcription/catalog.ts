/**
 * Whisper hardware detection + remote catalog overlay.
 *
 * Same pattern as lib/llm/catalog.ts: `detect_hardware` (Tauri/Rust) owns
 * hardware probing + the recommendation; the browsable model list is
 * overlaid with the resolved remote catalog (kind `whisper_model`, engine
 * `GET /catalogs/whisper_model`) so a new Whisper tier row in the DB
 * reaches the selector UI without a Tauri rebuild. Engine fetch failure →
 * compiled Rust consts stay.
 *
 * The catalog also carries the VAD companion file as `role: "vad"` — the
 * selector list only shows `role: "transcription"` entries, mirroring the
 * Rust `MODELS` const.
 */

import { fetchCatalog } from "@/lib/catalogs";
import type {
  HardwareDetectionResult,
  ModelInfo,
} from "@/lib/transcription/types";

type WhisperModelPayload = ModelInfo & {
  download_url: string;
  role: "transcription" | "vad";
};

/**
 * Fetch the resolved Whisper model catalog (transcription tiers only).
 * Throws on any failure — callers keep the Rust consts as fallback.
 */
export async function fetchWhisperModelCatalog(): Promise<ModelInfo[]> {
  const entries = await fetchCatalog<WhisperModelPayload>("whisper_model");
  const models = entries
    .filter((e) => e.payload.role === "transcription")
    .map((e) => {
      const { download_url: _url, role: _role, ...model } = e.payload;
      return model as ModelInfo;
    });
  if (models.length === 0) throw new Error("whisper_model catalog is empty");
  return models;
}

/**
 * Overlay a Tauri `detect_hardware` result with the remote catalog.
 * Hardware + recommendation stay Rust-owned; only the browsable model list
 * is replaced. Falls back to the compiled `all_models` when the engine
 * fetch fails (logged, never thrown).
 */
export async function overlayWhisperCatalog(
  result: HardwareDetectionResult,
): Promise<HardwareDetectionResult> {
  try {
    const models = await fetchWhisperModelCatalog();
    return { ...result, all_models: models };
  } catch (e) {
    console.warn(
      "[catalogs] whisper_model overlay unavailable — using compiled Rust catalog:",
      e,
    );
    return result;
  }
}
