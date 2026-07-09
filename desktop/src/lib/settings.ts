import { isTauri, setCloseToTray } from "@/lib/sidecar";
import { engine } from "@/lib/api";

const STORAGE_KEY = "matrx-settings";

export interface AppSettings {
  // ── Application ─────────────────────────────────────────────────────
  launchOnStartup: boolean;
  minimizeToTray: boolean;
  theme: "dark" | "light" | "system";

  // ── Updates ─────────────────────────────────────────────────────────
  autoCheckUpdates: boolean;
  updateCheckInterval: number; // minutes between automatic checks (minimum 60)

  // ── Scraping ────────────────────────────────────────────────────────
  headlessScraping: boolean;
  scrapeDelay: string;

  // ── Proxy ───────────────────────────────────────────────────────────
  proxyEnabled: boolean;
  proxyPort: number;

  // ── Remote access ───────────────────────────────────────────────────
  tunnelEnabled: boolean;

  // ── Instance ────────────────────────────────────────────────────────
  instanceName: string;

  // ── Notifications ───────────────────────────────────────────────────
  notificationSound: boolean;
  notificationSoundStyle: "chime" | "alert" | "success" | "error";

  // ── Wake word / listen mode ─────────────────────────────────────────
  wakeWordEnabled: boolean;
  wakeWordListenOnStartup: boolean;
  wakeWordEngine: "whisper" | "oww";
  wakeWordOwwModel: string;
  wakeWordOwwThreshold: number;
  wakeWordCustomKeyword: string;

  // ── Chat & AI defaults ──────────────────────────────────────────────
  chatDefaultModel: string;
  chatDefaultMode: "chat" | "co-work" | "code";
  chatMaxConversations: number;
  chatDefaultSystemPromptId: string; // "" = use builtin assistant

  // ── Local LLM inference ─────────────────────────────────────────────
  llmDefaultModel: string; // filename of preferred local model ("" = auto)
  llmDefaultGpuLayers: number; // -1 = auto-detect
  llmDefaultContextLength: number;
  llmAutoStartServer: boolean; // start llama-server on app launch
  llmChatTemperature: number;
  llmChatTopP: number;
  llmChatTopK: number;
  llmChatMaxTokens: number;
  llmReasoningTemperature: number;
  llmReasoningTopP: number;
  llmReasoningTopK: number;
  llmReasoningMaxTokens: number;
  llmEnableThinking: boolean; // default thinking mode for reasoning
  llmToolCallTemperature: number;
  llmToolCallTopP: number;
  llmToolCallTopK: number;
  llmStructuredOutputTemperature: number;
  llmStreamMaxTokens: number;

  // ── Transcription / Voice ───────────────────────────────────────────
  transcriptionDefaultModel: string; // "" = auto (hardware-recommended)
  transcriptionAutoInit: boolean; // auto-initialize on app start
  transcriptionAudioDevice: string; // "" = system default
  transcriptionProcessingTimeout: number; // ms before force-reset

  // ── Text to Speech ──────────────────────────────────────────────────
  ttsDefaultVoice: string; // voice_id for the TTS page, default "af_heart"
  ttsDefaultSpeed: number; // 0.25-4.0, default 1.0
  ttsAutoDownloadModel: boolean; // auto-download on first visit
  ttsFavoriteVoices: string[]; // pinned voice IDs
  ttsChatVoice: string; // voice for chat read-aloud ("" = use ttsDefaultVoice)
  ttsChatSpeed: number; // speed for chat read-aloud (0 = use ttsDefaultSpeed)
  ttsNotificationVoice: string; // voice for notification read-aloud ("" = use ttsDefaultVoice)
  ttsReadAloudEnabled: boolean; // show read-aloud button on chat messages
  ttsReadAloudAutoPlay: boolean; // auto-play TTS for new assistant messages
  ttsStreamingThreshold: number; // char count above which streaming mode is used (0 = always stream)
  ttsAutoCleanMarkdown: boolean; // auto-clean markdown on TTS page before speaking

