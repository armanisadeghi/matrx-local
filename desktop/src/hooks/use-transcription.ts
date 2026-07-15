import { useState, useEffect, useCallback, useRef, useContext, useMemo } from "react";
import { isTauri } from "@/lib/sidecar";
import type { UnlistenFn } from "@tauri-apps/api/event";
import type {
  HardwareDetectionResult,
  WhisperSegment,
  DownloadProgress,
  VoiceSetupStatus,
  AudioDeviceInfo,
} from "@/lib/transcription/types";
import { AudioDevicesContext } from "@/contexts/AudioDevicesContext";

async function tauriInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  if (!isTauri()) {
    throw new Error("Voice transcription controls are only available in the desktop app.");
  }
  const core = await import("@tauri-apps/api/core");
  if (typeof core.invoke !== "function") {
    throw new Error("The desktop command bridge is unavailable.");
  }
  return core.invoke<T>(cmd, args);
}

async function tauriListen<T>(event: string, handler: (e: { payload: T }) => void): Promise<UnlistenFn> {
  const { listen } = await import("@tauri-apps/api/event");
  return listen<T>(event, handler);
}

export interface TranscriptionDownloadQueueEntry {
  filename: string;
}

export interface TranscriptionState {
  // Setup
  setupStatus: VoiceSetupStatus | null;
  hardwareResult: HardwareDetectionResult | null;
  downloadProgress: DownloadProgress | null;
  isDetecting: boolean;
  isDownloading: boolean;
  /** Filename of the model actively being downloaded right now */
  downloadingFilename: string | null;
  /** Models queued to download after the current one finishes */
  downloadQueue: TranscriptionDownloadQueueEntry[];
  isInitializing: boolean;

  // Recording
  isRecording: boolean;
  /**
   * True while the mic has been stopped but Rust is still flushing remaining
   * buffered audio through Whisper (may be multiple chunks). Listeners are kept
   * alive until whisper-stopped fires, so no transcription is ever lost.
   */
  isProcessingTail: boolean;
  segments: WhisperSegment[];
  fullTranscript: string;
  activeModel: string | null;
  /** Live RMS energy from the microphone (0–1). Updated ~5Hz while recording. */
  liveRms: number;
  /** Whether the adaptive silence calibration has finished (first 2s of recording). */
  isCalibrating: boolean;

  // Devices
  audioDevices: AudioDeviceInfo[];
  /** Name of the selected input device. null = use system default. */
  selectedDevice: string | null;

  // Errors
  error: string | null;
}

export interface TranscriptionActions {
  detectHardware: () => Promise<HardwareDetectionResult>;
  downloadModel: (filename: string) => Promise<void>;
  /**
   * Add a Whisper model to the download queue. If nothing is currently
   * downloading, the download starts immediately. Otherwise it runs after
   * the current one finishes (or fails). Already-downloaded or already-queued
   * models are ignored.
   */
  queueDownload: (filename: string) => void;
  /**
   * Queue all provided model filenames for download, skipping any already
   * downloaded. Downloads happen sequentially via the queue.
   */
  downloadAll: (filenames: string[]) => void;
  downloadVadModel: () => Promise<void>;
  initTranscription: (filename: string) => Promise<void>;
  startRecording: (deviceName?: string) => Promise<void>;
  stopRecording: () => Promise<void>;
  checkModelExists: (filename: string) => Promise<boolean>;
  listDownloadedModels: () => Promise<string[]>;
  deleteModel: (filename: string) => Promise<void>;
  refreshSetupStatus: () => Promise<void>;
  listAudioDevices: () => Promise<AudioDeviceInfo[]>;
  setSelectedDevice: (deviceName: string | null) => void;
  clearSegments: () => void;
  clearError: () => void;

  /** One-click setup: detect hardware, download recommended model + VAD, init */
  quickSetup: () => Promise<void>;

  /** Cancel an in-flight whisper model download. No-op if nothing is downloading. */
  cancelDownload: () => Promise<void>;

  /**
   * Emergency reset: forcibly clears all in-flight state (isRecording,
   * isProcessingTail, isCalibrating) and tears down all Tauri event listeners.
   * Sends a best-effort stop_transcription to Rust. Use when the UI gets stuck.
   */
  forceReset: () => void;
}

