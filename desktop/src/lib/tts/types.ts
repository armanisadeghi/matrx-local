export interface TtsVoice {
  voice_id: string;
  name: string;
  gender: "female" | "male";
  language: string;
  lang_code: string;
  quality_grade: string;
  traits: string[];
  is_custom: boolean;
  is_default: boolean;
  blend_recipe?: Array<{ voice_id: string; weight: number }>;
}

export interface TtsStatus {
  model_downloaded: boolean;
  model_loaded: boolean;
  is_downloading: boolean;
  download_progress: number;
  model_dir: string;
  voice_count: number;
}

export interface SynthesizeRequest {
  text: string;
  voice_id?: string;
  speed?: number;
  lang?: string;
  /**
   * Character count above which the server switches to streaming synthesis.
   * Mirrors the user's `ttsStreamingThreshold` configuration (0 = always
   * stream). Omitted → the server falls back to its own default.
   */
  streaming_threshold?: number;
}

/**
 * Backend 409 contract for a synth/stream call when the TTS model is not on
 * disk yet. Matches `{"detail","needs_download","downloading"}` emitted by the
 * engine's TTS routes. `downloading` is true while a download is in flight.
 */
export interface TtsModelNotDownloadedPayload {
  detail: string;
  needs_download: boolean;
  downloading: boolean;
}

export interface DownloadResponse {
  success: boolean;
  already_downloaded?: boolean;
  error?: string;
  error_code?: string;
}

/**
 * v2 streaming protocol — matches app/services/tts/models.py constants.
 * Each frame on the wire is: 1B tag · 4B BE uint32 length · N bytes payload.
 */
export const STREAM_PROTOCOL_VERSION = 2;
export const STREAM_TAG_CHUNK = 0x01;
export const STREAM_TAG_END = 0x02;
export const STREAM_TAG_ERROR = 0xff;

export interface TtsStreamErrorPayload {
  code: string;
  message: string;
}

export type TtsLanguageGroup = {
  language: string;
  lang_code: string;
  voices: TtsVoice[];
};
