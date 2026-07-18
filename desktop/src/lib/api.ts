/**
 * API client for communication with the Python/FastAPI sidecar engine.
 *
 * In development, the Python server runs standalone on a port (default 22140).
 * In production, Tauri spawns it as a managed sidecar process.
 */

import { emitClientLog } from "@/hooks/use-client-log";
import { getOwnedEngineUrl, discoverEnginePort } from "@/lib/sidecar";
import { enginePortList } from "@/lib/engine-ports";

const DISCOVERY_PORTS = enginePortList();

/** Operator broadcast carried by the remote app config (level-styled, shown once). */
export interface AppConfigNotice {
  level: "info" | "warning" | "critical";
  title: string;
  body: string;
  url?: string | null;
}

/** Remote app-config provenance as reported on GET /health (`app_config`). */
export interface AppConfigStatus {
  /** Which precedence tier the applied config came from. */
  tier: "env" | "remote" | "cache" | "defaults";
  fetched_at: string | null;
  /** True when the installed app version is below min_supported_app_version. */
  update_required: boolean;
  notice: AppConfigNotice | null;
}

export interface EngineHealth {
  /** Liveness literal — always "ok" (matrx-extend pins it). */
  status: string;
  /** Health detail: "ok" | "degraded" | "failed_services". */
  health?: string;
  service: string;
  version?: string;
  failed?: string[];
  degraded?: string[];
  app_config?: AppConfigStatus;
}

export interface ToolInfo {
  name: string;
  description?: string;
  category?: string;
}

/** Full tool schema as returned by /chat/tools (Anthropic-compatible with category). */
export interface EngineToolSchema {
  name: string;
  description: string;
  category: string;
  input_schema: {
    type: "object";
    properties: Record<
      string,
      { type: string; description?: string; default?: unknown }
    >;
    required: string[];
  };
}

export interface ToolResult {
  type: "success" | "error";
  output: string;
  /** Deprecated provider-only inline image. New captures use `artifact`. */
  image?: ToolImageData;
  artifact?: ToolMediaArtifact;
  metadata?: Record<string, unknown>;
}

export interface ToolImageData {
  media_type: string;
  base64_data: string;
}

export interface ToolMediaArtifact {
  kind: "image_ref";
  artifact_id: string;
  availability: "sync_pending" | "cloud_ready" | "sync_failed";
  media_type: string;
  file_name: string;
  size_bytes: number;
  checksum: string;
  source_width: number;
  source_height: number;
  capture_source: "desktop" | "browser";
  file_id?: string;
  media_ref?: { file_id: string; vision_class?: string | null };
  url?: string;
  cdn_url?: string;
  signed_url?: string;
  download_url?: string;
  visibility: "private" | "shared" | "public";
  capture: Record<string, unknown>;
}

export interface BrowserStatus {
  chrome_found: boolean;
  chrome_path: string | null;
  chrome_version: string | null;
  profile_found: boolean;
  browser_running: boolean;
}

export interface ScrapeResultData {
  url: string;
  success: boolean;
  status_code: number;
  content: string;
  title: string;
  content_type: string;
  response_url: string;
  error: string | null;
  elapsed_ms: number;
}

export interface RemoteScrapeResult {
  status: "success" | "error";
  url: string;
  error: string | null;
  status_code: number | null;
  content_type: string | null;
  text_data: string | null;
  from_cache: boolean;
  overview: Record<string, unknown> | null;
  scraped_at: string | null;
}

export interface RemoteScrapeResponse {
  status: string;
  execution_time_ms: number;
  results: RemoteScrapeResult[];
}

export interface EngineSettings {
  headless_scraping: boolean;
  scrape_delay: number;
}

/** A configurable storage path entry from GET /settings/paths */
export interface StoragePath {
  name: string; // e.g. "notes"
  label: string; // e.g. "Notes folder"
  current: string; // resolved absolute path
  default: string; // compiled default path
  is_custom: boolean; // true if user has set a custom path
  user_visible: boolean; // whether to show in Settings UI
}

export interface StoragePathStats {
  name: string;
  file_count: number;
  size_bytes: number;
  exists: boolean;
}

export interface SystemInfo {
  platform: string;
  architecture: string;
  python_version: string;
  hostname: string;
  username: string;
  cwd: string;
  home_dir: string;
}

class EngineAPI {
  private baseUrl: string | null = null;
  private wsUrl: string | null = null;
  private ws: WebSocket | null = null;
  private pendingRequests = new Map<
    string,
    { resolve: (v: ToolResult) => void; reject: (e: Error) => void }
  >();
  private eventListeners = new Map<string, Set<(data: unknown) => void>>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private requestIdCounter = 0;
  private _getAccessToken: (() => Promise<string | null>) | null = null;
  private static readonly TOKEN_PROVIDER_TIMEOUT_MS = 10_000;
  // Self-healing re-discovery guards: prevent concurrent scans and rate-limit
  // how often we re-run discovery when the engine URL goes null or stale.
  private rediscovering = false;
  private lastRediscoverAt = 0;
  private static readonly REDISCOVER_BACKOFF_MS = 2000;

  /** Register a function that provides the current Supabase JWT. */
  setTokenProvider(fn: () => Promise<string | null>) {
    this._getAccessToken = fn;
  }

  /**
   * Bound token resolution independently of the HTTP request timeout.
   *
   * Media requests obtain auth before fetch() is created, so an auth-provider
   * deadlock would otherwise bypass AbortSignal.timeout() and spin forever.
   */
  private async resolveAccessToken(): Promise<string | null> {
    if (!this._getAccessToken) return null;
    const timeoutMs = EngineAPI.TOKEN_PROVIDER_TIMEOUT_MS;
    let timeoutId: ReturnType<typeof setTimeout> | undefined;
    try {
      return await Promise.race([
        this._getAccessToken(),
        new Promise<never>((_, reject) => {
          timeoutId = setTimeout(
            () => reject(new Error(`Authentication token lookup timed out after ${timeoutMs / 1000}s`)),
            timeoutMs,
          );
        }),
      ]);
    } finally {
      if (timeoutId !== undefined) clearTimeout(timeoutId);
    }
  }

  private async authHeaders(): Promise<Record<string, string>> {
    const token = await this.resolveAccessToken();
    if (!token) return {};
    return { Authorization: `Bearer ${token}` };
  }

  /** Headers for authenticated engine HTTP calls (same JWT as tools, settings, etc.). */
  async getEngineAuthHeaders(): Promise<Record<string, string>> {
    return this.authHeaders();
  }

  /** Expose the current access token for SSE/EventSource connections that need ?token=. */
  async getAccessToken(): Promise<string | null> {
    return this.resolveAccessToken();
  }

  /**
   * Discover the engine port by scanning the known range.
   *
   * Accepts an optional `knownUrl` that bypasses the port scan — used when
   * the Rust layer has already identified the port (e.g. via `discover_engine_port`
   * which bypasses Windows WebView2 loopback network isolation).
   */
  async discover(knownUrl?: string): Promise<string | null> {
    if (knownUrl) {
      this.setBase(knownUrl);
      return knownUrl;
    }

    for (const port of DISCOVERY_PORTS) {
      try {
        const resp = await fetch(`http://127.0.0.1:${port}/tools/list`, {
          signal: AbortSignal.timeout(500),
        });
        if (resp.ok) {
          this.setBase(`http://127.0.0.1:${port}`);
          return this.baseUrl;
        }
      } catch {
        continue;
      }
    }

    return null;
  }

  /** Point the REST base URL and its derived WS URL at a single engine origin. */
  private setBase(url: string) {
    this.baseUrl = url;
    this.wsUrl = url.replace("http://", "ws://") + "/ws";
  }

  /**
   * Self-healing re-discovery.
   *
   * Runs when the current base URL is null (discovery never succeeded) or stale
   * (the engine moved ports — e.g. a rogue instance overwrote ~/.matrx/local.json,
   * or our own child engine rebound). Prefers the Rust-owned sidecar's port,
   * which is authoritative for THIS app, then falls back to a port scan.
   *
   * Guarded against concurrent runs and rate-limited, so the various callers
   * (the useEngine health interval, the WS reconnect loop) can all invoke it
   * freely without hammering discovery. Returns a CONFIRMED-healthy URL on
   * success, or null when no live engine could be located this attempt.
   */
  async rediscover(): Promise<string | null> {
    if (this.rediscovering) return null;
    const now = Date.now();
    if (now - this.lastRediscoverAt < EngineAPI.REDISCOVER_BACKOFF_MS) {
      return null;
    }
    this.rediscovering = true;
    this.lastRediscoverAt = now;
    try {
      // 1. Prefer the app's own Rust-owned sidecar (health-confirmed natively).
      const ownedUrl = await getOwnedEngineUrl();
      if (ownedUrl) {
        if (ownedUrl !== this.baseUrl) {
          emitClientLog(
            "info",
            `Engine URL re-discovered (Rust-owned sidecar): ${ownedUrl}`,
            "engine",
          );
        }
        this.setBase(ownedUrl);
        return ownedUrl;
      }
      // 2. Fall back to a port scan (dev / browser, or Rust IPC unavailable).
      const scanned = await discoverEnginePort();
      if (scanned) {
        if (scanned !== this.baseUrl) {
          emitClientLog(
            "info",
            `Engine URL re-discovered (port scan): ${scanned}`,
            "engine",
          );
        }
        this.setBase(scanned);
        return scanned;
      }
      return null;
    } finally {
      this.rediscovering = false;
    }
  }

  /**
   * Check if the engine is reachable — and self-heal a null/stale base URL.
   *
   * A plain probe of the current base URL is the fast path. When that probe
   * fails (or there is no base URL at all), the engine may simply have moved
   * ports; rather than reporting "unreachable" forever, we re-run discovery
   * (preferring the Rust-owned sidecar) and treat a confirmed re-discovery as
   * healthy. This is what unwedges `engine.engineUrl` after an engine flap —
   * the useEngine health interval calls this every 10s.
   */
  async isHealthy(): Promise<boolean> {
    if (this.baseUrl) {
      try {
        const resp = await fetch(`${this.baseUrl}/tools/list`, {
          signal: AbortSignal.timeout(2000),
        });
        if (resp.ok) return true;
      } catch {
        // Current URL is null/stale — fall through to self-healing discovery.
      }
    }
    const healed = await this.rediscover();
    return healed !== null;
  }

