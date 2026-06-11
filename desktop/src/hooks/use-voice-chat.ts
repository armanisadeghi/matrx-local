/**
 * use-voice-chat
 *
 * Owns the voice-chat state machine for the Confidential Chat inference tab.
 *
 * Phases:
 *   idle        — waiting; user can click mic or speak after wake word
 *   recording   — Whisper is capturing audio
 *   processing  — mic stopped, Rust flushing remaining audio buffers
 *   transcribed — transcript ready; waiting for user to click Send (manual mode)
 *   generating  — LLM is streaming a response
 *   speaking    — TTS is playing the response audio
 *
 * Two submission modes (controlled by `autoMode`):
 *   Manual   — user clicks Send to submit; TTS auto-plays the response.
 *   Auto     — silence detection auto-submits; TTS auto-plays; recording
 *              restarts automatically for the next turn.
 *
 * Barge-in (auto mode only):
 *   While TTS is playing, if liveRms exceeds BARGE_IN_THRESHOLD the
 *   response audio is cut and recording restarts immediately.
 *
 * Silence timeout:
 *   Read from settings (`voiceSilenceTimeoutMs`) at mount and whenever
 *   "matrx-settings-changed" fires.  Defaults to 1400ms.
 *   The silence timer is ONLY active during the "recording" phase —
 *   it is always cleared while generating or speaking so the system
 *   never shuts down because it thinks nothing is happening.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  TranscriptionState,
  TranscriptionActions,
} from "./use-transcription";
import type { UseChatTtsReturn } from "./use-chat-tts";
import type { TtsPlaybackState } from "./use-tts";
import { loadSettings } from "@/lib/settings";

export type VoiceChatPhase =
  | "idle"
  | "recording"
  | "processing"
  | "transcribed"
  | "generating"
  | "speaking";

export interface VoiceChatState {
  phase: VoiceChatPhase;
  isActive: boolean;
  autoMode: boolean;
  pendingTranscript: string;
  sessionBaseTranscript: string;
}

export interface VoiceChatActions {
  activate: () => void;
  deactivate: () => void;
  toggleRecording: () => void;
  sendPendingTranscript: () => void;
  stopSpeaking: () => void;
  setAutoMode: (on: boolean) => void;
  clearTranscript: () => void;
}

// liveRms threshold to detect barge-in while TTS is playing.
const BARGE_IN_THRESHOLD = 0.012;

// Hard floor / ceiling for the user-configurable silence timeout.
const SILENCE_TIMEOUT_MIN_MS = 400;
const SILENCE_TIMEOUT_MAX_MS = 30_000;

interface UseVoiceChatOptions {
  transcriptionState: TranscriptionState;
  transcriptionActions: TranscriptionActions;
  chatTts: UseChatTtsReturn;
  ttsPlaybackState: TtsPlaybackState;
  onSend: (text: string) => void;
  onStartTts: () => string | null;
  isGenerating: boolean;
  modelReady: boolean;
}

export function useVoiceChat({
  transcriptionState,
  transcriptionActions,
  chatTts,
  ttsPlaybackState,
  onSend,
  onStartTts,
  isGenerating,
  modelReady,
}: UseVoiceChatOptions): [VoiceChatState, VoiceChatActions] {
  const [isActive, setIsActive] = useState(false);
  const [autoMode, setAutoModeState] = useState(false);
  const [phase, setPhase] = useState<VoiceChatPhase>("idle");
  const [pendingTranscript, setPendingTranscript] = useState("");

  // User-configurable silence timeout (ms) — loaded from settings.
  const silenceTimeoutMsRef = useRef(1400);

  // Load silence timeout from settings on mount and on settings changes.
  useEffect(() => {
    let cancelled = false;
    const load = () => {
      loadSettings().then((s) => {
        if (cancelled) return;
        const ms = s.voiceSilenceTimeoutMs ?? 1400;
        silenceTimeoutMsRef.current = Math.min(
          SILENCE_TIMEOUT_MAX_MS,
          Math.max(SILENCE_TIMEOUT_MIN_MS, ms),
        );
      });
    };
    load();
    window.addEventListener("matrx-settings-changed", load);
    return () => {
      cancelled = true;
      window.removeEventListener("matrx-settings-changed", load);
    };
  }, []);

  const lastSegmentCountRef = useRef(0);
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sessionBaseTranscriptRef = useRef("");
  const autoSendFiredRef = useRef(false);
  const weStartedRef = useRef(false);
  const bargeInCooldownRef = useRef(false);
  const prevIsGeneratingRef = useRef(false);

  // ── Stable refs — updated every render so closures always see current values ──
  const chatTtsRef = useRef(chatTts);
  chatTtsRef.current = chatTts;

  const onStartTtsRef = useRef(onStartTts);
  onStartTtsRef.current = onStartTts;

  const onSendRef = useRef(onSend);
  onSendRef.current = onSend;

  const autoModeRef = useRef(autoMode);
  autoModeRef.current = autoMode;

  // phaseRef: lets timer callbacks check the current phase without being
  // captured in a stale closure.
  const phaseRef = useRef(phase);
  phaseRef.current = phase;

  // isRecordingRef: lets timer callbacks check live isRecording state —
  // fixes the stale closure bug where the silence timer could fire after
  // recording had already stopped.
  const isRecordingRef = useRef(transcriptionState.isRecording);
  isRecordingRef.current = transcriptionState.isRecording;

  // isActiveRef: lets timers check whether voice chat is still active
  // before calling state setters post-timeout.
  const isActiveRef = useRef(isActive);
  isActiveRef.current = isActive;

  const ttsStartTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stopSpeakingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );
  const bargeInTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── startRecordingSession (kept in ref so effects use latest version) ────
  const startRecordingSessionRef = useRef<(() => Promise<void>) | null>(null);
  const startRecordingSession = useCallback(async () => {
    if (!modelReady) return;
    sessionBaseTranscriptRef.current = transcriptionState.fullTranscript;
    lastSegmentCountRef.current = transcriptionState.segments.length;
    autoSendFiredRef.current = false;
    weStartedRef.current = true;
    setPendingTranscript("");
    await transcriptionActions.startRecording();
  }, [
    modelReady,
    transcriptionState.fullTranscript,
    transcriptionState.segments.length,
    transcriptionActions,
  ]);
  startRecordingSessionRef.current = startRecordingSession;

  const stopRecordingSession = useCallback(() => {
    if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);
    weStartedRef.current = false;
    transcriptionActions.stopRecording();
  }, [transcriptionActions]);

  // ── Sync phase with external transcription state ──────────────────────────
  // Handles the case where Rust reports isRecording=false and isProcessingTail=false
  // atomically in the same render batch by checking both flags together.

  useEffect(() => {
    if (!isActive) return;

    if (transcriptionState.isRecording && phase !== "recording") {
      setPhase("recording");
      autoSendFiredRef.current = false;
      return;
    }

    if (!transcriptionState.isRecording && phase === "recording") {
      if (transcriptionState.isProcessingTail) {
        setPhase("processing");
      } else {
        // Rust reported both flags false in the same batch — skip "processing"
        const newTranscript = transcriptionState.fullTranscript
          .slice(sessionBaseTranscriptRef.current.length)
          .trim();
        setPendingTranscript(newTranscript);

        if (autoModeRef.current && newTranscript && !autoSendFiredRef.current) {
          autoSendFiredRef.current = true;
          setPhase("generating");
          onSendRef.current(newTranscript);
        } else if (newTranscript) {
          setPhase("transcribed");
        } else {
          setPhase("idle");
        }
      }
      return;
    }

    if (
      !transcriptionState.isRecording &&
      !transcriptionState.isProcessingTail &&
      phase === "processing"
    ) {
      const newTranscript = transcriptionState.fullTranscript
        .slice(sessionBaseTranscriptRef.current.length)
        .trim();
      setPendingTranscript(newTranscript);

      if (autoModeRef.current && newTranscript && !autoSendFiredRef.current) {
        autoSendFiredRef.current = true;
        setPhase("generating");
        onSendRef.current(newTranscript);
      } else if (newTranscript) {
        setPhase("transcribed");
      } else {
        setPhase("idle");
      }
    }
  }, [
    isActive,
    transcriptionState.isRecording,
    transcriptionState.isProcessingTail,
    transcriptionState.fullTranscript,
    phase,
  ]);

  // ── Track LLM generating → done ──────────────────────────────────────────

  useEffect(() => {
    if (!isActive) return;
    const wasGenerating = prevIsGeneratingRef.current;
    prevIsGeneratingRef.current = isGenerating;

    if (wasGenerating && !isGenerating && phase === "generating") {
      if (ttsStartTimerRef.current) clearTimeout(ttsStartTimerRef.current);
      ttsStartTimerRef.current = setTimeout(() => {
        ttsStartTimerRef.current = null;
        // Guard: don't call state setters if the component deactivated
        if (!isActiveRef.current) return;
        const content = onStartTtsRef.current();
        if (content) {
          chatTtsRef.current.readCompleteMessage(content);
          setPhase("speaking");
        } else {
          setPhase("idle");
        }
      }, 80);
    }
  }, [isActive, isGenerating, phase]);

  // ── Track TTS done → restart recording (auto mode) ────────────────────────
  // Only fires when phase === "speaking" so it doesn't interfere with
  // other phases.  This also means we never auto-restart while generating.

  useEffect(() => {
    if (!isActive || !autoMode) return;
    if (phase === "speaking" && ttsPlaybackState === "idle") {
      setPhase("idle");
      void startRecordingSessionRef.current?.();
    }
  }, [isActive, autoMode, phase, ttsPlaybackState]);

  // ── Silence detection (auto mode) ─────────────────────────────────────────
  // CRITICAL: Only active during "recording" phase.
  // If phase transitions to anything else, the timeout is cleared.
  // Timer callbacks use `isRecordingRef` (not the stale closure value) to
  // verify that recording is still active before calling stopRecording().

  useEffect(() => {
    // Always clear the silence timer when phase changes away from "recording"
    if (phase !== "recording") {
      if (silenceTimerRef.current) {
        clearTimeout(silenceTimerRef.current);
        silenceTimerRef.current = null;
      }
      return;
    }

    if (!isActive || !autoMode) return;

    const newCount = transcriptionState.segments.length;
    if (newCount <= lastSegmentCountRef.current) return;
    lastSegmentCountRef.current = newCount;

    if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);
    silenceTimerRef.current = setTimeout(() => {
      silenceTimerRef.current = null;
      // Use refs (not closure-captured state) to guard against stale values.
      if (
        phaseRef.current === "recording" &&
        isRecordingRef.current && // ← live ref, not stale closure
        autoModeRef.current &&
        !autoSendFiredRef.current
      ) {
        transcriptionActions.stopRecording();
      }
    }, silenceTimeoutMsRef.current);
  }, [
    isActive,
    autoMode,
    phase,
    transcriptionState.segments,
    transcriptionActions,
  ]);

  // ── Barge-in detection (auto mode) ────────────────────────────────────────
  // Only active during "speaking" phase — not during "generating".

  useEffect(() => {
    if (!isActive || !autoMode) return;
    if (phase !== "speaking") return;
    if (ttsPlaybackState === "idle") return;
    if (bargeInCooldownRef.current) return;

    if (transcriptionState.liveRms > BARGE_IN_THRESHOLD) {
      bargeInCooldownRef.current = true;
      chatTtsRef.current.stopReadAloud();
      setPhase("idle");
      if (bargeInTimerRef.current) clearTimeout(bargeInTimerRef.current);
      bargeInTimerRef.current = setTimeout(() => {
        bargeInTimerRef.current = null;
        bargeInCooldownRef.current = false;
        if (isActiveRef.current) {
          void startRecordingSessionRef.current?.();
        }
      }, 300);
    }
  }, [isActive, autoMode, phase, ttsPlaybackState, transcriptionState.liveRms]);

  // ── Public actions ────────────────────────────────────────────────────────

  const activate = useCallback(() => {
    setIsActive(true);
    setPhase("idle");
    setPendingTranscript("");
  }, []);

  // deactivate uses isRecordingRef so its identity stays stable even as
  // transcriptionState.isRecording changes (fixes unstable actions object).
  const deactivate = useCallback(() => {
    if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);
    if (ttsStartTimerRef.current) clearTimeout(ttsStartTimerRef.current);
    if (stopSpeakingTimerRef.current)
      clearTimeout(stopSpeakingTimerRef.current);
    if (bargeInTimerRef.current) clearTimeout(bargeInTimerRef.current);

    if (isRecordingRef.current) transcriptionActions.stopRecording();
    chatTtsRef.current.stopReadAloud();
    setIsActive(false);
    setPhase("idle");
    setPendingTranscript("");
    weStartedRef.current = false;
    autoSendFiredRef.current = false;
  }, [transcriptionActions]);

  const toggleRecording = useCallback(() => {
    if (transcriptionState.isRecording || transcriptionState.isProcessingTail) {
      stopRecordingSession();
    } else {
      void startRecordingSession();
    }
  }, [
    transcriptionState.isRecording,
    transcriptionState.isProcessingTail,
    startRecordingSession,
    stopRecordingSession,
  ]);

  const sendPendingTranscript = useCallback(() => {
    if (!pendingTranscript.trim()) return;
    const text = pendingTranscript.trim();
    setPendingTranscript("");
    setPhase("generating");
    onSendRef.current(text);
  }, [pendingTranscript]);

  const stopSpeaking = useCallback(() => {
    chatTtsRef.current.stopReadAloud();
    setPhase("idle");
    if (autoModeRef.current) {
      if (stopSpeakingTimerRef.current)
        clearTimeout(stopSpeakingTimerRef.current);
      stopSpeakingTimerRef.current = setTimeout(() => {
        stopSpeakingTimerRef.current = null;
        if (isActiveRef.current) {
          void startRecordingSessionRef.current?.();
        }
      }, 400);
    }
  }, []);

  const setAutoMode = useCallback((on: boolean) => {
    setAutoModeState(on);
  }, []);

  // clearTranscript uses phaseRef so its identity stays stable across phase
  // changes (fixes unstable actions object per CLAUDE.md rules).
  const clearTranscript = useCallback(() => {
    setPendingTranscript("");
    if (phaseRef.current === "transcribed") setPhase("idle");
  }, []);

  // ── Cleanup on unmount ────────────────────────────────────────────────────

  useEffect(() => {
    return () => {
      if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);
      if (ttsStartTimerRef.current) clearTimeout(ttsStartTimerRef.current);
      if (stopSpeakingTimerRef.current)
        clearTimeout(stopSpeakingTimerRef.current);
      if (bargeInTimerRef.current) clearTimeout(bargeInTimerRef.current);
    };
  }, []);

  const state: VoiceChatState = {
    phase,
    isActive,
    autoMode,
    pendingTranscript,
    sessionBaseTranscript: sessionBaseTranscriptRef.current,
  };

  // Stable actions object — see React Patterns in CLAUDE.md.
  const actions: VoiceChatActions = useMemo(
    () => ({
      activate,
      deactivate,
      toggleRecording,
      sendPendingTranscript,
      stopSpeaking,
      setAutoMode,
      clearTranscript,
    }),
    [
      activate,
      deactivate,
      toggleRecording,
      sendPendingTranscript,
      stopSpeaking,
      setAutoMode,
      clearTranscript,
    ],
  );

  return [state, actions];
}
