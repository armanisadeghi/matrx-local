import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import type { TtsStatus, TtsVoice, TtsLanguageGroup } from "@/lib/tts/types";
import {
  getTtsStatus,
  getTtsVoices,
  downloadTtsModel,
  synthesize,
  synthesizeStream,
  previewVoice,
  unloadTts,
  TtsStreamError,
} from "@/lib/tts/api";
import { loadSettings, saveSetting } from "@/lib/settings";
import { catchAndLog, logError, logWarn } from "@/lib/error-reporting";
import { audioBuffersToWavBlob } from "@/lib/tts/wav";

export interface TtsHistoryEntry {
  id: string;
  text: string;
  voiceId: string;
  voiceName: string;
  duration: number;
  elapsed: number;
  audioUrl: string;
  createdAt: number;
}

/**
 * Playback state machine:
 *  idle         — nothing happening
 *  synthesizing — fetch stream open, chunks arriving, audio being scheduled
 *  playing      — synthesis done (or in progress), AudioContext is running
 *  paused       — AudioContext suspended; position preserved
 */
export type TtsPlaybackState = "idle" | "synthesizing" | "playing" | "paused";

export interface TtsLastError {
  code: string;
  message: string;
}

export interface UseTtsState {
  status: TtsStatus | null;
  voices: TtsVoice[];
  languageGroups: TtsLanguageGroup[];
  selectedVoice: string;
  speed: number;
  /** @deprecated use playbackState */
  isSynthesizing: boolean;
  isDownloading: boolean;
  isPreviewPlaying: string | null;
  currentAudioUrl: string | null;
  currentDuration: number;
  currentElapsed: number;
  history: TtsHistoryEntry[];
  error: string | null;
  lastError: TtsLastError | null;
  playbackState: TtsPlaybackState;
}

export interface UseTtsActions {
  refreshStatus: () => Promise<void>;
  refreshVoices: () => Promise<void>;
  downloadModel: () => Promise<void>;
  setSelectedVoice: (voiceId: string) => void;
  setSpeed: (speed: number) => void;
  speak: (text: string) => Promise<void>;
  speakStreaming: (text: string) => Promise<void>;
  speakText: (
    text: string,
    voiceId?: string,
    speed?: number,
    signal?: AbortSignal,
  ) => Promise<void>;
  preview: (voiceId: string) => Promise<void>;
  stopAudio: () => void;
  pauseAudio: () => void;
  resumeAudio: () => void;
  unload: () => Promise<void>;
  clearHistory: () => void;
  clearError: () => void;
}

const MAX_HISTORY = 50;

function groupVoicesByLanguage(voices: TtsVoice[]): TtsLanguageGroup[] {
  const map = new Map<string, TtsVoice[]>();
  for (const v of voices) {
    const key = v.language;
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(v);
  }
  const groups: TtsLanguageGroup[] = [];
  for (const [language, list] of map) {
    groups.push({ language, lang_code: list[0].lang_code, voices: list });
  }
  return groups;
}

function errorToLastError(e: unknown): TtsLastError {
  if (e instanceof TtsStreamError) return { code: e.code, message: e.message };
  if (e instanceof Error) return { code: "client_error", message: e.message };
  return { code: "client_error", message: String(e) };
}

