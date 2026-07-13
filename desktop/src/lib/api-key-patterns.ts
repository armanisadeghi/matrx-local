/**
 * API Key Provider Patterns
 *
 * This file defines all the rules for recognising AI provider API keys when
 * a user pastes a .env file (or any block of KEY=VALUE lines) into the bulk
 * import dialog.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * HOW TO ADD / UPDATE ENTRIES
 * ─────────────────────────────────────────────────────────────────────────────
 * 1. Find the provider object in PROVIDER_PATTERNS (or add a new one).
 * 2. Add the new name/alias to `names` — the first entry is the canonical ID
 *    sent to the backend and must match VALID_PROVIDERS in repositories.py.
 * 3. Add any extra env-var names (exact or partial) to `envVarNames`.
 * 4. Prefixes/suffixes already handled globally — see GLOBAL_STRIP_PREFIXES /
 *    GLOBAL_STRIP_SUFFIXES below.  Override per-provider if needed.
 * ─────────────────────────────────────────────────────────────────────────────
 */

// ── Global env-var name noise to strip before matching ───────────────────────
//
// After stripping, the remaining token is matched (case-insensitive) against
// every provider's `names` list.  The order matters: longer patterns first.

/** Prefixes to strip from env var names before provider matching. */
export const GLOBAL_STRIP_PREFIXES: string[] = [
  "VITE_",
  "NEXT_PUBLIC_",
  "REACT_APP_",
  "EXPO_PUBLIC_",
  "NUXT_PUBLIC_",
  "PUBLIC_",
  "APP_",
];

/** Suffixes to strip from env var names before provider matching. */
export const GLOBAL_STRIP_SUFFIXES: string[] = [
  "_API_KEY",
  "_APIKEY",
  "_SECRET_KEY",
  "_SECRET",
  "_KEY",
  "_TOKEN",
  "_ACCESS_TOKEN",
  "_AUTH_TOKEN",
  "_AUTH_KEY",
  "_CREDENTIAL",
  "_CREDENTIALS",
];

// ── Per-provider definitions ──────────────────────────────────────────────────

export interface ProviderPattern {
  /**
   * `names[0]` is the canonical provider ID — must match the backend's
   * VALID_PROVIDERS set.  All other entries are recognised aliases.
   * All comparisons are case-insensitive.
   */
  names: string[];

  /**
   * Specific env-var name fragments (after prefix/suffix stripping) OR
   * full env-var names that should map to this provider.
   * Use this for unusual names that wouldn't be caught by the name list alone.
   * All comparisons are case-insensitive.
   */
  envVarNames?: string[];

  /** Human-readable label for the UI. */
  label: string;
}

export const PROVIDER_PATTERNS: ProviderPattern[] = [
  // ── OpenAI ────────────────────────────────────────────────────────────────
  {
    names: ["openai", "open_ai", "oai", "openai_api"],
    envVarNames: [
      "OPENAI_API_KEY",
      "OPENAI_KEY",
      "OAI_API_KEY",
      "OAI_KEY",
      "OPENAI_SECRET",
    ],
    label: "OpenAI",
  },

  // ── Anthropic ─────────────────────────────────────────────────────────────
  {
    names: ["anthropic", "claude", "claude_ai"],
    envVarNames: [
      "ANTHROPIC_API_KEY",
      "ANTHROPIC_KEY",
      "CLAUDE_API_KEY",
      "CLAUDE_KEY",
    ],
    label: "Anthropic",
  },

  // ── Google / Gemini ───────────────────────────────────────────────────────
  {
    names: ["google", "gemini", "google_ai", "googleai", "gemini_ai", "google_gemini"],
    envVarNames: [
      "GOOGLE_API_KEY",
      "GEMINI_API_KEY",
      "GOOGLE_GEMINI_API_KEY",
      "GOOGLEAI_API_KEY",
      "GOOGLE_GENERATIVE_AI_API_KEY",
      "GOOGLE_AI_API_KEY",
    ],
    label: "Google",
  },

  // ── Hugging Face (local GGUF / Hub token) ─────────────────────────────────
  {
    names: ["huggingface", "hf", "hf_hub", "hugging_face", "huggingface_hub"],
    envVarNames: [
      "HUGGING_FACE_HUB_TOKEN",
      "HF_TOKEN",
      "HUGGINGFACE_TOKEN",
      "HUGGING_FACE_TOKEN",
    ],
    label: "Hugging Face",
  },

  // ── Civitai (image-gen models / LoRAs) ────────────────────────────────────
  // In the engine's VALID_PROVIDERS but missing here, so the bulk .env import
  // silently ignored a CIVITAI_API_KEY — while Civitai downloads failed 401 and
  // told the user to go set that very key. (tests/parity/test_api_key_providers)
  {
    names: ["civitai", "civit_ai"],
    envVarNames: [
      "CIVITAI_API_KEY",
      "CIVITAI_API_TOKEN",
      "CIVITAI_TOKEN",
    ],
    label: "Civitai",
  },

  // ── Groq ──────────────────────────────────────────────────────────────────
  {
    names: ["groq", "groq_ai"],
    envVarNames: [
      "GROQ_API_KEY",
      "GROQ_KEY",
    ],
    label: "Groq",
  },

  // ── Together AI ───────────────────────────────────────────────────────────
  {
    names: ["together", "togetherai", "together_ai", "together_xyz"],
    envVarNames: [
      "TOGETHER_API_KEY",
      "TOGETHER_AI_API_KEY",
      "TOGETHERAI_API_KEY",
    ],
    label: "Together AI",
  },

  // ── xAI / Grok ────────────────────────────────────────────────────────────
  {
    names: ["xai", "x_ai", "grok", "grok_ai"],
    envVarNames: [
      "XAI_API_KEY",
      "XAI_KEY",
      "GROK_API_KEY",
      "X_AI_API_KEY",
    ],
    label: "xAI",
  },

  // ── Cerebras ──────────────────────────────────────────────────────────────
  {
    names: ["cerebras", "cerebras_ai"],
    envVarNames: [
      "CEREBRAS_API_KEY",
      "CEREBRAS_KEY",
    ],
    label: "Cerebras",
  },

  // ── ElevenLabs ────────────────────────────────────────────────────────────
  {
    names: ["elevenlabs", "eleven_labs", "11labs"],
    envVarNames: [
      "ELEVENLABS_API_KEY",
      "ELEVEN_LABS_API_KEY",
      "ELEVENLABS_KEY",
    ],
    label: "ElevenLabs",
  },

  // ── Fastino / Pioneer ─────────────────────────────────────────────────────
  // Two env var names for one provider: matrx-ai resolves PIONEER_API_KEY
  // first, FASTINO_API_KEY second.
  {
    names: ["fastino", "pioneer"],
    envVarNames: [
      "FASTINO_API_KEY",
      "PIONEER_API_KEY",
    ],
    label: "Fastino",
  },
];

