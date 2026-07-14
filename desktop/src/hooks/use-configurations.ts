/**
 * useConfigurations — centralized configuration state management hook.
 *
 * Loads all settings from localStorage, provides per-section dirty tracking,
 * and exposes save/cancel at both section and global levels.
 *
 * Settings are persisted to localStorage on save and synced to the engine
 * and cloud as needed via the existing saveSetting/syncAllSettings pipeline.
 */

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  loadSettings,
  saveSettings,
  syncAllSettings,
  broadcastSettingsChanged,
  type AppSettings,
  type SyncResult,
} from "@/lib/settings";

/** Section names — each gets its own dirty tracking and save/cancel. */
export type ConfigSection =
  | "application"
  | "appearance"
  | "chatAi"
  | "localLlm"
  | "localLlmSampling"
  | "voice"
  | "tts"
  | "wakeWord"
  | "scraping"
  | "proxy"
  | "fileSync"
  | "notifications";

/**
 * Maps each section to the settings keys it owns.
 * Used for dirty-checking and partial saves.
 */
const SECTION_KEYS: Record<ConfigSection, (keyof AppSettings)[]> = {
  application: [
    "instanceName",
    "launchOnStartup",
    "minimizeToTray",
    "autoCheckUpdates",
    "updateCheckInterval",
    "extensionBroadcastEnabled",
  ],
  appearance: ["theme", "sidebarCollapsed"],
  chatAi: [
    "chatDefaultModel",
    "chatDefaultMode",
    "chatMaxConversations",
    "chatDefaultSystemPromptId",
  ],
  localLlm: [
    "llmDefaultModel",
    "llmDefaultGpuLayers",
    "llmDefaultContextLength",
    "llmAutoStartServer",
  ],
  localLlmSampling: [
    "llmChatTemperature",
    "llmChatTopP",
    "llmChatTopK",
    "llmChatMaxTokens",
    "llmReasoningTemperature",
    "llmReasoningTopP",
    "llmReasoningTopK",
    "llmReasoningMaxTokens",
    "llmEnableThinking",
    "llmToolCallTemperature",
    "llmToolCallTopP",
    "llmToolCallTopK",
    "llmStructuredOutputTemperature",
    "llmStreamMaxTokens",
  ],
  voice: [
    "transcriptionDefaultModel",
    "transcriptionAutoInit",
    "transcriptionAudioDevice",
    "transcriptionProcessingTimeout",
    "voiceAssistantSystemPromptId",
    "voiceSilenceTimeoutMs",
    "voiceRestorePromptOnExit",
  ],
  tts: [
    "ttsDefaultVoice",
    "ttsDefaultSpeed",
    "ttsAutoDownloadModel",
    "ttsFavoriteVoices",
    "ttsChatVoice",
    "ttsChatSpeed",
    "ttsNotificationVoice",
    "ttsReadAloudEnabled",
    "ttsReadAloudAutoPlay",
    "ttsStreamingThreshold",
    "ttsAutoCleanMarkdown",
  ],
  wakeWord: [
    "wakeWordEnabled",
    "wakeWordListenOnStartup",
    "wakeWordEngine",
    "wakeWordOwwModel",
    "wakeWordOwwThreshold",
    "wakeWordCustomKeyword",
  ],
  scraping: ["headlessScraping", "scrapeDelay"],
  proxy: ["proxyEnabled", "proxyPort", "tunnelEnabled"],
  // File Sync's mode selector applies immediately (via useFileSync.setMode →
  // saveSetting) rather than through draft/save — the key lives here so the
  // dirty machinery and cloud merge know it belongs to this section.
  fileSync: ["fileSyncMode"],
  notifications: ["notificationSound", "notificationSoundStyle"],
};

export interface ConfigurationsState {
  /** The working copy — reflects user's in-progress edits. */
  draft: AppSettings | null;
  /** The last-saved version — used for dirty comparison. */
  saved: AppSettings | null;
  /** Whether any section has unsaved changes. */
  isGlobalDirty: boolean;
  /** Per-section dirty flags. */
  sectionDirty: Record<ConfigSection, boolean>;
  /** True while settings are being saved. */
  isSaving: boolean;
  /** Error from last save attempt, if any. */
  saveError: string | null;
  /** Granular result from the last save: local / engine / cloud status. */
  lastSyncResult: SyncResult | null;
}

export interface ConfigurationsActions {
  /** Update a single setting in the draft. */
  set: <K extends keyof AppSettings>(key: K, value: AppSettings[K]) => void;
  /** Update multiple settings at once. */
  setMany: (updates: Partial<AppSettings>) => void;
  /** Save only the settings belonging to a specific section. */
  saveSection: (section: ConfigSection) => Promise<void>;
  /** Cancel changes for a specific section (revert to saved). */
  cancelSection: (section: ConfigSection) => void;
  /** Save all changes globally. */
  saveAll: () => Promise<void>;
  /** Cancel all changes globally. */
  cancelAll: () => void;
  /** Reload settings from storage. */
  reload: () => Promise<void>;
}