export function useTts(): [UseTtsState, UseTtsActions] {
  const [status, setStatus] = useState<TtsStatus | null>(null);
  const [voices, setVoices] = useState<TtsVoice[]>([]);
  const [languageGroups, setLanguageGroups] = useState<TtsLanguageGroup[]>([]);
  const [selectedVoice, setSelectedVoiceState] = useState("af_heart");
  const [speed, setSpeedState] = useState(1.0);

  useEffect(() => {
    loadSettings().then((s) => {
      if (s.ttsDefaultVoice) setSelectedVoiceState(s.ttsDefaultVoice);
      if (s.ttsDefaultSpeed != null) setSpeedState(s.ttsDefaultSpeed);
    });
  }, []);

  const [playbackState, setPlaybackState] = useState<TtsPlaybackState>("idle");
  const [isDownloading, setIsDownloading] = useState(false);
  const [isPreviewPlaying, setIsPreviewPlaying] = useState<string | null>(null);
  const [currentAudioUrl, setCurrentAudioUrl] = useState<string | null>(null);
  const [currentDuration, setCurrentDuration] = useState(0);
  const [currentElapsed, setCurrentElapsed] = useState(0);
  const [history, setHistory] = useState<TtsHistoryEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [lastError, setLastError] = useState<TtsLastError | null>(null);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const prevAudioUrlRef = useRef<string | null>(null);
  const streamAbortRef = useRef<AbortController | null>(null);

  // ── AudioContext scheduler (single source of truth for streaming) ───────
  const audioCtxRef = useRef<AudioContext | null>(null);
  const nextStartTimeRef = useRef<number>(0);
  const scheduledSourcesRef = useRef<AudioBufferSourceNode[]>([]);
  // Set to true when the synthesis stream emits its END frame
  const synthDoneRef = useRef<boolean>(false);
  // Decoded AudioBuffers captured during the current streaming run.
  // Used after stream completion to encode a single WAV blob for the
  // history entry so it can be replayed via the Recent list.
  const decodedBuffersRef = useRef<AudioBuffer[]>([]);

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await getTtsStatus());
    } catch (e) {
      // Pre-discovery fetches are expected to fail at app boot — not a warning.
      if (e instanceof Error && e.message === "Engine not discovered") return;
      logWarn("tts", "status fetch failed", e);
    }
  }, []);

  const refreshVoices = useCallback(async () => {
    try {
      const v = await getTtsVoices();
      setVoices(v);
      setLanguageGroups(groupVoicesByLanguage(v));
    } catch (e) {
      if (e instanceof Error && e.message === "Engine not discovered") return;
      logWarn("tts", "voices fetch failed", e);
    }
  }, []);

  useEffect(() => {
    // Init fetch with quiet retry: the engine usually isn't discovered yet
    // when this provider mounts at app boot. Retry until it is (or give up
    // after ~2 min) instead of warning on each pre-discovery attempt.
    let cancelled = false;
    void (async () => {
      for (let i = 0; i < 60 && !cancelled; i++) {
        try {
          const s = await getTtsStatus();
          const v = await getTtsVoices();
          if (cancelled) return;
          setStatus(s);
          setVoices(v);
          setLanguageGroups(groupVoicesByLanguage(v));
          return;
        } catch {
          await new Promise((r) => setTimeout(r, 2000));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!status?.is_downloading) return;
    const id = setInterval(() => void refreshStatus(), 2000);
    return () => clearInterval(id);
  }, [status?.is_downloading, refreshStatus]);

  useEffect(() => {
    return () => {
      if (prevAudioUrlRef.current) {
        URL.revokeObjectURL(prevAudioUrlRef.current);
      }
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
      if (audioCtxRef.current) {
        audioCtxRef.current
          .close()
          .catch(catchAndLog("tts", "AudioContext close on unmount"));
        audioCtxRef.current = null;
      }
    };
  }, []);

  const downloadModel = useCallback(async () => {
    setIsDownloading(true);
    setError(null);
    setLastError(null);
    try {
      const r = await downloadTtsModel();
      // The engine returns HTTP 200 with {success:false, error} on failure —
      // surface it instead of silently treating it as success.
      if (!r.success && !r.already_downloaded) {
        throw new TtsStreamError(
          r.error_code ?? "download_failed",
          r.error ?? "TTS model download failed",
        );
      }
      await refreshStatus();
    } catch (e) {
      const le = errorToLastError(e);
      setError(le.message);
      setLastError(le);
      logError("tts", "download model", e);
    } finally {
      setIsDownloading(false);
    }
  }, [refreshStatus]);

  const setSelectedVoice = useCallback((voiceId: string) => {
    setSelectedVoiceState(voiceId);
    saveSetting("ttsDefaultVoice", voiceId);
  }, []);

  const setSpeed = useCallback((s: number) => {
    setSpeedState(s);
    saveSetting("ttsDefaultSpeed", s);
  }, []);

  /**
   * Hard-stop: abort fetch, stop all scheduled nodes, close AudioContext.
   * Resets to idle immediately. Safe to call from any state.
   */
  const stopAudio = useCallback(() => {
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;

    const sources = scheduledSourcesRef.current;
    scheduledSourcesRef.current = [];
    for (const src of sources) {
      try {
        src.onended = null;
        src.stop(0);
        src.disconnect();
      } catch {
        // already stopped / detached
      }
    }
    synthDoneRef.current = false;
    nextStartTimeRef.current = 0;
    decodedBuffersRef.current = [];

    if (audioCtxRef.current) {
      audioCtxRef.current
        .close()
        .catch(catchAndLog("tts", "AudioContext close on stop"));
      audioCtxRef.current = null;
    }

    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
    }

    setIsPreviewPlaying(null);
    setPlaybackState("idle");
  }, []);

  const pauseAudio = useCallback(() => {
    if (!audioCtxRef.current || audioCtxRef.current.state !== "running") {
      // Also cover the plain <audio> path used by speak() and preview()
      if (audioRef.current && !audioRef.current.paused) {
        audioRef.current.pause();
        setPlaybackState("paused");
      }
      return;
    }
    audioCtxRef.current
      .suspend()
      .catch(catchAndLog("tts", "AudioContext suspend"));
    if (audioRef.current && !audioRef.current.paused) {
      audioRef.current.pause();
    }
    setPlaybackState("paused");
  }, []);

  const resumeAudio = useCallback(() => {
    if (audioCtxRef.current && audioCtxRef.current.state === "suspended") {
      audioCtxRef.current
        .resume()
        .catch(catchAndLog("tts", "AudioContext resume"));
    }
    if (
      audioRef.current &&
      audioRef.current.paused &&
      audioRef.current.currentTime > 0
    ) {
      audioRef.current
        .play()
        .catch(catchAndLog("tts", "audio element resume play"));
    }
    setPlaybackState(synthDoneRef.current ? "playing" : "synthesizing");
  }, []);

  /** One-shot non-streaming playback via plain <audio>. Used by speak() and
   *  preview() because the audio is already a complete WAV.
   *
   *  By default the created URL is tracked in ``prevAudioUrlRef`` and revoked
   *  on the next play. Pass ``trackForRevocation: false`` when the URL's
   *  lifetime is owned elsewhere (e.g. a history entry, revoked on eviction
   *  or clearHistory) — otherwise replaying older Recent entries breaks. */
  const playBlob = useCallback(
    (
      blob: Blob,
      onEnd?: () => void,
      opts?: { trackForRevocation?: boolean },
    ): string => {
    if (prevAudioUrlRef.current) {
      URL.revokeObjectURL(prevAudioUrlRef.current);
      prevAudioUrlRef.current = null;
    }
    if (audioRef.current) {
      audioRef.current.pause();
    }

    const url = URL.createObjectURL(blob);
    if (opts?.trackForRevocation !== false) {
      prevAudioUrlRef.current = url;
    }

    const audio = new Audio(url);
    audioRef.current = audio;
    audio.onended = () => {
      setPlaybackState("idle");
      onEnd?.();
    };
    audio.onerror = () => {
      logWarn("tts", "audio element error");
      setPlaybackState("idle");
      onEnd?.();
    };
    audio.play().catch((e) => {
      logWarn("tts", "audio element play rejected", e);
      setPlaybackState("idle");
      onEnd?.();
    });
    return url;
    },
    [],
  );

  const onAllSourcesEnded = useCallback(() => {
    if (synthDoneRef.current && scheduledSourcesRef.current.length === 0) {
      setPlaybackState("idle");
    }
  }, []);

  /**
   * Schedule a WAV blob for gapless playback using the Web Audio API.
   * Throws (not silently falls back) if decoding fails — the caller surfaces
   * the error to the UI rather than producing inconsistent playback.
   */
  const scheduleChunk = useCallback(
    async (wavBlob: Blob): Promise<void> => {
      if (!audioCtxRef.current || audioCtxRef.current.state === "closed") {
        // AudioContext.constructor throws in environments without Web Audio
        // (very old browsers). We treat that as a hard failure.
        const Ctx = window.AudioContext;
        if (!Ctx) {
          throw new TtsStreamError(
            "no_audio_context",
            "Web Audio API is not available in this environment",
          );
        }
        audioCtxRef.current = new Ctx();
        nextStartTimeRef.current = 0;
        scheduledSourcesRef.current = [];
      }
      const ctx = audioCtxRef.current;
      if (ctx.state === "suspended") {
        await ctx.resume();
      }

      const arrayBuf = await wavBlob.arrayBuffer();
      const audioBuf = await ctx.decodeAudioData(arrayBuf);
      // Capture for post-stream WAV encoding (history replay).
      decodedBuffersRef.current.push(audioBuf);

      const src = ctx.createBufferSource();
      src.buffer = audioBuf;
      src.connect(ctx.destination);

      const startAt = Math.max(ctx.currentTime, nextStartTimeRef.current);
      src.start(startAt);
      nextStartTimeRef.current = startAt + audioBuf.duration;

      scheduledSourcesRef.current.push(src);

      src.onended = () => {
        src.onended = null;
        scheduledSourcesRef.current = scheduledSourcesRef.current.filter(
          (s) => s !== src,
        );
        try {
          src.disconnect();
        } catch {
          // already disconnected
        }
        onAllSourcesEnded();
      };
    },
    [onAllSourcesEnded],
  );

  const speak = useCallback(
    async (text: string) => {
      if (!text.trim()) return;
      stopAudio();
      setPlaybackState("synthesizing");
      setError(null);
      setLastError(null);

      try {
        const result = await synthesize({
          text,
          voice_id: selectedVoice,
          speed,
        });

        // This URL goes into the history list — its lifetime is owned by
        // history eviction (below) / clearHistory, NOT by playBlob's
        // next-play revocation, so older Recent entries stay playable.
        const url = playBlob(result.blob, undefined, {
          trackForRevocation: false,
        });
        setCurrentAudioUrl(url);
        setCurrentDuration(result.duration);
        setCurrentElapsed(result.elapsed);
        setPlaybackState("playing");

        const voiceObj = voices.find((v) => v.voice_id === selectedVoice);
        const entry: TtsHistoryEntry = {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          text: text.slice(0, 500),
          voiceId: selectedVoice,
          voiceName: voiceObj?.name ?? selectedVoice,
          duration: result.duration,
          elapsed: result.elapsed,
          audioUrl: url,
          createdAt: Date.now(),
        };
        setHistory((prev) => {
          const next = [entry, ...prev];
          if (next.length <= MAX_HISTORY) return next;
          // Revoke URLs of evicted entries to release the underlying blobs.
          for (const dropped of next.slice(MAX_HISTORY)) {
            if (dropped.audioUrl) {
              try {
                URL.revokeObjectURL(dropped.audioUrl);
              } catch {
                // ignore
              }
            }
          }
          return next.slice(0, MAX_HISTORY);
        });
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          const le = errorToLastError(e);
          setError(le.message);
          setLastError(le);
          logError("tts", "speak", e);
        }
        setPlaybackState("idle");
      }
    },
    [selectedVoice, speed, voices, playBlob, stopAudio],
  );

  /**
   * Internal core: drives a streaming synthesis through the Web Audio
   * scheduler. Both speakStreaming() and speakText() delegate here.
   *
   * Returns a result describing the synthesis. ``audioUrl`` is non-null only
   * on a clean stream completion with at least one decoded chunk; callers use
   * it to attach the audio to the history list so the user can replay it.
   * On abort or error the returned ``audioUrl`` is null.
   */
  const _runStream = useCallback(
    async (
      text: string,
      voiceId: string,
      spd: number,
      externalSignal?: AbortSignal,
    ): Promise<{
      audioUrl: string | null;
      duration: number;
      elapsed: number;
    }> => {
      stopAudio();

      synthDoneRef.current = false;
      setPlaybackState("synthesizing");
      setError(null);
      setLastError(null);

      const abort = new AbortController();
      streamAbortRef.current = abort;

      const onExternalAbort = () => abort.abort();
      if (externalSignal) {
        if (externalSignal.aborted) abort.abort();
        else externalSignal.addEventListener("abort", onExternalAbort, { once: true });
      }

      const t0 = performance.now();
      let firstChunk = true;
      let elapsed = 0;
      let succeeded = false;

      try {
        const gen = synthesizeStream(
          { text, voice_id: voiceId, speed: spd },
          abort.signal,
        );
        for await (const wavBlob of gen) {
          if (abort.signal.aborted) break;
          try {
            await scheduleChunk(wavBlob);
          } catch (decodeErr) {
            // Hard fail — first chunk decode failure leaves us with no audio
            // and the synthesis must abort to avoid stuck "synthesizing" state.
            abort.abort();
            throw decodeErr;
          }
          if (firstChunk && !abort.signal.aborted) {
            firstChunk = false;
            setPlaybackState("playing");
          }
        }
        if (!abort.signal.aborted) {
          elapsed = (performance.now() - t0) / 1000;
          setCurrentElapsed(elapsed);
          synthDoneRef.current = true;
          succeeded = true;
          // If audio is still scheduled, onAllSourcesEnded will flip to idle
          // once the last source finishes playing.
          if (scheduledSourcesRef.current.length === 0) {
            setPlaybackState("idle");
          }
        }
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          const le = errorToLastError(e);
          setError(le.message);
          setLastError(le);
          logError("tts", "stream", e);
        }
        // Tear everything down — partial audio is worse than silence.
        stopAudio();
      } finally {
        if (externalSignal) {
          externalSignal.removeEventListener("abort", onExternalAbort);
        }
      }

      // On clean completion, encode the captured PCM into a single WAV blob
      // so the caller can attach a replayable URL to its history entry.
      // ``decodedBuffersRef`` is cleared by ``stopAudio`` (start-of-run), so
      // it only contains the buffers produced by this run.
      if (!succeeded || decodedBuffersRef.current.length === 0) {
        return { audioUrl: null, duration: 0, elapsed };
      }
      try {
        const wavBlob = audioBuffersToWavBlob(decodedBuffersRef.current);
        const audioUrl = URL.createObjectURL(wavBlob);
        const duration = decodedBuffersRef.current.reduce(
          (s, b) => s + b.duration,
          0,
        );
        return { audioUrl, duration, elapsed };
      } catch (e) {
        logWarn("tts", "wav encode for history failed", e);
        return { audioUrl: null, duration: 0, elapsed };
      }
    },
    [scheduleChunk, stopAudio],
  );

  const speakStreaming = useCallback(
    async (text: string) => {
      if (!text.trim()) return;
      const result = await _runStream(text, selectedVoice, speed);

      // Only record history when the stream produced playable audio.
      // Aborts (user pressed Stop) and errors return audioUrl: null and we
      // skip the history entry — otherwise the Recent list fills with dead
      // rows whose Play button does nothing.
      if (!result.audioUrl) return;

      const voiceObj = voices.find((v) => v.voice_id === selectedVoice);
      const entry: TtsHistoryEntry = {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        text: text.slice(0, 500),
        voiceId: selectedVoice,
        voiceName: voiceObj?.name ?? selectedVoice,
        duration: result.duration,
        elapsed: result.elapsed,
        audioUrl: result.audioUrl,
        createdAt: Date.now(),
      };
      setCurrentAudioUrl(result.audioUrl);
      setCurrentDuration(result.duration);
      setHistory((prev) => {
        const next = [entry, ...prev];
        if (next.length <= MAX_HISTORY) return next;
        // Revoke URLs of evicted entries to release the underlying blobs.
        for (const dropped of next.slice(MAX_HISTORY)) {
          if (dropped.audioUrl) {
            try {
              URL.revokeObjectURL(dropped.audioUrl);
            } catch {
              // ignore
            }
          }
        }
        return next.slice(0, MAX_HISTORY);
      });
    },
    [selectedVoice, speed, voices, _runStream],
  );

  const speakText = useCallback(
    async (
      text: string,
      voiceId?: string,
      spd?: number,
      signal?: AbortSignal,
    ) => {
      if (!text.trim()) return;
      const result = await _runStream(
        text,
        voiceId ?? selectedVoice,
        spd ?? speed,
        signal,
      );
      // Chat TTS calls this once per sentence. We don't surface a history
      // entry from this path, so revoke the URL the stream created to avoid
      // leaking a blob per sentence over a long read-aloud session.
      if (result.audioUrl) {
        try {
          URL.revokeObjectURL(result.audioUrl);
        } catch {
          // ignore
        }
      }
      // Resolve at PLAYBACK drain, not synthesis end. Sequential callers
      // (chat read-aloud's chunk queue) start the next chunk when this
      // promise settles, and the next chunk's _runStream begins with a hard
      // stopAudio() — resolving while audio is still scheduled would cut off
      // this chunk's tail. Poll the scheduler until all sources have ended
      // (stopAudio clears the list, so a user Stop also exits the wait).
      while (!signal?.aborted && scheduledSourcesRef.current.length > 0) {
        await new Promise((r) => setTimeout(r, 100));
      }
    },
    [selectedVoice, speed, _runStream],
  );

  const preview = useCallback(
    async (voiceId: string) => {
      setIsPreviewPlaying(voiceId);
      setError(null);
      setLastError(null);
      try {
        const result = await previewVoice(voiceId);
        playBlob(result.blob, () => setIsPreviewPlaying(null));
      } catch (e) {
        setIsPreviewPlaying(null);
        const le = errorToLastError(e);
        setError(le.message);
        setLastError(le);
        logError("tts", "preview", e);
      }
    },
    [playBlob],
  );

  const unload = useCallback(async () => {
    try {
      await unloadTts();
      await refreshStatus();
    } catch (e) {
      const le = errorToLastError(e);
      setError(le.message);
      setLastError(le);
      logError("tts", "unload", e);
    }
  }, [refreshStatus]);

  const clearHistory = useCallback(() => {
    history.forEach((h) => {
      try {
        URL.revokeObjectURL(h.audioUrl);
      } catch {
        // ignore
      }
    });
    setHistory([]);
  }, [history]);

  const clearError = useCallback(() => {
    setError(null);
    setLastError(null);
  }, []);

  const state: UseTtsState = {
    status,
    voices,
    languageGroups,
    selectedVoice,
    speed,
    isSynthesizing: playbackState === "synthesizing",
    isDownloading,
    isPreviewPlaying,
    currentAudioUrl,
    currentDuration,
    currentElapsed,
    history,
    error,
    lastError,
    playbackState,
  };

  const actions: UseTtsActions = useMemo(
    () => ({
      refreshStatus,
      refreshVoices,
      downloadModel,
      setSelectedVoice,
      setSpeed,
      speak,
      speakStreaming,
      speakText,
      preview,
      stopAudio,
      pauseAudio,
      resumeAudio,
      unload,
      clearHistory,
      clearError,
    }),
    [
      refreshStatus,
      refreshVoices,
      downloadModel,
      setSelectedVoice,
      setSpeed,
      speak,
      speakStreaming,
      speakText,
      preview,
      stopAudio,
      pauseAudio,
      resumeAudio,
      unload,
      clearHistory,
      clearError,
    ],
  );

  return [state, actions];
}