  /**
   * Get the full /health JSON — health detail, failed/degraded services, and
   * the remote app-config provenance ({tier, fetched_at, update_required,
   * notice}). Distinct from isHealthy(): this reads the payload, that one
   * only answers reachability.
   */
  async getHealth(): Promise<EngineHealth> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const resp = await fetch(`${this.baseUrl}/health`, {
      signal: AbortSignal.timeout(3000),
    });
    if (!resp.ok) throw new Error(`Health check failed: ${resp.status}`);
    return resp.json();
  }

  /** Get the engine version string from the root endpoint. */
  async getVersion(): Promise<string> {
    if (!this.baseUrl) return "";
    try {
      const resp = await fetch(`${this.baseUrl}/`, {
        signal: AbortSignal.timeout(2000),
      });
      if (resp.ok) {
        const data = await resp.json();
        return data.version ?? "";
      }
    } catch {
      /* non-critical */
    }
    return "";
  }

  /** Get engine runtime settings. */
  async getSettings(): Promise<EngineSettings> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/settings`, { headers });
    if (!resp.ok) throw new Error(`Failed to get settings: ${resp.status}`);
    return resp.json();
  }

  /** Update engine runtime settings. */
  async updateSettings(settings: EngineSettings): Promise<EngineSettings> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    const resp = await fetch(`${this.baseUrl}/settings`, {
      method: "PUT",
      headers,
      body: JSON.stringify(settings),
    });
    if (!resp.ok) throw new Error(`Failed to update settings: ${resp.status}`);
    return resp.json();
  }

  // ── Storage path management ────────────────────────────────────────────

  /** List all configurable storage paths with their current resolved values. */
  async getStoragePaths(): Promise<StoragePath[]> {
    return this.request<StoragePath[]>("/settings/paths");
  }

  /** Set a custom path for a named storage location. */
  async setStoragePath(name: string, path: string): Promise<StoragePath> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    const resp = await fetch(`${this.baseUrl}/settings/paths/${name}`, {
      method: "PUT",
      headers,
      body: JSON.stringify({ path }),
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`Failed to set path: ${text}`);
    }
    return resp.json();
  }

  /** Reset a storage path to its compiled default. */
  async resetStoragePath(name: string): Promise<StoragePath> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/settings/paths/${name}`, {
      method: "DELETE",
      headers,
    });
    if (!resp.ok) throw new Error(`Failed to reset path: ${resp.status}`);
    return resp.json();
  }

  /** Get file count and total size for a named storage path. */
  async getStoragePathStats(name: string): Promise<StoragePathStats> {
    return this.request<StoragePathStats>(`/settings/paths/${name}/stats`);
  }

  // ── AI provider status ─────────────────────────────────────────────────

  /** Check which AI providers are configured (have API keys) on the engine. */
  async getAiStatus(): Promise<{
    providers: {
      available: string[];
      missing: string[];
      any_available: boolean;
    };
    jwt_validation: { configured: boolean; warning: string | null };
    engine: { initialized: boolean; client_mode: boolean };
    local_llm: {
      available: boolean;
      port: number | null;
      model_name: string | null;
      canonical_model_name: string | null;
      matrx_ai_support: boolean;
      instructions: string | null;
    };
  }> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const resp = await fetch(`${this.baseUrl}/chat/ai-status`);
    if (!resp.ok) throw new Error(`Failed to get AI status: ${resp.status}`);
    return resp.json();
  }

  // ── Local LLM (llama-server) bridge ────────────────────────────────────

  /**
   * Notify the Python engine that a local llama-server is running.
   * Called from use-llm.ts when the llm-server-ready Tauri event fires.
   * Non-fatal — errors are caught and logged by the caller.
   */
  async connectLocalLlm(port: number, modelName: string): Promise<void> {
    // The llama-server-ready event can beat sidecar discovery during startup.
    // Heal the engine URL here instead of dropping the one event that joins
    // the desktop model process to the Python agent runtime.
    if (!this.baseUrl) await this.rediscover();
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const resp = await fetch(`${this.baseUrl}/chat/local-llm/connect`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(await this.authHeaders()),
      },
      body: JSON.stringify({ port, model_name: modelName }),
    });
    if (!resp.ok)
      throw new Error(
        `Failed to connect local LLM: ${resp.status} ${await resp.text().catch(() => resp.statusText)}`,
      );
  }

  /**
   * Notify the Python engine that the local llama-server has stopped.
   * Called from use-llm.ts when the llm-server-stopped Tauri event fires.
   * Non-fatal — errors are caught and logged by the caller.
   */
  async disconnectLocalLlm(): Promise<void> {
    if (!this.baseUrl) return;
    const resp = await fetch(`${this.baseUrl}/chat/local-llm/disconnect`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(await this.authHeaders()),
      },
    });
    if (!resp.ok)
      throw new Error(
        `Failed to disconnect local LLM: ${resp.status} ${await resp.text().catch(() => resp.statusText)}`,
      );
  }

  /** Get the current local LLM registration status from the engine. */
  async getLocalLlmStatus(): Promise<{
    available: boolean;
    port: number | null;
    model_name: string | null;
    canonical_model_name: string | null;
    matrx_ai_support: boolean;
    instructions: string | null;
  }> {
    if (!this.baseUrl) await this.rediscover();
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const resp = await fetch(`${this.baseUrl}/chat/local-llm/status`, {
      headers: await this.authHeaders(),
    });
    if (!resp.ok)
      throw new Error(
        `Failed to get local LLM status: ${resp.status} ${await resp.text().catch(() => resp.statusText)}`,
      );
    return resp.json();
  }

  // ── Wake word settings (SQLite-persisted) ──────────────────────────────

  /** Fetch the user's wake word engine preference from the sidecar SQLite store. */
  async getWakeWordSettings(): Promise<
    import("./transcription/types").WakeWordSettings
  > {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/settings/wake-word`, { headers });
    if (!resp.ok)
      throw new Error(`Failed to get wake word settings: ${resp.status}`);
    const raw = await resp.json();
    // Convert snake_case → camelCase
    return {
      engine: raw.engine,
      owwModel: raw.oww_model,
      owwThreshold: raw.oww_threshold,
      customKeyword: raw.custom_keyword,
    };
  }

  /** Persist the user's wake word engine preference to the sidecar SQLite store. */
  async saveWakeWordSettings(
    settings: import("./transcription/types").WakeWordSettings,
  ): Promise<void> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    // Convert camelCase → snake_case for the Python API
    const body = {
      engine: settings.engine,
      oww_model: settings.owwModel,
      oww_threshold: settings.owwThreshold,
      custom_keyword: settings.customKeyword,
    };
    const resp = await fetch(`${this.baseUrl}/settings/wake-word`, {
      method: "PUT",
      headers,
      body: JSON.stringify(body),
    });
    if (!resp.ok)
      throw new Error(`Failed to save wake word settings: ${resp.status}`);
  }

  // ── openWakeWord engine control ──────────────────────────────────────────

  /** Get OWW engine runtime status. */
  async owwStatus(): Promise<import("./transcription/types").OwwStatus> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/wake-word/status`, { headers });
    if (!resp.ok) throw new Error(`OWW status failed: ${resp.status}`);
    return resp.json();
  }

  /** Start the OWW detection loop. */
  async owwStart(opts?: {
    modelName?: string;
    threshold?: number;
    deviceName?: string;
  }): Promise<void> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    const resp = await fetch(`${this.baseUrl}/wake-word/start`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        model_name: opts?.modelName ?? null,
        threshold: opts?.threshold ?? null,
        device_name: opts?.deviceName ?? null,
      }),
    });
    if (!resp.ok)
      throw new Error(`OWW start failed: ${resp.status} ${await resp.text()}`);
  }

  /** Stop the OWW detection loop entirely. */
  async owwStop(): Promise<void> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const resp = await fetch(`${this.baseUrl}/wake-word/stop`, {
      method: "POST",
      headers: await this.authHeaders(),
    });
    if (!resp.ok) throw new Error(`OWW stop failed: ${resp.status}`);
  }

  /** Mute OWW (keeps thread alive). */
  async owwMute(): Promise<void> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const resp = await fetch(`${this.baseUrl}/wake-word/mute`, {
      method: "POST",
      headers: await this.authHeaders(),
    });
    if (!resp.ok) throw new Error(`OWW mute failed: ${resp.status}`);
  }

  /** Unmute OWW. */
  async owwUnmute(): Promise<void> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const resp = await fetch(`${this.baseUrl}/wake-word/unmute`, {
      method: "POST",
      headers: await this.authHeaders(),
    });
    if (!resp.ok) throw new Error(`OWW unmute failed: ${resp.status}`);
  }

  /** Dismiss OWW (10-second false-trigger cooldown). */
  async owwDismiss(): Promise<void> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const resp = await fetch(`${this.baseUrl}/wake-word/dismiss`, {
      method: "POST",
      headers: await this.authHeaders(),
    });
    if (!resp.ok) throw new Error(`OWW dismiss failed: ${resp.status}`);
  }

  /** Manually fire a wake-word-detected event (for testing). */
  async owwTrigger(): Promise<void> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const resp = await fetch(`${this.baseUrl}/wake-word/trigger`, {
      method: "POST",
      headers: await this.authHeaders(),
    });
    if (!resp.ok) throw new Error(`OWW trigger failed: ${resp.status}`);
  }

  /** Configure OWW model / threshold at runtime. */
  async owwConfigure(opts: {
    modelName?: string;
    threshold?: number;
  }): Promise<void> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    const resp = await fetch(`${this.baseUrl}/wake-word/configure`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        model_name: opts.modelName ?? null,
        threshold: opts.threshold ?? null,
      }),
    });
    if (!resp.ok) throw new Error(`OWW configure failed: ${resp.status}`);
  }

  /** List all available OWW models (pre-trained + custom). */
  async owwListModels(): Promise<
    import("./transcription/types").OwwModelsResponse
  > {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/wake-word/models`, { headers });
    if (!resp.ok) throw new Error(`OWW list models failed: ${resp.status}`);
    return resp.json();
  }

  /**
   * @deprecated Blocking download with no progress feedback and no timeout.
   * The Wake Word UI now uses {@link owwDownloadModelStream} (SSE) for real
   * progress + cancel. The underlying route `POST /wake-word/models/download`
   * is retained for now, but new callers should prefer the streaming variant.
   */
  async owwDownloadModel(
    name: string,
  ): Promise<import("./transcription/types").OwwModelInfo> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    const resp = await fetch(`${this.baseUrl}/wake-word/models/download`, {
      method: "POST",
      headers,
      body: JSON.stringify({ model_name: name }),
    });
    if (!resp.ok)
      throw new Error(
        `OWW download failed: ${resp.status} ${await resp.text()}`,
      );
    return resp.json();
  }

  /**
   * Download a pre-trained OWW model, streaming progress via SSE.
   *
   * Consumes the named events emitted by `POST /wake-word/models/download-stream`:
   *   progress → { bytes_done, total_bytes, percent }
   *   complete → { name, size_mb }
   *   error    → string
   *
   * This is a POST-with-body SSE stream, so it uses fetch()+ReadableStream
   * (EventSource cannot POST) and does NOT route through request() — no
   * default timeout applies. Cancel via `callbacks.signal` (AbortController).
   */
  async owwDownloadModelStream(
    name: string,
    callbacks: {
      onProgress?: (data: {
        bytes_done: number;
        total_bytes: number;
        percent: number;
      }) => void;
      onComplete: (data: { name: string; size_mb: number }) => void;
      onError: (error: string) => void;
      signal?: AbortSignal;
    },
  ): Promise<void> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    (headers as Record<string, string>)["Content-Type"] = "application/json";
    const resp = await fetch(
      `${this.baseUrl}/wake-word/models/download-stream`,
      {
        method: "POST",
        headers,
        body: JSON.stringify({ model_name: name }),
        signal: callbacks.signal ?? null,
      },
    );
    if (!resp.ok) {
      callbacks.onError(
        `OWW download failed: ${resp.status} ${await resp.text()}`,
      );
      return;
    }
    const reader = resp.body?.getReader();
    if (!reader) {
      callbacks.onError("No response body");
      return;
    }
    const decoder = new TextDecoder();
    let buffer = "";
    let eventType = "";
    let receivedTerminal = false;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (line.startsWith("event: ")) {
          eventType = line.slice(7).trim();
        } else if (line.startsWith("data: ")) {
          try {
            const data = JSON.parse(line.slice(6));
            if (eventType === "progress") {
              callbacks.onProgress?.(data);
            } else if (eventType === "complete") {
              receivedTerminal = true;
              callbacks.onComplete(data);
            } else if (eventType === "error") {
              receivedTerminal = true;
              callbacks.onError(typeof data === "string" ? data : String(data));
            }
          } catch {
            /* skip malformed */
          }
          eventType = "";
        }
      }
    }
    if (!receivedTerminal) {
      callbacks.onError("Download stream ended without a completion event");
    }
  }

  /**
   * Open an EventSource SSE stream to the OWW detection service.
   * The caller is responsible for closing it (eventSource.close()).
   * The base URL must be discovered before calling this.
   */
  owwStream(): EventSource {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    return new EventSource(`${this.baseUrl}/wake-word/stream`);
  }

  /** Get the list of available tools from the engine. */
  async listTools(): Promise<string[]> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/tools/list`, { headers });
    if (!resp.ok) throw new Error(`Failed to list tools: ${resp.status}`);
    const data = await resp.json();
    return data.tools ?? data;
  }

  /** Get tool schemas grouped by category (from /chat/tools/by-category). */
  async getToolSchemasByCategory(): Promise<
    Record<string, EngineToolSchema[]>
  > {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/chat/tools/by-category`, {
      headers,
    });
    if (!resp.ok) throw new Error(`Failed to get tool schemas: ${resp.status}`);
    const data = await resp.json();
    return (data.categories ?? {}) as Record<string, EngineToolSchema[]>;
  }

  /** Get all tool schemas as a flat list (from /chat/tools). */
  async getAllToolSchemas(): Promise<EngineToolSchema[]> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/chat/tools`, { headers });
    if (!resp.ok) throw new Error(`Failed to get tool schemas: ${resp.status}`);
    const data = await resp.json();
    return (data.tools ?? []) as EngineToolSchema[];
  }

  /** Invoke a tool via REST (stateless, one-shot). */
  async invokeTool(
    tool: string,
    input: Record<string, unknown>,
  ): Promise<ToolResult> {
    const callId = `${tool}-${Date.now()}`;
    const startTs = Date.now();

    if (!this.baseUrl) {
      console.error(`[api/invokeTool] BLOCKED — engine not discovered`, {
        callId,
        tool,
        input,
        timestamp: new Date().toISOString(),
      });
      throw new Error("Engine not discovered — start the Python engine first");
    }

    const authHdrs = await this.authHeaders();
    const hasAuth = Object.keys(authHdrs).length > 0;
    console.info(`[api/invokeTool] → ${tool}`, {
      callId,
      baseUrl: this.baseUrl,
      hasAuth,
      inputKeys: Object.keys(input),
      timestamp: new Date().toISOString(),
    });

    const headers = { "Content-Type": "application/json", ...authHdrs };
    const TIMEOUT_MS = 120_000;

    let resp: Response;
    try {
      resp = await fetch(`${this.baseUrl}/tools/invoke`, {
        method: "POST",
        headers,
        body: JSON.stringify({ tool, input }),
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
    } catch (fetchErr) {
      const elapsed = Date.now() - startTs;
      const isTimeout =
        (fetchErr as Error).name === "TimeoutError" ||
        (fetchErr as Error).name === "AbortError";
      console.error(`[api/invokeTool] FETCH FAILED — ${tool}`, {
        callId,
        tool,
        baseUrl: this.baseUrl,
        elapsed_ms: elapsed,
        isTimeout,
        error: (fetchErr as Error).message,
        stack: (fetchErr as Error).stack,
        hint: isTimeout
          ? `Request timed out after ${TIMEOUT_MS}ms — engine may be overloaded or hung`
          : "Network error — engine may have crashed or restarted",
        timestamp: new Date().toISOString(),
      });
      throw fetchErr;
    }

    const elapsed = Date.now() - startTs;

    if (!resp.ok) {
      let body = "";
      try {
        body = await resp.text();
      } catch {
        /* ignore */
      }
      console.error(`[api/invokeTool] HTTP ERROR — ${tool}`, {
        callId,
        tool,
        baseUrl: this.baseUrl,
        status: resp.status,
        statusText: resp.statusText,
        elapsed_ms: elapsed,
        responseBody: body.slice(0, 1000),
        hint:
          resp.status === 401
            ? "401 Unauthorized — JWT not attached or expired; check auth setup"
            : resp.status === 404
              ? `404 Not Found — is the tool name correct? Tool="${tool}"`
              : resp.status === 422
                ? "422 Unprocessable Entity — check input shape matches the tool's schema"
                : resp.status >= 500
                  ? "5xx Server Error — check Python engine logs for the exception"
                  : "Unexpected HTTP error",
        timestamp: new Date().toISOString(),
      });
      throw new Error(
        `Tool invocation failed: ${resp.status} ${resp.statusText} — ${body.slice(0, 200)}`,
      );
    }

    const result: ToolResult = await resp.json();
    console.info(`[api/invokeTool] ✓ ${tool} — ${elapsed}ms`, {
      callId,
      resultType: result.type,
      outputLength: result.output?.length ?? 0,
      hasMetadata: !!result.metadata,
    });
    return result;
  }

  /** Connect via WebSocket for persistent, stateful sessions. */
  async connectWebSocket(): Promise<void> {
    if (!this.wsUrl) throw new Error("Engine not discovered");

    // Guard against duplicate sockets: the SIGNED_IN auth handler and
    // scheduleReconnect can both land here. Reuse a socket that is already
    // open or still connecting; tear down any half-open leftover first.
    if (this.ws) {
      if (
        this.ws.readyState === WebSocket.OPEN ||
        this.ws.readyState === WebSocket.CONNECTING
      ) {
        return;
      }
      const stale = this.ws;
      this.ws = null;
      stale.onopen = null;
      stale.onmessage = null;
      stale.onclose = null;
      stale.onerror = null;
      try {
        stale.close();
      } catch {
        // already closed
      }
    }

    // WebSocket does not support arbitrary headers in the browser.
    // The server validates auth via a `?token=` query parameter instead.
    const token = await this.resolveAccessToken();
    const url = token
      ? `${this.wsUrl}?token=${encodeURIComponent(token)}`
      : this.wsUrl;

    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(url);

      this.ws.onopen = () => {
        this.emit("connected", null);
        resolve();
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          // If this is a response to a pending request
          if (data.id && this.pendingRequests.has(data.id)) {
            const pending = this.pendingRequests.get(data.id)!;
            this.pendingRequests.delete(data.id);
            if (data.type === "error") {
              pending.reject(new Error(data.output || data.error));
            } else {
              pending.resolve(data as ToolResult);
            }
          }
          // Emit as a general event
          this.emit("message", data);
        } catch {
          // Non-JSON message
        }
      };

      this.ws.onclose = () => {
        // Settle every in-flight request — their responses can never arrive
        // on a closed socket, and leaving them pending hangs callers for the
        // full 2-minute timeout.
        this.rejectAllPending("WebSocket disconnected");
        this.emit("disconnected", null);
        this.scheduleReconnect();
      };

      this.ws.onerror = (err) => {
        this.emit("error", err);
        reject(new Error("WebSocket connection failed"));
      };
    });
  }

  /** Whether the engine WebSocket is currently in OPEN state. */
  isWsConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }

  /**
   * Wait briefly for the engine WebSocket to reach OPEN state.
   *
   * The engine flips ``engineStatus`` to ``"connected"`` as soon as REST
   * is reachable, but ``connectWebSocket()`` runs slightly later in the
   * init sequence (it waits for the Supabase session token). Any page
   * that fires a ``invokeToolWs`` call inside an ``engineStatus ===
   * "connected"`` ``useEffect`` race would otherwise see a synchronous
   * "WebSocket not connected" throw on the first render. Waiting up to
   * a short window collapses the race without changing call sites.
   *
   * Resolves to ``true`` if the socket comes up in time, ``false``
   * otherwise. Callers can then decide to show a friendly "connecting…"
   * state instead of a raw error toast.
   */
  async waitForWs(timeoutMs: number = 3000): Promise<boolean> {
    if (this.isWsConnected()) return true;
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (this.isWsConnected()) return true;
      await new Promise((r) => setTimeout(r, 100));
    }
    return this.isWsConnected();
  }

  /** Invoke a tool via WebSocket (stateful, supports concurrent ops). */
  async invokeToolWs(
    tool: string,
    input: Record<string, unknown>,
  ): Promise<ToolResult> {
    // Brief grace window — see ``waitForWs`` docstring. Without this,
    // a page that mounts before the WS upgrade completes throws
    // immediately and shows the user an error UI for what is really a
    // sub-second ordering race during startup.
    if (!this.isWsConnected()) {
      const ready = await this.waitForWs(3000);
      if (!ready) {
        throw new Error(
          "WebSocket not connected (engine is still finishing startup — please retry in a moment)",
        );
      }
    }

    const id = `req-${++this.requestIdCounter}`;

    return new Promise((resolve, reject) => {
      this.pendingRequests.set(id, { resolve, reject });
      try {
        this.ws!.send(JSON.stringify({ id, tool, input }));
      } catch (e) {
        // A throwing send (socket torn down between the readyState check and
        // here) must not leak the pending entry.
        this.pendingRequests.delete(id);
        reject(e instanceof Error ? e : new Error(String(e)));
        return;
      }

      // Timeout after 2 minutes
      setTimeout(() => {
        if (this.pendingRequests.has(id)) {
          this.pendingRequests.delete(id);
          reject(new Error("Tool invocation timed out"));
        }
      }, 120000);
    });
  }

  /** Get system information from the engine. */
  async getSystemInfo(): Promise<SystemInfo> {
    const result = await this.invokeTool("SystemInfo", {});
    if (result.metadata) {
      return {
        platform: String(result.metadata.platform ?? ""),
        architecture: String(result.metadata.architecture ?? ""),
        python_version: String(result.metadata.python_version ?? ""),
        hostname: String(result.metadata.hostname ?? ""),
        username: String(result.metadata.user ?? ""),
        cwd: String(result.metadata.cwd ?? ""),
        home_dir: String(result.metadata.home ?? ""),
      };
    }
    return {
      platform: "",
      architecture: "",
      python_version: "",
      hostname: "",
      username: "",
      cwd: "",
      home_dir: "",
    };
  }

  /** Get the cached system hardware profile from the engine. */
  async getHardware(): Promise<HardwareResponse> {
    return this.request<HardwareResponse>("/hardware");
  }

  /** Re-run full hardware detection, update the cache, and push to cloud. */
  async refreshHardware(): Promise<HardwareResponse> {
    return this.request<HardwareResponse>("/hardware/refresh", {
      method: "POST",
    });
  }

  /** Get browser status — returns defaults until a real endpoint is added. */
  async getBrowserStatus(): Promise<BrowserStatus> {
    if (!this.baseUrl)
      return {
        chrome_found: false,
        chrome_path: null,
        chrome_version: null,
        profile_found: false,
        browser_running: false,
      };
    try {
      const result = await this.invokeTool("SystemInfo", {});
      const meta = result.metadata ?? {};
      return {
        chrome_found: Boolean(meta.playwright_available),
        chrome_path: meta.chrome_path ? String(meta.chrome_path) : null,
        chrome_version: meta.chrome_version
          ? String(meta.chrome_version)
          : null,
        profile_found: false,
        browser_running: false,
      };
    } catch {
      return {
        chrome_found: false,
        chrome_path: null,
        chrome_version: null,
        profile_found: false,
        browser_running: false,
      };
    }
  }

  /** Scrape URLs using the engine's multi-strategy scraper. */
  async scrape(urls: string[], useCache = true): Promise<ToolResult> {
    return this.invokeTool("Scrape", { urls, use_cache: useCache });
  }

  /** Search the web via the engine. */
  async search(
    keywords: string[],
    count = 10,
    country = "us",
  ): Promise<ToolResult> {
    return this.invokeTool("Search", { keywords, count, country });
  }

  /** Deep research via the engine. */
  async research(
    query: string,
    effort = "medium",
    country = "us",
  ): Promise<ToolResult> {
    return this.invokeTool("Research", { query, effort, country });
  }

  // ---- Remote Scraper Server (via /remote-scraper/* proxy) ----

  /** Check if the remote scraper server is available. */
  async remoteScraperStatus(): Promise<{
    available: boolean;
    reason?: string;
    status?: string;
  }> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/remote-scraper/status`, {
      headers,
      signal: AbortSignal.timeout(10000),
    });
    if (!resp.ok)
      throw new Error(`Remote scraper status failed: ${resp.status}`);
    return resp.json();
  }

  /** Scrape URLs via the remote scraper server. */
  async scrapeRemotely(
    urls: string[],
    options?: Record<string, unknown>,
  ): Promise<RemoteScrapeResponse> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    console.info(`[api/scrapeRemotely] → ${urls.length} URL(s)`, {
      urls,
      options,
    });
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    let resp: Response;
    try {
      resp = await fetch(`${this.baseUrl}/remote-scraper/scrape`, {
        method: "POST",
        headers,
        body: JSON.stringify({ urls, options: options ?? {} }),
        signal: AbortSignal.timeout(120_000),
      });
    } catch (err) {
      console.error(`[api/scrapeRemotely] FETCH FAILED`, {
        urls,
        options,
        error: (err as Error).message,
        stack: (err as Error).stack,
        timestamp: new Date().toISOString(),
      });
      throw err;
    }
    if (!resp.ok) {
      let body = "";
      try {
        body = await resp.text();
      } catch {
        /* ignore */
      }
      console.error(`[api/scrapeRemotely] HTTP ${resp.status}`, {
        urls,
        status: resp.status,
        body: body.slice(0, 500),
      });
      throw new Error(
        `Remote scrape failed: ${resp.status} — ${body.slice(0, 200)}`,
      );
    }
    const result = await resp.json();
    console.info(`[api/scrapeRemotely] ✓`, {
      resultCount: result.results?.length,
      execution_time_ms: result.execution_time_ms,
    });
    return result;
  }

  // ---- SSE streaming (remote scraper) ----

  /**
   * Open an SSE stream to a remote-scraper proxy endpoint.
   * Calls `onEvent` for each parsed SSE event, `onDone` when stream ends.
   * Returns an AbortController the caller can use to cancel the stream.
   */
  async streamSSE(
    path: string,
    payload: Record<string, unknown>,
    onEvent: (event: string, data: unknown) => void,
    onDone?: () => void,
    onError?: (err: Error) => void,
  ): Promise<AbortController> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const controller = new AbortController();
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };

    const run = async () => {
      try {
        const resp = await fetch(`${this.baseUrl}${path}`, {
          method: "POST",
          headers,
          body: JSON.stringify(payload),
          signal: controller.signal,
        });
        if (!resp.ok) throw new Error(`SSE stream failed: ${resp.status}`);
        const reader = resp.body?.getReader();
        if (!reader) throw new Error("No response body");

        const decoder = new TextDecoder();
        let buffer = "";
        let currentEvent = "message";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            if (line.startsWith("event:")) {
              currentEvent = line.slice(6).trim();
            } else if (line.startsWith("data:")) {
              const raw = line.slice(5).trim();
              try {
                const parsed = JSON.parse(raw);
                onEvent(currentEvent, parsed);
              } catch {
                onEvent(currentEvent, raw);
              }
              currentEvent = "message";
            }
            // Ignore empty lines and comments
          }
        }
        onDone?.();
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        onError?.(err instanceof Error ? err : new Error(String(err)));
      }
    };

    run();
    return controller;
  }

  /** Stream scrape results via SSE. */
  scrapeRemotelyStream(
    urls: string[],
    options: Record<string, unknown> | undefined,
    onEvent: (event: string, data: unknown) => void,
    onDone?: () => void,
    onError?: (err: Error) => void,
  ): Promise<AbortController> {
    return this.streamSSE(
      "/remote-scraper/scrape/stream",
      { urls, options: options ?? {} },
      onEvent,
      onDone,
      onError,
    );
  }

  // ---- Remote search & research ----

  /** Search via Brave Search API on the remote server. */
  async remoteSearch(
    keywords: string[],
    count = 20,
    country = "US",
  ): Promise<Record<string, unknown>> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    const resp = await fetch(`${this.baseUrl}/remote-scraper/search`, {
      method: "POST",
      headers,
      body: JSON.stringify({ keywords, count, country }),
    });
    if (!resp.ok) throw new Error(`Remote search failed: ${resp.status}`);
    return resp.json();
  }

  /** Search then scrape top results. Results are stored server-side immediately. */
  async remoteSearchAndScrape(
    keywords: string[],
    totalResultsPerKeyword = 10,
    options?: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    const resp = await fetch(
      `${this.baseUrl}/remote-scraper/search-and-scrape`,
      {
        method: "POST",
        headers,
        body: JSON.stringify({
          keywords,
          total_results_per_keyword: totalResultsPerKeyword,
          options: options ?? {},
        }),
      },
    );
    if (!resp.ok)
      throw new Error(`Remote search-and-scrape failed: ${resp.status}`);
    return resp.json();
  }

  /** Stream search + scrape results via SSE. */
  remoteSearchAndScrapeStream(
    keywords: string[],
    totalResultsPerKeyword = 10,
    options: Record<string, unknown> | undefined,
    onEvent: (event: string, data: unknown) => void,
    onDone?: () => void,
    onError?: (err: Error) => void,
  ): Promise<AbortController> {
    return this.streamSSE(
      "/remote-scraper/search-and-scrape/stream",
      {
        keywords,
        total_results_per_keyword: totalResultsPerKeyword,
        options: options ?? {},
      },
      onEvent,
      onDone,
      onError,
    );
  }

  /** Deep research — iterative search + scrape + compile. */
  async remoteResearch(
    query: string,
    effort = "extreme",
    country = "US",
  ): Promise<Record<string, unknown>> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    const resp = await fetch(`${this.baseUrl}/remote-scraper/research`, {
      method: "POST",
      headers,
      body: JSON.stringify({ query, effort, country }),
    });
    if (!resp.ok) throw new Error(`Remote research failed: ${resp.status}`);
    return resp.json();
  }

  /** Stream deep research results via SSE. */
  remoteResearchStream(
    query: string,
    effort = "extreme",
    country = "US",
    onEvent: (event: string, data: unknown) => void,
    onDone?: () => void,
    onError?: (err: Error) => void,
  ): Promise<AbortController> {
    return this.streamSSE(
      "/remote-scraper/research/stream",
      { query, effort, country },
      onEvent,
      onDone,
      onError,
    );
  }

  // ---- Content save-back ----

  /**
   * Save locally-scraped content to the server database immediately.
   * Call this after every successful local scrape so the web app and
   * all other devices see the result instantly.
   */
  async saveContent(
    url: string,
    content: Record<string, unknown>,
    contentType = "html",
    charCount?: number,
    ttlDays = 30,
  ): Promise<{
    status: string;
    page_name: string;
    url: string;
    domain: string;
    char_count: number;
  }> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    const resp = await fetch(`${this.baseUrl}/remote-scraper/content/save`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        url,
        content,
        content_type: contentType,
        char_count: charCount,
        ttl_days: ttlDays,
      }),
    });
    if (!resp.ok) throw new Error(`Content save failed: ${resp.status}`);
    return resp.json();
  }

  // ---- Retry queue ----

  /** Get URLs that failed on the server and need local retry. */
  async queuePending(
    tier: "desktop" | "extension" = "desktop",
    limit = 10,
  ): Promise<{
    items: Array<{
      id: string;
      target_url: string;
      domain_name: string;
      failure_reason: string;
      attempt_count: number;
    }>;
  }> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(
      `${this.baseUrl}/remote-scraper/queue/pending?tier=${tier}&limit=${limit}`,
      { headers },
    );
    if (!resp.ok) throw new Error(`Queue pending failed: ${resp.status}`);
    return resp.json();
  }

  /** Retry queue statistics from the remote server. */
  async queueStats(): Promise<Record<string, unknown>> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/remote-scraper/queue/stats`, {
      headers,
    });
    if (!resp.ok) throw new Error(`Queue stats failed: ${resp.status}`);
    return resp.json();
  }

  /** Local retry queue poller statistics (this engine's activity). */
  async queuePollerStats(): Promise<{
    polled: number;
    claimed: number;
    submitted: number;
    failed: number;
    running: boolean;
    client_id: string;
  }> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(
      `${this.baseUrl}/remote-scraper/queue/poller-stats`,
      { headers },
    );
    if (!resp.ok) throw new Error(`Queue poller stats failed: ${resp.status}`);
    return resp.json();
  }

  /** Subscribe to engine events. */
  on(event: string, callback: (data: unknown) => void): () => void {
    if (!this.eventListeners.has(event)) {
      this.eventListeners.set(event, new Set());
    }
    this.eventListeners.get(event)!.add(callback);
    return () => this.eventListeners.get(event)?.delete(callback);
  }

  private emit(event: string, data: unknown) {
    this.eventListeners.get(event)?.forEach((cb) => cb(data));
  }

  private reconnectDelay = 3000;
  private readonly MAX_RECONNECT_DELAY = 60000;

  private scheduleReconnect() {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(async () => {
      this.reconnectTimer = null;
      // Don't attempt reconnect if there's no token — the server will
      // reject with 403 and we'd loop forever. Wait for auth state changes
      // to trigger a reconnect via connectWebSocket() instead.
      const token = this._getAccessToken ? await this._getAccessToken() : null;
      if (!token) {
        this.reconnectDelay = 3000; // reset backoff
        return;
      }
      // The socket dropped — the engine may have moved ports (a rogue instance
      // overwrote discovery, or our child rebound). Re-point baseUrl/wsUrl at
      // the live (preferably Rust-owned) engine before reconnecting, so the WS
      // and every subsequent REST call target the correct origin.
      await this.rediscover().catch(() => null);
      try {
        await this.connectWebSocket();
        this.reconnectDelay = 3000; // reset on success
      } catch {
        this.reconnectDelay = Math.min(
          this.reconnectDelay * 2,
          this.MAX_RECONNECT_DELAY,
        );
        this.scheduleReconnect();
      }
    }, this.reconnectDelay);
  }

  /** Reject and clear every in-flight WS request. */
  private rejectAllPending(reason: string) {
    const pending = Array.from(this.pendingRequests.values());
    this.pendingRequests.clear();
    for (const p of pending) {
      p.reject(new Error(reason));
    }
  }

  /** Disconnect and clean up. */
  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.rejectAllPending("WebSocket disconnected");
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  get engineUrl(): string | null {
    return this.baseUrl;
  }

  // ---- Proxy API ----

  /** Get proxy server status. */
  async proxyStatus(): Promise<ProxyStatus> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/proxy/status`, { headers });
    if (!resp.ok) throw new Error(`Proxy status failed: ${resp.status}`);
    return resp.json();
  }

  /** Start the proxy server. */
  async proxyStart(port = 0): Promise<ProxyStatus> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    const resp = await fetch(`${this.baseUrl}/proxy/start`, {
      method: "POST",
      headers,
      body: JSON.stringify({ port }),
    });
    if (!resp.ok) throw new Error(`Proxy start failed: ${resp.status}`);
    return resp.json();
  }

  /** Stop the proxy server. */
  async proxyStop(): Promise<void> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    await fetch(`${this.baseUrl}/proxy/stop`, { method: "POST", headers });
  }

  /** Test proxy connectivity. */
  async proxyTest(): Promise<ProxyTestResult> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    const resp = await fetch(`${this.baseUrl}/proxy/test`, {
      method: "POST",
      headers,
    });
    if (!resp.ok) throw new Error(`Proxy test failed: ${resp.status}`);
    return resp.json();
  }

  // ---- Cloud Sync API ----

  /** Configure cloud sync with user credentials. */
  async configureCloudSync(
    jwt: string,
    userId: string,
  ): Promise<CloudConfigResult> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    const resp = await fetch(`${this.baseUrl}/cloud/configure`, {
      method: "POST",
      headers,
      body: JSON.stringify({ jwt, user_id: userId }),
    });
    if (!resp.ok) throw new Error(`Cloud configure failed: ${resp.status}`);
    return resp.json();
  }

  /** Reconfigure cloud sync with fresh JWT. */
  async reconfigureCloudSync(jwt: string, userId: string): Promise<void> {
    if (!this.baseUrl) return;
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    await fetch(`${this.baseUrl}/cloud/reconfigure`, {
      method: "POST",
      headers,
      body: JSON.stringify({ jwt, user_id: userId }),
    }).catch((e) => console.warn("[api] reconfigureCloudSync failed:", e));
  }

  /** Get cloud-synced settings. */
  async getCloudSettings(): Promise<CloudSettingsResponse> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/cloud/settings`, { headers });
    if (!resp.ok) throw new Error(`Cloud settings failed: ${resp.status}`);
    return resp.json();
  }

  /** Update cloud-synced settings. */
  async updateCloudSettings(
    settings: Record<string, unknown>,
  ): Promise<CloudSettingsResponse> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    const resp = await fetch(`${this.baseUrl}/cloud/settings`, {
      method: "PUT",
      headers,
      body: JSON.stringify({ settings }),
    });
    if (!resp.ok)
      throw new Error(`Cloud settings update failed: ${resp.status}`);
    return resp.json();
  }

  /** Trigger a bidirectional sync. */
  async triggerCloudSync(): Promise<CloudSyncResult> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    const resp = await fetch(`${this.baseUrl}/cloud/sync`, {
      method: "POST",
      headers,
    });
    if (!resp.ok) throw new Error(`Cloud sync failed: ${resp.status}`);
    return resp.json();
  }

  /** Force push local settings to cloud. */
  async pushCloudSettings(): Promise<CloudSyncResult> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    const resp = await fetch(`${this.baseUrl}/cloud/sync/push`, {
      method: "POST",
      headers,
    });
    if (!resp.ok) throw new Error(`Cloud push failed: ${resp.status}`);
    return resp.json();
  }

  /** Force pull cloud settings to local. */
  async pullCloudSettings(): Promise<CloudSyncResult> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    const resp = await fetch(`${this.baseUrl}/cloud/sync/pull`, {
      method: "POST",
      headers,
    });
    if (!resp.ok) throw new Error(`Cloud pull failed: ${resp.status}`);
    return resp.json();
  }

  /** Get this instance's info. */
  async getInstanceInfo(): Promise<InstanceInfo> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/cloud/instance`, { headers });
    if (!resp.ok) throw new Error(`Instance info failed: ${resp.status}`);
    return resp.json();
  }

  /** List all registered instances for the current user. */
  async listInstances(): Promise<{ instances: InstanceInfo[] }> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/cloud/instances`, { headers });
    if (!resp.ok) throw new Error(`List instances failed: ${resp.status}`);
    return resp.json();
  }

  // ---- Generic HTTP helpers ----

  /**
   * Default per-request ceiling for the generic JSON helpers. A hung engine
   * fetch (never resolves, never rejects) would otherwise leave a caller's
   * catch-block waiting forever. Callers that legitimately need longer (tunnel
   * start, bulk ops) pass their own `signal` — a caller-provided signal
   * REPLACES this default rather than stacking on top of it.
   *
   * Note: streaming/SSE paths (owwDownloadModelStream, runTranscriptionInstall,
   * streamLogs, chat, tts) use raw fetch() and do NOT route through request(),
   * so this timeout never truncates a long-lived stream.
   */
  private static readonly DEFAULT_REQUEST_TIMEOUT_MS = 60_000;

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const authHdrs = await this.authHeaders();
    // A caller-supplied signal takes over timeout responsibility entirely;
    // otherwise apply the default timeout ceiling.
    const timeoutMs = EngineAPI.DEFAULT_REQUEST_TIMEOUT_MS;
    const signal =
      init?.signal ??
      (typeof AbortSignal !== "undefined" && "timeout" in AbortSignal
        ? AbortSignal.timeout(timeoutMs)
        : undefined);
    let resp: Response;
    try {
      resp = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        signal: signal ?? null,
        headers: {
          ...authHdrs,
          ...(init?.headers as Record<string, string> | undefined),
        },
      });
    } catch (e) {
      // A default-timeout abort surfaces as a TimeoutError; turn it into an
      // actionable message. A caller-signal abort is re-thrown untouched so
      // the caller can distinguish its own cancellation.
      if (
        !init?.signal &&
        e instanceof DOMException &&
        (e.name === "TimeoutError" || e.name === "AbortError")
      ) {
        throw new Error(
          `Engine request timed out after ${timeoutMs / 1000}s: ${path}`,
        );
      }
      throw e;
    }
    if (!resp.ok)
      throw new Error(
        `${init?.method ?? "GET"} ${path} failed: ${resp.status}`,
      );
    return resp.json();
  }

  async get(
    path: string,
    init?: Pick<RequestInit, "signal">,
  ): Promise<unknown> {
    return this.request(path, init);
  }

  async post(
    path: string,
    body: unknown,
    // Optional extra RequestInit — only `signal` (and any future opt-in field)
    // is honored; method/body/Content-Type are fixed by this method. Additive
    // and backward-compatible: existing 2-arg callers are unaffected. Lets a
    // one-shot caller attach an AbortController for timeout/cancel without
    // changing global post() behavior.
    init?: Pick<RequestInit, "signal">,
  ): Promise<unknown> {
    return this.request(path, {
      ...init,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  async put(
    path: string,
    body: unknown,
    init?: Pick<RequestInit, "signal">,
  ): Promise<unknown> {
    return this.request(path, {
      ...init,
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  async delete(
    path: string,
    init?: Pick<RequestInit, "signal">,
  ): Promise<unknown> {
    return this.request(path, { ...init, method: "DELETE" });
  }

  // ---- File sync (desktop replica of the matrx-files cloud tree) ----

  /** Get file-sync engine status (mode, counts, cursor, last cycle). */
  async fileSyncStatus(): Promise<FileSyncStatus> {
    return this.request("/file-sync/status");
  }

  /** Run one sync cycle now and return its summary. */
  async fileSyncNow(): Promise<FileSyncCycleSummary> {
    return this.request("/file-sync/sync", { method: "POST" });
  }

  /** Fetch real bytes for a pointer file (rel path or cloud file id). */
  async fileSyncHydrate(path: string): Promise<{ path: string }> {
    return this.request("/file-sync/hydrate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
  }

  /** List open file-sync conflicts. */
  async fileSyncConflicts(): Promise<{ conflicts: FileSyncConflict[] }> {
    return this.request("/file-sync/conflicts");
  }

  /** Resolve a file-sync conflict by keeping one side. */
  async fileSyncResolveConflict(
    fileId: string,
    resolution: "keep_local" | "keep_remote",
  ): Promise<unknown> {
    return this.request(`/file-sync/conflicts/${fileId}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resolution }),
    });
  }

  /** Change the file-sync mode (off | pointers | full). */
  async fileSyncSetMode(mode: FileSyncMode): Promise<{ mode: FileSyncMode }> {
    return this.request("/file-sync/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
  }

  /**
   * Hugging Face token stored like other API keys (SQLite). Exposed only for
   * the Tauri GGUF downloader; returns null if unset or engine unavailable.
   */
  async getHuggingfaceTokenForDownloads(): Promise<string | null> {
    if (!this.baseUrl) return null;
    try {
      const authHdrs = await this.authHeaders();
      const resp = await fetch(
        `${this.baseUrl}/settings/api-keys/huggingface/value`,
        {
          headers: authHdrs,
        },
      );
      if (resp.status === 404) return null;
      if (!resp.ok) return null;
      const data = (await resp.json()) as { key: string };
      const k = data.key?.trim();
      return k || null;
    } catch {
      return null;
    }
  }

  /**
   * Ask the engine to open a browser and automate Hugging Face token acquisition.
   * Always resolves (never throws). Returns:
   *   { status: "token_ready", token: "hf_..." } — token extracted, auto-fill it
   *   { status: "opened" }                        — browser opened, user may need to interact
   *   { status: "manual" }                        — Playwright unavailable, show manual steps
   */
  async hfTokenAssist(
    hasAccount: boolean,
  ): Promise<{ status: "token_ready" | "opened" | "manual"; token?: string }> {
    if (!this.baseUrl) return { status: "manual" };
    try {
      const resp = (await this.post("/hf-token/assist", {
        has_account: hasAccount,
      })) as { status: string; token?: string };
      return {
        status:
          (resp.status as "token_ready" | "opened" | "manual") ?? "manual",
        ...(resp.token !== undefined ? { token: resp.token } : {}),
      };
    } catch {
      return { status: "manual" };
    }
  }

  /** Update instance display name. */
  async updateInstanceName(name: string): Promise<void> {
    if (!this.baseUrl) return;
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    await fetch(`${this.baseUrl}/cloud/instance/name`, {
      method: "PUT",
      headers,
      body: JSON.stringify({ name }),
    });
  }

  /** Send heartbeat for this instance. */
  async cloudHeartbeat(): Promise<void> {
    if (!this.baseUrl) return;
    const headers = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    await fetch(`${this.baseUrl}/cloud/heartbeat`, {
      method: "POST",
      headers,
    }).catch((e) => console.warn("[api] cloudHeartbeat failed:", e));
  }

  /**
   * Push the current Supabase JWT to Python so it persists in SQLite across restarts.
   * Called automatically on every auth state change (login, token refresh).
   * Python reads this on startup so it can make authenticated API calls without
   * waiting for React to boot.
   */
  async syncTokenToPython(
    accessToken: string,
    userId: string,
    refreshToken?: string,
    expiresIn?: number,
  ): Promise<void> {
    if (!this.baseUrl) return;
    await fetch(`${this.baseUrl}/auth/token`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        access_token: accessToken,
        refresh_token: refreshToken ?? null,
        user_id: userId,
        expires_in: expiresIn ?? null,
      }),
    }).catch(() => {
      // Non-critical — Python will work without the persisted token,
      // it just won't survive a restart until React pushes again.
    });
  }

  /** Clear the stored JWT on logout. */
  async clearPythonToken(): Promise<void> {
    if (!this.baseUrl) return;
    await fetch(`${this.baseUrl}/auth/token`, { method: "DELETE" }).catch(
      () => {},
    );
  }

  // ---- Documents API ----

  private async docRequest<T>(
    method: string,
    path: string,
    body?: unknown,
    userId?: string,
    timeoutMs = 15_000,
  ): Promise<T> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(await this.authHeaders()),
    };
    if (userId) headers["X-User-Id"] = userId;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    let resp: Response;
    try {
      resp = await fetch(`${this.baseUrl}/notes${path}`, {
        method,
        headers,
        body: body ? JSON.stringify(body) : null,
        signal: controller.signal,
      });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        throw new Error(`Documents API timed out after ${timeoutMs / 1000}s`);
      }
      throw err;
    } finally {
      clearTimeout(timer);
    }

    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`Documents API error (${resp.status}): ${text}`);
    }
    // DELETE responses return JSON but guard against 204 No Content.
    if (resp.status === 204) return undefined as T;
    return resp.json();
  }

  /** Get folder tree with note counts. */
  async getDocTree(userId: string): Promise<DocTree> {
    return this.docRequest("GET", "/tree", undefined, userId);
  }

  /** List notes, optionally filtered by folder or search. */
  async listNotes(
    userId: string,
    opts?: { folder_id?: string; search?: string },
  ): Promise<DocNote[]> {
    const params = new URLSearchParams();
    if (opts?.folder_id) params.set("folder_id", opts.folder_id);
    if (opts?.search) params.set("search", opts.search);
    const qs = params.toString();
    return this.docRequest(
      "GET",
      `/notes${qs ? `?${qs}` : ""}`,
      undefined,
      userId,
    );
  }

  /** Get a single note with full content. */
  async getNote(noteId: string, userId: string): Promise<DocNote> {
    return this.docRequest("GET", `/notes/${noteId}`, undefined, userId);
  }

  /** Create a new note. */
  async createNote(userId: string, data: CreateNoteData): Promise<DocNote> {
    return this.docRequest("POST", "/notes", data, userId);
  }

  /** Update a note. */
  async updateNote(
    noteId: string,
    userId: string,
    data: Partial<CreateNoteData>,
  ): Promise<DocNote> {
    return this.docRequest("PUT", `/notes/${noteId}`, data, userId);
  }

  /** Delete a note (soft delete). */
  async deleteNote(noteId: string, userId: string): Promise<void> {
    await this.docRequest("DELETE", `/notes/${noteId}`, undefined, userId);
  }

  /** Create a folder. */
  async createFolder(
    userId: string,
    data: { name: string; parent_id?: string },
  ): Promise<DocFolder> {
    return this.docRequest("POST", "/folders", data, userId);
  }

  /** Update a folder. */
  async updateFolder(
    folderId: string,
    userId: string,
    data: Partial<{
      name: string;
      parent_id: string;
      path: string;
      position: number;
    }>,
  ): Promise<DocFolder> {
    return this.docRequest("PUT", `/folders/${folderId}`, data, userId);
  }

  /** Delete a folder (soft delete). */
  async deleteFolder(folderId: string, userId: string): Promise<void> {
    await this.docRequest("DELETE", `/folders/${folderId}`, undefined, userId);
  }

  /** Get version history for a note. */
  async listVersions(noteId: string, userId: string): Promise<DocVersion[]> {
    return this.docRequest(
      "GET",
      `/notes/${noteId}/versions`,
      undefined,
      userId,
    );
  }

  /** Revert a note to a specific version. */
  async revertNote(
    noteId: string,
    userId: string,
    versionNumber: number,
  ): Promise<DocNote> {
    return this.docRequest(
      "POST",
      `/notes/${noteId}/revert`,
      { version_number: versionNumber },
      userId,
    );
  }

  /** Get sync status. */
  async getSyncStatus(userId: string): Promise<SyncStatus> {
    return this.docRequest("GET", "/sync/status", undefined, userId);
  }

  /**
   * Canonical filesystem access-health snapshot (all registered resources:
   * notes, mapped dirs, files replica). Cheap — in-memory evidence, no
   * filesystem probe. Works signed-out; the resources are local.
   */
  async getAccessHealth(): Promise<AccessHealth> {
    return this.request("/access/health");
  }

  /**
   * Actively re-probe access ("Check again"). Runs the engine's capability
   * probe (enumerate/create/write/replace/delete with a disposable probe
   * file) and clears stale degraded state the moment access is restored.
   * Pass `createMissing: true` for the "Create folder" action.
   */
  async recheckAccess(opts?: {
    resourceIds?: string[];
    createMissing?: boolean;
  }): Promise<AccessHealth> {
    return this.request("/access/recheck", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        resource_ids: opts?.resourceIds ?? null,
        create_missing: opts?.createMissing ?? false,
      }),
    });
  }

  /**
   * Clear ALL access evidence and re-probe everything. The backend behind
   * every user-facing "reset" that could involve access state.
   */
  async resetAccessHealth(): Promise<AccessHealth> {
    return this.request("/access/reset", { method: "POST" });
  }

  /** Trigger a sync. Mode: "push" | "pull" | "bidirectional" */
  async triggerSync(
    userId: string,
    mode: "push" | "pull" | "bidirectional" = "bidirectional",
  ): Promise<SyncResult> {
    return this.docRequest("POST", "/sync/trigger", { mode }, userId);
  }

  /** Pull incremental changes. */
  async pullChanges(userId: string): Promise<SyncResult> {
    return this.docRequest("POST", "/sync/pull", undefined, userId);
  }

  /** Pull a single note (after Realtime notification). */
  async pullNote(noteId: string, userId: string): Promise<DocNote> {
    return this.docRequest(
      "POST",
      "/sync/pull-note",
      { note_id: noteId },
      userId,
    );
  }

  /** Register this device for sync. */
  async registerDevice(userId: string): Promise<unknown> {
    return this.docRequest("POST", "/sync/register-device", undefined, userId);
  }

  /** Start the file watcher. */
  async startDocWatcher(userId: string): Promise<void> {
    await this.docRequest("POST", "/sync/start-watcher", undefined, userId);
  }

  /** Stop the file watcher. */
  async stopDocWatcher(userId: string): Promise<void> {
    await this.docRequest("POST", "/sync/stop-watcher", undefined, userId);
  }

  /** List conflicts. */
  async listConflicts(
    userId: string,
  ): Promise<{ conflicts: string[]; count: number }> {
    return this.docRequest("GET", "/conflicts", undefined, userId);
  }

  /** Resolve a conflict. */
  async resolveConflict(
    noteId: string,
    userId: string,
    resolution:
      | "keep_local"
      | "keep_remote"
      | "merge"
      | "append"
      | "split"
      | "exclude",
    mergedContent?: string,
  ): Promise<void> {
    await this.docRequest(
      "POST",
      `/conflicts/${noteId}/resolve`,
      { resolution, merged_content: mergedContent },
      userId,
    );
  }

  /** Get conflict details with both versions' content. */
  async getConflicts(userId: string): Promise<ConflictList> {
    return this.docRequest("GET", "/conflicts", undefined, userId);
  }

  /** Exclude a note from sync. */
  async setNoteExcluded(
    noteId: string,
    userId: string,
    excluded: boolean,
  ): Promise<void> {
    await this.docRequest(
      "POST",
      `/notes/${noteId}/exclude`,
      { excluded },
      userId,
    );
  }

  // Note-share client methods removed 2026-07-13 — the cloud note_shares
  // table was retired; the backend /shares routes return 501 until sharing
  // returns via the platform iam permission system.

  /** List directory mappings. */
  async listMappings(userId: string): Promise<DocMappings> {
    return this.docRequest("GET", "/mappings", undefined, userId);
  }

  /** Create a directory mapping. */
  async createMapping(
    userId: string,
    data: { folder_id: string; local_path: string },
  ): Promise<unknown> {
    return this.docRequest("POST", "/mappings", data, userId);
  }

  /** Delete a directory mapping. */
  async deleteMapping(
    mappingId: string,
    userId: string,
    folderId?: string,
    localPath?: string,
  ): Promise<void> {
    const params = new URLSearchParams();
    if (folderId) params.set("folder_id", folderId);
    if (localPath) params.set("local_path", localPath);
    const qs = params.toString();
    await this.docRequest(
      "DELETE",
      `/mappings/${mappingId}${qs ? `?${qs}` : ""}`,
      undefined,
      userId,
    );
  }

  // ---- Device & Permission API ----

  /**
   * Get all device/OS permission statuses.
   *
   * The engine caches the response for 30 s, so the typical hit is
   * sub-millisecond. A cold call (every 30 s, plus the very first one)
   * triggers ``check_all_permissions`` which probes 15 OS surfaces —
   * macOS ``system_profiler`` invocations alone can take 15–25 s on a
   * cold daemon, so the timeout is generous enough to cover the worst
   * case without aborting in production.
   *
   * @param forceRefresh - bypass the engine's TTL cache and re-probe.
   *   Use this from "Refresh" affordances; default to ``false`` for
   *   automatic page mounts so they hit the cache.
   */
  async getDevicePermissions(
    forceRefresh: boolean = false,
  ): Promise<DevicePermissionsResponse> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const url = new URL(`${this.baseUrl}/devices/permissions`);
    if (forceRefresh) url.searchParams.set("force_refresh", "true");
    const resp = await fetch(url.toString(), {
      headers,
      signal: AbortSignal.timeout(35000),
    });
    if (!resp.ok) throw new Error(`Permissions check failed: ${resp.status}`);
    return resp.json();
  }

  /** Get a single permission status. */
  async getDevicePermission(name: string): Promise<PermissionInfo> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/devices/permissions/${name}`, {
      headers,
      signal: AbortSignal.timeout(10000),
    });
    if (!resp.ok) throw new Error(`Permission check failed: ${resp.status}`);
    return resp.json();
  }

  /** List audio input/output devices. */
  async getAudioDevices(): Promise<DeviceProbeResult> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/devices/audio`, {
      headers,
      signal: AbortSignal.timeout(10000),
    });
    if (!resp.ok) throw new Error(`Audio device check failed: ${resp.status}`);
    return resp.json();
  }

  /** List Bluetooth devices. */
  async getBluetoothDevices(): Promise<DeviceProbeResult> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/devices/bluetooth`, {
      headers,
      signal: AbortSignal.timeout(15000),
    });
    if (!resp.ok) throw new Error(`Bluetooth check failed: ${resp.status}`);
    return resp.json();
  }

  /** List WiFi networks. */
  async getWifiNetworks(): Promise<DeviceProbeResult> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/devices/wifi`, {
      headers,
      signal: AbortSignal.timeout(15000),
    });
    if (!resp.ok) throw new Error(`WiFi scan failed: ${resp.status}`);
    return resp.json();
  }

  /** Get network interface info. */
  async getNetworkInfo(): Promise<DeviceProbeResult> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/devices/network`, {
      headers,
      signal: AbortSignal.timeout(10000),
    });
    if (!resp.ok) throw new Error(`Network info failed: ${resp.status}`);
    return resp.json();
  }

  /** List connected peripherals (USB, Bluetooth, etc.). */
  async getConnectedDevices(): Promise<DeviceProbeResult> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/devices/connected`, {
      headers,
      signal: AbortSignal.timeout(10000),
    });
    if (!resp.ok)
      throw new Error(`Connected devices check failed: ${resp.status}`);
    return resp.json();
  }

  /**
   * Fetch all named path aliases as resolved absolute paths from the engine.
   *
   * Use this instead of ever constructing paths in React or any remote caller.
   * The engine knows the user's OS, drive letter, and configuration — React does not.
   *
   * Example usage:
   *   const paths = await engine.getPaths();
   *   engine.invokeTool("Read", { file_path: paths.resolved.settings });
   *
   * Or use the alias directly in tool calls (engine resolves it):
   *   engine.invokeTool("Read", { file_path: "@matrx/local.json" });
   */
  async getPaths(): Promise<EnginePaths> {
    return this.request<EnginePaths>("/system/paths");
  }

  async getFilesystemPlaces(): Promise<FilesystemPlacesResponse> {
    return this.request<FilesystemPlacesResponse>("/filesystem/places");
  }

  async getFilesystemIndexingSettings(): Promise<FilesystemIndexingSettings> {
    return this.request<FilesystemIndexingSettings>("/filesystem/indexing-settings");
  }

  async setFilesystemPriorityRoots(
    roots: FilesystemPriorityRoot[],
  ): Promise<FilesystemPlacesResponse> {
    return this.request<FilesystemPlacesResponse>("/filesystem/priority-roots", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ roots }),
    });
  }

  async getFilesystemIndexStatus(): Promise<FilesystemIndexStatus> {
    return this.request<FilesystemIndexStatus>("/filesystem/status");
  }

  async listFilesystem(
    path: string,
    options: { cursor?: string; limit?: number; showHidden?: boolean } = {},
  ): Promise<FilesystemPageResponse> {
    const params = new URLSearchParams({ path });
    if (options.cursor) params.set("cursor", options.cursor);
    if (options.limit != null) params.set("limit", String(options.limit));
    if (options.showHidden) params.set("show_hidden", "true");
    return this.request<FilesystemPageResponse>(`/filesystem/list?${params}`);
  }

  async findFilesystem(
    query: string,
    options: { root?: string; cursor?: string; limit?: number } = {},
  ): Promise<FilesystemPageResponse> {
    const params = new URLSearchParams({ query });
    if (options.root) params.set("root", options.root);
    if (options.cursor) params.set("cursor", options.cursor);
    if (options.limit != null) params.set("limit", String(options.limit));
    return this.request<FilesystemPageResponse>(`/filesystem/find?${params}`);
  }

  /** Open a system folder (logs or data) in the file manager. */
  async openSystemFolder(folder: "logs" | "data"): Promise<{ opened: string }> {
    return this.request<{ opened: string }>("/system/open-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder }),
    });
  }

  async getCapabilities(): Promise<CapabilitiesResponse> {
    return this.request<CapabilitiesResponse>("/capabilities");
  }

  /**
   * Start a capability install. Heavy caps (Whisper) return immediately with
   * `async_install: true` — follow with `streamCapabilityInstall` for progress.
   * Light caps complete synchronously (still frozen-safe on the engine).
   */
  async installCapability(
    capabilityId: string,
  ): Promise<CapabilityInstallStatus> {
    // Light sync installs (playwright browsers) can exceed 60s; heavy ones
    // return immediately so this timeout only covers the kickoff / light path.
    return this.request<CapabilityInstallStatus>("/capabilities/install", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ capability_id: capabilityId }),
      signal: AbortSignal.timeout(600_000),
    });
  }

  async getCapabilityInstallStatus(
    capabilityId: string,
  ): Promise<CapabilityInstallStatus> {
    return this.request<CapabilityInstallStatus>(
      `/capabilities/install/status?capability_id=${encodeURIComponent(capabilityId)}`,
      { signal: AbortSignal.timeout(15_000) },
    );
  }

  /**
   * SSE progress for managed capability installs (Whisper). Returns cleanup.
   */
  streamCapabilityInstall(
    capabilityId: string,
    onEvent: (e: CapabilityInstallStatus) => void,
  ): () => void {
    let closed = false;
    let es: EventSource | null = null;
    let sawTerminal = false;

    const connect = async () => {
      if (!this.baseUrl) return;
      const token = await this.getAccessToken();
      const qs = new URLSearchParams({ capability_id: capabilityId });
      if (token) qs.set("token", token);
      const url = `${this.baseUrl}/capabilities/install/stream?${qs}`;
      es = new EventSource(url);
      es.onmessage = (ev) => {
        if (closed) return;
        try {
          const data = JSON.parse(ev.data) as CapabilityInstallStatus;
          if (data.status === "complete" || data.status === "error") {
            sawTerminal = true;
          }
          onEvent(data);
          if (data.status === "complete" || data.status === "error") {
            es?.close();
          }
        } catch {
          // ignore parse errors
        }
      };
      es.onerror = () => {
        if (closed) return;
        es?.close();
        if (!sawTerminal) {
          onEvent({
            status: "error",
            capability_id: capabilityId,
            stage: "error",
            percent: 0,
            message:
              "Lost connection to the engine during installation. Check Engine Monitor, then try again.",
            error:
              "Lost connection to the engine during installation. Check Engine Monitor, then try again.",
          });
        }
      };
    };

    void connect();
    return () => {
      closed = true;
      es?.close();
    };
  }

  async getSystemResources(): Promise<DeviceProbeResult> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/devices/system`, {
      headers,
      signal: AbortSignal.timeout(10000),
    });
    if (!resp.ok)
      throw new Error(`System resources check failed: ${resp.status}`);
    return resp.json();
  }

  /** List cameras. */
  async getCameraDevices(): Promise<DeviceProbeResult> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/devices/camera`, {
      headers,
      signal: AbortSignal.timeout(15000),
    });
    if (!resp.ok) throw new Error(`Camera probe failed: ${resp.status}`);
    return resp.json();
  }

  /** List all connected screens/monitors. */
  async getScreens(): Promise<DeviceProbeResult> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/devices/screens`, {
      headers,
      signal: AbortSignal.timeout(10000),
    });
    if (!resp.ok) throw new Error(`Screens probe failed: ${resp.status}`);
    return resp.json();
  }

  /** Take a screenshot (optionally for a specific monitor index or "all"/"primary"). */
  async takeScreenshot(
    monitor: string | number = "all",
  ): Promise<DeviceProbeResult> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(
      `${this.baseUrl}/devices/screenshot?monitor=${encodeURIComponent(String(monitor))}`,
      {
        headers,
        signal: AbortSignal.timeout(15000),
      },
    );
    if (!resp.ok) throw new Error(`Screenshot failed: ${resp.status}`);
    return resp.json();
  }

  /** Get device location (lat/lon if permission granted). */
  async getLocation(): Promise<DeviceProbeResult> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/devices/location`, {
      headers,
      signal: AbortSignal.timeout(20000),
    });
    if (!resp.ok) throw new Error(`Location probe failed: ${resp.status}`);
    return resp.json();
  }

  /** Record audio from microphone and return base64 WAV. */
  async recordAudio(opts: {
    device_index?: number;
    duration_seconds?: number;
  }): Promise<DeviceProbeResult> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/devices/record-audio`, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify(opts),
      signal: AbortSignal.timeout((opts.duration_seconds ?? 5) * 1000 + 10000),
    });
    if (!resp.ok) throw new Error(`Audio recording failed: ${resp.status}`);
    return resp.json();
  }

  /** Capture a photo from webcam and return base64 JPEG. */
  async capturePhoto(opts: {
    device_index?: number;
  }): Promise<DeviceProbeResult> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/devices/capture-photo`, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify(opts),
      signal: AbortSignal.timeout(15000),
    });
    if (!resp.ok) throw new Error(`Photo capture failed: ${resp.status}`);
    return resp.json();
  }

  /** Record a short video from webcam and return base64 MP4. */
  async recordVideo(opts: {
    device_index?: number;
    duration_seconds?: number;
  }): Promise<DeviceProbeResult> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/devices/record-video`, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify(opts),
      signal: AbortSignal.timeout((opts.duration_seconds ?? 5) * 1000 + 10000),
    });
    if (!resp.ok) throw new Error(`Video recording failed: ${resp.status}`);
    return resp.json();
  }

  /** Record screen video and return base64 MP4. */
  async recordScreen(opts: {
    screen_index?: number;
    duration_seconds?: number;
  }): Promise<DeviceProbeResult> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}/devices/record-screen`, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify(opts),
      signal: AbortSignal.timeout((opts.duration_seconds ?? 5) * 1000 + 30000),
    });
    if (!resp.ok) throw new Error(`Screen recording failed: ${resp.status}`);
    return resp.json();
  }

  // ── Platform context ───────────────────────────────────────────────────

  async getPlatformContext(): Promise<import("./platformCtx").PlatformContext> {
    return this.request<import("./platformCtx").PlatformContext>(
      "/platform/context",
    );
  }

  async refreshPlatformContext(): Promise<
    import("./platformCtx").PlatformContext
  > {
    return this.request<import("./platformCtx").PlatformContext>(
      "/platform/context/refresh",
      {
        method: "POST",
      },
    );
  }

  // ── Setup / First-run ──────────────────────────────────────────────────

  async getSetupStatus(): Promise<SetupStatus> {
    return this.request<SetupStatus>("/setup/status");
  }

  /**
   * Run the setup install and stream progress via SSE.
   * Calls onProgress for each event, onComplete when done.
   */
  async runSetupInstall(callbacks: {
    onProgress: (data: SetupProgressEvent) => void;
    onComplete: (data: SetupCompleteEvent) => void;
    onError: (error: string) => void;
    /** Fired for every total_progress event — drives the grand progress bar */
    onTotalProgress?: (percent: number, message: string) => void;
    /** Raw SSE line callback for full transparency logging */
    onRawLine?: (line: string) => void;
    signal?: AbortSignal;
    mode?: "standard" | "first_run";
  }): Promise<void> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    const mode = callbacks.mode ?? "standard";
    const resp = await fetch(`${this.baseUrl}/setup/install?mode=${mode}`, {
      method: "POST",
      headers,
      signal: callbacks.signal ?? null,
    });
    if (!resp.ok) {
      callbacks.onError(`Setup install failed: ${resp.status}`);
      return;
    }
    const reader = resp.body?.getReader();
    if (!reader) {
      callbacks.onError("No response body");
      return;
    }
    const decoder = new TextDecoder();
    let buffer = "";
    let receivedComplete = false;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      let eventType = "";
      for (const line of lines) {
        callbacks.onRawLine?.(line);
        if (line.startsWith("event: ")) {
          eventType = line.slice(7).trim();
        } else if (line.startsWith("data: ")) {
          try {
            const data = JSON.parse(line.slice(6));
            if (eventType === "progress") callbacks.onProgress(data);
            else if (eventType === "total_progress") {
              if (typeof data.total_percent === "number") {
                callbacks.onTotalProgress?.(
                  data.total_percent as number,
                  (data.message as string) ?? "",
                );
              }
            } else if (eventType === "complete") {
              receivedComplete = true;
              callbacks.onComplete(data as SetupCompleteEvent);
            } else if (eventType === "cancelled") {
              receivedComplete = true;
              callbacks.onError("Setup cancelled");
            } else if (eventType === "started")
              callbacks.onProgress({
                component: "_system",
                status: "installing",
                message: data.message,
                percent: 0,
              });
          } catch {
            /* skip malformed */
          }
          eventType = "";
        }
      }
    }
    if (!receivedComplete) {
      callbacks.onError(
        "Setup stream ended without a completion event — check the debug terminal for details",
      );
    }
  }

  /**
   * Install the transcription model via SSE stream.
   */
  async runTranscriptionInstall(
    model: string,
    callbacks: {
      onProgress: (data: SetupProgressEvent) => void;
      onComplete: (data: {
        message: string;
        had_errors?: boolean;
        errors?: string[];
      }) => void;
      onError: (error: string) => void;
      onRawLine?: (line: string) => void;
      signal?: AbortSignal;
    },
  ): Promise<void> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const headers = await this.authHeaders();
    (headers as Record<string, string>)["Content-Type"] = "application/json";
    const resp = await fetch(`${this.baseUrl}/setup/install-transcription`, {
      method: "POST",
      headers,
      body: JSON.stringify({ model }),
      signal: callbacks.signal ?? null,
    });
    if (!resp.ok) {
      callbacks.onError(`Transcription install failed: ${resp.status}`);
      return;
    }
    const reader = resp.body?.getReader();
    if (!reader) {
      callbacks.onError("No response body");
      return;
    }
    const decoder = new TextDecoder();
    let buffer = "";
    let receivedComplete = false;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      let eventType = "";
      for (const line of lines) {
        callbacks.onRawLine?.(line);
        if (line.startsWith("event: ")) {
          eventType = line.slice(7).trim();
        } else if (line.startsWith("data: ")) {
          try {
            const data = JSON.parse(line.slice(6));
            if (eventType === "progress") callbacks.onProgress(data);
            else if (eventType === "complete") {
              receivedComplete = true;
              callbacks.onComplete(data);
            } else if (eventType === "cancelled") {
              receivedComplete = true;
              callbacks.onError("Install cancelled");
            }
          } catch {
            /* skip malformed */
          }
          eventType = "";
        }
      }
    }
    if (!receivedComplete) {
      callbacks.onError(
        "Transcription install stream ended without completion event",
      );
    }
  }

  /**
   * Stream live log lines from the engine's system.log via SSE.
   *
   * First delivers the last `lines` lines of history, then follows the file
   * in real-time until `signal` is aborted or the stream ends.
   *
   * Each SSE "log" event carries: { line: string; level: string; timestamp: number }
   */
  streamLogs(callbacks: {
    onLine: (data: { line: string; level: string; timestamp: number }) => void;
    onHistoryEnd?: (linesSent: number) => void;
    onConnected?: (logPath: string) => void;
    onError?: (error: string) => void;
    signal?: AbortSignal;
    lines?: number;
  }): () => void {
    if (!this.baseUrl) {
      callbacks.onError?.("Engine not discovered");
      return () => {};
    }
    const url = `${this.baseUrl}/setup/logs?lines=${callbacks.lines ?? 200}`;
    let active = true;

    const run = async () => {
      try {
        const resp = await fetch(url, { signal: callbacks.signal ?? null });
        if (!resp.ok) {
          callbacks.onError?.(`Log stream failed: ${resp.status}`);
          return;
        }
        const reader = resp.body?.getReader();
        if (!reader) {
          callbacks.onError?.("No response body");
          return;
        }
        const decoder = new TextDecoder();
        let buffer = "";
        let eventType = "";

        while (active) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          for (const line of lines) {
            if (line.startsWith("event: ")) {
              eventType = line.slice(7).trim();
            } else if (line.startsWith("data: ")) {
              try {
                const data = JSON.parse(line.slice(6));
                if (eventType === "log") {
                  callbacks.onLine(
                    data as { line: string; level: string; timestamp: number },
                  );
                } else if (eventType === "history_end") {
                  callbacks.onHistoryEnd?.(data.lines_sent ?? 0);
                } else if (eventType === "connected") {
                  callbacks.onConnected?.(data.log_path ?? "");
                }
              } catch {
                /* skip malformed */
              }
              eventType = "";
            }
          }
        }
      } catch (err) {
        if (active) {
          callbacks.onError?.(err instanceof Error ? err.message : String(err));
        }
      }
    };

    run();
    return () => {
      active = false;
    };
  }

  /** Fetch the full diagnostic snapshot from /setup/debug (no auth required). */
  async getDebugState(): Promise<Record<string, unknown>> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const resp = await fetch(`${this.baseUrl}/setup/debug`, {
      signal: AbortSignal.timeout(15000),
    });
    if (!resp.ok) throw new Error(`Debug state failed: ${resp.status}`);
    return resp.json();
  }

  // ── matrx-extend bridge — Bridge Test page helpers ──────────────────────

  /** POST /extension/rpc — synchronous RPC the extension uses for health/version/etc. */
  async extensionRpc(
    command: string,
    args?: Record<string, unknown>,
  ): Promise<ExtensionRpcResponse> {
    return this.post("/extension/rpc", {
      command,
      args: args ?? {},
    }) as Promise<ExtensionRpcResponse>;
  }

  /**
   * GET /extension/pair — the engine-issued pairing token.
   *
   * Loopback-only on the engine side (tunnel requests are hard-rejected),
   * which is fine here: the desktop UI always talks to the engine over
   * loopback. Shown in the Bridge Test panel so the user can manually pair
   * a REMOTE browser's extension; same-machine extensions auto-pair via
   * this endpoint without any user action.
   */
  async extensionGetPairInfo(): Promise<ExtensionPairInfo> {
    return this.request("/extension/pair");
  }

  /** GET /extension/sessions — list every active extension WS session. */
  async extensionListSessions(): Promise<{
    sessions: ExtensionSessionInfo[];
    count: number;
  }> {
    return this.request("/extension/sessions");
  }

  /** POST /extension/sessions/disconnect — close a session by id. */
  async extensionDisconnectSession(
    session_id: string,
    reason?: string,
  ): Promise<{ ok: boolean; found: boolean }> {
    return this.post("/extension/sessions/disconnect", {
      session_id,
      reason,
    }) as Promise<{ ok: boolean; found: boolean }>;
  }

  /** POST /extension/invoke — engine→browser tool dispatch. */
  async extensionInvoke(
    session_id: string,
    tool_name: string,
    args: Record<string, unknown>,
    timeout_seconds = 30,
  ): Promise<ExtensionInvokeResponse> {
    return this.post("/extension/invoke", {
      session_id,
      tool_name,
      args,
      timeout_seconds,
    }) as Promise<ExtensionInvokeResponse>;
  }

  /** GET /extension/broadcast/status — feature-flag + channel template. */
  async extensionBroadcastStatus(): Promise<ExtensionBroadcastStatus> {
    return this.request("/extension/broadcast/status");
  }

  /**
   * GET /extension/tunnel/status — runtime introspection of the
   * Cloudflare tunnel state.
   *
   * Returns ``active``, the public tunnel URL (and ws variant), the
   * engine's bound local URL (and ws variant), and a ``preferred`` hint
   * the extension should follow when it has a choice between local and
   * tunnel. The hint flips to ``"tunnel"`` only when the tunnel is up
   * AND the engine was started with ``MATRX_PREFER_TUNNEL=true`` —
   * otherwise the recommendation is ``"local"``.
   *
   * Mirrors ``app/api/tunnel_state.py::get_tunnel_snapshot``.
   */
  async extensionTunnelStatus(): Promise<ExtensionTunnelStatus> {
    return this.request("/extension/tunnel/status");
  }

  /**
   * GET /extension/metrics — per-command stats snapshot.
   *
   * Returns a JSON map of command name -> ExtensionCommandMetrics. The
   * shape mirrors `app/api/extension_metrics.py::get_snapshot`. The
   * synthetic "_overflow" row is only present when the distinct-command
   * cap (200) has been hit; UI can render a banner if so.
   */
  async extensionGetMetrics(): Promise<ExtensionMetricsSnapshot> {
    return this.request("/extension/metrics");
  }

  /** POST /extension/metrics/reset — clear every recorded stat. Idempotent. */
  async extensionResetMetrics(): Promise<{ ok: boolean }> {
    return this.post("/extension/metrics/reset", {}) as Promise<{
      ok: boolean;
    }>;
  }

  /**
   * GET /extension/boot-check — last cached boot self-check summary.
   *
   * Reflects the LAST self-check run — populated at engine startup and
   * refreshed whenever ``extensionBootCheckRun()`` fires. Cheap (no
   * engine work per request); the cache lives in
   * ``app/api/extension_boot_check.py`` and resets only on engine
   * restart.
   *
   * When the engine has not yet completed its first sweep (extremely
   * early in boot), the response carries ``ok: false`` and an empty
   * ``checks`` array with an explanatory ``message`` field.
   */
  async extensionBootCheckGet(): Promise<ExtensionBootCheckSummary> {
    return this.request("/extension/boot-check");
  }

  /**
   * POST /extension/boot-check/run — re-run the boot self-check live.
   *
   * Replaces the cached summary returned by ``extensionBootCheckGet()``.
   * Intended for the desktop "Re-run self-check" button so users can
   * refresh the picture after toggling tunnel state or flipping
   * ``MATRX_PREFER_TUNNEL`` without having to restart the engine.
   */
  async extensionBootCheckRun(): Promise<ExtensionBootCheckSummary> {
    return this.post(
      "/extension/boot-check/run",
      {},
    ) as Promise<ExtensionBootCheckSummary>;
  }

  /** POST /extension/broadcast/test — fires a no-op publish if the flag is on. */
  async extensionBroadcastTest(
    user_id: string,
    type = "bridge.test",
    payload: Record<string, unknown> = {},
  ): Promise<{ ok: boolean; sent: boolean; enabled: boolean }> {
    return this.post("/extension/broadcast/test", {
      user_id,
      type,
      payload,
    }) as Promise<{ ok: boolean; sent: boolean; enabled: boolean }>;
  }

  /**
   * Subscribe to the bridge live event log.
   *
   * Opens a dedicated WebSocket to /extension/bridge-events and invokes
   * `onEvent` for every received envelope. Returns a teardown function
   * that closes the socket. Auth via ?token query param (browsers can't
   * set headers on a WS upgrade — same convention as /ws and /extension/ws).
   *
   * The connection is best-effort: on disconnect, callers should manage
   * their own reconnect loop. This deliberately mirrors how the existing
   * SSE helpers in this file behave.
   */
  subscribeBridgeEvents(
    onEvent: (event: BridgeEvent) => void,
    onError?: (error: Event) => void,
    onOpen?: () => void,
    onClose?: () => void,
  ): () => void {
    if (!this.baseUrl || !this.wsUrl) {
      throw new Error("Engine not discovered");
    }
    let closed = false;
    let socket: WebSocket | null = null;

    const connect = async () => {
      const token = this._getAccessToken ? await this._getAccessToken() : null;
      if (closed) return;
      const url = token
        ? `${this.wsUrl!.replace(/\/ws$/, "")}/extension/bridge-events?token=${encodeURIComponent(token)}`
        : `${this.wsUrl!.replace(/\/ws$/, "")}/extension/bridge-events`;
      socket = new WebSocket(url);
      socket.onopen = () => {
        if (!closed) onOpen?.();
      };
      socket.onmessage = (ev) => {
        try {
          onEvent(JSON.parse(ev.data) as BridgeEvent);
        } catch {
          /* ignore non-JSON */
        }
      };
      socket.onerror = (err) => {
        if (onError) onError(err);
      };
      socket.onclose = () => {
        if (!closed) onClose?.();
      };
    };

    void connect();
    return () => {
      closed = true;
      try {
        socket?.close();
      } catch {
        /* ignore */
      }
    };
  }
}

// ---- Document types ----

export interface DocFolder {
  id: string;
  user_id: string;
  name: string;
  parent_id: string | null;
  path: string;
  position: number;
  is_deleted: boolean;
  created_at: string;
  updated_at: string;
  note_count?: number;
  children?: DocFolder[];
}

export interface DocTree {
  folders: DocFolder[];
  total_notes: number;
  unfiled_notes: number;
}

export interface DocNote {
  id: string;
  user_id?: string;
  label: string;
  content?: string;
  folder_name: string;
  folder_id: string | null;
  tags: string[];
  file_path: string | null;
  content_hash: string | null;
  sync_version: number;
  position: number;
  is_deleted: boolean;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  sync_status?: "never_synced" | "synced" | "pending_push" | "excluded";
  sync_enabled?: boolean;
}

export interface CreateNoteData {
  label: string;
  content: string;
  folder_name?: string;
  folder_id?: string;
  tags?: string[];
  metadata?: Record<string, unknown>;
}

export interface DocVersion {
  id: string;
  note_id: string;
  user_id?: string;
  content: string;
  label?: string;
  version_number: number;
  change_source?: string;
  change_type?: string | null;
  content_hash?: string;
  diff_metadata?: Record<string, unknown>;
  created_at: string;
  _source?: "local" | "cloud";
}

export interface SyncStatus {
  configured: boolean;
  device_id: string;
  last_pull_at: string | null;
  last_full_sync: number | null;
  tracked_files: number;
  conflicts: string[];
  conflict_count: number;
  watcher_active: boolean;
  base_dir: string;
  pending_push_count?: number;
  excluded_count?: number;
  /** True while the OS is denying access to the notes directory. */
  notes_access_degraded?: boolean;
  notes_access_reason?: string | null;
  notes_access_kind?: AccessKind | null;
}

/** Why a resource's access is degraded. */
export type AccessKind = "permission" | "missing_dir";

/** Filesystem operations the engine tracks separately per resource. */
export type AccessCapability =
  | "enumerate"
  | "read"
  | "create"
  | "write"
  | "replace"
  | "delete";

/** One piece of evidence: an actual filesystem operation and its outcome. */
export interface AccessObservation {
  path: string;
  capability: AccessCapability;
  ok: boolean;
  errno: number | null;
  error: string | null;
  op: string;
  source: string;
  at: number;
  generation: number;
}

/** Health of one registered resource (notes dir, a mapped dir, files replica). */
export interface AccessResourceHealth {
  resource_id: string;
  label: string;
  root: string;
  provenance: "default" | "override" | "fallback" | "mapped";
  status: "ok" | "degraded" | "unknown";
  kind: AccessKind | null;
  /** Evidence-based, diagnosis-aware sentence from the engine. */
  message: string;
  capabilities: Partial<Record<AccessCapability, AccessObservation>>;
  last_success_at: number | null;
  last_failure: AccessObservation | null;
  recent: AccessObservation[];
  generation: number;
}

/**
 * Engine-process Full Disk Access diagnosis. `denied` is POSITIVE evidence
 * (an FDA-protected location exists and was refused) — the only verdict that
 * justifies telling the user to grant FDA. `granted` exonerates FDA.
 */
export interface AccessFdaDiagnosis {
  status: "granted" | "denied" | "indeterminate" | "not_applicable";
  evidence: Array<{ probe: string; result: string }>;
  source: string;
  checked_at: number;
}

/**
 * Canonical filesystem access-health snapshot — GET /access/health.
 * Replaces the old single-boolean notes access state.
 */
export interface AccessHealth {
  generation: number;
  /** Engine's sys.platform: "darwin" | "win32" | "linux" | ... */
  platform: string;
  degraded: boolean;
  resources: AccessResourceHealth[];
  fda: AccessFdaDiagnosis | null;
}

export interface SyncResult {
  pushed?: number;
  pulled?: number;
  conflicts?: number;
  deleted?: number;
  unchanged?: number;
  skipped?: number;
  failed?: number;
  error?: string;
}

export interface ConflictDetail {
  note_id: string;
  local_content?: string;
  remote_content?: string;
  label?: string;
  folder_name?: string;
}

export interface ConflictList {
  conflicts: ConflictDetail[];
  count: number;
}

// ---- File sync types (desktop replica of the matrx-files cloud tree) ----

export type FileSyncMode = "off" | "pointers" | "full";

export interface FileSyncCounts {
  pointer?: number;
  synced?: number;
  pending_push?: number;
  conflict?: number;
  pending_ops: number;
  tracked: number;
}

export interface FileSyncStatus {
  mode: FileSyncMode;
  root: string;
  configured: boolean;
  auto_sync_active: boolean;
  watcher_active: boolean;
  interval_seconds: number;
  counts: FileSyncCounts;
  cursor: string | null;
  last_sync_status: string | null;
  last_sync_error: string | null;
  // {} until the first cycle of this process completes.
  last_cycle: FileSyncCycleSummary | Record<string, never>;
}

export interface FileSyncCycleSummary {
  mode: FileSyncMode;
  folders: { applied?: number; error?: number };
  pulled: {
    applied?: number;
    tombstones?: number;
    conflicts?: number;
    pages?: number;
    error?: number;
  };
  pushed: { sent: number; failed: number };
  conflict_captures_retried: number;
  hydration_enqueued: number;
  at: string;
}

export interface FileSyncConflict {
  file_id: string;
  rel_path: string;
  local_state: string;
  error: string | null;
  updated_at: string | null;
}

export interface DocMappings {
  local_mappings: Record<string, string[]>;
  device_id: string;
}

// ---- Proxy types ----

export interface ProxyStatus {
  running: boolean;
  port: number;
  proxy_url: string;
  request_count: number;
  bytes_forwarded: number;
  active_connections: number;
  uptime_seconds: number;
}

export interface ProxyTestResult {
  success: boolean;
  status_code?: number;
  body?: string;
  error?: string;
  proxy_url: string;
}

// ---- Cloud Sync types ----

export interface CloudConfigResult {
  configured: boolean;
  instance_id: string;
  sync_result: { status: string; reason?: string };
}

export interface CloudSettingsResponse {
  settings: Record<string, unknown>;
  configured: boolean;
  push_result?: { status: string; reason?: string };
}

export interface CloudSyncResult {
  status: string;
  reason?: string;
  settings?: Record<string, unknown>;
}

export interface InstanceInfo {
  instance_id: string;
  instance_name: string;
  platform?: string;
  os_version?: string;
  architecture?: string;
  hostname?: string;
  username?: string;
  python_version?: string;
  home_dir?: string;
  cpu_model?: string;
  cpu_cores?: number;
  ram_total_gb?: number;
  last_seen?: string;
  is_active?: boolean;
  id?: string;
  // Remote access — both REST and WebSocket URLs for the active Cloudflare tunnel
  tunnel_url?: string | null;
  tunnel_ws_url?: string | null;
  tunnel_active?: boolean;
  tunnel_updated_at?: string | null;
}

// ---- Device & Permission types ----

export type PermissionStatusValue =
  | "granted"
  | "denied"
  | "not_determined"
  | "restricted"
  | "unavailable"
  | "unknown";

export interface PermissionInfo {
  permission: string;
  status: PermissionStatusValue;
  details: string;
  grant_instructions: string;
  user_details?: string;
  user_instructions?: string;
  fixable?: boolean;
  fix_capability_id?: string | null;
  devices?: Array<Record<string, unknown>>;
  deep_link?: string | null;
}

export interface DevicePermissionsResponse {
  permissions: PermissionInfo[];
  platform: string;
}

export interface DeviceProbeResult {
  output: string;
  metadata: Record<string, unknown> | null;
  type: string;
}

// ---- Capabilities types ----

export type CapabilityStatus = "installed" | "not_installed" | "checking";

export interface Capability {
  id: string;
  name: string;
  description: string;
  status: CapabilityStatus;
  packages: string[];
  install_extra: string | null;
  size_warning: string | null;
  docs_url: string | null;
}

export interface CapabilitiesResponse {
  capabilities: Capability[];
}

/** Response from POST /capabilities/install and related status/stream events. */
export interface CapabilityInstallStatus {
  status: "idle" | "running" | "complete" | "error" | "connected" | "waiting";
  capability_id: string;
  stage: string;
  percent: number;
  message: string;
  error?: string | null;
  already_installed?: boolean;
  install_dir?: string | null;
  log_lines?: string[];
  async_install?: boolean;
  /** True when this SSE event is a raw pip log line */
  log?: boolean;
}

/** @deprecated Prefer CapabilityInstallStatus — kept for Dashboard call sites. */
export interface InstallCapabilityResult {
  success: boolean;
  message: string;
}

/** Map engine install status → legacy success/message shape. */
export function capabilityInstallToResult(
  status: CapabilityInstallStatus,
): InstallCapabilityResult {
  return {
    success: status.status === "complete",
    message: status.error || status.message || status.status,
  };
}

// ---- Path types ----

/**
 * Named path aliases and resolved absolute paths on the user's machine.
 * Returned by GET /system/paths.  React and microservices should fetch this
 * once on startup and never construct OS paths themselves.
 *
 * All aliases can be used in tool calls:
 *   @notes  → ~/Documents/Matrx/Notes/
 *   @files  → ~/Documents/Matrx/Files/
 *   @code   → ~/Documents/Matrx/Code/
 *   @matrx  → ~/.matrx/   (engine internals)
 *   @home   → user home directory
 *   @temp   → OS temp/cache dir
 */
export interface EnginePaths {
  /** Logical alias → absolute directory path */
  aliases: {
    "@matrx": string; // ~/.matrx/ — engine internals
    "@notes": string; // ~/Documents/Matrx/Notes/
    "@files": string; // ~/Documents/Matrx/Files/
    "@code": string; // ~/Documents/Matrx/Code/
    "@workspaces": string; // ~/.matrx/workspaces/
    "@agentdata": string; // ~/.matrx/data/
    "@user": string; // ~/Documents/Matrx/
    "@temp": string;
    "@data": string;
    "@logs": string;
    "@home": string;
    "@docs": string; // deprecated alias for @notes
    [key: string]: string; // allow future aliases without TS errors
  };
  /** Named locations with their full absolute paths. */
  resolved: {
    // Engine internals
    discovery: string; // local.json — engine discovery
    settings: string; // settings.json
    instance: string; // instance.json
    agent_data: string; // ~/.matrx/data/
    workspaces: string; // ~/.matrx/workspaces/
    // User-visible
    user_root: string; // ~/Documents/Matrx/
    notes: string; // ~/Documents/Matrx/Notes/
    files: string; // ~/Documents/Matrx/Files/
    code: string; // ~/Documents/Matrx/Code/
    // Platform cache
    temp: string;
    screenshots: string;
    data: string;
    logs: string;
    config: string;
  };
}

export interface FilesystemPlaceResponse {
  id: string;
  label: string;
  path: string;
  category: "home" | "standard" | "configured" | "volume";
  priority: number;
  available: boolean;
  configured: boolean;
}

export interface FilesystemPlacesResponse {
  kind: "filesystem.places";
  namespace: "host";
  places: FilesystemPlaceResponse[];
}

export interface FilesystemPriorityRoot {
  path: string;
  label?: string;
}

export interface FilesystemIndexingSettings {
  priority_roots: FilesystemPriorityRoot[];
  content_enabled: boolean;
  semantic_enabled: boolean;
  embedding_model: string;
  max_content_bytes: number;
  max_embedding_entries: number;
}

export interface FilesystemScanFailure {
  path: string;
  root_id: string;
  attempts: number;
  consecutive_failures: number;
  last_error_kind: string | null;
  last_error: string;
  last_failed_at: number | null;
  next_retry_at: number | null;
}

export interface FilesystemIndexStatus {
  started: boolean;
  database: string;
  fts5: boolean;
  entries: number;
  directories_pending: number;
  directories_failed: number;
  directories_claimed: number;
  directories_ready: number;
  scan_failures: FilesystemScanFailure[];
  metadata_state: "complete" | "indexing" | "partial";
  index_complete: boolean;
  indexed_this_run: number;
  places: number;
  last_reconcile_at: number | null;
  content_indexing: string;
  embedding_indexing: string;
  policy: string;
}

export interface FilesystemEntryResponse {
  name: string;
  path: string;
  kind: "file" | "dir" | "symlink" | "other";
  size: number;
  modified_at: number | null;
  hidden: boolean;
  extension: string | null;
  indexed: boolean;
}

export interface FilesystemPageResponse {
  kind: "filesystem.directory-page" | "filesystem.search-page";
  namespace: "host";
  entries: FilesystemEntryResponse[];
  next_cursor: string | null;
  path?: string;
  query?: string;
  root?: string | null;
  total?: number;
  source?: "index" | "disk";
  index_complete?: boolean;
}

// ---- Setup types ----

export interface SetupComponentStatus {
  id: string;
  label: string;
  description: string;
  /** "warning" = advisory only (cannot be auto-fixed, e.g. macOS TCC permissions) */
  status:
    | "ready"
    | "not_ready"
    | "installing"
    | "error"
    | "skipped"
    | "warning";
  detail: string | null;
  optional: boolean;
  size_hint: string | null;
  /** macOS x-apple.systempreferences deep link or other OS settings URL */
  deep_link: string | null;
}

export interface SetupStatus {
  setup_complete: boolean;
  components: SetupComponentStatus[];
  platform: string;
  architecture: string;
  gpu_available: boolean;
  gpu_name: string | null;
}

export interface SetupProgressEvent {
  component: string;
  status: string;
  message: string;
  percent: number;
  /** Optional deep link forwarded from Python backend */
  deep_link?: string | null;
  /** Raw byte counts for download progress */
  bytes_downloaded?: number;
  total_bytes?: number;
}

export interface SetupCompleteEvent {
  message: string;
  had_errors: boolean;
  errors: string[];
  timestamp: number;
}

// ---- Hardware profile types ----

export interface HardwareCpu {
  model: string;
  physical_cores: number | null;
  logical_cores: number | null;
  threads_per_core: number | null;
  architecture: string | null;
  frequency_mhz: number | null;
  frequency_max_mhz: number | null;
}

export interface HardwareGpu {
  name: string;
  vram_mb: number | null;
  driver_version: string | null;
  backend: string; // 'metal' | 'cuda' | 'vulkan' | 'cuda+vulkan' | 'rocm' | 'cpu' | 'unknown'
  is_primary: boolean;
  device_type?: string;
  vram_note?: string; // 'unified_memory' for Apple Silicon
}

export interface HardwareRam {
  total_mb: number | null;
  available_mb: number | null;
  type: string | null; // 'DDR4', 'DDR5', etc.
  speed_mhz: number | null;
}

export interface HardwareAudioDevice {
  name: string;
  host_api: string;
  channels: number | null;
  default_sample_rate: number | null;
}

export interface HardwareVideoDevice {
  name: string;
  index: number;
  device?: string;
}

export interface HardwareMonitor {
  name: string;
  width_px: number | null;
  height_px: number | null;
  width_mm?: number | null;
  height_mm?: number | null;
  x?: number;
  y?: number;
  is_primary: boolean;
  refresh_hz: number | null;
}

export interface HardwareNetworkAdapter {
  name: string;
  type: string; // 'wifi' | 'ethernet' | 'loopback' | 'vpn' | 'bluetooth' | 'other'
  mac: string | null;
  ipv4: string[];
  ipv6: string[];
  is_up: boolean;
  speed_mbps: number | null;
}

export interface HardwareStorageDevice {
  device: string;
  mountpoint: string;
  fstype: string;
  disk_type: string; // 'ssd' | 'hdd' | 'optical_or_usb' | 'unknown'
  total_gb: number;
  used_gb: number;
  free_gb: number;
  percent_used: number;
}

export interface HardwareProfile {
  detected_at: string;
  cpus: HardwareCpu[];
  gpus: HardwareGpu[];
  ram: HardwareRam;
  audio_inputs: HardwareAudioDevice[];
  audio_outputs: HardwareAudioDevice[];
  video_devices: HardwareVideoDevice[];
  monitors: HardwareMonitor[];
  network_adapters: HardwareNetworkAdapter[];
  storage: HardwareStorageDevice[];
  error?: string;
}

export interface HardwareResponse {
  profile: HardwareProfile;
  cached: boolean;
  detected_at: string | null;
}

// ── matrx-extend bridge — Bridge Test types ─────────────────────────────

export interface ExtensionRpcResponse {
  ok: boolean;
  data?: unknown;
  error?: string;
}

export interface ExtensionPairInfo {
  pair_token: string;
  engine_version: string;
  service: string;
}

export interface ExtensionSessionInfo {
  session_id: string;
  connected_at: number; // seconds since epoch
  last_seen_at: number; // seconds since epoch
  pending_calls: number;
  extension_id?: string | null;
  extension_version?: string | null;
  extension_name?: string | null;
  identified_at?: number | null;
}

export interface ExtensionInvokeEnvelope {
  type?: string;
  callId?: string;
  ok?: boolean;
  result?: unknown;
  error?: string;
  errorType?: string;
}

export interface ExtensionInvokeResponse {
  ok: boolean;
  envelope?: ExtensionInvokeEnvelope;
  error?: string;
  error_type?: string;
}

export interface ExtensionBroadcastStatus {
  enabled: boolean;
  channel_template: string;
  setting_key: string;
}

/**
 * Runtime tunnel state surfaced by ``GET /extension/tunnel/status``.
 *
 * Wire-shape mirror of ``app/api/tunnel_state.py::get_tunnel_snapshot``.
 * ``preferred`` is the recommendation the extension should follow when
 * choosing between the local loopback URL and the public tunnel URL —
 * ``"tunnel"`` only when the tunnel is up AND the engine was started
 * with ``MATRX_PREFER_TUNNEL=true``.
 */
export interface ExtensionTunnelStatus {
  active: boolean;
  tunnel_url: string | null;
  tunnel_ws: string | null;
  local_url: string;
  local_ws: string;
  preferred: "local" | "tunnel";
  prefer_tunnel: boolean;
  mode: "quick" | "named";
  uptime_seconds: number;
}

export interface BridgeEvent {
  timestamp: number; // ms since epoch
  kind: string; // "rpc.in", "ws.open", "invoke.send", etc.
  direction: "in" | "out" | "internal";
  payload: Record<string, unknown>;
}

export interface ExtensionCommandMetrics {
  count: number;
  error_count: number;
  last_n_latencies_ms: number[]; // ring buffer, capped at 100
  last_called_at: number; // unix ms; 0 if never
  last_error: string | null;
}

/**
 * Map of command name -> rolling stats. Includes a synthetic "_overflow"
 * row when the engine has hit its distinct-command cap (200) — UI can
 * render a warning when present.
 */
export type ExtensionMetricsSnapshot = Record<string, ExtensionCommandMetrics>;

/** One row of the boot-time self-check, as returned by the engine. */
export interface ExtensionBootCheckResult {
  name: string; // e.g. "routes_registered"
  status: "ok" | "warn" | "fail";
  message: string; // short human-readable detail
  duration_ms: number;
}

/**
 * Snapshot from ``GET /extension/boot-check`` and
 * ``POST /extension/boot-check/run``.
 *
 * Wire-shape mirror of ``app/api/extension_boot_check.BootCheckSummary``.
 * ``ok`` is ``true`` when every check has ``status !== 'fail'``; warnings
 * are tolerated. ``checks`` is empty and ``message`` is populated only
 * when the engine has not yet completed an initial sweep.
 */
export interface ExtensionBootCheckSummary {
  ok: boolean;
  checks: ExtensionBootCheckResult[];
  started_at: number; // unix seconds
  finished_at: number; // unix seconds
  duration_ms: number;
  message?: string; // present only when checks is empty
}

// Singleton instance
export const engine = new EngineAPI();

export type RecoveryServiceAction = "probe" | "refresh" | "repair" | "stop" | "start" | "restart" | "snapshot";
export interface EngineRecoveryService {
  state: string;
  capabilities: RecoveryServiceAction[];
  metadata: Record<string, unknown>;
  error: string | null;
}
export interface EngineRecoveryOperation {
  id: string;
  service: string;
  action: string;
  status: string;
  started_at: number;
  finished_at: number | null;
  error: string | null;
  result?: Record<string, unknown>;
}
export interface EngineRecoveryStatus {
  services: Record<string, EngineRecoveryService>;
  operations: EngineRecoveryOperation[];
}
export async function getEngineRecoveryStatus(): Promise<EngineRecoveryStatus> {
  return engine.get("/admin/recovery") as Promise<EngineRecoveryStatus>;
}
export async function runEngineRecoveryAction(
  service: string,
  action: RecoveryServiceAction,
  timeoutSeconds = 30,
): Promise<EngineRecoveryOperation> {
  return engine.post(`/admin/recovery/${encodeURIComponent(service)}`, {
    action,
    timeout_seconds: timeoutSeconds,
  }) as Promise<EngineRecoveryOperation>;
}

// ── Image Generation Types ─────────────────────────────────────────────────

export interface ImageGenModelInfo {
  model_id: string;
  name: string;
  provider: string;
  pipeline_type: string;
  vram_gb: number;
  ram_gb: number;
  description: string;
  quality_rating: number;
  speed_rating: number;
  recommended_steps: number;
  recommended_guidance: number;
  supports_negative_prompt: boolean;
  model_card_url: string;
  default_width: number;
  default_height: number;
  requires_hf_token: boolean;
  tags: string[];
  /** Approximate on-disk download size of the model weights, in GB. */
  download_size_gb: number;
  /** True when the weights are already present in the local model dir. */
  is_downloaded: boolean;
  /** True when the detected hardware can run this model. */
  hardware_ok: boolean;
  /** Human-readable reason when `hardware_ok` is false. */
  hardware_reason: string | null;
  /**
   * True when the model can take an input image (img2img).  Optional until
   * every engine build reports it — the UI hides the input-image controls
   * when absent/false, never guesses.
   */
  supports_img2img?: boolean;
  /**
   * True for user-added checkpoints (Hugging Face / Civitai custom models).
   * Custom entries get a "Custom" badge and a delete affordance in the
   * picker.  Optional until every engine build reports it.
   */
  custom?: boolean;
  /** Optional model-compatible replacements for the stock text encoder. */
  text_encoders?: ImageGenTextEncoderInfo[];
}

export interface ImageGenTextEncoderInfo {
  encoder_id: string;
  name: string;
  description: string;
  repo_id: string;
  format: "transformers" | "gguf" | "state_dict";
  files: string[];
  revision: string;
  subfolder: string | null;
  weight_name: string | null;
  requires_hf_token: boolean;
  license: string;
  unverified: boolean;
  download_size_gb: number;
  source_url: string | null;
  installed: boolean;
}

export interface ImageGenWorkflowPreset {
  preset_id: string;
  name: string;
  description: string;
  prompt_template: string;
  negative_prompt: string;
  suggested_model_id: string;
  steps: number;
  guidance: number;
  width: number;
  height: number;
  tags: string[];
}

export interface ImageGenStatus {
  available: boolean;
  unavailable_reason: string | null;
  loaded_model_id: string | null;
  /** null means the model's stock encoder is loaded. */
  loaded_text_encoder_id?: string | null;
  is_loading: boolean;
  load_progress: number;
  /**
   * LOUD model-load failure from the engine.  When set (with `is_loading`
   * back to false) the UI must resolve any loading spinner into this error —
   * never leave the spinner running.  Optional until every engine build
   * reports it.
   */
  load_error?: string | null;
  /** True while a one-shot /generate is executing on the engine. */
  is_generating?: boolean;
  /** True after POST /image-gen/cancel until the in-flight work stops. */
  cancel_requested?: boolean;
  /** Installed diffusers version, or null when packages are not installed. */
  packages_version: string | null;
  /** True when the installed diffusers is older than the required minimum. */
  packages_outdated: boolean;
  /** Compute device the pipeline runs on. */
  device: "mps" | "cuda" | "cpu";
}

/** Result of a model-load request that distinguishes the "not downloaded" case. */
export interface MediaLoadResult {
  success: boolean;
  error?: string;
  /** True when a 409 was returned because the weights are not on disk yet. */
  needs_download?: boolean;
}

export interface ImageGenResult {
  success: boolean;
  /** True when the generation was cancelled via POST /image-gen/cancel. */
  cancelled?: boolean;
  /** Base64-encoded PNG. Use as: `data:image/png;base64,${image_b64}` */
  image_b64?: string;
  width: number;
  height: number;
  model_id: string;
  elapsed_seconds: number;
  error?: string;
  /** The concrete seed used — always returned, even when it was random. */
  seed?: number | null;
  /** Media-library item id, when the engine persisted the result. */
  item_id?: string | null;
  /** On-disk path of the saved image, when the engine persisted the result. */
  file_path?: string | null;
}

// ── Media-gen parameter schema (params endpoints) ───────────────────────────

/**
 * The "common" (beautifully-rendered) parameter defaults for a model, as
 * returned by `GET /image-gen/params/{model_id}` and
 * `GET /video-gen/params/{model_id}`.  `num_frames`/`fps` are video-only.
 */
export interface MediaGenCommonParams {
  steps?: number;
  guidance?: number;
  width?: number;
  height?: number;
  negative_prompt?: string | null;
  seed?: number | null;
  num_frames?: number;
  fps?: number;
  /** img2img denoise strength default (0..1) — present when supported. */
  strength?: number;
}

export interface MediaGenParams {
  common: MediaGenCommonParams;
  /** Every remaining pipeline kwarg with its default value. */
  advanced: Record<string, unknown>;
  supports_negative_prompt: boolean;
}

// ── Image Generation API helper ────────────────────────────────────────────

function imageGenUrl(baseUrl: string, path: string): string {
  return `${baseUrl}/image-gen${path}`;
}

/**
 * Default bound for media-gen control-plane calls (status, params, lists,
 * enqueue, cancel…).  Generation and model-load calls pass `null` — they can
 * legitimately take many minutes (CPU fallback) and are escaped via the
 * Cancel button instead of a hard timeout.
 */
const MEDIA_GEN_TIMEOUT_MS = 30_000;

/** Never-hang guarantee: bounded requests carry an AbortSignal.timeout. */
function mediaGenTimeoutSignal(
  timeoutMs: number | null,
): AbortSignal | undefined {
  return timeoutMs === null ? undefined : AbortSignal.timeout(timeoutMs);
}

function isAbortTimeout(e: unknown): boolean {
  return (
    e instanceof DOMException &&
    (e.name === "TimeoutError" || e.name === "AbortError")
  );
}

/**
 * FastAPI's `detail` is NOT always a string: 422 validation errors carry an
 * array of `{loc, msg, type}` objects, and some routes return object details.
 * Coercing those with template literals / new Error() renders the dreaded
 * "[object Object]". This flattens any detail shape into a readable string.
 */
function stringifyErrorDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => {
        if (typeof d === "string") return d;
        if (d && typeof d === "object") {
          const rec = d as { loc?: unknown[]; msg?: unknown };
          const loc = Array.isArray(rec.loc)
            ? rec.loc.filter((p) => p !== "body").join(".")
            : "";
          const msg = typeof rec.msg === "string" ? rec.msg : JSON.stringify(d);
          return loc ? `${loc}: ${msg}` : msg;
        }
        return String(d);
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  if (detail && typeof detail === "object") {
    try {
      return JSON.stringify(detail);
    } catch {
      // fall through
    }
  }
  return fallback;
}

/**
 * Error thrown by media-gen fetch helpers for non-OK HTTP responses.  Carries
 * the status code so callers can branch on it (401 → "set your Civitai API
 * key" affordance, 404 → "engine build too old" messaging) without string
 * matching.  `message` is the flattened FastAPI `detail` — user-facing.
 */
export class MediaGenHttpError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "MediaGenHttpError";
    this.status = status;
  }
}

function mediaGenTimeoutError(
  surface: "image-gen" | "video-gen" | "media-library",
  method: string,
  url: string,
  timeoutMs: number,
): Error {
  let path = url;
  try {
    const u = new URL(url);
    path = `${u.pathname}${u.search}`;
  } catch {
    // keep full url
  }
  const msg = `Engine did not respond within ${Math.round(timeoutMs / 1000)}s (${method} ${path}) — the request was aborted so the UI never hangs`;
  emitClientLog("error", `[${surface}] ${msg}`, "engine");
  return new Error(msg);
}

async function imageGenFetch<T>(
  url: string,
  options?: RequestInit,
  timeoutMs: number | null = MEDIA_GEN_TIMEOUT_MS,
): Promise<T> {
  const auth = await engine.getEngineAuthHeaders();
  const mergedHeaders = new Headers({
    "Content-Type": "application/json",
    ...auth,
  });
  if (options?.headers) {
    const extra = new Headers(options.headers);
    extra.forEach((value, key) => mergedHeaders.set(key, value));
  }
  const method = options?.method ?? "GET";
  let resp: Response;
  try {
    resp = await fetch(url, {
      signal: mediaGenTimeoutSignal(timeoutMs) ?? null,
      ...options,
      headers: mergedHeaders,
    });
  } catch (e) {
    if (isAbortTimeout(e) && timeoutMs !== null) {
      throw mediaGenTimeoutError("image-gen", method, url, timeoutMs);
    }
    throw e;
  }
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    let detail = body;
    try {
      const parsed = JSON.parse(body) as { detail?: unknown };
      if (parsed.detail) detail = stringifyErrorDetail(parsed.detail, body);
    } catch {
      // use raw body
    }
    try {
      const u = new URL(url);
      const path = `${u.pathname}${u.search}`;
      emitClientLog(
        "error",
        `[image-gen] ${method} ${path} → HTTP ${resp.status}: ${detail.slice(0, 240)}`,
        "engine",
      );
    } catch {
      emitClientLog(
        "error",
        `[image-gen] ${method} request failed → HTTP ${resp.status}: ${detail.slice(0, 240)}`,
        "engine",
      );
    }
    throw new MediaGenHttpError(detail || `HTTP ${resp.status}`, resp.status);
  }
  return resp.json() as Promise<T>;
}

export async function getImageGenStatus(
  baseUrl: string,
): Promise<ImageGenStatus> {
  return imageGenFetch<ImageGenStatus>(imageGenUrl(baseUrl, "/status"));
}

export async function listImageGenModels(
  baseUrl: string,
): Promise<ImageGenModelInfo[]> {
  return imageGenFetch<ImageGenModelInfo[]>(imageGenUrl(baseUrl, "/models"));
}

export async function listImageGenPresets(
  baseUrl: string,
): Promise<ImageGenWorkflowPreset[]> {
  return imageGenFetch<ImageGenWorkflowPreset[]>(
    imageGenUrl(baseUrl, "/presets"),
  );
}

/**
 * Full parameter schema (common defaults + every advanced pipeline kwarg)
 * for one image model.  404s throw with the engine's detail message.
 */
export async function getImageGenParams(
  baseUrl: string,
  model_id: string,
): Promise<MediaGenParams> {
  return imageGenFetch<MediaGenParams>(
    imageGenUrl(baseUrl, `/params/${encodeURIComponent(model_id)}`),
  );
}

/**
 * POST a model-load request to a media-gen surface (`/image-gen` or
 * `/video-gen`).  Unlike `imageGenFetch`, this treats a 409 as a structured
 * "not downloaded" result rather than throwing, so the UI can prompt a
 * download instead of surfacing a raw error.
 */
async function mediaGenLoad(
  baseUrl: string,
  prefix: "/image-gen" | "/video-gen",
  model_id: string,
): Promise<MediaLoadResult> {
  const auth = await engine.getEngineAuthHeaders();
  let resp: Response;
  try {
    resp = await fetch(`${baseUrl}${prefix}/load`, {
      method: "POST",
      headers: new Headers({ "Content-Type": "application/json", ...auth }),
      body: JSON.stringify({ model_id }),
      // Loading multi-GB weights can legitimately take minutes; this cap only
      // guards against a truly wedged connection so the Load spinner is never
      // permanent.
      signal: AbortSignal.timeout(30 * 60_000),
    });
  } catch (e) {
    if (isAbortTimeout(e)) {
      throw mediaGenTimeoutError(
        prefix === "/image-gen" ? "image-gen" : "video-gen",
        "POST",
        `${baseUrl}${prefix}/load`,
        30 * 60_000,
      );
    }
    throw e;
  }
  if (resp.status === 409) {
    const body = (await resp.json().catch(() => ({}))) as {
      detail?: unknown;
      needs_download?: boolean;
    };
    return {
      success: false,
      error: stringifyErrorDetail(body.detail, "Model not downloaded"),
      needs_download: body.needs_download ?? true,
    };
  }
  if (!resp.ok) {
    const detail = await resp.text().catch(() => `HTTP ${resp.status}`);
    emitClientLog(
      "error",
      `[${prefix.slice(1)}] POST ${prefix}/load → HTTP ${resp.status}: ${detail.slice(0, 240)}`,
      "engine",
    );
    throw new Error(detail || `HTTP ${resp.status}`);
  }
  return resp.json() as Promise<MediaLoadResult>;
}

export async function loadImageGenModel(
  baseUrl: string,
  model_id: string,
): Promise<MediaLoadResult> {
  return mediaGenLoad(baseUrl, "/image-gen", model_id);
}

/**
 * Trigger a background weights download for an image model.  Progress is
 * reported through the universal DownloadManager (category `image_gen`) and is
 * observed by the frontend via the `/downloads/stream` SSE — not by this call.
 */
export async function downloadImageGenModel(
  baseUrl: string,
  model_id: string,
): Promise<{ queued: boolean }> {
  return imageGenFetch<{ queued: boolean }>(imageGenUrl(baseUrl, "/download"), {
    method: "POST",
    body: JSON.stringify({ model_id }),
  });
}

/** Install a model-compatible optional text encoder through DownloadManager. */
export async function downloadImageGenTextEncoder(
  baseUrl: string,
  model_id: string,
  text_encoder_id: string,
): Promise<{
  queued: boolean;
  download_id: string | null;
  already_installed: boolean;
  text_encoder_id: string;
}> {
  return imageGenFetch(
    imageGenUrl(baseUrl, "/text-encoders/download"),
    {
      method: "POST",
      body: JSON.stringify({ model_id, text_encoder_id }),
    },
  );
}

export async function unloadImageGenModel(baseUrl: string): Promise<void> {
  await imageGenFetch(imageGenUrl(baseUrl, "/unload"), { method: "POST" });
}

export async function generateImage(
  baseUrl: string,
  req: {
    prompt: string;
    model_id: string;
    negative_prompt?: string;
    steps?: number;
    guidance?: number;
    width?: number;
    height?: number;
    seed?: number;
    /** Base64-encoded input image (no data: prefix) for img2img. */
    init_image_b64?: string;
    /** img2img denoise strength (0..1) — how much to change the input. */
    strength?: number;
    /** Durable parent/root lineage for an explicit image revision. */
    revision?: { parent_item_id: string; root_item_id?: string };
    /** LoRA adapters to apply, each with its scale. */
    loras?: { id: string; scale: number }[];
    /** Optional model-compatible replacement text encoder. */
    text_encoder_id?: string;
    /** Extra pipeline kwargs merged into the diffusers call (user wins). */
    extra_params?: Record<string, unknown>;
  },
): Promise<ImageGenResult> {
  // No hard timeout: generation can legitimately take many minutes (CPU
  // fallback).  The escape hatch is POST /image-gen/cancel (Cancel button).
  return imageGenFetch<ImageGenResult>(
    imageGenUrl(baseUrl, "/generate"),
    {
      method: "POST",
      body: JSON.stringify(req),
    },
    null,
  );
}

export async function generateImageFromWorkflow(
  baseUrl: string,
  req: {
    preset_id: string;
    subject: string;
    model_id?: string;
    seed?: number;
  },
): Promise<ImageGenResult> {
  // No hard timeout — same rationale as generateImage; cancel is the escape.
  return imageGenFetch<ImageGenResult>(
    imageGenUrl(baseUrl, "/generate-workflow"),
    {
      method: "POST",
      body: JSON.stringify(req),
    },
    null,
  );
}

// ── One-shot generation cancel ───────────────────────────────────────────────

/** Result of POST /image-gen/cancel. */
export interface MediaGenCancelResult {
  cancelled: boolean;
  /** What was cancelled, when anything was. */
  was?: "oneshot" | "job";
  job_id?: string;
  reason?: string;
}

/**
 * Cancel the in-flight image generation (one-shot or running job).  The
 * awaited /generate request then resolves with
 * `{ success: false, cancelled: true }`.  Short timeout — cancel must never
 * itself hang.
 */
export async function cancelImageGeneration(
  baseUrl: string,
): Promise<MediaGenCancelResult> {
  return imageGenFetch<MediaGenCancelResult>(
    imageGenUrl(baseUrl, "/cancel"),
    { method: "POST" },
    15_000,
  );
}

// ── Image generation job queue ───────────────────────────────────────────────

export type ImageGenJobStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export interface ImageGenJob {
  job_id: string;
  status: ImageGenJobStatus;
  /**
   * True after a cancel was requested for a RUNNING job, until the status
   * flips to "cancelled".  UI shows "Cancelling…" while set — a video/CPU
   * step can take tens of seconds to actually stop.
   */
  cancel_requested?: boolean;
  /**
   * "next" when the job was enqueued at the FRONT of the pending queue
   * (queue-first Generate while another generation was running). The queue
   * UI labels such still-queued jobs "Up next".
   */
  priority?: "normal" | "next";
  prompt: string;
  model_id: string;
  /** 0..1 fractional progress while running. */
  progress?: number;
  elapsed_seconds?: number;
  error?: string | null;
  /** The concrete seed used (or queued to be used). */
  seed?: number | null;
  /** The full generation parameters the job was enqueued with. */
  params?: Record<string, unknown>;
  revision_parent_item_id?: string | null;
  revision_root_item_id?: string | null;
  text_encoder_id?: string | null;
  /** Media-library item id, set on completion. */
  item_id?: string | null;
  file_path?: string | null;
  created_at?: number;
  /** Terminal timestamp; completed history is ordered by this, not enqueue time. */
  finished_at?: number | null;
  /** Durable tie-breaker for exact terminal completion order. */
  finished_sequence?: number;

  // ── Batch (prompt matrix) ──────────────────────────────────────────────────
  /** Set when the job belongs to a batch enqueued via POST /image-gen/jobs/batch. */
  batch_id?: string | null;
  /** Position within the batch (0-based) — also its run order. */
  batch_index?: number;
  batch_size?: number;
  batch_label?: string | null;
  /** The matrix variables this job realizes, e.g. `{ style: "noir" }`. */
  variables?: Record<string, string>;
  /** `style=noir · subject=cat` — the queue row's subtitle. */
  combo_label?: string | null;

  // ── Retry ─────────────────────────────────────────────────────────────────
  /** Runs started so far (1 during the first attempt). */
  attempts?: number;
  max_attempts?: number;
  /** True while the job is queued for ANOTHER attempt after a failure. */
  retrying?: boolean;
  /** Seconds of backoff left before the retry starts. */
  retry_in_seconds?: number;
  /** Error from the most recent failed attempt (set while it retries). */
  last_error?: string | null;
}

/** One run of a batch: a full generate request plus its matrix identity.
 *
 * A type alias, not an interface: an interface cannot `extends` an indexed
 * access type. Sourcing the request shape from generateImage's own parameter
 * keeps it in lockstep with the engine contract without importing the hook
 * layer (which would be an import cycle). */
export type ImageGenBatchJobSpec = Parameters<typeof generateImage>[1] & {
  /** The variable values this run realizes, e.g. `{ subject: "cat" }`. */
  variables?: Record<string, string>;
  /** Rendered summary, e.g. `subject=cat · style=noir`. */
  combo_label?: string;
};

/** Roll-up of one batch — the queue UI shows this as a single row. */
export interface ImageGenBatch {
  batch_id: string;
  label: string | null;
  total: number;
  queued: number;
  running: number;
  completed: number;
  failed: number;
  cancelled: number;
  /** completed + failed + cancelled. */
  done: number;
  /** True when nothing is queued or running any more. */
  finished: boolean;
  created_at: number;
}

export interface ImageGenQueueState {
  /** True when the queue is not starting new jobs (the running one finishes). */
  paused: boolean;
  queued: number;
  running: number;
}

/**
 * Enqueue N jobs as ONE batch — what the prompt matrix submits.
 *
 * Every job is validated server-side BEFORE any is queued: a single bad run
 * rejects the whole request naming the offending index. Half a sweep in the
 * queue is worse than none.
 */
export async function enqueueImageGenBatch(
  baseUrl: string,
  req: { label?: string; jobs: ImageGenBatchJobSpec[] },
): Promise<{ batch_id: string; job_ids: string[]; count: number }> {
  return imageGenFetch<{ batch_id: string; job_ids: string[]; count: number }>(
    imageGenUrl(baseUrl, "/jobs/batch"),
    { method: "POST", body: JSON.stringify(req) },
    // A 500-job batch is a big body and a lot of server-side validation.
    60_000,
  );
}

/** GET /image-gen/batches — per-batch roll-up, newest first. */
export async function listImageGenBatches(
  baseUrl: string,
): Promise<ImageGenBatch[]> {
  return imageGenFetch<ImageGenBatch[]>(imageGenUrl(baseUrl, "/batches"));
}

/**
 * Cancel every unfinished job of a batch. Images the batch already produced
 * are NOT deleted — cancelling the last 80 of a 120-image sweep must never
 * destroy the 40 already made.
 */
export async function cancelImageGenBatch(
  baseUrl: string,
  batchId: string,
): Promise<{
  batch_id: string;
  cancelled: number;
  cancelling_job_id: string | null;
}> {
  return imageGenFetch(
    imageGenUrl(baseUrl, `/batches/${encodeURIComponent(batchId)}`),
    { method: "DELETE" },
  );
}

/** GET /image-gen/queue — paused flag + how much work is left. */
export async function getImageGenQueueState(
  baseUrl: string,
): Promise<ImageGenQueueState> {
  return imageGenFetch<ImageGenQueueState>(imageGenUrl(baseUrl, "/queue"));
}

/**
 * Pause/resume the queue. A pause lets the RUNNING job finish (aborting a
 * 90%-denoised generation to honour a pause would throw away the GPU minutes
 * already spent); nothing new starts until resume.
 */
export async function setImageGenQueuePaused(
  baseUrl: string,
  paused: boolean,
): Promise<ImageGenQueueState> {
  return imageGenFetch<ImageGenQueueState>(
    imageGenUrl(baseUrl, paused ? "/queue/pause" : "/queue/resume"),
    { method: "POST" },
  );
}

/**
 * Reorder the pending queue (drag-and-drop). `jobIds` is the desired order of
 * the QUEUED jobs; the running job is never moved, and ids that are no longer
 * queued are ignored (the user dragged a row as it started running).
 * Returns the resulting pending order.
 */
export async function reorderImageGenQueue(
  baseUrl: string,
  jobIds: string[],
): Promise<ImageGenJob[]> {
  return imageGenFetch<ImageGenJob[]>(imageGenUrl(baseUrl, "/queue/reorder"), {
    method: "POST",
    body: JSON.stringify({ job_ids: jobIds }),
  });
}

/** Requeue a failed or cancelled job with a fresh attempt budget. */
export async function retryImageGenJob(
  baseUrl: string,
  jobId: string,
): Promise<ImageGenJob> {
  return imageGenFetch<ImageGenJob>(
    imageGenUrl(baseUrl, `/jobs/${encodeURIComponent(jobId)}/retry`),
    { method: "POST" },
  );
}

/** Drop every finished job record. Media-library items are NOT deleted. */
export async function clearFinishedImageGenJobs(
  baseUrl: string,
): Promise<{ removed: number }> {
  return imageGenFetch<{ removed: number }>(
    imageGenUrl(baseUrl, "/jobs/clear-finished"),
    { method: "POST" },
  );
}

/**
 * Enqueue an image generation job (same body as /generate). Returns job id.
 * `priority: "next"` inserts the job at the FRONT of the pending queue — it
 * runs right after the currently-running generation, before every other
 * queued job (queue-first Generate).
 */
export async function enqueueImageGenJob(
  baseUrl: string,
  req: Parameters<typeof generateImage>[1] & { priority?: "normal" | "next" },
): Promise<{ job_id: string }> {
  return imageGenFetch<{ job_id: string }>(imageGenUrl(baseUrl, "/jobs"), {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export async function listImageGenJobs(
  baseUrl: string,
  limit = 50,
): Promise<ImageGenJob[]> {
  return imageGenFetch<ImageGenJob[]>(
    imageGenUrl(baseUrl, `/jobs?limit=${encodeURIComponent(String(limit))}`),
  );
}

export async function getImageGenJob(
  baseUrl: string,
  jobId: string,
): Promise<ImageGenJob> {
  return imageGenFetch<ImageGenJob>(
    imageGenUrl(baseUrl, `/jobs/${encodeURIComponent(jobId)}`),
  );
}

/**
 * Cancel a queued OR RUNNING job / remove a finished one from the queue.
 * Running jobs flip to `cancel_requested: true` first, then to "cancelled".
 */
export async function cancelImageGenJob(
  baseUrl: string,
  jobId: string,
): Promise<void> {
  await imageGenFetch<unknown>(
    imageGenUrl(baseUrl, `/jobs/${encodeURIComponent(jobId)}`),
    { method: "DELETE" },
  );
}

// ── Prompt-matrix on-disk library + templates ────────────────────────────────

export interface PromptMatrixPaths {
  root: string;
  library: string;
  templates: string;
}

export async function getPromptMatrixPaths(
  baseUrl: string,
): Promise<PromptMatrixPaths> {
  return imageGenFetch<PromptMatrixPaths>(`${baseUrl}/prompt-matrix/paths`);
}

export async function getPromptMatrixLibrary(
  baseUrl: string,
): Promise<{ v: number; entries: unknown[] }> {
  return imageGenFetch<{ v: number; entries: unknown[] }>(
    `${baseUrl}/prompt-matrix/library`,
  );
}

export async function putPromptMatrixLibrary(
  baseUrl: string,
  entries: unknown[],
): Promise<{ v: number; entries: unknown[] }> {
  return imageGenFetch<{ v: number; entries: unknown[] }>(
    `${baseUrl}/prompt-matrix/library`,
    { method: "PUT", body: JSON.stringify({ entries }) },
  );
}

export async function getPromptMatrixTemplates(
  baseUrl: string,
): Promise<{ v: number; templates: unknown[] }> {
  return imageGenFetch<{ v: number; templates: unknown[] }>(
    `${baseUrl}/prompt-matrix/templates`,
  );
}

export async function putPromptMatrixTemplates(
  baseUrl: string,
  templates: unknown[],
): Promise<{ v: number; templates: unknown[] }> {
  return imageGenFetch<{ v: number; templates: unknown[] }>(
    `${baseUrl}/prompt-matrix/templates`,
    { method: "PUT", body: JSON.stringify({ templates }) },
  );
}

// ── Image-gen LoRA adapters ──────────────────────────────────────────────────

export interface ImageGenLoraInfo {
  id: string;
  repo_id: string;
  /** Human label — present on curated catalog rows; installed rows may omit. */
  name?: string;
  description?: string;
  weight_name: string | null;
  /** Base model family this LoRA was trained for (e.g. "sdxl", "flux", "z-image"). */
  base_family: string;
  size_bytes: number;
  added_at: string | null;
  /** Catalog entries the engine has not verified may carry this flag. */
  unverified?: boolean;
  /**
   * Where the LoRA came from ("hf" | "civitai").  Optional until every
   * engine build reports it.
   */
  source?: string;
  license?: string;
  installed?: boolean;
}

export interface ImageGenLoraList {
  installed: ImageGenLoraInfo[];
  catalog: ImageGenLoraInfo[];
}

/** GET /image-gen/loras — installed + downloadable catalog. 404 → throws (backend not landed yet; callers surface it LOUDLY). */
export async function listImageGenLoras(
  baseUrl: string,
): Promise<ImageGenLoraList> {
  return imageGenFetch<ImageGenLoraList>(imageGenUrl(baseUrl, "/loras"));
}

/**
 * A LoRA download reference: either a Hugging Face repo (id or URL, with an
 * optional specific weight file) or a Civitai link / numeric model id.
 */
export type LoraDownloadRef =
  | { repo_id: string; weight_name?: string }
  | { civitai: string };

/**
 * Classify a user-pasted LoRA/model reference into the wire shape the engine
 * expects.
 *
 * Civitai (→ `{ civitai }`):
 *   - `https://civitai.com/models/<id>[/<slug>]?modelVersionId=<ver>`
 *   - `https://civitai.red/models/<id>[/<slug>]?modelVersionId=<ver>`
 *     (.com = SFW front door, .red = full/NSFW — same DB, same path shape;
 *      only the host differs. Prefer keeping `?modelVersionId=` — multi-base
 *      pages resolve to the newest version without it.)
 *   - `https://civitai.com/api/download/models/<versionId>`
 *   - short form `civitai:<modelId>@<versionId>` (or `civitai:<modelId>`)
 *   - bare numeric model id
 *
 * Everything else (org/name repo ids and huggingface.co URLs) → `{ repo_id }`.
 */
