/**
 * Public runtime configuration for the desktop webview.
 *
 * `public.app_config` is the administrative source of truth.  This module
 * starts with the last validated copy persisted on this device, then refreshes
 * from Supabase in the background.  It never blocks app boot and never reads
 * a customer environment variable.
 *
 * The Supabase URL and publishable key used by `lib/supabase` are immutable
 * public bootstrap values: without them a new installation cannot locate the
 * app-config row.  They are intentionally the only renderer configuration
 * outside this store.
 */

import supabase from "@/lib/supabase";

const APP_KEY = "matrx-local";
const CACHE_KEY = "matrx-app-config-v1";
const REQUEST_TIMEOUT_MS = 5_000;

/** Last-resort public defaults for a first launch while completely offline. */
const COMPILED_DEFAULTS: AppRuntimeConfig = {
  aidreamServerUrl: "https://server.app.matrxserver.com",
  webAppOrigin: "https://www.aimatrx.com",
  flags: {},
  fetchedAt: null,
  source: "defaults",
};

export interface AppRuntimeConfig {
  aidreamServerUrl: string;
  webAppOrigin: string;
  flags: Record<string, boolean>;
  fetchedAt: string | null;
  source: "remote" | "cache" | "defaults";
}

interface StoredConfig {
  version: 1;
  aidreamServerUrl: string;
  webAppOrigin: string;
  flags: Record<string, boolean>;
  fetchedAt: string;
}

let resolvedConfig: AppRuntimeConfig = loadCachedConfig() ?? COMPILED_DEFAULTS;
let refreshInFlight: Promise<AppRuntimeConfig> | null = null;

function isHttpsUrl(value: unknown): value is string {
  if (typeof value !== "string" || !value.trim()) return false;
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

function readFlags(value: unknown): Record<string, boolean> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const flags: Record<string, boolean> = {};
  for (const [name, enabled] of Object.entries(value)) {
    if (typeof enabled !== "boolean") return null;
    flags[name] = enabled;
  }
  return flags;
}

function parseStoredConfig(value: unknown): AppRuntimeConfig | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  if (
    record.version !== 1 ||
    !isHttpsUrl(record.aidreamServerUrl) ||
    !isHttpsUrl(record.webAppOrigin)
  ) return null;
  const flags = readFlags(record.flags);
  if (!flags || typeof record.fetchedAt !== "string") return null;
  return {
    aidreamServerUrl: record.aidreamServerUrl.replace(/\/$/, ""),
    webAppOrigin: record.webAppOrigin.replace(/\/$/, ""),
    flags,
    fetchedAt: record.fetchedAt,
    source: "cache",
  };
}

function loadCachedConfig(): AppRuntimeConfig | null {
  try {
    return parseStoredConfig(JSON.parse(localStorage.getItem(CACHE_KEY) ?? "null"));
  } catch {
    return null;
  }
}

function saveCachedConfig(config: AppRuntimeConfig): void {
  const stored: StoredConfig = {
    version: 1,
    aidreamServerUrl: config.aidreamServerUrl,
    webAppOrigin: config.webAppOrigin,
    flags: config.flags,
    fetchedAt: config.fetchedAt ?? new Date().toISOString(),
  };
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(stored));
  } catch {
    // Browser storage may be unavailable or full. The in-memory value remains
    // usable for this launch and the next refresh can retry persistence.
  }
}

function parseRemoteConfig(row: unknown): AppRuntimeConfig | null {
  if (!row || typeof row !== "object" || Array.isArray(row)) return null;
  const record = row as Record<string, unknown>;
  const config = record.config;
  if (!config || typeof config !== "object" || Array.isArray(config)) return null;
  const payload = config as Record<string, unknown>;
  if (!isHttpsUrl(payload.aidream_server_url)) return null;
  const flags = readFlags(payload.flags);
  if (!flags) return null;
  const webAppOrigin = payload.web_app_origin === undefined
    ? COMPILED_DEFAULTS.webAppOrigin
    : isHttpsUrl(payload.web_app_origin)
      ? payload.web_app_origin.replace(/\/$/, "")
      : null;
  if (!webAppOrigin) return null;
  return {
    aidreamServerUrl: payload.aidream_server_url.replace(/\/$/, ""),
    webAppOrigin,
    flags,
    fetchedAt: typeof record.updated_at === "string" ? record.updated_at : new Date().toISOString(),
    source: "remote",
  };
}

function withTimeout<T>(promise: PromiseLike<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = globalThis.setTimeout(
      () => reject(new Error("app-config request timed out")),
      REQUEST_TIMEOUT_MS,
    );
    promise.then(
      (value) => {
        globalThis.clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        globalThis.clearTimeout(timer);
        reject(error);
      },
    );
  });
}

/** The currently usable config. This is always cache/default-backed and synchronous. */
export function getAppRuntimeConfig(): AppRuntimeConfig {
  return resolvedConfig;
}

/**
 * Refresh the administrative config row. Failures deliberately preserve the
 * already-resolved last-good cache/default so application features continue.
 */
export function refreshAppRuntimeConfig(): Promise<AppRuntimeConfig> {
  if (refreshInFlight) return refreshInFlight;

  const request = supabase
    .from("app_config")
    .select("config, updated_at")
    .eq("app", APP_KEY)
    .maybeSingle() as PromiseLike<{ data: unknown; error: unknown }>;

  refreshInFlight = withTimeout(request)
    .then(({ data, error }) => {
      if (error) throw error;
      const remote = parseRemoteConfig(data);
      if (!remote) throw new Error("app-config returned an invalid matrx-local row");
      resolvedConfig = remote;
      saveCachedConfig(remote);
      return resolvedConfig;
    })
    .catch(() => resolvedConfig)
    .finally(() => {
      refreshInFlight = null;
    });

  return refreshInFlight!;
}

/** Begin a non-blocking refresh during renderer startup. */
export function startAppRuntimeConfig(): void {
  void refreshAppRuntimeConfig();
}

/** Resolve the current authoritative AIDream base URL for a request. */
export async function getAIDreamServerUrl(): Promise<string> {
  return (await refreshAppRuntimeConfig()).aidreamServerUrl;
}

/** Resolve the administrative AI Matrx web origin for a handoff request. */
export async function getWebAppOrigin(): Promise<string> {
  return (await refreshAppRuntimeConfig()).webAppOrigin;
}
