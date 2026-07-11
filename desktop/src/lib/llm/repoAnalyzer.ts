/**
 * Model Repository Analyzer — types and API client.
 *
 * Mirrors the Python response models in app/api/model_repo_routes.py.
 * Call analyzeModelRepo() to get a hardware-scored file list for any
 * HuggingFace repo URL (or direct .gguf link).
 */

import type { LlmHardwareResult } from "./types";
import { engine } from "@/lib/api";

// ── Request / response types ───────────────────────────────────────────────

export type CompatibilityStatus =
  | "works"
  | "needs_more_ram"
  | "accessory_only"
  | "incompatible_format";

export type FileFormat = "gguf" | "safetensors" | "bin" | "onnx" | "other";
export type FileRole = "main_model" | "mmproj" | "adapter" | "tokenizer" | "config" | "other";

export interface HardwarePayload {
  total_ram_mb: number;
  gpu_vram_mb: number | null;
  supports_cuda: boolean;
  supports_metal: boolean;
  is_apple_silicon: boolean;
}

export interface ModelFileEntry {
  filename: string;
  format: FileFormat;
  role: FileRole;
  quant: string | null;
  is_split: boolean;
  split_group: string | null;
  part_index: number | null;
  total_parts: number | null;
  size_bytes: number;
  total_size_bytes: number;
  ram_required_gb: number;
  compatibility_status: CompatibilityStatus;
  compatibility_reason: string;
  download_urls: string[];
  recommended: boolean;
}

export interface RepoAnalysisResult {
  provider: string;
  repo_id: string;
  repo_url: string;
  author: string | null;
  model_name: string | null;
  architecture: string | null;
  total_files: number;
  hardware_label: string | null;
  effective_capacity_gb: number | null;
  files: ModelFileEntry[];
}

export interface AnalyzeRequest {
  url: string;
  hardware: HardwarePayload | null;
}

// ── Helper to extract hardware payload from LlmHardwareResult ─────────────

export function hardwarePayload(hw: LlmHardwareResult): HardwarePayload {
  return {
    total_ram_mb: hw.hardware.total_ram_mb,
    gpu_vram_mb: hw.hardware.gpu_vram_mb,
    supports_cuda: hw.hardware.supports_cuda,
    supports_metal: hw.hardware.supports_metal,
    is_apple_silicon: hw.hardware.is_apple_silicon,
  };
}

// ── Quant descriptions for tooltips ───────────────────────────────────────

export const QUANT_DESCRIPTIONS: Record<string, string> = {
  Q4_K_M: "Best balance of quality and size — recommended for most users",
  Q4_K_S: "Slightly smaller than Q4_K_M with minor quality trade-off",
  Q8_0: "Near-lossless quality, uses about 2x more RAM than Q4_K_M",
  BF16: "Full precision (bfloat16) — enormous, only for servers with 64+ GB VRAM",
  F16: "Full precision (float16) — very large",
  F32: "Maximum precision — impractically large for inference",
  IQ4_XS: "Good quality, slightly smaller than Q4_K_M",
  IQ4_NL: "Similar to IQ4_XS",
  Q5_K_M: "High quality, between Q4 and Q8 in size",
  Q5_K_S: "Similar to Q5_K_M, slightly smaller",
  Q6_K: "Very high quality, close to Q8",
  Q3_K_M: "Moderate compression with some quality loss — for smaller machines",
  Q3_K_S: "More aggressive than Q3_K_M",
  Q3_K_L: "Less aggressive than Q3_K_M",
  IQ3_M: "Aggressive compression, similar quality to Q3_K_M",
  IQ3_XS: "Very aggressive compression",
  Q2_K: "Maximum compression, significant quality loss — last resort",
  IQ2_M: "Very aggressive compression, similar to Q2_K",
  IQ2_XS: "Extreme compression, noticeable quality degradation",
  IQ1_M: "Extreme compression — severely degraded quality",
  IQ1_S: "Extreme compression — severely degraded quality",
};

// ── API client function ────────────────────────────────────────────────────

/**
 * Analyze a model repository URL.
 *
 * Uses the shared engine singleton; requires the engine to be discovered first.
 *
 * @param url      - The repo URL to analyze (e.g. "https://huggingface.co/owner/repo")
 * @param hardware - Optional hardware info from LlmContext; enables personalized scoring
 */
export async function analyzeModelRepo(
  url: string,
  hardware: HardwarePayload | null,
  // Optional abort signal — the analyzer UI passes one wired to a 45s timeout
  // and a Cancel button so a hung engine fetch can't spin forever.
  signal?: AbortSignal
): Promise<RepoAnalysisResult> {
  const body: AnalyzeRequest = { url, hardware };
  return engine.post("/model-repo/analyze", body, {
    ...(signal !== undefined ? { signal } : {}),
  }) as Promise<RepoAnalysisResult>;
}

// ── UI helpers ─────────────────────────────────────────────────────────────

/** Format bytes to a human-readable GB/MB string. */
export function formatRepoBytes(bytes: number): string {
  if (bytes === 0) return "Unknown size";
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  const mb = bytes / 1024 ** 2;
  return `${mb.toFixed(0)} MB`;
}

/** Badge color class for a compatibility status. */
export function statusColor(status: CompatibilityStatus): string {
  switch (status) {
    case "works":
      return "text-green-600 bg-green-500/10 border-green-500/30";
    case "needs_more_ram":
      return "text-yellow-600 bg-yellow-500/10 border-yellow-500/30";
    case "accessory_only":
      return "text-blue-500 bg-blue-500/10 border-blue-500/20";
    case "incompatible_format":
      return "text-muted-foreground bg-muted/30 border-border";
  }
}

/** Short label for a compatibility status. */
export function statusLabel(status: CompatibilityStatus): string {
  switch (status) {
    case "works":
      return "Ready";
    case "needs_more_ram":
      return "Needs More RAM";
    case "accessory_only":
      return "Accessory";
    case "incompatible_format":
      return "Wrong Format";
  }
}