export function classifyLoraRef(input: string): LoraDownloadRef {
  const t = input.trim();
  if (
    /civitai\.(com|red|green)/i.test(t) ||
    /^civitai:\d+/i.test(t) ||
    /^\d+$/.test(t)
  ) {
    return { civitai: t };
  }
  return { repo_id: t };
}

/**
 * POST /image-gen/loras/download — starts a DownloadManager download for a
 * LoRA (catalog entry, pasted HF repo id/URL, or Civitai link/id). Progress
 * arrives via the universal downloads SSE; the returned id joins it to a
 * specific entry.  Throws MediaGenHttpError — 401 means a Civitai API key is
 * required (Settings → API Keys).
 */
export async function downloadImageGenLora(
  baseUrl: string,
  req: LoraDownloadRef,
): Promise<{ download_id: string }> {
  return imageGenFetch<{ download_id: string }>(
    imageGenUrl(baseUrl, "/loras/download"),
    { method: "POST", body: JSON.stringify(req) },
  );
}

/** DELETE /image-gen/loras/{id} — remove an installed LoRA from disk. */
export async function deleteImageGenLora(
  baseUrl: string,
  loraId: string,
): Promise<void> {
  await imageGenFetch<unknown>(
    imageGenUrl(baseUrl, `/loras/${encodeURIComponent(loraId)}`),
    { method: "DELETE" },
  );
}

