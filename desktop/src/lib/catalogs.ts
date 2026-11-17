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
import { getCatalogKind } from "@ai-matrx/api/apps";
import { EngineHttpError } from "@/lib/engine-errors";

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

/** How long a catalog read waits for engine discovery before using the compiled fallback. */
export const CATALOG_ENGINE_WAIT_MS = 5000;
const CATALOG_ENGINE_POLL_MS = 250;

/**
 * The engine's URL for a catalog read. At launch the catalog overlays (LLM, Whisper) run in the same
 * tick as the first engine discovery, so the URL was still null and every launch filed "engine not
 * connected" warnings while the engine was coming up (2026-11-16). Discovery is healed here (the same
 * self-healing `rediscover` other engine calls use, which is rate-limited and single-flight) and the
 * read waits, bounded, for the URL; only an engine still missing after the wait falls back.
 */
async function engineBaseForCatalogs(): Promise<string | null> {
  const deadline = Date.now() + CATALOG_ENGINE_WAIT_MS;
  while (!engine.engineUrl && Date.now() < deadline) {
    await engine.rediscover().catch(() => null);
    if (engine.engineUrl) break;
    await new Promise((r) => setTimeout(r, CATALOG_ENGINE_POLL_MS));
  }
  return engine.engineUrl;
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
  const base = await engineBaseForCatalogs();
  if (!base) throw new Error("engine not connected");
  // 5s cap: a hung engine must read as "unreachable" (throw → compiled
  // fallback), never stall SetupWizard or a selector behind a dead socket.
  let body: CatalogResponse<P>;
  try {
    body = (await getCatalogKind(
      engine.api,
      { path: { kind } },
      { signal: AbortSignal.timeout(5000) },
    )) as unknown as CatalogResponse<P>;
  } catch (e) {
    if (e instanceof EngineHttpError) throw new Error(`GET /catalogs/${kind} → ${e.status}`);
    throw e;
  }
  if (!Array.isArray(body.entries)) {
    throw new Error(`GET /catalogs/${kind}: malformed response (no entries)`);
  }
  return body.entries;
}
