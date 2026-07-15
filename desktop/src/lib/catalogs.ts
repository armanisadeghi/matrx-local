/**
 * Remote Catalogs client — resolved catalog entries from the Python engine.
 *
 * The engine owns the Remote Catalogs consumer (fetch from Supabase/aidream,
 * disk cache, compiled fallback, 6h refresh — app/services/catalogs/ in the
 * Python tree) and serves the RESOLVED view over `GET /catalogs/{kind}`.
 * The webview reads through this module so a new model/prompt/provider row
 * in the DB shows up in the desktop UI without a Tauri rebuild.
 *
 * Callers keep their own compiled fallback (Rust consts via Tauri commands,
 * TS constants) for when the engine is unreachable — a fetch failure here
 * throws and the caller falls back.
 *
 * System-of-record:
 * /Users/armanisadeghi/code/common-docs/remote-catalogs/FEATURE.md
 */

import { engine } from "@/lib/api";

export type CatalogKind =
  | "llm_model"
  | "whisper_model"
  | "image_gen_model"
  | "video_gen_model"
  | "lora"
  | "workflow_preset"
  | "system_prompt"
  | "tts_voice"
  | "tts_language"
  | "tts_model_file"
  | "ner_model"
  | "ner_pii_labels"
  | "wake_word_model"
  | "api_key_provider";

export interface CatalogEntry<P = Record<string, unknown>> {
  kind: CatalogKind;
  key: string;
  schema_version: number;
  payload: P;
  artifact_url: string | null;
  artifact_sha256: string | null;
  artifact_size_bytes: number | null;
  min_app_version: string | null;
  is_active: boolean;
  sort_order: number;
  notes: string | null;
  updated_at: string | null;
}

export interface CatalogResponse<P = Record<string, unknown>> {
  kind: CatalogKind;
  /** Provenance tier the engine resolved from: remote | cache | defaults. */
  tier: string;
  fetched_at: string | null;
  entries: CatalogEntry<P>[];
}

/**
 * Fetch the resolved entries for one catalog kind from the engine.
 *
 * Throws when the engine is unreachable, the kind 404s, or the response is
 * malformed — callers treat any throw as "use the compiled fallback".
 */
export async function fetchCatalog<P = Record<string, unknown>>(
  kind: CatalogKind,
): Promise<CatalogEntry<P>[]> {
  const base = engine.engineUrl;
  if (!base) throw new Error("engine not connected");
  const resp = await fetch(`${base}/catalogs/${kind}`);
  if (!resp.ok) throw new Error(`GET /catalogs/${kind} → ${resp.status}`);
  const body = (await resp.json()) as CatalogResponse<P>;
  if (!Array.isArray(body.entries)) {
    throw new Error(`GET /catalogs/${kind}: malformed response (no entries)`);
  }
  return body.entries;
}