// ── Custom image models (checkpoints from Hugging Face / Civitai) ───────────

/**
 * A registered (or proposed) user-added checkpoint.  Wire mirror of the
 * engine's `CustomModelEntry` (app/api/image_gen_routes.py).  The FULL
 * object from /inspect is round-tripped unchanged to POST /custom-models —
 * never rebuild it client-side.
 */
export interface CustomImageModelEntry {
  /** Namespaced id: "custom/<sanitized-ref>". */
  model_id: string;
  name: string;
  source: "hf" | "civitai";
  /** HF repo id, or "civitai:<modelId>@<versionId>". */
  source_ref: string;
  /** Base model family (e.g. "sdxl", "flux"). "unknown" is not registerable. */
  family: string;
  pipeline_type: string;
  format: "diffusers" | "single_file";
  /** single_file: the checkpoint filename. */
  weight_name?: string | null;
  /** diffusers: the filtered file listing used for sizing. */
  files?: Array<Record<string, unknown>> | null;
  size_gb: number;
  requires_hf_token: boolean;
  /** Civitai direct download URL. */
  download_url?: string | null;
  civitai_model_id?: number | null;
  civitai_version_id?: number | null;
  vram_gb?: number;
  ram_gb?: number;
  added_at?: string | null;
}

/**
 * Inspect result: the proposed entry plus prominent, user-facing warnings
 * (community model, unknown license, …).  The UI must render `warnings`
 * visibly before the user confirms, and must honor `registerable` /
 * `refusal_reason` (family "unknown" etc. — the register route enforces the
 * same gate).
 */