export function useConfigurations(): [
  ConfigurationsState,
  ConfigurationsActions,
] {
  const [draft, setDraft] = useState<AppSettings | null>(null);
  const [saved, setSaved] = useState<AppSettings | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [lastSyncResult, setLastSyncResult] = useState<SyncResult | null>(null);
  const initialLoadDone = useRef(false);

  // Load on mount
  useEffect(() => {
    if (initialLoadDone.current) return;
    initialLoadDone.current = true;
    loadSettings().then((s) => {
      setSaved(s);
      setDraft({ ...s });
    });
  }, []);

  // Reload from localStorage when another part of the app saves settings
  // (e.g. Settings.tsx, AppSidebar). Update `saved` to the fresh values so
  // dirty computation stays correct. Update `draft` only for keys the user
  // has NOT edited (draft[key] === saved[key]) to preserve in-progress edits.
  const savedRef = useRef<AppSettings | null>(null);
  useEffect(() => {
    savedRef.current = saved;
  }, [saved]);

  useEffect(() => {
    const onChanged = () => {
      loadSettings().then((fresh) => {
        setSaved(fresh);
        setDraft((prev) => {
          if (!prev) return { ...fresh };
          const prevSaved = savedRef.current;
          if (!prevSaved) return { ...fresh };
          const merged = { ...prev };
          (Object.keys(fresh) as (keyof AppSettings)[]).forEach((key) => {
            const draftVal = JSON.stringify(prev[key]);
            const oldSavedVal = JSON.stringify(prevSaved[key]);
            if (draftVal === oldSavedVal) {
              // User hasn't edited this key — accept the fresh value
              (merged as Record<string, unknown>)[key] = fresh[key];
            }
            // else: user has unsaved changes for this key — keep their draft value
          });
          return merged;
        });
      });
    };
    window.addEventListener("matrx-settings-changed", onChanged);
    return () =>
      window.removeEventListener("matrx-settings-changed", onChanged);
  }, []);

  // Compute per-section dirty flags
  const sectionDirty = useMemo(() => {
    const result = {} as Record<ConfigSection, boolean>;
    const sections = Object.keys(SECTION_KEYS) as ConfigSection[];
    for (const section of sections) {
      if (!draft || !saved) {
        result[section] = false;
        continue;
      }
      result[section] = SECTION_KEYS[section].some(
        (key) => JSON.stringify(draft[key]) !== JSON.stringify(saved[key]),
      );
    }
    return result;
  }, [draft, saved]);

  const isGlobalDirty = useMemo(
    () => Object.values(sectionDirty).some(Boolean),
    [sectionDirty],
  );

  const set = useCallback(
    <K extends keyof AppSettings>(key: K, value: AppSettings[K]) => {
      setDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
    },
    [],
  );

  const setMany = useCallback((updates: Partial<AppSettings>) => {
    setDraft((prev) => (prev ? { ...prev, ...updates } : prev));
  }, []);

  const saveSection = useCallback(
    async (section: ConfigSection) => {
      if (!draft || !saved) return;
      setIsSaving(true);
      setSaveError(null);
      setLastSyncResult(null);
      try {
        // Merge section keys from draft into saved
        const updated = { ...saved };
        for (const key of SECTION_KEYS[section]) {
          (updated as Record<string, unknown>)[key] = draft[key];
        }
        // Step 1: Write to localStorage (always succeeds)
        await saveSettings(updated);
        setSaved(updated);
        // Step 2+3: Push to engine (which writes to disk + cloud)
        const syncResult = await syncAllSettings();
        setLastSyncResult(syncResult);
        // Surface engine/cloud errors as saveError so the UI can show them
        if (syncResult.engine !== "ok" && syncResult.engine !== "skipped") {
          setSaveError(
            `Saved locally, but engine sync failed: ${syncResult.engine}`,
          );
        }
        // Notify other mounted components (Settings page, etc.) to reload
        broadcastSettingsChanged();
      } catch (err) {
        setSaveError(err instanceof Error ? err.message : String(err));
      } finally {
        setIsSaving(false);
      }
    },
    [draft, saved],
  );

  const cancelSection = useCallback(
    (section: ConfigSection) => {
      if (!saved) return;
      setDraft((prev) => {
        if (!prev) return prev;
        const restored = { ...prev };
        for (const key of SECTION_KEYS[section]) {
          (restored as Record<string, unknown>)[key] = saved[key];
        }
        return restored;
      });
    },
    [saved],
  );

  const saveAll = useCallback(async () => {
    if (!draft) return;
    setIsSaving(true);
    setSaveError(null);
    setLastSyncResult(null);
    try {
      await saveSettings(draft);
      setSaved({ ...draft });
      const syncResult = await syncAllSettings();
      setLastSyncResult(syncResult);
      if (syncResult.engine !== "ok" && syncResult.engine !== "skipped") {
        setSaveError(
          `Saved locally, but engine sync failed: ${syncResult.engine}`,
        );
      }
      broadcastSettingsChanged();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsSaving(false);
    }
  }, [draft]);

  const cancelAll = useCallback(() => {
    if (saved) {
      setDraft({ ...saved });
    }
  }, [saved]);

  const reload = useCallback(async () => {
    const s = await loadSettings();
    setSaved(s);
    setDraft({ ...s });
  }, []);

  const state: ConfigurationsState = {
    draft,
    saved,
    isGlobalDirty,
    sectionDirty,
    isSaving,
    saveError,
    lastSyncResult,
  };

  // Stable actions object — see React Patterns in CLAUDE.md.
  const actions: ConfigurationsActions = useMemo(
    () => ({
      set,
      setMany,
      saveSection,
      cancelSection,
      saveAll,
      cancelAll,
      reload,
    }),
    [set, setMany, saveSection, cancelSection, saveAll, cancelAll, reload],
  );

  return [state, actions];
}
