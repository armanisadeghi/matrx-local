// ── LLM Model Types ───────────────────────────────────────────────────────

export type LlmTier =
  // Tiny / Edge
  | "LowAlt" // Phi-4-mini 2.5 GB
  | "Qwen35_2B" // Qwen3.5-2B 1.3 GB (+ mmproj)
  | "Phi4MiniReasoning" // Phi-4-mini-reasoning 2.5 GB
  | "Low" // Qwen3-4B 2.5 GB (legacy)
  | "Qwen35_4B" // Qwen3.5-4B 2.7 GB (+ mmproj; preferred tiny)
  | "Gemma3nE2B" // Gemma-3n-E2B 2.8 GB
  | "UltraLow" // Gemma-3n-E4B 4.5 GB
  | "Low2" // DeepSeek-R1-0528-Qwen3-8B 5.0 GB
  | "Low3" // Llama-3.1-8B 4.9 GB
  // Mid-range
  | "Default" // Qwen3-8B 5.1 GB (legacy)
  | "Qwen35_9B" // Qwen3.5-9B 5.7 GB (+ mmproj; preferred default)
  | "GlmZ1_9B" // GLM-Z1-9B 6.2 GB
  | "Gemma4E2B" // Gemma-4-E2B 3.1 GB (text+image; audio pending llama.cpp)
  | "Gemma4E4B" // Gemma-4-E4B 5.0 GB (text+image; audio pending llama.cpp)
  | "Mid" // Gemma-3-12B 7.3 GB (legacy)
  | "Gemma4_12B" // Gemma-4-12B Unified 7.1 GB (+ mmproj)
  | "Mid2" // Phi-4-reasoning-plus 9.1 GB
  | "High" // GPT-OSS-20B 12.1 GB
  | "HighAlt" // Mistral-Small-3.1-24B 14.4 GB
  | "Devstral24B" // Devstral Small 2 24B 14.3 GB (coding)
  | "Coder30B" // Qwen3-Coder-30B-A3B 18.6 GB (coding MoE)
  | "Glm47Flash" // GLM-4.7-Flash 18.3 GB (agent MoE)
  | "Gemma4A4B" // Gemma-4-26B-A4B 17.0 GB (MoE, text+image)
  | "High2" // Qwen3.5-27B (multi-variant)
  | "Gemma4_31B" // Gemma-4-31B 18.3 GB (dense, text+image)
  | "High3" // DeepSeek-R1-Distill-32B 19.85 GB
  | "High4" // Gemma-3-27B 16.55 GB (legacy)
  | "VHigh" // Qwen3.5-35B-A3B (multi-variant)
  // Uncensored
  | "UncensoredCompact"
  | "UncensoredBalanced"
  // Server-grade
  | "Server" // Llama-3.3-70B 42.5 GB
  | "Server2" // Qwen3.5-122B-A10B 39.1 GB
  | "Server3" // Mistral-Small-4-119B 72.6 GB
  | "Server4" // Llama-4-Scout-17B-16E 67.5 GB
  | "Server5" // GPT-OSS-120B 88 GB
  | "Server6" // Qwen3.5-397B-A17B 115 GB
  | "Server7" // Qwen3-Next-80B-A3B 48.7 GB
  | "Server8"; // Qwen3-Coder-Next 48.7 GB

/** A single quantization variant for a model that ships in multiple sizes. */
export interface LlmModelVariant {
  /** Human-readable label: "Compact", "Balanced", "Quality". */
  label: string;
  /** Technical quant name shown in tooltip: "IQ3_XXS", "Q4_K_M", etc. */
  quant: string;
  filename: string;
  disk_size_gb: number;
  ram_required_gb: number;
  hf_url: string;
  hf_parts: string[];
  is_split: boolean;
  all_part_urls: string[];
  expected_size_bytes: number;
  /** Multimodal projector filename (empty = no vision). */
  mmproj_filename: string;
  /** Download URL for the mmproj file (empty = no vision). */
  mmproj_url: string;
  /** Expected byte size of the mmproj file (0 = no mmproj). */
  mmproj_expected_size_bytes: number;
}

export interface LlmModelInfo {
  tier: LlmTier;
  name: string;
  /** Provider / organization: "Alibaba", "OpenAI", "Meta", etc. */
  provider: string;
  filename: string;
  disk_size_gb: number;
  ram_required_gb: number;
  // ── Ratings (0–5) ────────────────────────────────────────────────────────
  text_rating: number;
  code_rating: number;
  vision_rating: number;
  tool_calling_rating: number;
  // ── Metadata ──────────────────────────────────────────────────────────────
  speed: string;
  description: string;
  /** Training data knowledge cutoff, e.g. "Feb 2026". */
  knowledge_cutoff: string;
  /** Link to the HuggingFace model card page. */
  hf_model_card_url: string;
  is_uncensored: boolean;
  is_server_grade: boolean;
  // ── Download URLs ─────────────────────────────────────────────────────────
  hf_url: string;
  /** Additional part URLs for split models (empty for single-file models). */
  hf_parts: string[];
  /** True when the model is distributed as multiple split files. */
  is_split: boolean;
  /** All part URLs in download order (hf_url + hf_parts). */
  all_part_urls: string[];
  context_length: number;
  /** Expected size in bytes. Used to detect partial downloads. */
  expected_size_bytes: number;
  // ── Multimodal projector ──────────────────────────────────────────────────
  /** Multimodal projector filename (empty = no vision). */
  mmproj_filename: string;
  /** Download URL for the mmproj file (empty = no vision). */
  mmproj_url: string;
  /** Expected byte size of the mmproj file (0 = no mmproj). */
  mmproj_expected_size_bytes: number;
  // ── Quant variants ────────────────────────────────────────────────────────
  /** Non-empty for models offered in multiple quantization sizes. */
  variants: LlmModelVariant[];
}