// ─────────────────────────────────────────────────────────────────────────────
// Matching logic
// ─────────────────────────────────────────────────────────────────────────────

/** Strip global prefixes from a raw env-var name (longest match first). */
function stripPrefixes(name: string): string {
  const upper = name.toUpperCase();
  // Sort descending by length so more specific prefixes win
  const sorted = [...GLOBAL_STRIP_PREFIXES].sort((a, b) => b.length - a.length);
  for (const prefix of sorted) {
    if (upper.startsWith(prefix.toUpperCase())) {
      return name.slice(prefix.length);
    }
  }
  return name;
}

/** Strip global suffixes from a raw env-var name (longest match first). */
function stripSuffixes(name: string): string {
  const upper = name.toUpperCase();
  const sorted = [...GLOBAL_STRIP_SUFFIXES].sort((a, b) => b.length - a.length);
  for (const suffix of sorted) {
    if (upper.endsWith(suffix.toUpperCase())) {
      return name.slice(0, name.length - suffix.length);
    }
  }
  return name;
}

/**
 * Given a raw env-var name (e.g. `NEXT_PUBLIC_GEMINI_API_KEY`), return the
 * canonical provider ID (e.g. `"google"`) or `null` if unrecognised.
 */
export function resolveProvider(rawName: string): string | null {
  const upper = rawName.trim().toUpperCase();

  for (const provider of PROVIDER_PATTERNS) {
    // 1. Check exact env-var name matches first
    if (provider.envVarNames) {
      for (const envName of provider.envVarNames) {
        if (upper === envName.toUpperCase()) {
          return provider.names[0] ?? null;
        }
      }
    }
  }

  // 2. Strip prefix + suffix, then match against provider names
  const stripped = stripSuffixes(stripPrefixes(rawName)).toUpperCase();

  for (const provider of PROVIDER_PATTERNS) {
    for (const alias of provider.names) {
      if (stripped === alias.toUpperCase()) {
        return provider.names[0] ?? null;
      }
    }

    // Also check env-var name fragments after stripping
    if (provider.envVarNames) {
      for (const envName of provider.envVarNames) {
        // Check if the stripped name contains the core of the env-var name
        const envCore = stripSuffixes(stripPrefixes(envName)).toUpperCase();
        if (stripped === envCore) {
          return provider.names[0] ?? null;
        }
      }
    }
  }

  return null;
}

// ─────────────────────────────────────────────────────────────────────────────
// .env file parser
// ─────────────────────────────────────────────────────────────────────────────

export interface ParsedEnvEntry {
  rawKey: string;
  rawValue: string;
  provider: string | null;   // canonical provider ID if matched, null otherwise
  label: string | null;      // human-readable provider label if matched
}

/** Looks like an API key: starts with sk-, key-, xai-, grk-, etc. or is long enough. */
function looksLikeApiKey(value: string): boolean {
  if (!value || value.length < 20) return false;
  // Skip obvious non-keys
  if (value.startsWith("http://") || value.startsWith("https://")) return false;
  if (value.includes(" ") && !value.startsWith('"')) return false;
  return true;
}

/**
 * Parse a block of text that may contain KEY=VALUE lines (like a .env file).
 * Returns every line that looks like it could be an API key, annotated with
 * the matched provider (if any).
 */
export function parseEnvBlock(text: string): ParsedEnvEntry[] {
  const results: ParsedEnvEntry[] = [];
  const seen = new Set<string>();

  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();

    // Skip blank lines and comments
    if (!line || line.startsWith("#")) continue;

    // KEY=VALUE  or  KEY = "VALUE"  or  export KEY=VALUE
    const match = line.match(
      /^(?:export\s+)?([A-Z][A-Z0-9_]*)[ \t]*=[ \t]*["']?(.*?)["']?$/i,
    );
    if (!match) continue;

    const [, key, value] = match;
    if (key === undefined || value === undefined) continue;
    const cleanValue = value.trim();

    // Deduplicate by key
    if (seen.has(key.toUpperCase())) continue;
    seen.add(key.toUpperCase());

    // Only bother with values that look like API keys
    if (!looksLikeApiKey(cleanValue)) continue;

    const provider = resolveProvider(key);
    const providerDef = provider
      ? PROVIDER_PATTERNS.find((p) => p.names[0] === provider)
      : null;

    results.push({
      rawKey: key,
      rawValue: cleanValue,
      provider,
      label: providerDef?.label ?? null,
    });
  }

  return results;
}