  // ── Voice Assistant (wake word + audio chat mode) ────────────────────
  /**
   * System prompt used when the voice assistant is active (wake word triggered
   * or voice tab open). "" = use the built-in voice-assistant prompt.
   */
  voiceAssistantSystemPromptId: string;
  /**
   * Silence duration in ms after the last Whisper segment before auto-submit
   * fires. Users who speak quickly may want 1000ms; slower speakers 3000-5000ms.
   */
  voiceSilenceTimeoutMs: number;
  /**
   * Whether to restore the previous system prompt when exiting voice mode
   * (true = restore; false = leave the voice prompt active).
   */
  voiceRestorePromptOnExit: boolean;

  // ── Extension bridge ────────────────────────────────────────────────
  /**
   * Enable the Supabase Broadcast plumb that lets the Chrome extension
   * reach this engine cross-machine when the local WebSocket bridge
   * isn't available. Default ON — only disable if you don't use the
   * matrx-extend extension or want to reduce outbound connections.
   */
  extensionBroadcastEnabled: boolean;

  // ── UI / Layout ─────────────────────────────────────────────────────
  sidebarCollapsed: boolean;
}

/** One storage path entry as returned by GET /settings/paths */
export interface StoragePath {
  name: string;
  label: string;
  current: string;
  default: string;
  is_custom: boolean;
  user_visible: boolean;
}

const DEFAULTS: AppSettings = {
  // Application
  launchOnStartup: false,
  minimizeToTray: true,
  theme: "dark",
  // Updates
  autoCheckUpdates: true,
  updateCheckInterval: 240,
  // Scraping
  headlessScraping: true,
  scrapeDelay: "1.0",
  // Proxy
  proxyEnabled: true,
  proxyPort: 22180,
  // Remote access
  tunnelEnabled: false,
  // Instance
  instanceName: "My Computer",
  // Notifications
  notificationSound: true,
  notificationSoundStyle: "chime",
  // Wake word
  wakeWordEnabled: true,
  wakeWordListenOnStartup: true,
  wakeWordEngine: "whisper",
  wakeWordOwwModel: "hey_jarvis",
  wakeWordOwwThreshold: 0.5,
  wakeWordCustomKeyword: "hey matrix",
  // Chat & AI — empty string means "use first model from DB" (no hard-coded model name)
  chatDefaultModel: "",
  chatDefaultMode: "chat",
  chatMaxConversations: 100,
  chatDefaultSystemPromptId: "",
  // Local LLM
  llmDefaultModel: "",
  llmDefaultGpuLayers: -1,
  llmDefaultContextLength: 8192,
  llmAutoStartServer: false,
  llmChatTemperature: 0.7,
  llmChatTopP: 0.8,
  llmChatTopK: 20,
  llmChatMaxTokens: 1024,
  llmReasoningTemperature: 0.6,
  llmReasoningTopP: 0.95,
  llmReasoningTopK: 20,
  llmReasoningMaxTokens: 4096,
  llmEnableThinking: false,
  llmToolCallTemperature: 0.7,
  llmToolCallTopP: 0.8,
  llmToolCallTopK: 20,
  llmStructuredOutputTemperature: 0.1,
  llmStreamMaxTokens: 1024,
  // Transcription
  transcriptionDefaultModel: "",
  transcriptionAutoInit: true,
  transcriptionAudioDevice: "",
  transcriptionProcessingTimeout: 15000,
  // Text to Speech
  ttsDefaultVoice: "af_heart",
  ttsDefaultSpeed: 1.0,
  ttsAutoDownloadModel: false,
  ttsFavoriteVoices: [],
  ttsChatVoice: "",
  ttsChatSpeed: 0,
  ttsNotificationVoice: "",
  ttsReadAloudEnabled: true,
  ttsReadAloudAutoPlay: false,
  ttsStreamingThreshold: 200,
  ttsAutoCleanMarkdown: false,
  // Voice Assistant
  voiceAssistantSystemPromptId: "builtin-voice-assistant",
  voiceSilenceTimeoutMs: 1400,
  voiceRestorePromptOnExit: true,
  // Extension bridge
  extensionBroadcastEnabled: true,
  // UI
  sidebarCollapsed: false,
};