export function useTranscription(): [TranscriptionState, TranscriptionActions] {
  // Audio device state comes from the shared AudioDevicesContext — single source of truth.
  // Fall back gracefully if the hook is used outside the provider (e.g. tests).
  const audioCtx = useContext(AudioDevicesContext);
  const audioDevices: AudioDeviceInfo[] = audioCtx?.audioDevices ?? [];
  const selectedDevice: string | null = audioCtx?.selectedDevice ?? null;
  const ctxSetSelectedDevice = audioCtx?.setSelectedDevice;
  const ctxListAudioDevices = audioCtx?.listAudioDevices;

  const [setupStatus, setSetupStatus] = useState<VoiceSetupStatus | null>(null);
  const [hardwareResult, setHardwareResult] =
    useState<HardwareDetectionResult | null>(null);
  const [downloadProgress, setDownloadProgress] =
    useState<DownloadProgress | null>(null);
  const [isDetecting, setIsDetecting] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [downloadingFilename, setDownloadingFilename] = useState<string | null>(null);
  const [downloadQueue, setDownloadQueue] = useState<TranscriptionDownloadQueueEntry[]>([]);
  const [isInitializing, setIsInitializing] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [isProcessingTail, setIsProcessingTail] = useState(false);
  const [segments, setSegments] = useState<WhisperSegment[]>([]);
  const [activeModel, setActiveModel] = useState<string | null>(null);
  const [liveRms, setLiveRms] = useState(0);
  const [isCalibrating, setIsCalibrating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const unlistenersRef = useRef<UnlistenFn[]>([]);
  // True from the moment startRecording begins until the recording is
  // stopped (or fails to start). Synchronous, unlike the isRecording state,
  // so it reliably blocks double-starts.
  const recordingGuardRef = useRef(false);
  // Ref-based queue so the processor callback always sees latest value
  const downloadQueueRef = useRef<TranscriptionDownloadQueueEntry[]>([]);
  const isDownloadingRef = useRef(false);
  const downloadingFilenameRef = useRef<string | null>(null);
  // Track downloaded filenames for queue dedup (subset of setupStatus.downloaded_models)
  const downloadedFilenamesRef = useRef<Set<string>>(new Set());

  // Full transcript derived from segments
  const fullTranscript = segments
    .map((s) => s.text)
    .filter((t) => t.length > 0)
    .join(" ");

  // Clean up event listeners on unmount
  useEffect(() => {
    return () => {
      unlistenersRef.current.forEach((fn) => fn());
      unlistenersRef.current = [];
    };
  }, []);

  // Mirror the LLM download-cancelled handler (use-llm.ts): when Rust emits
  // `whisper-download-cancelled` (transcription/commands.rs), immediately clear
  // the downloading flags so the UI drops the in-flight spinner/progress
  // without waiting for the download loop's own finally-block to unwind.
  useEffect(() => {
    if (!isTauri()) return;
    let mounted = true;
    let unlisten: UnlistenFn | undefined;
    tauriListen<{ reason?: string }>("whisper-download-cancelled", () => {
      if (!mounted) return;
      setIsDownloading(false);
      isDownloadingRef.current = false;
      setDownloadProgress(null);
      setDownloadingFilename(null);
      downloadingFilenameRef.current = null;
    }).then((fn) => {
      if (mounted) unlisten = fn;
      else fn();
    });
    return () => {
      mounted = false;
      unlisten?.();
    };
  }, []);

  // Load initial setup status
  useEffect(() => {
    if (!isTauri()) return;
    refreshSetupStatus();
  }, []);

  // Poll get_active_model until the Rust auto-init completes (max 30s).
  // This bridges the gap between the config saying "setup_complete: true"
  // and the TranscriptionManager actually being loaded into memory.
  useEffect(() => {
    if (!isTauri()) return;

    let cancelled = false;
    let attempts = 0;
    const MAX_ATTEMPTS = 30;

    const poll = async () => {
      if (cancelled || attempts >= MAX_ATTEMPTS) return;
      attempts++;
      try {
        const { invoke } = await import("@tauri-apps/api/core");
        const model = await invoke<string | null>("get_active_model");
        if (model) {
          setActiveModel(model);
          return; // done — model is loaded
        }
      } catch {
        // not ready yet — keep polling
      }
      setTimeout(poll, 1000);
    };

    poll();
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshSetupStatus = useCallback(async () => {
    if (!isTauri()) return;
    try {
      const status = await tauriInvoke<VoiceSetupStatus>("get_voice_setup_status");
      setSetupStatus(status);
      if (status.selected_model) {
        setActiveModel(status.selected_model);
      }
      // Keep downloaded filenames ref in sync for queue dedup
      downloadedFilenamesRef.current = new Set(status.downloaded_models ?? []);
    } catch {
      // Not critical — app may work without voice features
    }
  }, []);

  const detectHardware = useCallback(async () => {
    setIsDetecting(true);
    setError(null);
    try {
      const result = await tauriInvoke<HardwareDetectionResult>("detect_hardware");
      setHardwareResult(result);
      return result;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      throw e;
    } finally {
      setIsDetecting(false);
    }
  }, []);

  /**
   * Execute a single Whisper model download. Does NOT manage queue state —
   * that's done by processQueue. Direct callers (quickSetup) can call this directly.
   */
  const downloadModel = useCallback(async (filename: string) => {
    setIsDownloading(true);
    isDownloadingRef.current = true;
    setDownloadingFilename(filename);
    downloadingFilenameRef.current = filename;
    setDownloadProgress(null);
    setError(null);

    const unlisten = await tauriListen<DownloadProgress>(
      "whisper-download-progress",
      (event) => {
        setDownloadProgress(event.payload);
      }
    );

    try {
      await tauriInvoke("download_whisper_model", { filename });
      setDownloadProgress(null);
      downloadedFilenamesRef.current.add(filename);
      await refreshSetupStatus();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      throw e;
    } finally {
      unlisten();
      setIsDownloading(false);
      isDownloadingRef.current = false;
      setDownloadingFilename(null);
      downloadingFilenameRef.current = null;
    }
  }, [refreshSetupStatus]);

  /**
   * Sequential queue processor. Pops the next entry and downloads it.
   * Calls itself recursively until the queue is empty. Continues on failure
   * so one bad download doesn't block the rest of the queue.
   */
  const processQueue = useCallback(async () => {
    if (isDownloadingRef.current) return;
    const next = downloadQueueRef.current.shift();
    if (!next) return;
    setDownloadQueue([...downloadQueueRef.current]);
    try {
      await downloadModel(next.filename);
    } catch {
      // Error already set by downloadModel — continue to next item
    }
    void processQueue();
  }, [downloadModel]);

  const queueDownload = useCallback(
    (filename: string) => {
      // Skip if already downloaded
      if (downloadedFilenamesRef.current.has(filename)) return;
      // Skip if already queued
      if (downloadQueueRef.current.some((e) => e.filename === filename)) return;
      // Skip if currently downloading this exact file
      if (isDownloadingRef.current && downloadingFilenameRef.current === filename) return;

      if (!isDownloadingRef.current) {
        // Nothing active — start immediately
        void downloadModel(filename).then(() => void processQueue()).catch(() => void processQueue());
      } else {
        // Something is active — push to queue
        downloadQueueRef.current.push({ filename });
        setDownloadQueue([...downloadQueueRef.current]);
      }
    },
    [downloadModel, processQueue]
  );

  const downloadAll = useCallback(
    (filenames: string[]) => {
      const queuedSet = new Set(downloadQueueRef.current.map((e) => e.filename));

      for (const filename of filenames) {
        if (downloadedFilenamesRef.current.has(filename)) continue;
        if (queuedSet.has(filename)) continue;
        if (isDownloadingRef.current && downloadingFilenameRef.current === filename) continue;
        downloadQueueRef.current.push({ filename });
        queuedSet.add(filename);
      }
      setDownloadQueue([...downloadQueueRef.current]);

      if (!isDownloadingRef.current) {
        void processQueue();
      }
    },
    [processQueue]
  );

  const downloadVadModel = useCallback(async () => {
    setIsDownloading(true);
    isDownloadingRef.current = true;
    setDownloadProgress(null);
    setError(null);

    const unlisten = await tauriListen<DownloadProgress>(
      "whisper-download-progress",
      (event) => {
        setDownloadProgress(event.payload);
      }
    );

    try {
      await tauriInvoke("download_vad_model");
      setDownloadProgress(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      throw e;
    } finally {
      unlisten();
      setIsDownloading(false);
      isDownloadingRef.current = false;
    }
  }, []);

  const initTranscription = useCallback(async (filename: string) => {
    setIsInitializing(true);
    setError(null);
    try {
      await tauriInvoke("init_transcription", { filename });
      setActiveModel(filename);
      await refreshSetupStatus();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      throw e;
    } finally {
      setIsInitializing(false);
    }
  }, [refreshSetupStatus]);

  const startRecording = useCallback(async (deviceName?: string) => {
    // Guard against double-start: a second call while recording (or while a
    // start is in flight) would attach a duplicate set of listeners and
    // double-append every whisper-segment.
    if (recordingGuardRef.current) return;
    recordingGuardRef.current = true;
    setError(null);
    setSegments([]);
    setLiveRms(0);
    setIsCalibrating(true);
    setIsProcessingTail(false);

    const segmentUnlisten = await tauriListen<WhisperSegment>(
      "whisper-segment",
      (event) => {
        setSegments((prev) => [...prev, event.payload]);
      }
    );

    const errorUnlisten = await tauriListen<string>("whisper-error", (event) => {
      setError(event.payload);
      recordingGuardRef.current = false;
      setIsRecording(false);
      setIsProcessingTail(false);
      setLiveRms(0);
      setIsCalibrating(false);
    });

    const rmsUnlisten = await tauriListen<number>("whisper-rms", (event) => {
      setLiveRms(event.payload);
    });

    const calibratedUnlisten = await tauriListen<{ floor_rms: number; threshold: number }>(
      "whisper-calibrated",
      () => {
        setIsCalibrating(false);
      }
    );

    // whisper-stopped fires after the Rust thread has flushed ALL remaining
    // buffered audio through Whisper and accumulated is empty. Only at this
    // point do we tear down the listeners — ensuring no segments are lost.
    const stoppedUnlisten = await tauriListen<null>("whisper-stopped", () => {
      setIsProcessingTail(false);
      setLiveRms(0);
      unlistenersRef.current.forEach((fn) => fn());
      unlistenersRef.current = [];
    });

    unlistenersRef.current.push(
      segmentUnlisten,
      errorUnlisten,
      rmsUnlisten,
      calibratedUnlisten,
      stoppedUnlisten,
    );

    // Prefer explicitly passed device, fall back to persisted selectedDevice state
    const resolvedDevice = deviceName ?? selectedDevice ?? undefined;

    try {
      await tauriInvoke("start_transcription", {
        deviceName: resolvedDevice ?? null,
      });
      setIsRecording(true);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      recordingGuardRef.current = false;
      segmentUnlisten();
      errorUnlisten();
      rmsUnlisten();
      calibratedUnlisten();
      stoppedUnlisten();
      // Remove ONLY the five listeners this call added — wiping the whole
      // array would orphan any pre-existing listeners (they'd stay attached
      // with no handle left to unlisten them → ghost double-appends).
      unlistenersRef.current.splice(-5, 5);
    }
  }, [selectedDevice]);

  const stopRecording = useCallback(async () => {
    // Tell Rust to stop accepting new mic input. The recording thread continues
    // flushing ALL remaining buffered audio through Whisper (completely decoupled
    // from mic state) until empty, then emits "whisper-stopped". We stay in
    // isProcessingTail until that event so the UI shows "Processing…" and the
    // segment listener stays alive — no audio is ever lost.
    recordingGuardRef.current = false;
    setIsRecording(false);
    setIsCalibrating(false);
    // Keep liveRms until whisper-stopped so the meter fades naturally
    try {
      await tauriInvoke("stop_transcription");
      setIsProcessingTail(true);
    } catch {
      // Ignore — the recording may have already stopped
      setIsProcessingTail(false);
      setLiveRms(0);
      unlistenersRef.current.forEach((fn) => fn());
      unlistenersRef.current = [];
    }
  }, []);

  const checkModelExists = useCallback(async (filename: string) => {
    return tauriInvoke<boolean>("check_model_exists", { filename });
  }, []);

  const listDownloadedModels = useCallback(async () => {
    return tauriInvoke<string[]>("list_downloaded_models");
  }, []);

  const deleteModel = useCallback(async (filename: string) => {
    await tauriInvoke("delete_model", { filename });
    downloadedFilenamesRef.current.delete(filename);
    await refreshSetupStatus();
  }, [refreshSetupStatus]);

  const listAudioDevices = useCallback(async (): Promise<AudioDeviceInfo[]> => {
    if (ctxListAudioDevices) {
      return ctxListAudioDevices();
    }
    // Fallback: call Tauri directly if context is unavailable
    const devices = await tauriInvoke<AudioDeviceInfo[]>("list_audio_input_devices");
    return devices;
  }, [ctxListAudioDevices]);

  const setSelectedDevice = useCallback((deviceName: string | null) => {
    ctxSetSelectedDevice?.(deviceName);
  }, [ctxSetSelectedDevice]);

  const clearSegments = useCallback(() => setSegments([]), []);
  const clearError = useCallback(() => setError(null), []);

  const cancelDownload = useCallback(async () => {
    if (!isTauri()) return;
    try {
      await tauriInvoke("cancel_whisper_download");
    } catch {
      // No-op if nothing is downloading
    }
    downloadQueueRef.current = [];
    setDownloadQueue([]);
    setIsDownloading(false);
    isDownloadingRef.current = false;
    setDownloadingFilename(null);
    downloadingFilenameRef.current = null;
    setDownloadProgress(null);
  }, []);

  const forceReset = useCallback(() => {
    // Best-effort — don't await, don't throw
    if (isTauri()) {
      tauriInvoke("stop_transcription").catch((e) => console.warn("[transcription] forceReset stop_transcription failed:", e));
    }
    // Tear down all Tauri event listeners to prevent ghost events
    unlistenersRef.current.forEach((fn) => fn());
    unlistenersRef.current = [];
    // Clear all stuck flags unconditionally
    recordingGuardRef.current = false;
    setIsRecording(false);
    setIsProcessingTail(false);
    setIsCalibrating(false);
    setLiveRms(0);
    setError(null);
  }, []);

  // Auto-reset if isProcessingTail stays true for more than 15 seconds.
  // This fires if the Rust whisper-stopped event never arrives (thread panic,
  // CPAL device dropped, etc.) and the UI would otherwise be frozen forever.
  const processingTailTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (isProcessingTail) {
      processingTailTimerRef.current = setTimeout(() => {
        console.warn("[transcription] isProcessingTail timeout — forcing reset");
        forceReset();
      }, 15_000);
    } else {
      if (processingTailTimerRef.current) {
        clearTimeout(processingTailTimerRef.current);
        processingTailTimerRef.current = null;
      }
    }
    return () => {
      if (processingTailTimerRef.current) {
        clearTimeout(processingTailTimerRef.current);
        processingTailTimerRef.current = null;
      }
    };
  }, [isProcessingTail, forceReset]);

  // One-click setup
  const quickSetup = useCallback(async () => {
    setError(null);
    try {
      // 1. Detect hardware
      const hw = await detectHardware();

      // 2. Check if recommended model already downloaded
      const exists = await checkModelExists(hw.recommended_filename);
      if (!exists) {
        // Download recommended model
        await downloadModel(hw.recommended_filename);
      }

      // 3. Download VAD model (small, fast)
      await downloadVadModel();

      // 4. Initialize transcription context
      await initTranscription(hw.recommended_filename);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(`Setup failed: ${msg}`);
      throw e;
    }
  }, [
    detectHardware,
    checkModelExists,
    downloadModel,
    downloadVadModel,
    initTranscription,
  ]);

  const state: TranscriptionState = useMemo(
    () => ({
      setupStatus,
      hardwareResult,
      downloadProgress,
      isDetecting,
      isDownloading,
      downloadingFilename,
      downloadQueue,
      isInitializing,
      isRecording,
      isProcessingTail,
      segments,
      fullTranscript,
      activeModel,
      liveRms,
      isCalibrating,
      audioDevices,
      selectedDevice,
      error,
    }),
    [
      setupStatus, hardwareResult, downloadProgress, isDetecting, isDownloading,
      downloadingFilename, downloadQueue, isInitializing, isRecording,
      isProcessingTail, segments, fullTranscript, activeModel, liveRms,
      isCalibrating, audioDevices, selectedDevice, error,
    ],
  );

  const actions: TranscriptionActions = useMemo(
    () => ({
      detectHardware,
      downloadModel,
      queueDownload,
      downloadAll,
      downloadVadModel,
      initTranscription,
      startRecording,
      stopRecording,
      checkModelExists,
      listDownloadedModels,
      deleteModel,
      refreshSetupStatus,
      listAudioDevices,
      setSelectedDevice,
      clearSegments,
      clearError,
      quickSetup,
      cancelDownload,
      forceReset,
    }),
    [
      detectHardware,
      downloadModel,
      queueDownload,
      downloadAll,
      downloadVadModel,
      initTranscription,
      startRecording,
      stopRecording,
      checkModelExists,
      listDownloadedModels,
      deleteModel,
      refreshSetupStatus,
      listAudioDevices,
      setSelectedDevice,
      clearSegments,
      clearError,
      cancelDownload,
      quickSetup,
      forceReset,
    ],
  );

  return [state, actions];
}