export interface CustomImageModelInspectResult {
  entry: CustomImageModelEntry;
  warnings: string[];
  registerable: boolean;
  refusal_reason?: string | null;
}

/** Wire mirror of the engine's CustomModelRegisterResponse. */
export interface CustomImageModelRegisterResult {
  registered: boolean;
  model_id: string;
  already_registered?: boolean;
  queued?: boolean;
  download_id?: string | null;
  already_downloaded?: boolean;
}

/**
 * POST /image-gen/custom-models/inspect — resolve a pasted HF repo / Civitai
 * link into a proposed model entry.  400s (unresolvable ref, unsupported /
 * unknown family) throw MediaGenHttpError whose message is the engine's
 * user-facing explanation — render it verbatim.  Generous timeout: the
 * engine calls out to HF/Civitai APIs.
 */
export async function inspectCustomImageModel(
  baseUrl: string,
  ref: string,
): Promise<CustomImageModelInspectResult> {
  return imageGenFetch<CustomImageModelInspectResult>(
    imageGenUrl(baseUrl, "/custom-models/inspect"),
    { method: "POST", body: JSON.stringify({ ref }) },
    60_000,
  );
}

/**
 * POST /image-gen/custom-models — register a confirmed entry (the body IS
 * the entry object from /inspect) and queue its weights download through the
 * DownloadManager (progress via the standard downloads SSE, joined by
 * model_id / category "image_gen").
 */
