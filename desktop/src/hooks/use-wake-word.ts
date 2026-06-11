/**
 * useWakeWord — unified wake word detection hook.
 *
 * Supports two backends, selectable at runtime via Settings:
 *
 *   "whisper" — Rust whisper-tiny engine (built-in, no model download needed,
 *               2-second inference windows, uses Tauri IPC + native events)
 *
 *   "oww"     — openWakeWord Python engine (ONNX, ~150ms latency, uses HTTP
 *               SSE stream from the FastAPI sidecar)
 *
 * Both backends emit the same internal events so WakeWordOverlay and
 * WakeWordControls require zero changes regardless of which engine is active.
 *
 * Engine preference is persisted in SQLite via PUT /settings/wake-word and
 * loaded on mount.  Changing the engine tears down the current backend and
 * starts the new one automatically.
 *
 * State machine (same for both engines):
 *   idle       — not started
 *   setup      — loading / downloading model
 *   listening  — actively detecting
 *   muted      — deliberately paused (fast resume)
 *   dismissed  — false-trigger 10s cooldown
 *   active     — wake word detected; transcription live; overlay shown
 */

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { isTauri } from "@/lib/sidecar";
import type { UnlistenFn } from "@tauri-apps/api/event";
import type {
  WakeWordMode,
  WakeWordDetectedEvent,
  WakeWordEngine,
  WakeWordSettings,
} from "@/lib/transcription/types";
import { engine as engineAPI } from "@/lib/api";

// ── Tauri IPC helpers (only used for whisper engine) ─────────────────────────

async function invoke<T>(
  cmd: string,
  args?: Record<string, unknown>,
): Promise<T> {
  const { invoke: tauriInvoke } = await import("@tauri-apps/api/core");
  return tauriInvoke<T>(cmd, args);
}

async function listen<T>(
  event: string,
  handler: (e: { payload: T }) => void,
): Promise<UnlistenFn> {
  const { listen: tauriListen } = await import("@tauri-apps/api/event");
  return tauriListen<T>(event, handler);
}

// ── Constants ─────────────────────────────────────────────────────────────────

/** After waking, auto-stop transcription after this many ms of silence. */
const ACTIVE_TIMEOUT_MS = 30_000;

// ── Public types ──────────────────────────────────────────────────────────────

export type WakeWordUIMode =
  | "idle" // not started
  | "setup" // loading / downloading model
  | "listening" // waiting for wake word
  | "muted" // explicitly muted
  | "dismissed" // false trigger cooldown
  | "active"; // wake word heard, transcription live

export interface WakeWordHookState {
  uiMode: WakeWordUIMode;
  /** Active backend engine */
  engine: WakeWordEngine;
  /** Live microphone RMS level (0–1) */
  listenRms: number;
  /** Raw segments text accumulated since waking */
  activeTranscript: string;
  /** Whether the whisper-tiny model (ggml-tiny.en.bin) is present */
  kmsModelReady: boolean;
  /** Always null — no separate download needed for whisper engine */
  downloadProgress: null;
  error: string | null;
}

