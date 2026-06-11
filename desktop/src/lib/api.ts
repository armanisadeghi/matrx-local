/**
 * API client for communication with the Python/FastAPI sidecar engine.
 *
 * In development, the Python server runs standalone on a port (default 22140).
 * In production, Tauri spawns it as a managed sidecar process.
 */

import { emitClientLog } from "@/hooks/use-client-log";

const DEFAULT_PORT = 22140;
const DISCOVERY_PORTS = Array.from({ length: 20 }, (_, i) => DEFAULT_PORT + i);

export interface EngineHealth {
  status: string;
  service: string;
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
  metadata?: Record<string, unknown>;
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

/**
 * Thrown by invokeToolGuarded() when a required macOS permission is not granted.
 * The UI layer catches this and shows the PermissionsModal or PermissionDeniedBanner.
 */
export class PermissionRequiredError extends Error {
  constructor(
    public readonly permissionKey: string,
    public readonly permissionLabel: string,
    public readonly permissionDescription: string,
    public readonly tool: string,
  ) {
    super(
      `"${tool}" requires ${permissionLabel} access. ` +
        `Go to System Settings → Privacy & Security to grant it.`,
    );
    this.name = "PermissionRequiredError";
  }
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

  /** Register a function that provides the current Supabase JWT. */
  setTokenProvider(fn: () => Promise<string | null>) {
    this._getAccessToken = fn;
  }

  private async authHeaders(): Promise<Record<string, string>> {
    if (!this._getAccessToken) return {};
    const token = await this._getAccessToken();
    if (!token) return {};
    return { Authorization: `Bearer ${token}` };
  }

  /** Headers for authenticated engine HTTP calls (same JWT as tools, settings, etc.). */
  async getEngineAuthHeaders(): Promise<Record<string, string>> {
    return this.authHeaders();
  }

  /** Expose the current access token for SSE/EventSource connections that need ?token=. */
  async getAccessToken(): Promise<string | null> {
    if (!this._getAccessToken) return null;
    return this._getAccessToken();
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
      this.baseUrl = knownUrl;
      this.wsUrl = knownUrl.replace("http://", "ws://") + "/ws";
      return knownUrl;
    }

    for (const port of DISCOVERY_PORTS) {
      try {
        const resp = await fetch(`http://127.0.0.1:${port}/tools/list`, {
          signal: AbortSignal.timeout(500),
        });
        if (resp.ok) {
          this.baseUrl = `http://127.0.0.1:${port}`;
          this.wsUrl = `ws://127.0.0.1:${port}/ws`;
          return this.baseUrl;
        }
      } catch {
        continue;
      }
    }

    return null;
  }

