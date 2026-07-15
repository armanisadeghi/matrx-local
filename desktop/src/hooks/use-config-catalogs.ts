/**
 * useConfigCatalogs — fetches real model catalogs, audio devices, and system
 * prompts so the Configurations page can display proper dropdowns instead of
 * dumb text inputs.
 *
 * All data is fetched once on mount and cached. Individual refresh methods
 * are exposed so sections can re-fetch when needed (e.g. after a model
 * download completes).
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { isTauri } from "@/lib/sidecar";
import { engine } from "@/lib/api";
import { overlayLlmCatalog } from "@/lib/llm/catalog";
import { overlayWhisperCatalog } from "@/lib/transcription/catalog";
import { systemPrompts, refreshBuiltinPrompts } from "@/lib/system-prompts";
import type { ModelOption } from "@/hooks/use-chat";
import type { LlmModelInfo, LlmHardwareResult } from "@/lib/llm/types";
import type {
  ModelInfo as WhisperModelInfo,
  HardwareDetectionResult,
  AudioDeviceInfo,
} from "@/lib/transcription/types";
import type { TtsVoice } from "@/lib/tts/types";
import { getTtsVoices } from "@/lib/tts/api";

// ── Types ────────────────────────────────────────────────────────────────────

export interface SystemPromptOption {
  id: string;
  name: string;
  category: string;
}

export interface ConfigCatalogs {
  /** Cloud AI models (Anthropic, OpenAI, Google, etc.) */
  chatModels: ModelOption[];
  chatModelsLoading: boolean;

  /** Local LLM models from Rust catalog (all available to download/use) */
  llmModels: LlmModelInfo[];
  /** Hardware recommendation for LLM */
  llmRecommended: string;
  llmModelsLoading: boolean;

  /** Whisper models from Rust catalog */
  whisperModels: WhisperModelInfo[];
  /** Hardware recommendation for Whisper */
  whisperRecommended: string;
  whisperModelsLoading: boolean;

  /** Available audio input devices from CPAL */
  audioDevices: AudioDeviceInfo[];
  audioDevicesLoading: boolean;

  /** System prompts (built-in + user-created) */
  systemPromptOptions: SystemPromptOption[];

  /** TTS voices from the Python engine */
  ttsVoices: TtsVoice[];
  ttsVoicesLoading: boolean;

  /** Refresh functions */
  refreshAudioDevices: () => Promise<void>;
  refreshLlmModels: () => Promise<void>;
  refreshWhisperModels: () => Promise<void>;
  refreshChatModels: () => Promise<void>;
  refreshTtsVoices: () => Promise<void>;
}

// ── Tauri invoke helper ──────────────────────────────────────────────────────

let _invoke:
  | ((cmd: string, args?: Record<string, unknown>) => Promise<unknown>)
  | null = null;

async function tauriInvoke<T>(
  cmd: string,
  args?: Record<string, unknown>,
): Promise<T> {
  if (!_invoke) {
    const mod = await import("@tauri-apps/api/core");
    _invoke = mod.invoke;
  }
  return _invoke!(cmd, args) as Promise<T>;
}

// ── Hook ─────────────────────────────────────────────────────────────────────

