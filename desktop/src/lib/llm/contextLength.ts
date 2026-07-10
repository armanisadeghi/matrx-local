import { loadSettings } from "@/lib/settings";

/** Preset context sizes offered in UI dropdowns (tokens). */
export const CONTEXT_LENGTH_OPTIONS = [
  2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144,
] as const;

export type ContextLengthOption = (typeof CONTEXT_LENGTH_OPTIONS)[number];

const OVERRIDES_KEY = "custom-model-context-lengths";
const FALLBACK_CONTEXT = 8192;

function readOverrides(): Record<string, number> {
  try {
    const raw = localStorage.getItem(OVERRIDES_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const out: Record<string, number> = {};
    for (const [k, v] of Object.entries(parsed)) {
      const n = typeof v === "number" ? v : Number(v);
      if (Number.isFinite(n) && n > 0) out[k] = Math.floor(n);
    }
    return out;
  } catch {
    return {};
  }
}

function writeOverrides(map: Record<string, number>): void {
  try {
    localStorage.setItem(OVERRIDES_KEY, JSON.stringify(map));
  } catch {
    // storage full — ignore
  }
}

/** Per-model context override, or null if unset. */
export function getModelContextOverride(filename: string): number | null {
  const n = readOverrides()[filename];
  return n != null && n > 0 ? n : null;
}

/** Persist a per-model context override (used for custom + catalog models). */
export function setModelContextOverride(
  filename: string,
  contextLength: number,
): void {
  if (!filename || !Number.isFinite(contextLength) || contextLength <= 0)
    return;
  const map = readOverrides();
  map[filename] = Math.floor(contextLength);
  writeOverrides(map);
}

/** Remove a per-model override so settings/catalog defaults apply again. */
export function clearModelContextOverride(filename: string): void {
  const map = readOverrides();
  if (!(filename in map)) return;
  delete map[filename];
  writeOverrides(map);
}

export interface ResolveContextOptions {
  /** Value the caller wants for this start (UI dropdown, catalog field, etc.). */
  explicit?: number | null;
  /** Catalog model.context_length when known. */
  catalogContext?: number | null;
}

/**
 * Resolve context length for starting llama-server.
 *
 * Priority: per-model override → explicit caller value → catalog → Settings → 8192.
 */
export async function resolveContextLength(
  filename: string,
  options?: ResolveContextOptions | number | null,
): Promise<number> {
  // Back-compat: second arg used to be catalogContext number
  const opts: ResolveContextOptions =
    typeof options === "number" || options == null
      ? { catalogContext: options ?? null }
      : options;

  const override = getModelContextOverride(filename);
  if (override != null) return override;

  if (opts.explicit != null && opts.explicit > 0) {
    return Math.floor(opts.explicit);
  }

  if (opts.catalogContext != null && opts.catalogContext > 0) {
    return Math.floor(opts.catalogContext);
  }

  try {
    const settings = await loadSettings();
    if (settings.llmDefaultContextLength > 0) {
      return settings.llmDefaultContextLength;
    }
  } catch {
    // settings unavailable — fall through
  }

  return FALLBACK_CONTEXT;
}

/** Format for display, e.g. "131,072". */
export function formatContextLength(n: number): string {
  return n.toLocaleString();
}