  /** Check if the engine is reachable. */
  async isHealthy(): Promise<boolean> {
    if (!this.baseUrl) return false;
    try {
      const resp = await fetch(`${this.baseUrl}/tools/list`, {
        signal: AbortSignal.timeout(2000),
      });
      return resp.ok;
    } catch {
      return false;
    }
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
    if (!this.baseUrl) return;
    const resp = await fetch(`${this.baseUrl}/chat/local-llm/connect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port, model_name: modelName }),
    });
    if (!resp.ok)
      throw new Error(`Failed to connect local LLM: ${resp.status}`);
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
      headers: { "Content-Type": "application/json" },
    });
    if (!resp.ok)
      throw new Error(`Failed to disconnect local LLM: ${resp.status}`);
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
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const resp = await fetch(`${this.baseUrl}/chat/local-llm/status`);
    if (!resp.ok)
      throw new Error(`Failed to get local LLM status: ${resp.status}`);
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

  /** Download a pre-trained OWW model (returns when download completes). */
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

  /**
   * Maps tool names to the macOS permission keys they require.
   * Used by invokeToolGuarded() to perform pre-flight checks.
   */
  static readonly TOOL_PERMISSION_REQUIREMENTS: Readonly<
    Record<string, ReadonlyArray<string>>
  > = {
    // Audio
    RecordAudio: ["microphone"],
    TranscribeAudio: ["microphone"],
    ListAudioDevices: ["microphone"],
    PlayAudio: ["microphone"],
    // Screen
    Screenshot: ["screen_recording"],
    BrowserScreenshot: ["screen_recording"],
    // Keyboard / mouse automation
    TypeText: ["accessibility"],
    Hotkey: ["accessibility"],
    MouseClick: ["accessibility"],
    MouseMove: ["accessibility"],
    // Window management
    ListWindows: ["accessibility"],
    FocusWindow: ["accessibility"],
    MoveWindow: ["accessibility"],
    MinimizeWindow: ["accessibility"],
    // App automation
    LaunchApp: ["accessibility"],
    FocusApp: ["accessibility"],
    KillProcess: ["accessibility"],
    // AppleScript — requires both accessibility and Apple Events
    AppleScript: ["accessibility", "automation"],
    PowerShellScript: ["automation"],
    // File system (Full Disk Access for paths outside app sandbox)
    ReadFile: ["full_disk_access"],
    WriteFile: ["full_disk_access"],
    DeleteFile: ["full_disk_access"],
    ListDirectory: ["full_disk_access"],
    SearchFiles: ["full_disk_access"],
    WatchDirectory: ["full_disk_access"],
    // Personal data
    SearchContacts: ["contacts"],
    GetContact: ["contacts"],
    ListEvents: ["calendar"],
    CreateEvent: ["calendar"],
    SearchPhotos: ["photos"],
    GetPhoto: ["photos"],
    // Bluetooth / local network
    BluetoothDevices: ["bluetooth"],
    ConnectedDevices: ["bluetooth", "local_network"],
    WifiNetworks: ["local_network"],
    NetworkScan: ["local_network"],
    MDNSDiscover: ["local_network"],
    // Location
    GetLocation: ["location"],
    // Input monitoring
    MonitorInput: ["input_monitoring"],
    // Reminders
    ListReminders: ["reminders"],
    CreateReminder: ["reminders"],
    // Messages (iMessage/SMS)
    ListMessages: ["messages", "full_disk_access"],
    ListConversations: ["messages", "full_disk_access"],
    SendMessage: ["messages", "automation"],
    // Mail
    ListEmails: ["mail", "automation"],
    SendEmail: ["mail", "automation"],
    GetEmailAccounts: ["mail", "automation"],
    // Speech Recognition
    TranscribeWithAppleSpeech: ["speech_recognition", "microphone"],
    ListSpeechLocales: ["speech_recognition"],
  } as const;

  /**
   * Invoke a tool with pre-flight permission checking.
   *
   * If the required permission is already granted (or unknown), proceeds normally.
   * If a required permission is denied or not_determined, throws a
   * PermissionRequiredError instead of calling the engine.
   *
   * The caller (UI layer) should catch PermissionRequiredError and display the
   * PermissionsModal or PermissionDeniedBanner.
   *
   * @param tool - Tool name matching TOOL_PERMISSION_REQUIREMENTS keys
   * @param input - Tool parameters
   * @param permissionSnapshot - Current permission states from usePermissions hook
   */
  async invokeToolGuarded(
    tool: string,
    input: Record<string, unknown>,
    permissionSnapshot: Map<
      string,
      { status: string; label: string; description: string }
    >,
  ): Promise<ToolResult> {
    const required = EngineAPI.TOOL_PERMISSION_REQUIREMENTS[tool];
    if (required) {
      for (const key of required) {
        const state = permissionSnapshot.get(key);
        if (state && state.status !== "granted" && state.status !== "unknown") {
          throw new PermissionRequiredError(
            key,
            state.label,
            state.description,
            tool,
          );
        }
      }
    }
    return this.invokeTool(tool, input);
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
    const token = this._getAccessToken ? await this._getAccessToken() : null;
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

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    if (!this.baseUrl) throw new Error("Engine not discovered");
    const authHdrs = await this.authHeaders();
    const resp = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        ...authHdrs,
        ...(init?.headers as Record<string, string> | undefined),
      },
    });
    if (!resp.ok)
      throw new Error(
        `${init?.method ?? "GET"} ${path} failed: ${resp.status}`,
      );
    return resp.json();
  }

  async get(path: string): Promise<unknown> {
    return this.request(path);
  }

  async post(path: string, body: unknown): Promise<unknown> {
    return this.request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  async put(path: string, body: unknown): Promise<unknown> {
    return this.request(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  async delete(path: string): Promise<unknown> {
    return this.request(path, { method: "DELETE" });
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
        token: resp.token ?? undefined,
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
        body: body ? JSON.stringify(body) : undefined,
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

  /** List shares. */
  async listShares(userId: string): Promise<DocShare[]> {
    return this.docRequest("GET", "/shares", undefined, userId);
  }

  /** Create a share. */
  async createShare(userId: string, data: CreateShareData): Promise<DocShare> {
    return this.docRequest("POST", "/shares", data, userId);
  }

  /** Update a share. */
  async updateShare(
    shareId: string,
    userId: string,
    data: { permission?: string; is_public?: boolean },
  ): Promise<DocShare> {
    return this.docRequest("PUT", `/shares/${shareId}`, data, userId);
  }

  /** Delete a share. */
  async deleteShare(shareId: string, userId: string): Promise<void> {
    await this.docRequest("DELETE", `/shares/${shareId}`, undefined, userId);
  }

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

  async installCapability(
    capabilityId: string,
  ): Promise<InstallCapabilityResult> {
    return this.request<InstallCapabilityResult>("/capabilities/install", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ capability_id: capabilityId }),
    });
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
      signal: callbacks.signal,
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
      signal: callbacks.signal,
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
        const resp = await fetch(url, { signal: callbacks.signal });
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

export interface DocShare {
  id: string;
  note_id: string | null;
  folder_id: string | null;
  owner_id: string;
  shared_with_id: string | null;
  permission: string;
  is_public: boolean;
  public_token: string | null;
  created_at: string;
  updated_at: string;
  _direction?: "owned" | "shared_with_me";
}

export interface CreateShareData {
  note_id?: string;
  folder_id?: string;
  shared_with_id?: string;
  permission?: string;
  is_public?: boolean;
}

export interface SyncStatus {
  configured: boolean;
  device_id: string;
  last_sync_version: number;
  last_full_sync: number | null;
  tracked_files: number;
  conflicts: string[];
  conflict_count: number;
  watcher_active: boolean;
  base_dir: string;
  pending_push_count?: number;
  excluded_count?: number;
}

export interface SyncResult {
  pushed?: number;
  pulled?: number;
  conflicts?: number;
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

export interface DocMappings {
  cloud_mappings: Array<{
    id: string;
    folder_id: string;
    local_path: string;
    device_id: string;
  }>;
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

export interface InstallCapabilityResult {
  success: boolean;
  message: string;
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

export interface ExtensionSessionInfo {
  session_id: string;
  connected_at: number; // seconds since epoch
  last_seen_at: number; // seconds since epoch
  pending_calls: number;
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
  is_loading: boolean;
  load_progress: number;
}

export interface ImageGenResult {
  success: boolean;
  /** Base64-encoded PNG. Use as: `data:image/png;base64,${image_b64}` */
  image_b64?: string;
  width: number;
  height: number;
  model_id: string;
  elapsed_seconds: number;
  error?: string;
}

// ── Image Generation API helper ────────────────────────────────────────────

function imageGenUrl(baseUrl: string, path: string): string {
  return `${baseUrl}/image-gen${path}`;
}

async function imageGenFetch<T>(
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
  const resp = await fetch(url, {
    ...options,
    headers: mergedHeaders,
  });
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    let detail = body;
    try {
      const parsed = JSON.parse(body) as { detail?: string };
      if (parsed.detail) detail = parsed.detail;
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
    throw new Error(detail || `HTTP ${resp.status}`);
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

export async function loadImageGenModel(
  baseUrl: string,
  model_id: string,
): Promise<{ success: boolean; error?: string }> {
  return imageGenFetch(imageGenUrl(baseUrl, "/load"), {
    method: "POST",
    body: JSON.stringify({ model_id }),
  });
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
  },
): Promise<ImageGenResult> {
  return imageGenFetch<ImageGenResult>(imageGenUrl(baseUrl, "/generate"), {
    method: "POST",
    body: JSON.stringify(req),
  });
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
  return imageGenFetch<ImageGenResult>(
    imageGenUrl(baseUrl, "/generate-workflow"),
    {
      method: "POST",
      body: JSON.stringify(req),
    },
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