export async function registerCustomImageModel(
  baseUrl: string,
  entry: CustomImageModelEntry,
): Promise<CustomImageModelRegisterResult> {
  return imageGenFetch<CustomImageModelRegisterResult>(
    imageGenUrl(baseUrl, "/custom-models"),
    { method: "POST", body: JSON.stringify(entry) },
  );
}

/** DELETE /image-gen/custom-models/{id} — unregister a custom model (and its weights). */
export async function deleteCustomImageModel(
  baseUrl: string,
  modelId: string,
): Promise<void> {
  await imageGenFetch<unknown>(
    imageGenUrl(baseUrl, `/custom-models/${encodeURIComponent(modelId)}`),
    { method: "DELETE" },
  );
}

export interface ImageGenInstallStatus {
  status: "idle" | "running" | "complete" | "error" | "connected" | "waiting";
  stage: string;
  percent: number;
  message: string;
  error?: string;
  already_installed?: boolean;
  install_dir?: string;
  log_lines?: string[];
  /** True when this event is a raw pip log line, not a stage update */
  log?: boolean;
}

export async function startImageGenInstall(
  baseUrl: string,
): Promise<ImageGenInstallStatus> {
  return imageGenFetch<ImageGenInstallStatus>(
    imageGenUrl(baseUrl, "/install"),
    { method: "POST" },
  );
}