export interface WakeWordHookActions {
  /** Load config, check model, and start listening. */
  setup: () => Promise<void>;
  /** Start listening (model must be ready). */
  startListening: (deviceName?: string) => Promise<void>;
  /** Stop and tear down the backend. */
  stopListening: () => Promise<void>;
  /** Mute without stopping. */
  mute: () => Promise<void>;
  /** Resume after mute. */
  unmute: () => Promise<void>;
  /** Dismiss a false trigger — suppresses for 10 s. */
  dismiss: () => Promise<void>;
  /** Fire wake-word as if spoken (the "Wake up" button). */
  manualTrigger: () => Promise<void>;
  /** Switch backend engine and persist the choice to SQLite. */
  setEngine: (e: WakeWordEngine) => Promise<void>;
  clearError: () => void;
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useWakeWord(
  onWake: () => Promise<void>,
  onSleep: () => Promise<void>,
  activeTranscript: string,
): [WakeWordHookState, WakeWordHookActions] {
  const [uiMode, setUiMode] = useState<WakeWordUIMode>("idle");
  const [activeEngine, setActiveEngine] = useState<WakeWordEngine>("whisper");
  const [listenRms, setListenRms] = useState(0);
  const [kmsModelReady, setKmsModelReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Tauri event unlisteners (whisper engine)
  const unlisteners = useRef<UnlistenFn[]>([]);
  // SSE EventSource (OWW engine)
  const sseRef = useRef<EventSource | null>(null);

  const activeTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Tracks the engine-switch restart delay so it can be cancelled on unmount.
  const engineSwitchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );
  const deviceNameRef = useRef<string | undefined>(undefined);
  // Prevent duplicate active→sleep transitions
  const isActiveRef = useRef(false);

  // Keep the latest onWake/onSleep in refs (same pattern as use-voice-chat).
  // The backend listeners are attached once and close over handleDetected at
  // attach time — without refs, the 30s auto-sleep would run a STALE onSleep
  // captured before the caller's session state existed (sessions were never
  // finalized because Voice's activeSessionId was null at attach time).
  const onWakeRef = useRef(onWake);
  const onSleepRef = useRef(onSleep);
  onWakeRef.current = onWake;
  onSleepRef.current = onSleep;

  // ── Cleanup helpers ────────────────────────────────────────────────────

  const clearActiveTimeout = useCallback(() => {
    if (activeTimeoutRef.current) {
      clearTimeout(activeTimeoutRef.current);
      activeTimeoutRef.current = null;
    }
  }, []);

  const teardownTauriListeners = useCallback(() => {
    unlisteners.current.forEach((fn) => fn());
    unlisteners.current = [];
  }, []);

  const teardownSse = useCallback(() => {
    if (sseRef.current) {
      sseRef.current.close();
      sseRef.current = null;
    }
  }, []);

  const teardownAll = useCallback(() => {
    teardownTauriListeners();
    teardownSse();
    clearActiveTimeout();
    if (engineSwitchTimerRef.current) {
      clearTimeout(engineSwitchTimerRef.current);
      engineSwitchTimerRef.current = null;
    }
    isActiveRef.current = false;
  }, [teardownTauriListeners, teardownSse, clearActiveTimeout]);

  useEffect(() => () => teardownAll(), [teardownAll]);

  // ── OS-level notification helper ──────────────────────────────────────

  const fireOsNotification = useCallback(async () => {
    if (!isTauri()) return;
    try {
      const { isPermissionGranted, requestPermission, sendNotification } =
        await import("@tauri-apps/plugin-notification");
      let granted = await isPermissionGranted();
      if (!granted) {
        const perm = await requestPermission();
        granted = perm === "granted";
      }
      if (granted) {
        sendNotification({
          title: "AI Matrx — Listening",
          body: "Wake word detected. Speak your command.",
        });
      }
    } catch {
      // Notification permission denied or plugin unavailable — non-critical
    }
  }, []);

  // ── Floating transcript overlay helpers ────────────────────────────────

  const showFloatingOverlay = useCallback(async () => {
    if (!isTauri()) return;
    try {
      const { invoke: tauriInvoke } = await import("@tauri-apps/api/core");
      await tauriInvoke("show_transcript_overlay");
    } catch {
      // Overlay window not yet implemented on this build — safe to ignore
    }
  }, []);

  const hideFloatingOverlay = useCallback(async () => {
    if (!isTauri()) return;
    try {
      const { invoke: tauriInvoke } = await import("@tauri-apps/api/core");
      await tauriInvoke("hide_transcript_overlay");
    } catch {
      /* ok */
    }
  }, []);

  // ── Shared wake / sleep logic (same for both engines) ─────────────────

  const handleDetected = useCallback(async () => {
    if (isActiveRef.current) return;
    isActiveRef.current = true;
    setUiMode("active");
    clearActiveTimeout();

    // Fire OS notification and show floating overlay immediately — before
    // waiting on transcription startup, so the user gets instant feedback
    // regardless of which app they're currently using.
    void fireOsNotification();
    void showFloatingOverlay();

    try {
      await onWakeRef.current();
    } catch {
      // transcription may already be running
    }
    activeTimeoutRef.current = setTimeout(async () => {
      isActiveRef.current = false;
      try {
        await onSleepRef.current();
      } finally {
        void hideFloatingOverlay();
        setUiMode("listening");
      }
    }, ACTIVE_TIMEOUT_MS);
  }, [
    clearActiveTimeout,
    fireOsNotification,
    showFloatingOverlay,
    hideFloatingOverlay,
  ]);

  // ── Load settings + check whisper model on mount ───────────────────────

  useEffect(() => {
    if (!isTauri()) return;
    let cancelled = false;

    (async () => {
      // Load persisted engine preference
      try {
        const settings: WakeWordSettings =
          await engineAPI.getWakeWordSettings();
        if (!cancelled) setActiveEngine(settings.engine);
      } catch {
        // Engine not yet discovered — keep default "whisper"
      }

      // Check whisper-tiny model presence (needed for whisper engine)
      try {
        const ready = await invoke<boolean>("check_kws_model_exists");
        if (!cancelled) setKmsModelReady(ready);
      } catch {
        if (!cancelled) setKmsModelReady(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Whisper engine: attach Tauri events ───────────────────────────────

  const attachWhisperListeners = useCallback(async () => {
    if (!isTauri()) return;

    const rmsUL = await listen<number>("wake-word-rms", (e) => {
      setListenRms(Math.min(e.payload, 1));
    });

    const detectedUL = await listen<WakeWordDetectedEvent>(
      "wake-word-detected",
      () => {
        void handleDetected();
      },
    );

    const modeUL = await listen<WakeWordMode>("wake-word-mode", (e) => {
      const m = e.payload;
      if (m === "listening") setUiMode("listening");
      else if (m === "muted") setUiMode("muted");
      else if (m === "dismissed") setUiMode("dismissed");
    });

    const errorUL = await listen<string>("wake-word-error", (e) => {
      setError(e.payload);
    });

    unlisteners.current.push(rmsUL, detectedUL, modeUL, errorUL);
  }, [handleDetected]);

  // ── OWW engine: attach SSE ────────────────────────────────────────────

  const attachOwwStream = useCallback(() => {
    if (!isTauri()) return;
    teardownSse();

    const es = engineAPI.owwStream();
    sseRef.current = es;

    es.addEventListener("wake-word-rms", (ev: MessageEvent) => {
      try {
        const val = JSON.parse(ev.data) as number;
        setListenRms(Math.min(val, 1));
      } catch {
        /* ignore */
      }
    });

    es.addEventListener("wake-word-detected", () => {
      void handleDetected();
    });

    es.addEventListener("wake-word-mode", (ev: MessageEvent) => {
      try {
        const m = JSON.parse(ev.data) as WakeWordMode;
        if (m === "listening") setUiMode("listening");
        else if (m === "muted") setUiMode("muted");
        else if (m === "dismissed") setUiMode("dismissed");
      } catch {
        /* ignore */
      }
    });

    es.addEventListener("wake-word-error", (ev: MessageEvent) => {
      try {
        setError(JSON.parse(ev.data) as string);
      } catch {
        /* ignore */
      }
    });

    es.onerror = () => {
      // SSE reconnects automatically; just show a transient warning
      setError("Wake word stream disconnected — reconnecting…");
    };
  }, [handleDetected, teardownSse]);

  // ── Actions ───────────────────────────────────────────────────────────

  const setup = useCallback(async () => {
    if (!isTauri()) return;
    setError(null);
    setUiMode("setup");

    if (activeEngine === "whisper") {
      const ready = await invoke<boolean>("check_kws_model_exists");
      if (!ready) {
        setError(
          "Speech to Text setup not complete. Go to Speech to Text → Setup tab and run Quick Setup first.",
        );
        setUiMode("idle");
        return;
      }
      setKmsModelReady(true);
    }

    await startListening(deviceNameRef.current);
  }, [activeEngine]); // eslint-disable-line react-hooks/exhaustive-deps

  const startListening = useCallback(
    async (deviceName?: string) => {
      if (!isTauri()) return;
      deviceNameRef.current = deviceName;
      setError(null);
      teardownAll();

      try {
        if (activeEngine === "whisper") {
          await attachWhisperListeners();
          await invoke("start_wake_word", { deviceName: deviceName ?? null });
        } else {
          await engineAPI.owwStart({ deviceName });
          attachOwwStream();
        }
        setUiMode("listening");
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        teardownAll();
        setUiMode("idle");
      }
    },
    [activeEngine, attachWhisperListeners, attachOwwStream, teardownAll],
  );

  const stopListening = useCallback(async () => {
    if (!isTauri()) return;
    clearActiveTimeout();
    isActiveRef.current = false;

    try {
      await onSleep();
    } catch {
      /* already stopped */
    }

    try {
      if (activeEngine === "whisper") {
        await invoke("stop_wake_word");
      } else {
        await engineAPI.owwStop();
      }
    } catch {
      /* already stopped */
    }

    teardownAll();
    void hideFloatingOverlay();
    setUiMode("idle");
    setListenRms(0);
  }, [
    activeEngine,
    clearActiveTimeout,
    onSleep,
    teardownAll,
    hideFloatingOverlay,
  ]);

  const mute = useCallback(async () => {
    if (!isTauri()) return;
    try {
      if (activeEngine === "whisper") {
        await invoke("mute_wake_word");
      } else {
        await engineAPI.owwMute();
      }
      setUiMode("muted");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [activeEngine]);

  const unmute = useCallback(async () => {
    if (!isTauri()) return;
    try {
      if (activeEngine === "whisper") {
        await invoke("unmute_wake_word");
      } else {
        await engineAPI.owwUnmute();
      }
      setUiMode("listening");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [activeEngine]);

  const dismiss = useCallback(async () => {
    if (!isTauri()) return;
    clearActiveTimeout();
    isActiveRef.current = false;
    try {
      await onSleep();
    } catch {
      /* ok */
    }
    void hideFloatingOverlay();
    try {
      if (activeEngine === "whisper") {
        await invoke("dismiss_wake_word");
      } else {
        await engineAPI.owwDismiss();
      }
      setUiMode("dismissed");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [activeEngine, clearActiveTimeout, onSleep, hideFloatingOverlay]);

  const manualTrigger = useCallback(async () => {
    if (!isTauri()) return;
    try {
      if (activeEngine === "whisper") {
        await invoke("trigger_wake_word");
      } else {
        await engineAPI.owwTrigger();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [activeEngine]);

  const setEngine = useCallback(
    async (newEngine: WakeWordEngine) => {
      if (newEngine === activeEngine) return;

      // Stop the currently running engine
      const wasListening = uiMode !== "idle";
      if (wasListening) {
        // If a wake session is live, finish it first — teardownAll() below
        // clears the 30s auto-sleep timer, so skipping this would leave
        // transcription recording with nothing left to ever stop it.
        if (uiMode === "active") {
          clearActiveTimeout();
          isActiveRef.current = false;
          try {
            await onSleepRef.current();
          } catch {
            /* already stopped */
          }
          void hideFloatingOverlay();
        }
        try {
          if (activeEngine === "whisper") {
            await invoke("stop_wake_word");
          } else {
            await engineAPI.owwStop();
          }
        } catch {
          /* ok */
        }
        teardownAll();
        setUiMode("idle");
      }

      setActiveEngine(newEngine);

      // Persist to SQLite
      try {
        const current = await engineAPI.getWakeWordSettings();
        await engineAPI.saveWakeWordSettings({ ...current, engine: newEngine });
      } catch {
        /* non-critical — preference just won't survive restart */
      }

      // Auto-start the new engine if we were previously listening.
      // Track the timer so teardownAll can cancel it if the component unmounts
      // in the 200ms window (prevents startListening on an unmounted hook).
      if (wasListening) {
        engineSwitchTimerRef.current = setTimeout(() => {
          engineSwitchTimerRef.current = null;
          void startListening(deviceNameRef.current);
        }, 200);
      }
    },
    [
      activeEngine,
      uiMode,
      teardownAll,
      startListening,
      clearActiveTimeout,
      hideFloatingOverlay,
    ],
  );

  const clearError = useCallback(() => setError(null), []);

  // ── Return (stable references — see React Patterns in CLAUDE.md) ────

  const state: WakeWordHookState = useMemo(
    () => ({
      uiMode,
      engine: activeEngine,
      listenRms,
      activeTranscript,
      kmsModelReady,
      downloadProgress: null,
      error,
    }),
    [uiMode, activeEngine, listenRms, activeTranscript, kmsModelReady, error],
  );

  const actions: WakeWordHookActions = useMemo(
    () => ({
      setup,
      startListening,
      stopListening,
      mute,
      unmute,
      dismiss,
      manualTrigger,
      setEngine,
      clearError,
    }),
    [
      setup,
      startListening,
      stopListening,
      mute,
      unmute,
      dismiss,
      manualTrigger,
      setEngine,
      clearError,
    ],
  );

  return [state, actions];
}