export async function loadSettings(): Promise<AppSettings> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      return { ...DEFAULTS, ...JSON.parse(raw) };
    }
  } catch (err) {
    console.error(
      '[settings] localStorage key "matrx-settings" contains invalid JSON — resetting to defaults.',
      err,
    );
  }
  return { ...DEFAULTS };
}

export async function saveSettings(settings: AppSettings): Promise<void> {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
}

export async function saveSetting<K extends keyof AppSettings>(
  key: K,
  value: AppSettings[K],
): Promise<void> {
  const current = await loadSettings();
  current[key] = value;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(current));

  // Sync to native/engine side effects.
  await syncSetting(key, current);
}

/** Push a setting change to Tauri or the engine. */
async function syncSetting<K extends keyof AppSettings>(
  key: K,
  all: AppSettings,
): Promise<void> {
  try {
    switch (key) {
      case "launchOnStartup":
        if (isTauri()) {
          const { enable, disable } =
            await import("@tauri-apps/plugin-autostart");
          if (all.launchOnStartup) {
            await enable();
          } else {
            await disable();
          }
        }
        break;

      case "minimizeToTray":
        await setCloseToTray(all.minimizeToTray);
        break;

      case "headlessScraping":
      case "scrapeDelay":
        if (engine.engineUrl) {
          await engine.updateSettings({
            headless_scraping: all.headlessScraping,
            scrape_delay: parseFloat(all.scrapeDelay) || 1.0,
          });
        }
        break;

      case "proxyEnabled":
        if (engine.engineUrl) {
          if (all.proxyEnabled) {
            await engine.proxyStart(all.proxyPort);
          } else {
            await engine.proxyStop();
          }
        }
        break;

      case "proxyPort":
        // Port changes require restart of proxy
        if (engine.engineUrl && all.proxyEnabled) {
          await engine.proxyStop();
          await engine.proxyStart(all.proxyPort);
        }
        break;

      case "tunnelEnabled":
        if (engine.engineUrl) {
          if (all.tunnelEnabled) {
            // Tunnel start can take up to ~63s server-side (two 30s
            // cloudflared URL-wait attempts + a 3s backoff), which exceeds
            // the generic 60s request timeout — give it explicit headroom.
            await engine.post(
              "/tunnel/start",
              {},
              { signal: AbortSignal.timeout(90_000) },
            );
          } else {
            await engine.post("/tunnel/stop", {});
          }
        }
        break;
    }
  } catch (err) {
    console.warn(`[settings] Failed to sync ${key}:`, err);
  }
}

export interface SyncResult {
  /** localStorage write always succeeds (it's synchronous under the hood). */
  local: "ok";
  /** Engine push: "ok" | "skipped" (not connected) | error message string */
  engine: "ok" | "skipped" | string;
  /** Cloud push: "ok" | "skipped" | error message string — the engine does this */
  cloud: "ok" | "skipped" | string;
}

/**
 * Sync ALL settings to their native/engine counterparts.
 *
 * Returns a structured result for each sync step so callers can surface
 * per-step success/failure to the user instead of silently swallowing errors.
 */
export async function syncAllSettings(): Promise<SyncResult> {
  const settings = await loadSettings();
  const result: SyncResult = {
    local: "ok",
    engine: "skipped",
    cloud: "skipped",
  };

  // ── Tauri-side sync ─────────────────────────────────────────────────────
  await setCloseToTray(settings.minimizeToTray);

  if (isTauri()) {
    try {
      const { enable, disable, isEnabled } =
        await import("@tauri-apps/plugin-autostart");
      const current = await isEnabled();
      if (settings.launchOnStartup && !current) await enable();
      if (!settings.launchOnStartup && current) await disable();
    } catch (err) {
      console.warn("[settings] Failed to sync autostart:", err);
    }
  }

  if (!engine.engineUrl) {
    return result;
  }

  // ── Push ALL settings to Python engine ──────────────────────────────────
  // The engine writes to ~/.matrx/settings.json and pushes to Supabase.
  try {
    const resp = await engine.updateCloudSettings(settingsToCloud(settings));
    result.engine = "ok";
    // The engine's push_result tells us if Supabase was updated
    const pushResult = (
      resp as { push_result?: { status?: string; reason?: string } }
    ).push_result;
    if (!pushResult) {
      result.cloud = "skipped"; // engine not configured for cloud yet
    } else if (pushResult.status === "pushed" || pushResult.status === "ok") {
      result.cloud = "ok";
    } else {
      result.cloud = pushResult.reason || pushResult.status || "unknown";
    }
  } catch (err) {
    result.engine = err instanceof Error ? err.message : String(err);
    result.cloud = "skipped";
    console.warn("[settings] Failed to push settings to engine:", err);
  }

  // Also sync engine-specific runtime settings (scraper config) — best effort
  try {
    await engine.updateSettings({
      headless_scraping: settings.headlessScraping,
      scrape_delay: parseFloat(settings.scrapeDelay) || 1.0,
    });
  } catch (err) {
    console.warn("[settings] Failed to sync engine runtime settings:", err);
  }

  return result;
}

