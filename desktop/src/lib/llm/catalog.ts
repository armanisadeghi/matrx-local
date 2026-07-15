/**
 * LLM hardware detection + remote catalog overlay.
 *
 * `detect_llm_hardware` (Tauri/Rust) owns HARDWARE probing and the
 * recommendation, and returns the compiled Rust model consts as
 * `all_models`. This module overlays `all_models` with the resolved
 * remote catalog (kind `llm_model`, served by the engine at
 * `GET /catalogs/llm_model`) so a new model row in the DB reaches the
 * selector UI without a Tauri rebuild. When the engine fetch fails the
 * compiled Rust consts stay — same data, older snapshot.
 *
 * Payloads mirror the Rust `LlmModelInfo` struct verbatim (snake_case);
 * the Rust-derived conveniences (`is_split`, `all_part_urls`) are computed
 * here exactly like model_selector.rs does.
 */

import { fetchCatalog } from "@/lib/catalogs";
import type {
  LlmHardwareResult,
  LlmModelInfo,
  LlmModelVariant,
} from "@/lib/llm/types";

type VariantPayload = Omit<LlmModelVariant, "is_split" | "all_part_urls">;
type LlmModelPayload = Omit<
  LlmModelInfo,
  "is_split" | "all_part_urls" | "variants"
> & {
  variants: VariantPayload[];
};

function withSplitDerived<T extends { hf_url: string; hf_parts: string[] }>(
  m: T,
): T & { is_split: boolean; all_part_urls: string[] } {
  const parts = Array.isArray(m.hf_parts) ? m.hf_parts : [];
  return {
    ...m,
    hf_parts: parts,
    is_split: parts.length > 0,
    // Mirrors Rust all_part_urls(): [hf_url, ...hf_parts]
    all_part_urls: [m.hf_url, ...parts],
  };
}

function payloadToModel(p: LlmModelPayload): LlmModelInfo {
  const variants = Array.isArray(p.variants)
    ? p.variants.map((v) => withSplitDerived(v))
    : [];
  return { ...withSplitDerived(p), variants };
}

/**
 * Fetch the resolved LLM catalog from the engine, mapped to `LlmModelInfo`.
 * Throws on any failure — callers keep the Rust consts as fallback.
 */
export async function fetchLlmModelCatalog(): Promise<LlmModelInfo[]> {
  const entries = await fetchCatalog<LlmModelPayload>("llm_model");
  const models = entries.map((e) => payloadToModel(e.payload));
  if (models.length === 0) throw new Error("llm_model catalog is empty");
  return models;
}

/**
 * Overlay a Tauri `detect_llm_hardware` result with the remote catalog.
 * Hardware + recommendation stay Rust-owned; only the browsable model list
 * is replaced. Falls back to the compiled `all_models` when the engine
 * fetch fails (logged, never thrown).
 */
export async function overlayLlmCatalog(
  result: LlmHardwareResult,
): Promise<LlmHardwareResult> {
  try {
    const models = await fetchLlmModelCatalog();
    return { ...result, all_models: models };
  } catch (e) {
    console.warn(
      "[catalogs] llm_model overlay unavailable — using compiled Rust catalog:",
      e,
    );
    return result;
  }
}