export async function getImageGenInstallStatus(
  baseUrl: string,
): Promise<ImageGenInstallStatus> {
  return imageGenFetch<ImageGenInstallStatus>(
    imageGenUrl(baseUrl, "/install/status"),
  );
}

/**
 * Open an SSE stream for image-gen install progress.
 * Returns a cleanup function.  Calls `onEvent` for each progress update.
 */
export function streamImageGenInstall(
  baseUrl: string,
  getToken: () => Promise<string | null>,
  onEvent: (e: ImageGenInstallStatus) => void,
): () => void {
  let closed = false;
  let es: EventSource | null = null;

  const connect = async () => {
    const token = await getToken();
    const url = token
      ? `${imageGenUrl(baseUrl, "/install/stream")}?token=${encodeURIComponent(token)}`
      : imageGenUrl(baseUrl, "/install/stream");
    es = new EventSource(url);
    es.onmessage = (ev) => {
      if (closed) return;
      try {
        const data = JSON.parse(ev.data) as ImageGenInstallStatus;
        onEvent(data);
        if (data.status === "complete" || data.status === "error") {
          es?.close();
        }
      } catch {
        // ignore parse errors
      }
    };
    es.onerror = () => {
      if (!closed) es?.close();
    };
  };

  void connect();
  return () => {
    closed = true;
    es?.close();
  };
}

// ── Video Generation Types ─────────────────────────────────────────────────

export interface VideoGenStatus {
  /** True only when packages are installed AND hardware is supported. */
  available: boolean;
  unavailable_reason: string | null;
  /** True when the shared image-gen package set (torch/diffusers) is present. */
  packages_installed: boolean;
  /** True when the detected hardware can run video generation at all. */
  hardware_supported: boolean;
  /** Human-readable reason when `hardware_supported` is false. */
  hardware_reason: string | null;
  loaded_model_id: string | null;
  is_loading: boolean;
  load_progress: number;
  /** LOUD model-load failure — see ImageGenStatus.load_error. */
  load_error?: string | null;
  device: "mps" | "cuda" | "cpu";
  /** The id of the currently-running job, if any. */
  active_job_id: string | null;
}

export interface VideoGenModelInfo {
  model_id: string;
  name: string;
  provider: string;
  /** diffusers pipeline family, e.g. "wan" | "ltx". */
  pipeline_type: string;
  description: string;
  license_name: string;
  vram_gb: number;
  ram_gb: number;
  default_width: number;
  default_height: number;
  default_num_frames: number;
  default_fps: number;
  max_num_frames: number;
  supports_image_to_video: boolean;
  supports_negative_prompt: boolean;
  model_card_url: string;
  requires_hf_token: boolean;
  quality_rating: number;
  speed_rating: number;
  tags: string[];
  /** Approximate on-disk download size of the model weights, in GB. */
  download_size_gb: number;
  is_downloaded: boolean;
  hardware_ok: boolean;
  hardware_reason: string | null;
}

export type VideoGenJobStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export interface VideoGenJob {
  job_id: string;
  status: VideoGenJobStatus;
  /** True after cancel was requested, until the status goes terminal. */
  cancel_requested?: boolean;
  /** 0..1 fractional progress. */
  progress: number;
  current_step: number;
  total_steps: number;
  elapsed_seconds: number;
  error: string | null;
  prompt: string;
  model_id: string;
  /**
   * Media-library id once completed. The engine has always sent these fields
   * (see app/api/video_gen_routes.py → VideoJobResponse); the client simply
   * never typed them, which is why a generated video had no identity and could
   * not be deleted, vaulted, inspected or remixed from the video surface.
   */
  item_id?: string | null;
  seed?: number | null;
  width?: number;
  height?: number;
  num_frames?: number;
  fps?: number;
}

export interface VideoGenRequest {
  prompt: string;
  negative_prompt?: string;
  model_id?: string;
  width?: number;
  height?: number;
  num_frames?: number;
  fps?: number;
  steps?: number;
  guidance?: number;
  seed?: number;
  /** Base64-encoded source image (no data: prefix) for image-to-video. */
  image_base64?: string;
  /** Extra pipeline kwargs merged into the diffusers call (user wins). */
  extra_params?: Record<string, unknown>;
}