/** Broadcast that settings changed so other mounted components can reload. */
export function broadcastSettingsChanged(): void {
  window.dispatchEvent(new CustomEvent("matrx-settings-changed"));
}

/**
 * Hydrate localStorage from the Python engine on startup.
 *
 * Called once when the engine first connects. Fetches the canonical
 * settings from Python (which may have been updated by cloud sync)
 * and merges them into localStorage. Python wins for any key it has.
 *
 * Returns the merged settings so the caller can update React state.
 */
export async function hydrateFromEngine(): Promise<AppSettings> {
  const local = await loadSettings();

  if (!engine.engineUrl) return local;

  try {
    const resp = await engine.getCloudSettings();
    if (resp?.settings && typeof resp.settings === "object") {
      const merged = mergeCloudSettings(local, resp.settings);
      await saveSettings(merged);
      return merged;
    }
  } catch (err) {
    console.warn("[settings] Failed to hydrate from engine:", err);
  }

  return local;
}

/** Helper: pick a cloud value or fall back to local. */
function cloudBool(
  cloud: Record<string, unknown>,
  key: string,
  fallback: boolean,
): boolean {
  return cloud[key] !== undefined ? Boolean(cloud[key]) : fallback;
}
function cloudNum(
  cloud: Record<string, unknown>,
  key: string,
  fallback: number,
): number {
  return cloud[key] !== undefined ? Number(cloud[key]) : fallback;
}
function cloudStr(
  cloud: Record<string, unknown>,
  key: string,
  fallback: string,
): string {
  return cloud[key] !== undefined ? String(cloud[key]) : fallback;
}

/**
 * Merge cloud settings into local settings.
 * Cloud settings use snake_case keys; local uses camelCase.
 */