export function useConfigCatalogs(): ConfigCatalogs {
  // Chat models — start empty; populated exclusively from engine SQLite cache
  const [chatModels, setChatModels] = useState<ModelOption[]>([]);
  const [chatModelsLoading, setChatModelsLoading] = useState(false);

  // LLM models
  const [llmModels, setLlmModels] = useState<LlmModelInfo[]>([]);
  const [llmRecommended, setLlmRecommended] = useState("");
  const [llmModelsLoading, setLlmModelsLoading] = useState(false);

  // Whisper models
  const [whisperModels, setWhisperModels] = useState<WhisperModelInfo[]>([]);
  const [whisperRecommended, setWhisperRecommended] = useState("");
  const [whisperModelsLoading, setWhisperModelsLoading] = useState(false);

  // Audio devices
  const [audioDevices, setAudioDevices] = useState<AudioDeviceInfo[]>([]);
  const [audioDevicesLoading, setAudioDevicesLoading] = useState(false);

  // TTS voices
  const [ttsVoices, setTtsVoices] = useState<TtsVoice[]>([]);
  const [ttsVoicesLoading, setTtsVoicesLoading] = useState(false);

  // System prompts (sync, no loading state needed)
  const [systemPromptOptions, setSystemPromptOptions] = useState<
    SystemPromptOption[]
  >([]);

  const mountedRef = useRef(true);

  // ── Fetch chat models from engine ─────────────────────────────────────────
  const refreshChatModels = useCallback(async () => {
    if (!engine.engineUrl) return;
    setChatModelsLoading(true);
    try {
      const resp = await fetch(`${engine.engineUrl}/chat/models`);
      if (resp.ok) {
        const data = await resp.json();
        if (
          data.models &&
          Array.isArray(data.models) &&
          data.models.length > 0
        ) {
          const mapped: ModelOption[] = data.models.map(
            (
              m: {
                name: string;
                common_name: string;
                provider: string;
                is_primary?: boolean;
                is_premium?: boolean;
              },
              i: number,
            ) => ({
              id: m.name,
              label: m.common_name,
              provider: m.provider,
              default: i === 0,
              is_primary: m.is_primary,
              is_premium: m.is_premium,
            }),
          );
          if (mountedRef.current) setChatModels(mapped);
        }
      }
    } catch {
      // Engine unreachable — leave list empty; UI shows "not available" state
    } finally {
      if (mountedRef.current) setChatModelsLoading(false);
    }
  }, []);

  // ── Fetch LLM catalog from Tauri ──────────────────────────────────────────
  const refreshLlmModels = useCallback(async () => {
    if (!isTauri()) return;
    setLlmModelsLoading(true);
    try {
      // Hardware + recommendation from Rust; model list overlaid with the
      // remote catalog (engine /catalogs/llm_model; Rust consts = fallback).
      const result = await overlayLlmCatalog(
        await tauriInvoke<LlmHardwareResult>("detect_llm_hardware"),
      );
      if (mountedRef.current) {
        setLlmModels(result.all_models);
        setLlmRecommended(result.recommended_filename);
      }
    } catch {
      // Not available — leave empty
    } finally {
      if (mountedRef.current) setLlmModelsLoading(false);
    }
  }, []);

  // ── Fetch Whisper catalog from Tauri ──────────────────────────────────────
  const refreshWhisperModels = useCallback(async () => {
    if (!isTauri()) return;
    setWhisperModelsLoading(true);
    try {
      const result = await overlayWhisperCatalog(
        await tauriInvoke<HardwareDetectionResult>("detect_hardware"),
      );
      if (mountedRef.current) {
        setWhisperModels(result.all_models);
        setWhisperRecommended(result.recommended_filename);
      }
    } catch {
      // Not available — leave empty
    } finally {
      if (mountedRef.current) setWhisperModelsLoading(false);
    }
  }, []);

  // ── Fetch audio devices from Tauri ────────────────────────────────────────
  const refreshAudioDevices = useCallback(async () => {
    if (!isTauri()) return;
    setAudioDevicesLoading(true);
    try {
      const devices = await tauriInvoke<AudioDeviceInfo[]>(
        "list_audio_input_devices",
      );
      if (mountedRef.current) setAudioDevices(devices);
    } catch {
      // Not available
    } finally {
      if (mountedRef.current) setAudioDevicesLoading(false);
    }
  }, []);

  // ── Fetch TTS voices from the Python engine ──────────────────────────────
  const refreshTtsVoices = useCallback(async () => {
    if (!engine.engineUrl) return;
    setTtsVoicesLoading(true);
    try {
      const voices = await getTtsVoices();
      if (mountedRef.current) setTtsVoices(voices);
    } catch {
      // Engine unreachable or TTS not ready
    } finally {
      if (mountedRef.current) setTtsVoicesLoading(false);
    }
  }, []);

  // ── Load system prompts (sync from localStorage + remote builtins) ────────
  const refreshSystemPrompts = useCallback(() => {
    // Kick a remote-builtins refresh (fire-and-forget); when it lands it
    // dispatches "matrx-prompts-changed" for listeners.
    void refreshBuiltinPrompts();
    const all = systemPrompts.listAll();
    const options: SystemPromptOption[] = all.map((p) => ({
      id: p.id,
      name: p.name,
      category: p.category || "Custom",
    }));
    setSystemPromptOptions(options);
  }, []);

  // ── Fetch all on mount ────────────────────────────────────────────────────
  useEffect(() => {
    mountedRef.current = true;
    refreshChatModels();
    refreshLlmModels();
    refreshWhisperModels();
    refreshAudioDevices();
    refreshTtsVoices();
    refreshSystemPrompts();
    return () => {
      mountedRef.current = false;
    };
  }, [
    refreshChatModels,
    refreshLlmModels,
    refreshWhisperModels,
    refreshAudioDevices,
    refreshTtsVoices,
    refreshSystemPrompts,
  ]);

  return {
    chatModels,
    chatModelsLoading,
    llmModels,
    llmRecommended,
    llmModelsLoading,
    whisperModels,
    whisperRecommended,
    whisperModelsLoading,
    audioDevices,
    audioDevicesLoading,
    systemPromptOptions,
    ttsVoices,
    ttsVoicesLoading,
    refreshAudioDevices,
    refreshLlmModels,
    refreshWhisperModels,
    refreshChatModels,
    refreshTtsVoices,
  };
}