// ── Video Generation API helper ────────────────────────────────────────────

function videoGenUrl(baseUrl: string, path: string): string {
  return `${baseUrl}/video-gen${path}`;
}

async function videoGenFetch<T>(
  url: string,
  options?: RequestInit,
  timeoutMs: number | null = MEDIA_GEN_TIMEOUT_MS,
): Promise<T> {
  const auth = await engine.getEngineAuthHeaders();
  const mergedHeaders = new Headers({
    "Content-Type": "application/json",
    ...auth,
  });
  if (options?.headers) {
    const extra = new Headers(options.headers);
    extra.forEach((value, key) => mergedHeaders.set(key, value));
  }
  const method = options?.method ?? "GET";
  let resp: Response;
  try {
    resp = await fetch(url, {
      signal: mediaGenTimeoutSignal(timeoutMs) ?? null,
      ...options,
      headers: mergedHeaders,
    });
  } catch (e) {
    if (isAbortTimeout(e) && timeoutMs !== null) {
      throw mediaGenTimeoutError("video-gen", method, url, timeoutMs);
    }
    throw e;
  }
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    let detail = body;
    try {
      const parsed = JSON.parse(body) as { detail?: unknown };
      if (parsed.detail) detail = stringifyErrorDetail(parsed.detail, body);
    } catch {
      // use raw body
    }
    try {
      const u = new URL(url);
      emitClientLog(
        "error",
        `[video-gen] ${method} ${u.pathname}${u.search} → HTTP ${resp.status}: ${detail.slice(0, 240)}`,
        "engine",
      );
    } catch {
      emitClientLog(
        "error",
        `[video-gen] ${method} request failed → HTTP ${resp.status}: ${detail.slice(0, 240)}`,
        "engine",
      );
    }
    const err = new Error(detail || `HTTP ${resp.status}`) as Error & {
      status?: number;
    };
    err.status = resp.status;
    throw err;
  }
  return resp.json() as Promise<T>;
}

export async function getVideoGenStatus(
  baseUrl: string,
): Promise<VideoGenStatus> {
  return videoGenFetch<VideoGenStatus>(videoGenUrl(baseUrl, "/status"));
}

export async function listVideoGenModels(
  baseUrl: string,
): Promise<VideoGenModelInfo[]> {
  return videoGenFetch<VideoGenModelInfo[]>(videoGenUrl(baseUrl, "/models"));
}

/**
 * Full parameter schema (common defaults + every advanced pipeline kwarg)
 * for one video model.  404s throw with the engine's detail message.
 */
export async function getVideoGenParams(
  baseUrl: string,
  model_id: string,
): Promise<MediaGenParams> {
  return videoGenFetch<MediaGenParams>(
    videoGenUrl(baseUrl, `/params/${encodeURIComponent(model_id)}`),
  );
}

/**
 * Trigger a background weights download for a video model.  Progress is
 * reported through the DownloadManager (category `video_gen`) and observed via
 * the `/downloads/stream` SSE — not by this call.
 */
export async function downloadVideoGenModel(
  baseUrl: string,
  model_id: string,
): Promise<{ queued: boolean }> {
  return videoGenFetch<{ queued: boolean }>(videoGenUrl(baseUrl, "/download"), {
    method: "POST",
    body: JSON.stringify({ model_id }),
  });
}

export async function loadVideoGenModel(
  baseUrl: string,
  model_id: string,
): Promise<MediaLoadResult> {
  return mediaGenLoad(baseUrl, "/video-gen", model_id);
}

export async function unloadVideoGenModel(baseUrl: string): Promise<void> {
  await videoGenFetch(videoGenUrl(baseUrl, "/unload"), { method: "POST" });
}

/** Enqueue a video generation job. Returns the new job id (202). */
export async function generateVideo(
  baseUrl: string,
  req: VideoGenRequest,
): Promise<{ job_id: string }> {
  return videoGenFetch<{ job_id: string }>(videoGenUrl(baseUrl, "/generate"), {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export async function getVideoGenJob(
  baseUrl: string,
  jobId: string,
): Promise<VideoGenJob> {
  return videoGenFetch<VideoGenJob>(
    videoGenUrl(baseUrl, `/jobs/${encodeURIComponent(jobId)}`),
  );
}

export async function listVideoGenJobs(
  baseUrl: string,
): Promise<VideoGenJob[]> {
  return videoGenFetch<VideoGenJob[]>(videoGenUrl(baseUrl, "/jobs"));
}

/**
 * Cancel a queued OR RUNNING video job (mirror of cancelImageGenJob).  A
 * running job flips to `cancel_requested: true` first; a diffusion step can
 * take tens of seconds to actually stop, so the UI shows "Cancelling…" until
 * the polled status goes terminal.
 */
export async function cancelVideoGenJob(
  baseUrl: string,
  jobId: string,
): Promise<void> {
  await videoGenFetch<unknown>(
    videoGenUrl(baseUrl, `/jobs/${encodeURIComponent(jobId)}`),
    { method: "DELETE" },
    15_000,
  );
}

/**
 * Fetch the finished mp4 for a job with auth headers and return an object URL
 * suitable for a `<video controls src=…>`.  The caller owns the returned URL
 * and must `URL.revokeObjectURL` it when done.
 */
export async function fetchVideoGenResult(
  baseUrl: string,
  jobId: string,
): Promise<string> {
  const auth = await engine.getEngineAuthHeaders();
  let resp: Response;
  try {
    resp = await fetch(
      videoGenUrl(baseUrl, `/jobs/${encodeURIComponent(jobId)}/result`),
      {
        headers: new Headers({ ...auth }),
        // Generous but bounded — an mp4 over loopback must not hang forever.
        signal: AbortSignal.timeout(120_000),
      },
    );
  } catch (e) {
    if (isAbortTimeout(e)) {
      throw mediaGenTimeoutError(
        "video-gen",
        "GET",
        videoGenUrl(baseUrl, `/jobs/${jobId}/result`),
        120_000,
      );
    }
    throw e;
  }
  if (!resp.ok) {
    const detail = await resp.text().catch(() => `HTTP ${resp.status}`);
    emitClientLog(
      "error",
      `[video-gen] GET /jobs/${jobId}/result → HTTP ${resp.status}: ${detail.slice(0, 240)}`,
      "engine",
    );
    throw new Error(detail || `HTTP ${resp.status}`);
  }
  const blob = await resp.blob();
  return URL.createObjectURL(blob);
}

// ── Media Library API ────────────────────────────────────────────────────────

export interface MediaLibraryItem {
  id: string;
  media_type: "image" | "video";
  model_id: string;
  prompt: string;
  negative_prompt: string | null;
  params: Record<string, unknown>;
  seed: number | null;
  width: number;
  height: number;
  num_frames?: number | null;
  fps?: number | null;
  elapsed_seconds: number;
  created_at: string;
  file_name: string;
  file_size_bytes: number;
  file_path: string;
  /**
   * Name of the stored img2img SOURCE image, when this item was generated from
   * one. Non-null means fetchMediaInitImage() serves those bytes — the "Remix"
   * action uses it to restore the input image, not just the settings.
   */
  init_image_file?: string | null;
}

export interface MediaLibraryListResponse {
  items: MediaLibraryItem[];
  /** Total matching items (before limit/offset) — for pagination. */
  total: number;
}

function mediaLibraryUrl(baseUrl: string, path: string): string {
  return `${baseUrl}/media-library${path}`;
}

async function mediaLibraryFetch<T>(
  url: string,
  options?: RequestInit,
): Promise<T> {
  const auth = await engine.getEngineAuthHeaders();
  const mergedHeaders = new Headers({
    "Content-Type": "application/json",
    ...auth,
  });
  if (options?.headers) {
    const extra = new Headers(options.headers);
    extra.forEach((value, key) => mergedHeaders.set(key, value));
  }
  const method = options?.method ?? "GET";
  // Bounded like imageGenFetch: a plain fetch against a HUNG (not dead)
  // engine never settles, and the Library list/delete would wait on it
  // forever with no error. Dead engines already reject fast (TypeError);
  // this cap covers the wedged-process case. Callers may pass their own
  // signal to override.
  let resp: Response;
  try {
    resp = await fetch(url, {
      signal: AbortSignal.timeout(MEDIA_GEN_TIMEOUT_MS),
      ...options,
      headers: mergedHeaders,
    });
  } catch (e) {
    // Translate the abort like imageGenFetch does — a raw DOMException
    // renders as WebKit's "The operation timed out." with no context.
    if (isAbortTimeout(e)) {
      throw mediaGenTimeoutError(
        "media-library",
        method,
        url,
        MEDIA_GEN_TIMEOUT_MS,
      );
    }
    throw e;
  }
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    let detail = body;
    try {
      const parsed = JSON.parse(body) as { detail?: unknown };
      if (parsed.detail) detail = stringifyErrorDetail(parsed.detail, body);
    } catch {
      // use raw body
    }
    try {
      const u = new URL(url);
      emitClientLog(
        "error",
        `[media-library] ${method} ${u.pathname}${u.search} → HTTP ${resp.status}: ${detail.slice(0, 240)}`,
        "engine",
      );
    } catch {
      emitClientLog(
        "error",
        `[media-library] ${method} request failed → HTTP ${resp.status}: ${detail.slice(0, 240)}`,
        "engine",
      );
    }
    throw new MediaGenHttpError(detail || `HTTP ${resp.status}`, resp.status);
  }
  return resp.json() as Promise<T>;
}

export async function listMediaLibraryItems(
  baseUrl: string,
  opts?: {
    media_type?: "image" | "video";
    limit?: number;
    offset?: number;
  },
): Promise<MediaLibraryListResponse> {
  const params = new URLSearchParams();
  if (opts?.media_type) params.set("media_type", opts.media_type);
  if (opts?.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts?.offset !== undefined) params.set("offset", String(opts.offset));
  const qs = params.toString();
  return mediaLibraryFetch<MediaLibraryListResponse>(
    mediaLibraryUrl(baseUrl, `/items${qs ? `?${qs}` : ""}`),
  );
}

/**
 * Fetch the raw bytes of a library item with auth headers and return an
 * object URL suitable for `<img src=…>` / `<video src=…>` (a plain src
 * attribute cannot carry the Authorization header).  The caller owns the
 * returned URL and must `URL.revokeObjectURL` it when done.
 */
export class MediaFileError extends Error {
  readonly status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.name = "MediaFileError";
    this.status = status;
  }
  /** The item is in the Private Vault and the vault is locked — NOT a failure:
   * it becomes readable the moment the user unlocks. Callers must treat this as
   * a retryable "locked" state, never as a missing item. */
  get isVaultLocked(): boolean {
    return this.status === 423;
  }
  /** The item is genuinely gone (unknown id, or the file was deleted on disk).
   * Retrying can never succeed. */
  get isGone(): boolean {
    return this.status === 404 || this.status === 410;
  }
}

export async function fetchMediaLibraryFile(
  baseUrl: string,
  itemId: string,
): Promise<string> {
  const auth = await engine.getEngineAuthHeaders();
  const resp = await fetch(
    mediaLibraryUrl(baseUrl, `/file/${encodeURIComponent(itemId)}`),
    { headers: new Headers({ ...auth }) },
  );
  if (!resp.ok) {
    const detail = await resp.text().catch(() => `HTTP ${resp.status}`);
    // A locked vault is an expected, user-resolvable state — logging it at
    // "error" is what turned one locked vault into 41 red lines in the issue
    // report.
    emitClientLog(
      resp.status === 423 ? "info" : "error",
      `[media-library] GET /file/${itemId} → HTTP ${resp.status}: ${detail.slice(0, 240)}`,
      "engine",
    );
    throw new MediaFileError(resp.status, detail || `HTTP ${resp.status}`);
  }
  const blob = await resp.blob();
  return URL.createObjectURL(blob);
}

/**
 * Small JPEG gallery thumb. The engine self-heals: a missing on-disk thumb
 * is generated from the full media, saved as ``<id>.thumb.jpg``, and returned.
 * Also resolves vaulted ids (423 when locked) — same contract as /file/{id}.
 * Caller owns the object URL and must revoke it.
 */
export async function fetchMediaLibraryThumb(
  baseUrl: string,
  itemId: string,
): Promise<string> {
  const auth = await engine.getEngineAuthHeaders();
  const resp = await fetch(
    mediaLibraryUrl(baseUrl, `/thumb/${encodeURIComponent(itemId)}`),
    {
      headers: new Headers({ ...auth }),
      // Thumb generation on a cold miss can take a moment for large PNGs /
      // video posters — longer than a JSON list call, shorter than a hang.
      signal: AbortSignal.timeout(MEDIA_GEN_TIMEOUT_MS),
    },
  );
  if (!resp.ok) {
    const detail = await resp.text().catch(() => `HTTP ${resp.status}`);
    emitClientLog(
      resp.status === 423 ? "info" : "error",
      `[media-library] GET /thumb/${itemId} → HTTP ${resp.status}: ${detail.slice(0, 240)}`,
      "engine",
    );
    throw new MediaFileError(resp.status, detail || `HTTP ${resp.status}`);
  }
  const blob = await resp.blob();
  return URL.createObjectURL(blob);
}

/**
 * The img2img SOURCE image an item was generated from, as a Blob.
 *
 * Only call this when the item's `init_image_file` is non-null — the engine
 * 404s otherwise. This is what makes "Remix" complete: the settings come from
 * the sidecar, the input image comes from here.
 */
export async function fetchMediaInitImage(
  baseUrl: string,
  itemId: string,
): Promise<Blob> {
  const auth = await engine.getEngineAuthHeaders();
  const resp = await fetch(
    mediaLibraryUrl(baseUrl, `/items/${encodeURIComponent(itemId)}/init-image`),
    { headers: new Headers({ ...auth }) },
  );
  if (!resp.ok) {
    const detail = await resp.text().catch(() => `HTTP ${resp.status}`);
    emitClientLog(
      "error",
      `[media-library] GET /items/${itemId}/init-image → HTTP ${resp.status}: ${detail.slice(0, 240)}`,
      "engine",
    );
    throw new MediaFileError(resp.status, detail || `HTTP ${resp.status}`);
  }
  return resp.blob();
}

export async function deleteMediaLibraryItem(
  baseUrl: string,
  itemId: string,
): Promise<void> {
  await mediaLibraryFetch<unknown>(
    mediaLibraryUrl(baseUrl, `/items/${encodeURIComponent(itemId)}`),
    { method: "DELETE" },
  );
}

// ── Media Vault API (password-protected private folder) ─────────────────────
//
// Same base URL + auth patterns as the media-library client above.
// HTTP 423 ("Locked") is a first-class signal — the vault auto-locked or was
// never unlocked — NOT an error condition. It is surfaced as VaultLockedError
// so callers can flip UI state to "locked" instead of toasting.

export interface MediaVaultStatus {
  exists: boolean;
  unlocked: boolean;
  item_count: number | null;
  auto_lock_seconds: number;
}

export interface MediaVaultOpResult {
  item_id: string;
  ok: boolean;
  error?: string;
}

export interface MediaVaultBatchResponse {
  results: MediaVaultOpResult[];
}

/** Thrown when the engine answers 423 — the vault is locked. Not an error toast. */
export class VaultLockedError extends Error {
  constructor() {
    super("Vault is locked");
    this.name = "VaultLockedError";
  }
}

/** Thrown when unlock/change-password is rejected with 403 — wrong password. */
export class VaultWrongPasswordError extends Error {
  constructor(detail?: string) {
    super(detail || "Wrong password");
    this.name = "VaultWrongPasswordError";
  }
}

function mediaVaultUrl(baseUrl: string, path: string): string {
  return `${baseUrl}/media-vault${path}`;
}

async function mediaVaultFetch<T>(
  url: string,
  options?: RequestInit,
): Promise<T> {
  const auth = await engine.getEngineAuthHeaders();
  const mergedHeaders = new Headers({
    "Content-Type": "application/json",
    ...auth,
  });
  if (options?.headers) {
    const extra = new Headers(options.headers);
    extra.forEach((value, key) => mergedHeaders.set(key, value));
  }
  const method = options?.method ?? "GET";
  const resp = await fetch(url, { ...options, headers: mergedHeaders });
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    let detail = body;
    try {
      const parsed = JSON.parse(body) as { detail?: unknown };
      if (parsed.detail) detail = stringifyErrorDetail(parsed.detail, body);
    } catch {
      // use raw body
    }
    // 423 = vault locked — an expected state transition, logged at info only.
    if (resp.status === 423) {
      emitClientLog(
        "info",
        `[media-vault] ${method} → 423 (vault locked)`,
        "engine",
      );
      throw new VaultLockedError();
    }
    if (resp.status === 403) {
      // Wrong password on unlock/change-password. Expected user error — no
      // error-level log spam, the UI shows it inline.
      throw new VaultWrongPasswordError(detail);
    }
    try {
      const u = new URL(url);
      emitClientLog(
        "error",
        `[media-vault] ${method} ${u.pathname}${u.search} → HTTP ${resp.status}: ${detail.slice(0, 240)}`,
        "engine",
      );
    } catch {
      emitClientLog(
        "error",
        `[media-vault] ${method} request failed → HTTP ${resp.status}: ${detail.slice(0, 240)}`,
        "engine",
      );
    }
    throw new MediaGenHttpError(detail || `HTTP ${resp.status}`, resp.status);
  }
  return resp.json() as Promise<T>;
}

export async function getMediaVaultStatus(
  baseUrl: string,
): Promise<MediaVaultStatus> {
  return mediaVaultFetch<MediaVaultStatus>(mediaVaultUrl(baseUrl, "/status"));
}

/** Create the vault (min 8 chars). Creates AND unlocks. */
export async function createMediaVault(
  baseUrl: string,
  password: string,
): Promise<void> {
  await mediaVaultFetch<unknown>(mediaVaultUrl(baseUrl, "/create"), {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

/** Unlock the vault. Throws VaultWrongPasswordError on 403. */
export async function unlockMediaVault(
  baseUrl: string,
  password: string,
): Promise<void> {
  await mediaVaultFetch<unknown>(mediaVaultUrl(baseUrl, "/unlock"), {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

export async function lockMediaVault(baseUrl: string): Promise<void> {
  await mediaVaultFetch<unknown>(mediaVaultUrl(baseUrl, "/lock"), {
    method: "POST",
  });
}

/**
 * List vault items (same shape as MediaLibraryItem). Throws VaultLockedError
 * on 423. Accepts either a bare array or an `{items: [...]}` envelope so the
 * client is robust to either serialization of the contract.
 */
export async function listMediaVaultItems(
  baseUrl: string,
): Promise<MediaLibraryItem[]> {
  const data = await mediaVaultFetch<
    MediaLibraryItem[] | { items: MediaLibraryItem[] }
  >(mediaVaultUrl(baseUrl, "/items"));
  return Array.isArray(data) ? data : data.items;
}

/**
 * Fetch the decrypted bytes of a vault item with auth headers and return an
 * object URL. Caller owns the URL and must revoke it. Throws VaultLockedError
 * on 423.
 */
export async function fetchMediaVaultFile(
  baseUrl: string,
  itemId: string,
): Promise<string> {
  const auth = await engine.getEngineAuthHeaders();
  const resp = await fetch(
    mediaVaultUrl(baseUrl, `/file/${encodeURIComponent(itemId)}`),
    { headers: new Headers({ ...auth }) },
  );
  if (!resp.ok) {
    if (resp.status === 423) throw new VaultLockedError();
    const detail = await resp.text().catch(() => `HTTP ${resp.status}`);
    emitClientLog(
      "error",
      `[media-vault] GET /file/${itemId} → HTTP ${resp.status}: ${detail.slice(0, 240)}`,
      "engine",
    );
    throw new Error(detail || `HTTP ${resp.status}`);
  }
  const blob = await resp.blob();
  return URL.createObjectURL(blob);
}

/** Move library items INTO the vault. Per-item results — never silent. */
export async function moveToMediaVault(
  baseUrl: string,
  itemIds: string[],
): Promise<MediaVaultBatchResponse> {
  return mediaVaultFetch<MediaVaultBatchResponse>(
    mediaVaultUrl(baseUrl, "/move"),
    { method: "POST", body: JSON.stringify({ item_ids: itemIds }) },
  );
}

/** Restore vault items back to the regular library. Per-item results. */
export async function restoreFromMediaVault(
  baseUrl: string,
  itemIds: string[],
): Promise<MediaVaultBatchResponse> {
  return mediaVaultFetch<MediaVaultBatchResponse>(
    mediaVaultUrl(baseUrl, "/restore"),
    { method: "POST", body: JSON.stringify({ item_ids: itemIds }) },
  );
}

/** Permanently delete a vault item. */
export async function deleteMediaVaultItem(
  baseUrl: string,
  itemId: string,
): Promise<void> {
  await mediaVaultFetch<unknown>(
    mediaVaultUrl(baseUrl, `/items/${encodeURIComponent(itemId)}`),
    { method: "DELETE" },
  );
}

/** Change the vault password. Throws VaultWrongPasswordError on 403. */
export async function changeMediaVaultPassword(
  baseUrl: string,
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  await mediaVaultFetch<unknown>(mediaVaultUrl(baseUrl, "/change-password"), {
    method: "POST",
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });
}