export function mergeCloudSettings(
  local: AppSettings,
  cloud: Record<string, unknown>,
): AppSettings {
  return {
    ...local,
    // Application
    launchOnStartup: cloudBool(
      cloud,
      "launch_on_startup",
      local.launchOnStartup,
    ),
    minimizeToTray: cloudBool(cloud, "minimize_to_tray", local.minimizeToTray),
    theme: (cloud.theme as AppSettings["theme"]) || local.theme,
    // Updates
    autoCheckUpdates: cloudBool(
      cloud,
      "auto_check_updates",
      local.autoCheckUpdates,
    ),
    updateCheckInterval:
      cloud.update_check_interval !== undefined
        ? Math.max(60, Number(cloud.update_check_interval))
        : local.updateCheckInterval,
    // Scraping
    headlessScraping: cloudBool(
      cloud,
      "headless_scraping",
      local.headlessScraping,
    ),
    // Normalize scrape_delay to always have one decimal place (e.g. 1 → "1.0", 0.5 → "0.5")
    // so the preset dropdown correctly matches stored strings like "1.0", "0.5", etc.
    scrapeDelay:
      cloud.scrape_delay !== undefined
        ? (() => {
            const n = Number(cloud.scrape_delay);
            return isNaN(n)
              ? local.scrapeDelay
              : Number.isInteger(n)
                ? `${n}.0`
                : String(n);
          })()
        : local.scrapeDelay,
    // Proxy
    proxyEnabled: cloudBool(cloud, "proxy_enabled", local.proxyEnabled),
    proxyPort: cloudNum(cloud, "proxy_port", local.proxyPort),
    // Remote access
    tunnelEnabled: cloudBool(cloud, "tunnel_enabled", local.tunnelEnabled),
    // Instance
    instanceName: cloudStr(cloud, "instance_name", local.instanceName),
    // Notifications
    notificationSound: cloudBool(
      cloud,
      "notification_sound",
      local.notificationSound,
    ),
    notificationSoundStyle:
      (cloud.notification_sound_style as AppSettings["notificationSoundStyle"]) ||
      local.notificationSoundStyle,
    // Wake word
    wakeWordEnabled: cloudBool(
      cloud,
      "wake_word_enabled",
      local.wakeWordEnabled,
    ),
    wakeWordListenOnStartup: cloudBool(
      cloud,
      "wake_word_listen_on_startup",
      local.wakeWordListenOnStartup,
    ),
    wakeWordEngine:
      (cloud.wake_word_engine as AppSettings["wakeWordEngine"]) ||
      local.wakeWordEngine,
    wakeWordOwwModel: cloudStr(
      cloud,
      "wake_word_oww_model",
      local.wakeWordOwwModel,
    ),
    wakeWordOwwThreshold: cloudNum(
      cloud,
      "wake_word_oww_threshold",
      local.wakeWordOwwThreshold,
    ),
    wakeWordCustomKeyword: cloudStr(
      cloud,
      "wake_word_custom_keyword",
      local.wakeWordCustomKeyword,
    ),
    // Chat & AI
    chatDefaultModel: cloudStr(
      cloud,
      "chat_default_model",
      local.chatDefaultModel,
    ),
    chatDefaultMode:
      (cloud.chat_default_mode as AppSettings["chatDefaultMode"]) ||
      local.chatDefaultMode,
    chatMaxConversations: cloudNum(
      cloud,
      "chat_max_conversations",
      local.chatMaxConversations,
    ),
    chatDefaultSystemPromptId: cloudStr(
      cloud,
      "chat_default_system_prompt_id",
      local.chatDefaultSystemPromptId,
    ),
    // Local LLM
    llmDefaultModel: cloudStr(
      cloud,
      "llm_default_model",
      local.llmDefaultModel,
    ),
    llmDefaultGpuLayers: cloudNum(
      cloud,
      "llm_default_gpu_layers",
      local.llmDefaultGpuLayers,
    ),
    llmDefaultContextLength: cloudNum(
      cloud,
      "llm_default_context_length",
      local.llmDefaultContextLength,
    ),
    llmAutoStartServer: cloudBool(
      cloud,
      "llm_auto_start_server",
      local.llmAutoStartServer,
    ),
    llmChatTemperature: cloudNum(
      cloud,
      "llm_chat_temperature",
      local.llmChatTemperature,
    ),
    llmChatTopP: cloudNum(cloud, "llm_chat_top_p", local.llmChatTopP),
    llmChatTopK: cloudNum(cloud, "llm_chat_top_k", local.llmChatTopK),
    llmChatMaxTokens: cloudNum(
      cloud,
      "llm_chat_max_tokens",
      local.llmChatMaxTokens,
    ),
    llmReasoningTemperature: cloudNum(
      cloud,
      "llm_reasoning_temperature",
      local.llmReasoningTemperature,
    ),
    llmReasoningTopP: cloudNum(
      cloud,
      "llm_reasoning_top_p",
      local.llmReasoningTopP,
    ),
    llmReasoningTopK: cloudNum(
      cloud,
      "llm_reasoning_top_k",
      local.llmReasoningTopK,
    ),
    llmReasoningMaxTokens: cloudNum(
      cloud,
      "llm_reasoning_max_tokens",
      local.llmReasoningMaxTokens,
    ),
    llmEnableThinking: cloudBool(
      cloud,
      "llm_enable_thinking",
      local.llmEnableThinking,
    ),
    llmToolCallTemperature: cloudNum(
      cloud,
      "llm_tool_call_temperature",
      local.llmToolCallTemperature,
    ),
    llmToolCallTopP: cloudNum(
      cloud,
      "llm_tool_call_top_p",
      local.llmToolCallTopP,
    ),
    llmToolCallTopK: cloudNum(
      cloud,
      "llm_tool_call_top_k",
      local.llmToolCallTopK,
    ),
    llmStructuredOutputTemperature: cloudNum(
      cloud,
      "llm_structured_output_temperature",
      local.llmStructuredOutputTemperature,
    ),
    llmStreamMaxTokens: cloudNum(
      cloud,
      "llm_stream_max_tokens",
      local.llmStreamMaxTokens,
    ),
    // Transcription
    transcriptionDefaultModel: cloudStr(
      cloud,
      "transcription_default_model",
      local.transcriptionDefaultModel,
    ),
    transcriptionAutoInit: cloudBool(
      cloud,
      "transcription_auto_init",
      local.transcriptionAutoInit,
    ),
    transcriptionAudioDevice: cloudStr(
      cloud,
      "transcription_audio_device",
      local.transcriptionAudioDevice,
    ),
    transcriptionProcessingTimeout: cloudNum(
      cloud,
      "transcription_processing_timeout",
      local.transcriptionProcessingTimeout,
    ),
    // Text to Speech
    ttsDefaultVoice: cloudStr(
      cloud,
      "tts_default_voice",
      local.ttsDefaultVoice,
    ),
    ttsDefaultSpeed: cloudNum(
      cloud,
      "tts_default_speed",
      local.ttsDefaultSpeed,
    ),
    ttsAutoDownloadModel: cloudBool(
      cloud,
      "tts_auto_download_model",
      local.ttsAutoDownloadModel,
    ),
    ttsFavoriteVoices: Array.isArray(cloud.tts_favorite_voices)
      ? (cloud.tts_favorite_voices as string[])
      : local.ttsFavoriteVoices,
    ttsChatVoice: cloudStr(cloud, "tts_chat_voice", local.ttsChatVoice),
    ttsChatSpeed: cloudNum(cloud, "tts_chat_speed", local.ttsChatSpeed),
    ttsNotificationVoice: cloudStr(
      cloud,
      "tts_notification_voice",
      local.ttsNotificationVoice,
    ),
    ttsReadAloudEnabled: cloudBool(
      cloud,
      "tts_read_aloud_enabled",
      local.ttsReadAloudEnabled,
    ),
    ttsReadAloudAutoPlay: cloudBool(
      cloud,
      "tts_read_aloud_auto_play",
      local.ttsReadAloudAutoPlay,
    ),
    ttsStreamingThreshold: cloudNum(
      cloud,
      "tts_streaming_threshold",
      local.ttsStreamingThreshold,
    ),
    ttsAutoCleanMarkdown: cloudBool(
      cloud,
      "tts_auto_clean_markdown",
      local.ttsAutoCleanMarkdown,
    ),
    // Voice Assistant
    voiceAssistantSystemPromptId: cloudStr(
      cloud,
      "voice_assistant_system_prompt_id",
      local.voiceAssistantSystemPromptId,
    ),
    voiceSilenceTimeoutMs: cloudNum(
      cloud,
      "voice_silence_timeout_ms",
      local.voiceSilenceTimeoutMs,
    ),
    voiceRestorePromptOnExit: cloudBool(
      cloud,
      "voice_restore_prompt_on_exit",
      local.voiceRestorePromptOnExit,
    ),
    // Extension bridge
    extensionBroadcastEnabled: cloudBool(
      cloud,
      "extension_broadcast_enabled",
      local.extensionBroadcastEnabled,
    ),
    // UI
    sidebarCollapsed: cloudBool(
      cloud,
      "sidebar_collapsed",
      local.sidebarCollapsed,
    ),
  };
}