export interface LlmHardwareResult {
  hardware: {
    total_ram_mb: number;
    cpu_threads: number;
    gpu_vram_mb: number | null;
    supports_cuda: boolean;
    supports_vulkan: boolean;
    supports_metal: boolean;
    is_apple_silicon: boolean;
    gpu_name: string | null;
  };
  recommended_tier: LlmTier;
  recommended_filename: string;
  recommended_name: string;
  recommended_size_gb: number;
  recommended_gpu_layers: number;
  reason: string;
  can_upgrade: boolean;
  all_models: LlmModelInfo[];
}

// ── Server Status ─────────────────────────────────────────────────────────

export interface LlmServerStatus {
  running: boolean;
  port: number;
  model_path: string;
  model_name: string;
  gpu_layers: number;
  context_length: number;
  last_error_output?: string;
}

// ── Setup Status ──────────────────────────────────────────────────────────

export interface LlmSetupStatus {
  setup_complete: boolean;
  selected_model: string | null;
  server_running: boolean;
  server_port: number;
  server_model: string;
  downloaded_models: string[];
}

// ── Download Progress ─────────────────────────────────────────────────────

export interface LlmDownloadProgress {
  filename: string;
  /** Current part index (1-based). */
  part: number;
  /** Total number of parts. */
  total_parts: number;
  /** Bytes downloaded for the current part. */
  part_bytes_downloaded: number;
  /** Total bytes for the current part (0 if unknown). */
  part_total_bytes: number;
  /** Total bytes downloaded across all parts. */
  bytes_downloaded: number;
  /** Grand total bytes across all parts (0 if unknown). */
  total_bytes: number;
  /** Overall download percentage (0–100). */
  percent: number;
  /** Lifecycle status hint from the backend. */
  status?: "downloading" | "already_complete";
}

export interface LlmDownloadCancelledEvent {
  reason: "user_cancelled" | "stalled" | string;
}

// ── Downloaded Model Info ─────────────────────────────────────────────────

export interface DownloadedLlmModel {
  filename: string;
  size_bytes: number;
  size_gb: string;
  name: string;
  tier: LlmTier | null;
  is_custom: boolean;
  is_split: boolean;
  all_parts_present: boolean;
  total_parts: number;
}

// ── Chat / Inference Types ────────────────────────────────────────────────

/** A text-only content part in an OpenAI-compatible multimodal message. */
export interface ChatContentText {
  type: "text";
  text: string;
}

/** An image content part — base64 data URI or remote URL. */
export interface ChatContentImageUrl {
  type: "image_url";
  image_url: { url: string };
}

/**
 * Content can be a plain string (text-only) or an array of multimodal parts
 * (text + images). llama-server supports both formats via the OpenAI-compatible API.
 */
export type ChatContent = string | (ChatContentText | ChatContentImageUrl)[];

export interface ChatMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: ChatContent | null;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
}

export interface ToolCall {
  id: string;
  type: "function";
  function: {
    name: string;
    arguments: string;
  };
}

export interface ToolDefinition {
  type: "function";
  function: {
    name: string;
    description: string;
    parameters: {
      type: "object";
      properties: Record<string, { type: string; description: string }>;
      required: string[];
    };
  };
}

export interface ChatCompletionResponse {
  choices: Array<{
    message: {
      role: string;
      content: string | null;
      tool_calls?: ToolCall[];
    };
    finish_reason: string;
  }>;
}

// ── Rating helpers ────────────────────────────────────────────────────────

export const RATING_SCALE: Record<number, string> = {
  0: "Not recommended for this task",
  1: "Limited — compromised by size or quantization",
  2: "Good enough for basic use",
  3: "Great — among the best running locally",
  4: "Excellent — top open-source for this task",
  5: "Near-frontier — best open-source available",
};

export const SERVER_GRADE_TIERS = new Set<LlmTier>([
  "Server",
  "Server2",
  "Server3",
  "Server4",
  "Server5",
  "Server6",
  "Server7",
  "Server8",
]);

export const UNCENSORED_TIERS = new Set<LlmTier>([
  "UncensoredCompact",
  "UncensoredBalanced",
]);

export function isServerGrade(tier: LlmTier): boolean {
  return SERVER_GRADE_TIERS.has(tier);
}

export function isUncensored(tier: LlmTier): boolean {
  return UNCENSORED_TIERS.has(tier);
}