/**
 * Convert local camelCase settings to cloud snake_case format.
 */
export function settingsToCloud(
  settings: AppSettings,
): Record<string, unknown> {
  return {
    // Application
    launch_on_startup: settings.launchOnStartup,
    minimize_to_tray: settings.minimizeToTray,
    theme: settings.theme,
    // Updates
    auto_check_updates: settings.autoCheckUpdates,
    update_check_interval: settings.updateCheckInterval,
    // Scraping
    headless_scraping: settings.headlessScraping,
    scrape_delay: parseFloat(settings.scrapeDelay) || 1.0,
    // Proxy
    proxy_enabled: settings.proxyEnabled,
    proxy_port: settings.proxyPort,
    // Remote access
    tunnel_enabled: settings.tunnelEnabled,
    // Instance
    instance_name: settings.instanceName,
    // Notifications
    notification_sound: settings.notificationSound,
    notification_sound_style: settings.notificationSoundStyle,
    // Wake word
    wake_word_enabled: settings.wakeWordEnabled,
    wake_word_listen_on_startup: settings.wakeWordListenOnStartup,
    wake_word_engine: settings.wakeWordEngine,
    wake_word_oww_model: settings.wakeWordOwwModel,
    wake_word_oww_threshold: settings.wakeWordOwwThreshold,
    wake_word_custom_keyword: settings.wakeWordCustomKeyword,
    // Chat & AI
    chat_default_model: settings.chatDefaultModel,
    chat_default_mode: settings.chatDefaultMode,
    chat_max_conversations: settings.chatMaxConversations,
    chat_default_system_prompt_id: settings.chatDefaultSystemPromptId,
    // Local LLM
    llm_default_model: settings.llmDefaultModel,
    llm_default_gpu_layers: settings.llmDefaultGpuLayers,
    llm_default_context_length: settings.llmDefaultContextLength,
    llm_auto_start_server: settings.llmAutoStartServer,
    llm_chat_temperature: settings.llmChatTemperature,
    llm_chat_top_p: settings.llmChatTopP,
    llm_chat_top_k: settings.llmChatTopK,
    llm_chat_max_tokens: settings.llmChatMaxTokens,
    llm_reasoning_temperature: settings.llmReasoningTemperature,
    llm_reasoning_top_p: settings.llmReasoningTopP,
    llm_reasoning_top_k: settings.llmReasoningTopK,
    llm_reasoning_max_tokens: settings.llmReasoningMaxTokens,
    llm_enable_thinking: settings.llmEnableThinking,
    llm_tool_call_temperature: settings.llmToolCallTemperature,
    llm_tool_call_top_p: settings.llmToolCallTopP,
    llm_tool_call_top_k: settings.llmToolCallTopK,
    llm_structured_output_temperature: settings.llmStructuredOutputTemperature,
    llm_stream_max_tokens: settings.llmStreamMaxTokens,
    // Transcription
    transcription_default_model: settings.transcriptionDefaultModel,
    transcription_auto_init: settings.transcriptionAutoInit,
    transcription_audio_device: settings.transcriptionAudioDevice,
    transcription_processing_timeout: settings.transcriptionProcessingTimeout,
    // Text to Speech
    tts_default_voice: settings.ttsDefaultVoice,
    tts_default_speed: settings.ttsDefaultSpeed,
    tts_auto_download_model: settings.ttsAutoDownloadModel,
    tts_favorite_voices: settings.ttsFavoriteVoices,
    tts_chat_voice: settings.ttsChatVoice,
    tts_chat_speed: settings.ttsChatSpeed,
    tts_notification_voice: settings.ttsNotificationVoice,
    tts_read_aloud_enabled: settings.ttsReadAloudEnabled,
    tts_read_aloud_auto_play: settings.ttsReadAloudAutoPlay,
    tts_streaming_threshold: settings.ttsStreamingThreshold,
    tts_auto_clean_markdown: settings.ttsAutoCleanMarkdown,
    // Voice Assistant
    voice_assistant_system_prompt_id: settings.voiceAssistantSystemPromptId,
    voice_silence_timeout_ms: settings.voiceSilenceTimeoutMs,
    voice_restore_prompt_on_exit: settings.voiceRestorePromptOnExit,
    // Extension bridge
    extension_broadcast_enabled: settings.extensionBroadcastEnabled,
    // UI
    sidebar_collapsed: settings.sidebarCollapsed,
  };
}
