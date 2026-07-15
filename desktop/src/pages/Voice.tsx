import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";
import { PageHeader } from "@/components/layout/PageHeader";
import { SubTabBar } from "@/components/layout/SubTabBar";
import { useTranscriptionApp } from "@/contexts/TranscriptionContext";
import type {
  TranscriptionState,
  TranscriptionActions,
} from "@/hooks/use-transcription";
import { useSessionsContext } from "@/contexts/TranscriptionSessionsContext";
import type {
  SessionsState,
  SessionsActions,
} from "@/hooks/use-transcription-sessions";
import { useWakeWord } from "@/hooks/use-wake-word";
import { usePublishWakeWord } from "@/contexts/WakeWordContext";
import { WakeWordOverlay } from "@/components/WakeWordOverlay";
import { WakeWordControls } from "@/components/WakeWordControls";
import { WakeWordPage } from "@/pages/WakeWord";
import { usePermissionsContext } from "@/contexts/PermissionsContext";
import { loadSettings } from "@/lib/settings";
import { Button } from "@/components/ui/button";
import { DownloadProgress } from "@/components/DownloadProgress";
import { TranscriptionMiniMode } from "@/components/TranscriptionMiniMode";
import { RecordingMicButton } from "@/components/recording/RecordingMicButton";
import { RmsLevelBar } from "@/components/recording/RmsLevelBar";
import { engine } from "@/lib/api";
import { isTauri } from "@/lib/sidecar";
import { useLlmApp } from "@/contexts/LlmContext";
import { useLlmPipeline, parsePolishOutput } from "@/hooks/use-llm-pipeline";
import type { TranscriptPolishOutput } from "@/hooks/use-llm-pipeline";
import { usePolishPresets } from "@/hooks/use-polish-presets";
import type { PolishPreset } from "@/hooks/use-polish-presets";
import { POLISH_JSON_INSTRUCTION } from "@/lib/polish-presets";
import {
  Popover,
  PopoverTrigger,
  PopoverContent,
} from "@/components/ui/popover";
import {
  Mic,
  MicOff,
  Download,
  CheckCircle2,
  AlertCircle,
  Cpu,
  HardDrive,
  Monitor,
  Trash2,
  RefreshCw,
  Zap,
  Volume2,
  ChevronRight,
  Loader2,
  Info,
  Clock,
  Copy,
  Check,
  Plus,
  Pencil,
  FileText,
  Minimize2,
  ChevronLeft,
  RotateCcw,
  Sparkles,
  Tag,
  RotateCcw as Undo2,
  X,
  Play,
  Square,
  Activity,
  Radio,
  Settings2,
  Waves,
  AudioLines,
  CircleDot,
  ChevronDown,
  Star,
  Pencil as PencilIcon,
  Trash2 as TrashIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { getSession } from "@/lib/transcription/sessions";
import { emitClientLog } from "@/hooks/use-client-log";
import type {
  WhisperModelTier,
  HardwareDetectionResult,
  ModelInfo,
  TranscriptionSession,
} from "@/lib/transcription/types";

const TABS = [
  { value: "setup", label: "Setup" },
  { value: "transcribe", label: "Transcribe" },
  { value: "models", label: "Models" },
  { value: "devices", label: "Audio Devices" },
  { value: "wakeword", label: "Wake Word" },
];

const LOG = (level: Parameters<typeof emitClientLog>[0], msg: string) =>
  emitClientLog(level, msg, "voice");

export function Voice() {
  const [tab, setTab] = useState("setup");
  const { state, actions } = useTranscriptionApp();
  const { openModal: openDownloadModal, downloads: dmDownloads } =
    useDownloadManager();
  const whisperDownloads = dmDownloads.filter(
    (d) =>
      d.category === "whisper" &&
      (d.status === "active" || d.status === "queued"),
  );
  const { state: sessionsState, actions: sessionsActions } =
    useSessionsContext();
  const [isMiniMode, setIsMiniMode] = useState(false);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const recordingStartRef = useRef<number>(0);

  // ── Wake word integration ────────────────────────────────────────────────
  // onWake: called when wake word fires — starts a new transcription session
  const handleWake = useCallback(async () => {
    const session = sessionsActions.startNew(
      state.activeModel,
      state.selectedDevice,
    );
    setActiveSessionId(session.id);
    recordingStartRef.current = Date.now();
    await actions.startRecording();
  }, [sessionsActions, state.activeModel, state.selectedDevice, actions]);

  // onSleep: called when active session should close
  const handleSleep = useCallback(async () => {
    await actions.stopRecording();
    if (activeSessionId) {
      const elapsed = Math.round(
        (Date.now() - recordingStartRef.current) / 1000,
      );
      // Accumulate: continued sessions keep the duration of previous legs.
      const prior = getSession(activeSessionId)?.durationSecs ?? 0;
      sessionsActions.finalize(activeSessionId, prior + elapsed);
    }
  }, [actions, activeSessionId, sessionsActions]);

  const [wwState, wwActions] = useWakeWord(
    handleWake,
    handleSleep,
    state.fullTranscript,
  );

  const publishWakeWord = usePublishWakeWord();
  useEffect(() => {
    publishWakeWord(wwState, wwActions);
  }, [wwState, wwActions, publishWakeWord]);

  // ── Auto-start listen mode — only after transcription model is loaded ────
  // This prevents a CPAL race where the wake word thread and the transcription
  // thread both try to open the mic before the model is ready. We wait until
  // state.activeModel is non-null (model fully loaded in Rust) before starting.
  const didAutoStartRef = useRef(false);
  useEffect(() => {
    if (didAutoStartRef.current) return;
    if (!state.activeModel) return; // wait for model to be ready
    didAutoStartRef.current = true;
    loadSettings()
      .then((s) => {
        if (s.wakeWordEnabled && s.wakeWordListenOnStartup) {
          void wwActions.setup();
        }
      })
      .catch((e) => console.warn("[voice] loadSettings for wake word failed:", e));
  }, [state.activeModel]); // re-runs when model finishes loading

  // ── Publish transcript from the overlay directly to a note ───────────────
  const handleOverlayPublishToNote = useCallback(async (text: string) => {
    // The overlay lives in its own floating Tauri window, so status is
    // communicated back to it over the event bus (same channel the transcript
    // stream uses) rather than via a main-window state banner.
    const emitStatus = (payload: { ok: boolean; error?: string }) => {
      if (!isTauri()) return;
      import("@tauri-apps/api/event")
        .then(({ emit }) => {
          void emit("overlay-publish-status", payload);
        })
        .catch((e) =>
          console.warn("[voice] overlay-publish-status emit failed:", e),
        );
    };
    if (!engine.engineUrl) {
      console.warn("[voice] overlay publish skipped — engine not connected");
      emitStatus({ ok: false, error: "Engine not connected — note not saved." });
      return;
    }
    const date = new Date();
    try {
      await engine.createNote("local", {
        label: `Voice Note — ${date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`,
        content: text,
        folder_name: "Voice Notes",
      });
      emitStatus({ ok: true });
    } catch (e) {
      console.error("[voice] overlay publish to note failed:", e);
      emitStatus({
        ok: false,
        error: `Failed to save note: ${e instanceof Error ? e.message : String(e)}`,
      });
    }
  }, []);

  // ── Stream live transcript to the floating overlay window ─────────────────
  // Emits whenever the wake word session is active and transcript changes.
  // Also emits the empty payload when a new session starts so the overlay
  // resets instead of showing the previous session's text until the first
  // new segment arrives.
  const prevOverlayUiModeRef = useRef(wwState.uiMode);
  useEffect(() => {
    const justActivated =
      prevOverlayUiModeRef.current !== "active" && wwState.uiMode === "active";
    prevOverlayUiModeRef.current = wwState.uiMode;
    if (wwState.uiMode !== "active") return;
    if (!isTauri()) return;
    // On activation the segments from the previous session may not be cleared
    // yet — emit a reset payload instead of the stale transcript.
    const payload = justActivated ? "" : state.fullTranscript;
    import("@tauri-apps/api/event")
      .then(({ emit }) => {
        void emit("overlay-transcript", payload);
      })
      .catch((e) => console.warn("[voice] overlay-transcript emit failed:", e));
  }, [state.fullTranscript, wwState.uiMode]);

  // ── Auto-persist transcript every 5 s during active wake-word session ─────
  // Ensures we never lose transcribed text even on a crash.
  const persistTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (
      wwState.uiMode !== "active" ||
      !activeSessionId ||
      !state.fullTranscript
    ) {
      return;
    }
    if (persistTimerRef.current) clearTimeout(persistTimerRef.current);
    persistTimerRef.current = setTimeout(() => {
      sessionsActions.updateText(activeSessionId, state.fullTranscript);
    }, 5_000);
    return () => {
      if (persistTimerRef.current) {
        clearTimeout(persistTimerRef.current);
        persistTimerRef.current = null;
      }
    };
  }, [state.fullTranscript, wwState.uiMode, activeSessionId, sessionsActions]);

  // Wire state changes to the unified log bus
  useEffect(() => {
    if (state.downloadProgress) {
      try {
        LOG(
          "data",
          `[whisper] download-progress: ${JSON.stringify(state.downloadProgress)}`,
        );
      } catch {
        LOG("data", "[whisper] download-progress: [unserializable]");
      }
    }
  }, [state.downloadProgress]);

  useEffect(() => {
    if (state.error) {
      LOG("error", `[whisper] ERROR: ${state.error}`);
    }
  }, [state.error]);

  // Wrapped actions that also log to the unified bus
  const wrappedDetectHardware = useCallback(async () => {
    LOG("cmd", "invoke detect_hardware");
    try {
      const result = await actions.detectHardware();
      try {
        LOG("data", `[whisper] hardware-result: ${JSON.stringify(result)}`);
      } catch {
        LOG("data", "[whisper] hardware-result: [unserializable]");
      }
      return result;
    } catch (e) {
      LOG("error", `detect_hardware failed: ${e}`);
      throw e;
    }
  }, [actions]);

  const wrappedDownloadModel = useCallback(
    async (filename: string) => {
      LOG("cmd", `invoke download_whisper_model: ${filename}`);
      LOG("info", `Starting download from HuggingFace: ggml/${filename}`);
      try {
        await actions.downloadModel(filename);
        LOG("success", `Model downloaded: ${filename}`);
      } catch (e) {
        LOG("error", `download_whisper_model failed: ${e}`);
        throw e;
      }
    },
    [actions],
  );

  const wrappedInitTranscription = useCallback(
    async (filename: string) => {
      LOG("cmd", `invoke init_transcription: ${filename}`);
      try {
        await actions.initTranscription(filename);
        LOG("success", `Transcription engine initialized with: ${filename}`);
      } catch (e) {
        LOG("error", `init_transcription failed: ${e}`);
        throw e;
      }
    },
    [actions],
  );

  const wrappedQuickSetup = useCallback(async () => {
    LOG("info", "=== Starting Voice Quick Setup ===");
    LOG("cmd", "quickSetup (detect + download + VAD + init)");
    try {
      await actions.quickSetup();
      LOG("success", "=== Voice Quick Setup complete ===");
    } catch (e) {
      LOG("error", `Quick setup failed: ${e}`);
    }
  }, [actions]);

  const wrappedDeleteModel = useCallback(
    async (filename: string) => {
      LOG("cmd", `invoke delete_model: ${filename}`);
      try {
        await actions.deleteModel(filename);
        LOG("success", `Model deleted: ${filename}`);
      } catch (e) {
        LOG("error", `delete_model failed: ${e}`);
      }
    },
    [actions],
  );

  const wrappedActions = useMemo(
    () => ({
      ...actions,
      detectHardware: wrappedDetectHardware,
      downloadModel: wrappedDownloadModel,
      initTranscription: wrappedInitTranscription,
      quickSetup: wrappedQuickSetup,
      deleteModel: wrappedDeleteModel,
    }),
    [
      actions,
      wrappedDetectHardware,
      wrappedDownloadModel,
      wrappedInitTranscription,
      wrappedQuickSetup,
      wrappedDeleteModel,
    ],
  );

  // Session-aware recording start: always create a new session
  const handleStartNewRecording = useCallback(async () => {
    const session = sessionsActions.startNew(
      state.activeModel,
      state.selectedDevice,
    );
    setActiveSessionId(session.id);
    recordingStartRef.current = Date.now();
    await actions.startRecording();
  }, [sessionsActions, state.activeModel, state.selectedDevice, actions]);

  // Session-aware continue recording (append to existing session)
  const handleContinueRecording = useCallback(
    async (sessionId: string) => {
      setActiveSessionId(sessionId);
      recordingStartRef.current = Date.now();
      await actions.startRecording();
    },
    [actions],
  );

  // When stopping, finalize the session
  const handleStopRecording = useCallback(async () => {
    await actions.stopRecording();
    if (activeSessionId) {
      const elapsed = Math.round(
        (Date.now() - recordingStartRef.current) / 1000,
      );
      // Accumulate: continued sessions keep the duration of previous legs.
      const prior = getSession(activeSessionId)?.durationSecs ?? 0;
      sessionsActions.finalize(activeSessionId, prior + elapsed);
    }
  }, [actions, activeSessionId, sessionsActions]);

  // When new segments arrive, persist them immediately
  const prevSegmentCountRef = useRef(0);
  useEffect(() => {
    if (!activeSessionId) return;
    if (state.segments.length > prevSegmentCountRef.current) {
      const newSegs = state.segments.slice(prevSegmentCountRef.current);
      prevSegmentCountRef.current = state.segments.length;
      sessionsActions.append(activeSessionId, newSegs);
    }
  }, [state.segments, activeSessionId, sessionsActions]);

  // Reset segment tracking when recording starts fresh
  useEffect(() => {
    if (state.isRecording) {
      prevSegmentCountRef.current = 0;
    }
  }, [state.isRecording]);

  // Note: we intentionally do NOT auto-switch viewingSessionId here.
  // The session is opened by sessionsActions.startNew() directly inside
  // the hook, and the user's sidebar selection must never be overridden
  // by the recording state.

  if (isMiniMode) {
    return (
      <TranscriptionMiniMode
        isRecording={state.isRecording}
        isProcessingTail={state.isProcessingTail}
        isCalibrating={state.isCalibrating}
        liveRms={state.liveRms}
        recentText={state.fullTranscript}
        onStartRecording={handleStartNewRecording}
        onStopRecording={handleStopRecording}
        onExpand={() => setIsMiniMode(false)}
        onClose={() => setIsMiniMode(false)}
      />
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Wake-word overlay — renders over the entire app window */}
      <WakeWordOverlay
        uiMode={wwState.uiMode}
        rms={state.liveRms || wwState.listenRms}
        transcript={state.fullTranscript}
        onDismiss={wwActions.dismiss}
        {...(engine.engineUrl ? { onPublishToNote: handleOverlayPublishToNote } : {})}
      />

      <PageHeader
        title="Speech to Text"
        description="Confidential speech-to-text transcription on your computer"
      >
        {/* Wake word controls strip — lives in the header action area */}
        <div className="flex items-center gap-2">
          {/* Emergency reset — visible whenever voice subsystem is stuck */}
          {(state.isProcessingTail ||
            state.isCalibrating ||
            state.isRecording) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={actions.forceReset}
              className="gap-1.5 text-red-500 hover:text-red-400 hover:bg-red-500/10"
              title="Force-reset the voice subsystem if it's stuck"
            >
              <RotateCcw className="h-4 w-4" />
              Reset Voice
            </Button>
          )}
          <WakeWordControls
            uiMode={wwState.uiMode}
            listenRms={wwState.listenRms}
            kmsModelReady={wwState.kmsModelReady}
            downloadProgress={wwState.downloadProgress}
            onSetup={wwActions.setup}
            onMute={wwActions.mute}
            onUnmute={wwActions.unmute}
            onManualTrigger={wwActions.manualTrigger}
            onDismiss={wwActions.dismiss}
            disabled={state.isInitializing}
          />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setIsMiniMode(true)}
            className="gap-1.5 text-muted-foreground"
            title="Switch to compact floating mode"
          >
            <Minimize2 className="h-4 w-4" />
            Mini Mode
          </Button>
        </div>
      </PageHeader>
      <SubTabBar tabs={TABS} value={tab} onValueChange={setTab} />
      <div className="flex-1 overflow-hidden">
        {tab === "setup" && (
          <div className="h-full overflow-y-auto p-6 space-y-4">
            {whisperDownloads.length > 0 && (
              <button
                onClick={openDownloadModal}
                className="flex items-center gap-2 w-full rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-sm text-primary hover:bg-primary/10 transition-colors"
              >
                <span className="h-2 w-2 rounded-full bg-primary animate-pulse shrink-0" />
                <span className="flex-1 text-left font-medium">
                  {whisperDownloads.length} model download
                  {whisperDownloads.length !== 1 ? "s" : ""} in progress
                </span>
                <span className="text-xs text-muted-foreground">
                  View progress →
                </span>
              </button>
            )}
            <SetupTab state={state} actions={wrappedActions} />
          </div>
        )}
        {tab === "transcribe" && (
          <TranscribeTab
            state={state}
            actions={{
              ...actions,
              startRecording: handleStartNewRecording,
              stopRecording: handleStopRecording,
            }}
            sessionsState={sessionsState}
            sessionsActions={sessionsActions}
            activeSessionId={activeSessionId}
            onContinueSession={handleContinueRecording}
          />
        )}
        {tab === "models" && (
          <div className="h-full overflow-y-auto p-6">
            <ModelsTab state={state} actions={wrappedActions} />
          </div>
        )}
        {tab === "devices" && (
          <div className="h-full overflow-hidden">
            <DevicesTab state={state} actions={actions} />
          </div>
        )}
        {tab === "wakeword" && (
          <WakeWordPage wwState={wwState} wwActions={wwActions} />
        )}
      </div>
    </div>
  );
}

// ── Setup Tab ──────────────────────────────────────────────────────────────

function SetupTab({
  state,
  actions,
}: {
  state: TranscriptionState;
  actions: TranscriptionActions;
}) {
  const desktopAvailable = isTauri();
  const isSetupDone = state.setupStatus?.setup_complete ?? false;
  const isActive =
    state.isDetecting || state.isDownloading || state.isInitializing;

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      {/* Quick Setup Card */}
      <div className="rounded-xl border bg-card p-6">
        <div className="flex items-start gap-4">
          <div
            className={cn(
              "flex h-12 w-12 items-center justify-center rounded-xl",
              isSetupDone
                ? "bg-emerald-500/10 text-emerald-500"
                : "bg-primary/10 text-primary",
            )}
          >
            {isSetupDone ? (
              <CheckCircle2 className="h-6 w-6" />
            ) : (
              <Zap className="h-6 w-6" />
            )}
          </div>
          <div className="flex-1 space-y-2">
            <h2 className="text-lg font-semibold">
              {isSetupDone
                ? "Voice Features Ready"
                : "Set Up Voice Transcription"}
            </h2>
            <p className="text-sm text-muted-foreground">
              {isSetupDone
                ? `Using ${state.activeModel ?? state.setupStatus?.selected_model ?? "unknown model"}. You can change models in the Models tab.`
                : "One-click setup detects your hardware, downloads the best model for your system, and gets everything ready."}
            </p>
            {!isSetupDone && (
              <Button
                onClick={actions.quickSetup}
                disabled={isActive || !desktopAvailable}
                title={
                  desktopAvailable
                    ? undefined
                    : "Voice setup is available in the desktop app"
                }
                className="mt-2"
                size="lg"
              >
                {state.isDetecting ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Detecting hardware...
                  </>
                ) : state.isDownloading ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Downloading model...
                  </>
                ) : state.isInitializing ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Loading model...
                  </>
                ) : (
                  <>
                    <Zap className="mr-2 h-4 w-4" />
                    One-Click Setup
                  </>
                )}
              </Button>
            )}
            {isSetupDone && (
              <Button
                variant="outline"
                size="sm"
                onClick={actions.refreshSetupStatus}
              >
                <RefreshCw className="mr-1 h-3 w-3" />
                Refresh Status
              </Button>
            )}
          </div>
        </div>

        <DownloadProgress
          progress={state.downloadProgress}
          isDownloading={state.isDownloading}
          error={state.error}
          className="mt-4"
        />
      </div>

      {state.error && (
        <div className="flex items-start gap-3 rounded-lg border border-red-500/20 bg-red-500/5 p-4">
          <AlertCircle className="mt-0.5 h-4 w-4 text-red-500 flex-shrink-0" />
          <div className="flex-1">
            <p className="text-sm font-medium text-red-500">Error</p>
            <p className="text-sm text-red-400 whitespace-pre-wrap">
              {state.error}
            </p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={actions.clearError}
            className="text-red-400 hover:text-red-300"
          >
            Dismiss
          </Button>
        </div>
      )}

      <HardwareInfoCard state={state} actions={actions} />

      <div className="rounded-xl border bg-card p-6 space-y-4">
        <h3 className="font-semibold flex items-center gap-2">
          <Info className="h-4 w-4 text-muted-foreground" />
          How it works
        </h3>
        <div className="space-y-3 text-sm text-muted-foreground">
          <div className="flex items-start gap-3">
            <div className="flex h-6 w-6 items-center justify-center rounded-full bg-primary/10 text-primary text-xs font-bold flex-shrink-0">
              1
            </div>
            <p>
              <strong className="text-foreground">Local processing</strong> —
              All transcription runs on your machine using Whisper. No audio
              leaves your device.
            </p>
          </div>
          <div className="flex items-start gap-3">
            <div className="flex h-6 w-6 items-center justify-center rounded-full bg-primary/10 text-primary text-xs font-bold flex-shrink-0">
              2
            </div>
            <p>
              <strong className="text-foreground">Adaptive models</strong> — The
              system picks the best model for your hardware. Faster machines get
              more accurate models.
            </p>
          </div>
          <div className="flex items-start gap-3">
            <div className="flex h-6 w-6 items-center justify-center rounded-full bg-primary/10 text-primary text-xs font-bold flex-shrink-0">
              3
            </div>
            <p>
              <strong className="text-foreground">Every recording saved</strong>{" "}
              — All sessions are persisted locally. Browse your history in the
              Transcribe tab.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Hardware Info Card ─────────────────────────────────────────────────────

function HardwareInfoCard({
  state,
  actions,
}: {
  state: TranscriptionState;
  actions: TranscriptionActions;
}) {
  const desktopAvailable = isTauri();
  const hw = state.hardwareResult;
  const didAutoDetect = useRef(false);

  useEffect(() => {
    if (
      desktopAvailable &&
      !hw &&
      !state.isDetecting &&
      !didAutoDetect.current
    ) {
      didAutoDetect.current = true;
      void actions.detectHardware();
    }
  }, [desktopAvailable, hw, state.isDetecting, actions]);

  return (
    <div className="rounded-xl border bg-card p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">System Hardware</h3>
        <Button
          variant="outline"
          size="sm"
          onClick={actions.detectHardware}
          disabled={state.isDetecting || !desktopAvailable}
          title={
            desktopAvailable
              ? undefined
              : "Hardware detection is available in the desktop app"
          }
        >
          {state.isDetecting ? (
            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
          ) : (
            <RefreshCw className="mr-1 h-3 w-3" />
          )}
          Rescan
        </Button>
      </div>

      {!hw ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin flex-shrink-0" />
          Scanning system hardware…
        </div>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <HardwareStat
              icon={<HardDrive className="h-4 w-4" />}
              label="System RAM"
              value={formatRam(hw.hardware.total_ram_mb)}
            />
            <HardwareStat
              icon={<Cpu className="h-4 w-4" />}
              label="CPU Threads"
              value={String(hw.hardware.cpu_threads)}
            />
            <HardwareStat
              icon={<Monitor className="h-4 w-4" />}
              label="GPU"
              value={getGpuLabel(hw)}
            />
            <HardwareStat
              icon={<Zap className="h-4 w-4" />}
              label="Acceleration"
              value={getAccelLabel(hw)}
            />
          </div>

          <div className="rounded-lg border bg-muted/30 p-4 space-y-2">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="h-4 w-4 text-emerald-500" />
              <span className="text-sm font-medium">Recommendation</span>
            </div>
            <p className="text-sm text-muted-foreground">{hw.reason}</p>
            <div className="flex items-center gap-2 text-sm">
              <span className="font-mono text-xs bg-muted px-2 py-0.5 rounded">
                {hw.recommended_filename}
              </span>
              <span className="text-muted-foreground">
                ({hw.recommended_size_mb} MB download)
              </span>
            </div>
            {hw.can_upgrade && (
              <p className="text-xs text-amber-500 mt-1">
                Your system can handle a higher-quality model. Check the Models
                tab for options.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function HardwareStat({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center gap-3 rounded-lg border bg-muted/20 p-3">
      <div className="text-muted-foreground">{icon}</div>
      <div>
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="text-sm font-medium">{value}</p>
      </div>
    </div>
  );
}

// ── Transcribe Tab ─────────────────────────────────────────────────────────

function TranscribeTab({
  state,
  actions,
  sessionsState,
  sessionsActions,
  activeSessionId,
  onContinueSession,
}: {
  state: TranscriptionState;
  actions: TranscriptionActions;
  sessionsState: SessionsState;
  sessionsActions: SessionsActions;
  activeSessionId: string | null;
  onContinueSession: (sessionId: string) => Promise<void>;
}) {
  const { permissions, check, request, openSettings } = usePermissionsContext();
  const [permError, setPermError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(true);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [editingTitleId, setEditingTitleId] = useState<string | null>(null);
  const [titleDraft, setTitleDraft] = useState("");
  const [pushingToNote, setPushingToNote] = useState<string | null>(null);
  const [pushSuccess, setPushSuccess] = useState<string | null>(null);
  const [pushError, setPushError] = useState<string | null>(null);
  // Local editable draft for the transcript text — synced from viewing session
  const [textDraft, setTextDraft] = useState<string>("");
  const textDraftSessionRef = useRef<string | null>(null);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Mirror of textDraft for effects that must read it without re-running
  const textDraftRef = useRef(textDraft);
  useEffect(() => {
    textDraftRef.current = textDraft;
  }, [textDraft]);
  // When continuing an existing session, the live transcript only contains
  // the NEW leg (startRecording clears segments). This holds the session's
  // text at continue time so the draft shows base + new leg, not just the leg.
  const continueBaseTextRef = useRef<string>("");

  // ── LLM Polish integration ─────────────────────────────────────────────
  const [llmState, llmActions] = useLlmApp();
  const llmServerPort = llmState.serverStatus?.port ?? null;
  const llmServerRunning = llmState.serverStatus?.running ?? false;
  const { run: runPipeline, running: polishing } = useLlmPipeline(
    () => llmServerPort,
  );
  const [polishError, setPolishError] = useState<string | null>(null);
  const [polishSuccess, setPolishSuccess] = useState<string | null>(null);
  /** When true, show the "LLM not running" modal */
  const [showLlmModal, setShowLlmModal] = useState(false);
  /** Filename selected in the LLM modal for starting */
  const [modalSelectedModel, setModalSelectedModel] = useState<string | null>(
    null,
  );
  /** When true, run polish automatically once the server comes up */
  const pendingPolishRef = useRef(false);
  /** The preset ID queued for auto-polish after server start */
  const pendingPolishPresetRef = useRef<string>("builtin-standard");

  // ── Polish presets ──────────────────────────────────────────────────────
  const polishPresets = usePolishPresets();
  const [polishDropdownOpen, setPolishDropdownOpen] = useState(false);
  const [showManagePresets, setShowManagePresets] = useState(false);
  const [editingPreset, setEditingPreset] = useState<PolishPreset | null>(null);
  const [presetDraftName, setPresetDraftName] = useState("");
  const [presetDraftPrompt, setPresetDraftPrompt] = useState("");

  // When the LLM server starts while the modal is open with a pending polish, close
  // the modal and fire polish automatically.
  // Shared polish execution — called by handlePolish and by the auto-fire
  // after server start. Accepts an optional preset override; falls back to
  // the user's default preset when omitted.
  const executePolish = useCallback(
    async (presetId?: string) => {
      const session = sessionsState.viewingSession;
      const transcriptText = textDraft.trim();
      if (!session || !transcriptText) return;

      const pid = presetId ?? polishPresets.defaultPresetId;
      const preset =
        polishPresets.presets.find((p) => p.id === pid) ??
        polishPresets.defaultPreset;

      // Build an inline template using the preset's system prompt
      const template = {
        description: preset.name,
        system: preset.systemPrompt,
        user: "Transcript:\n\n{{transcript}}",
        outputSchema: {
          type: "object",
          properties: {
            title: { type: "string" },
            description: { type: "string" },
            tags: { type: "array", items: { type: "string" } },
            cleaned: { type: "string" },
          },
          required: ["title", "description", "tags", "cleaned"],
          additionalProperties: false,
        },
        maxTokens: 4096,
        temperature: 0.2,
      };

      setPolishError(null);
      setPolishSuccess(null);
      try {
        const raw = await runPipeline<TranscriptPolishOutput>(template, {
          transcript: transcriptText,
        });
        const result = parsePolishOutput(
          raw,
          session.title ?? "",
          transcriptText,
        );
        if (saveTimerRef.current) {
          clearTimeout(saveTimerRef.current);
          saveTimerRef.current = null;
          sessionsActions.updateText(session.id, textDraft);
        }
        sessionsActions.applyPolish(session.id, {
          polishedText: result.cleaned,
          aiTitle: result.title || null,
          aiDescription: result.description || null,
          aiTags: result.tags,
        });
        setTextDraft(result.cleaned);
        textDraftSessionRef.current = session.id;
        setPolishSuccess(session.id);
        setTimeout(() => setPolishSuccess(null), 4000);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setPolishError(msg);
      }
    },
    [
      sessionsState.viewingSession,
      textDraft,
      polishPresets.defaultPresetId,
      polishPresets.defaultPreset,
      polishPresets.presets,
      runPipeline,
      sessionsActions,
    ],
  );

  useEffect(() => {
    if (llmServerRunning && pendingPolishRef.current) {
      pendingPolishRef.current = false;
      setShowLlmModal(false);
      // Trigger polish on next tick so runPipeline sees the updated port
      setTimeout(() => void executePolish(pendingPolishPresetRef.current), 200);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [llmServerRunning]);

  // Sync textDraft when the viewed session changes or gets new segments
  const viewingSession = sessionsState.viewingSession;
  const isViewingActive =
    viewingSession?.id === activeSessionId && state.isRecording;

  useEffect(() => {
    if (!viewingSession) return;
    // If switching to a different session, load its saved text.
    if (textDraftSessionRef.current !== viewingSession.id) {
      textDraftSessionRef.current = viewingSession.id;
      if (!isViewingActive) continueBaseTextRef.current = "";
      setTextDraft(viewingSession.fullText);
      return;
    }
    // Same session — while actively recording, show prior text (when
    // continuing a session) plus the live new-leg transcript.
    if (isViewingActive) {
      const base = continueBaseTextRef.current;
      const liveText = state.fullTranscript;
      setTextDraft(
        base ? (liveText ? `${base} ${liveText}` : base) : liveText,
      );
      return;
    }
    // Not recording this session — follow the persisted text (e.g. after a
    // stop finalizes/flushes segments) unless the user has a pending
    // debounced edit in flight. Do NOT overwrite unsaved user edits.
    continueBaseTextRef.current = "";
    if (
      !saveTimerRef.current &&
      viewingSession.fullText !== textDraftRef.current
    ) {
      setTextDraft(viewingSession.fullText);
    }
  }, [
    viewingSession,
    viewingSession?.fullText,
    isViewingActive,
    state.fullTranscript,
  ]);

  // Debounced save: persist user edits 600ms after they stop typing
  const handleTextChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const newText = e.target.value;
      setTextDraft(newText);
      if (!textDraftSessionRef.current) return;
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      const sid = textDraftSessionRef.current;
      saveTimerRef.current = setTimeout(() => {
        saveTimerRef.current = null;
        sessionsActions.updateText(sid, newText);
      }, 600);
    },
    [sessionsActions],
  );

  // Clear the debounce timer on unmount so it doesn't fire on stale state.
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, []);

  // Load device list if not yet populated
  useEffect(() => {
    if (state.audioDevices.length === 0) {
      actions.listAudioDevices();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleStartRecording = useCallback(async () => {
    setPermError(null);
    const status = await check("microphone");
    if (status === "granted") {
      await actions.startRecording();
      return;
    }
    if (status === "not_determined") {
      await request("microphone");
      const recheck = await check("microphone");
      if (recheck === "granted") {
        await actions.startRecording();
      } else {
        setPermError(
          "Microphone access is required for transcription. Please allow access when prompted.",
        );
      }
      return;
    }
    if (status === "denied" || status === "restricted") {
      setPermError(
        status === "restricted"
          ? "Microphone access is restricted on this device (parental controls or MDM policy)."
          : "Microphone access was denied. Open System Settings → Privacy & Security → Microphone and enable access for Matrx Local.",
      );
      await openSettings("microphone");
      return;
    }
    await actions.startRecording();
  }, [check, request, openSettings, actions]);

  const handleContinueRecording = useCallback(
    async (sessionId: string) => {
      setPermError(null);
      // Remember the session's existing text so the draft can show it ahead
      // of the live new-leg transcript (startRecording clears segments).
      const beginContinue = async () => {
        continueBaseTextRef.current = getSession(sessionId)?.fullText ?? "";
        sessionsActions.open(sessionId);
        await onContinueSession(sessionId);
      };
      const status = await check("microphone");
      if (status === "granted") {
        await beginContinue();
        return;
      }
      if (status === "not_determined") {
        await request("microphone");
        const recheck = await check("microphone");
        if (recheck === "granted") {
          await beginContinue();
        } else {
          setPermError("Microphone access is required for transcription.");
        }
        return;
      }
      setPermError(
        "Microphone access denied. Check System Settings → Privacy & Security.",
      );
    },
    [check, request, onContinueSession, sessionsActions],
  );

  const handleCopy = useCallback((text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  }, []);

  const handlePushToNote = useCallback(
    async (session: TranscriptionSession, currentText: string) => {
      if (!engine.engineUrl) {
        console.warn(
          "[voice] push to note skipped — engine not discovered yet",
        );
        setPushError(
          "Engine not connected yet — cannot save the note. Try again in a moment.",
        );
        return;
      }
      if (!currentText.trim()) {
        console.warn("[voice] push to note skipped — empty transcript");
        return;
      }
      setPushingToNote(session.id);
      setPushError(null);
      try {
        const date = new Date(session.createdAt);
        const label = session.title
          ? session.title
          : `Voice Note — ${date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`;
        await engine.createNote("local", {
          label,
          content: currentText,
          folder_name: "Voice Notes",
        });
        setPushSuccess(session.id);
        setTimeout(() => setPushSuccess(null), 3000);
      } catch (err) {
        console.error("[voice] push to note failed:", err);
        setPushError(
          `Failed to save note: ${err instanceof Error ? err.message : String(err)}`,
        );
      } finally {
        setPushingToNote(null);
      }
    },
    [],
  );

  const handleStartTitleEdit = useCallback((session: TranscriptionSession) => {
    setEditingTitleId(session.id);
    setTitleDraft(session.title ?? "");
  }, []);

  const handleSaveTitle = useCallback(() => {
    if (!editingTitleId) return;
    sessionsActions.rename(editingTitleId, titleDraft.trim() || null);
    setEditingTitleId(null);
  }, [editingTitleId, titleDraft, sessionsActions]);

  const handlePolish = useCallback(
    async (presetId?: string) => {
      const session = sessionsState.viewingSession;
      if (!session) return;
      const transcriptText = textDraft.trim();
      if (!transcriptText) return;

      // If LLM is not running, open the model-picker modal
      if (!llmServerRunning) {
        const lastModel = llmState.serverStatus?.model_name ?? null;
        const firstDownloaded = llmState.downloadedModels[0]?.filename ?? null;
        setModalSelectedModel(lastModel ?? firstDownloaded);
        pendingPolishPresetRef.current =
          presetId ?? polishPresets.defaultPresetId;
        setShowLlmModal(true);
        return;
      }

      await executePolish(presetId);
    },
    [
      sessionsState.viewingSession,
      textDraft,
      llmServerRunning,
      llmState.serverStatus,
      llmState.downloadedModels,
      polishPresets.defaultPresetId,
      executePolish,
    ],
  );

  const handleRestoreRaw = useCallback(() => {
    const session = sessionsState.viewingSession;
    if (!session?.rawText) return;
    sessionsActions.updateText(session.id, session.rawText);
    setTextDraft(session.rawText);
    textDraftSessionRef.current = session.id;
  }, [sessionsState.viewingSession, sessionsActions]);

  const setupDoneInConfig = state.setupStatus?.setup_complete ?? false;
  const modelLoadedInMemory = state.activeModel !== null;

  const defaultDevice = state.audioDevices.find((d) => d.is_default);
  const activeDeviceName =
    state.selectedDevice ?? defaultDevice?.name ?? "System default";

  if (setupDoneInConfig && !modelLoadedInMemory) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="flex flex-col items-center justify-center rounded-xl border bg-card p-12 text-center space-y-4 max-w-md w-full">
          <Loader2 className="h-10 w-10 text-primary animate-spin" />
          <h3 className="text-lg font-semibold">Loading model…</h3>
          <p className="text-sm text-muted-foreground">
            {state.setupStatus?.selected_model
              ? `Loading ${state.setupStatus.selected_model} into memory. This takes a few seconds.`
              : "Initializing transcription engine…"}
          </p>
          <button
            className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
            onClick={async () => {
              const model = state.setupStatus?.selected_model;
              if (model) await actions.initTranscription(model);
            }}
          >
            Taking too long? Click to retry
          </button>
        </div>
      </div>
    );
  }

  if (!setupDoneInConfig) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="flex flex-col items-center justify-center rounded-xl border bg-card p-12 text-center space-y-4 max-w-md w-full">
          <Mic className="h-12 w-12 text-muted-foreground/30" />
          <h3 className="text-lg font-semibold">Setup Required</h3>
          <p className="text-sm text-muted-foreground max-w-md">
            Complete the voice setup first to enable transcription. Go to the
            Setup tab and click "One-Click Setup".
          </p>
        </div>
      </div>
    );
  }

  // viewingSession and isViewingActive are now declared at the top of TranscribeTab
  // so the text-sync effect can reference them.

  return (
    <div className="flex h-full overflow-hidden">
      {/* ── Sidebar: Session History ── */}
      {showHistory && (
        <div className="w-64 shrink-0 border-r flex flex-col h-full">
          <div className="flex items-center justify-between px-3 py-2 border-b shrink-0">
            <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
              Recordings
            </span>
            <button
              onClick={() => setShowHistory(false)}
              className="h-6 w-6 flex items-center justify-center rounded hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
              title="Hide sidebar"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
            </button>
          </div>

          {/* New Recording button */}
          <div className="px-2 py-2 shrink-0">
            <button
              onClick={handleStartRecording}
              disabled={state.isRecording || state.isProcessingTail}
              className={cn(
                "w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors",
                state.isRecording || state.isProcessingTail
                  ? "bg-muted text-muted-foreground cursor-not-allowed"
                  : "bg-primary text-primary-foreground hover:bg-primary/90",
              )}
            >
              <Plus className="h-4 w-4 shrink-0" />
              New Recording
            </button>
          </div>

          {/* Session list */}
          <div className="flex-1 overflow-y-auto">
            {sessionsState.sessions.length === 0 ? (
              <div className="px-4 py-8 text-center">
                <Mic className="h-8 w-8 text-muted-foreground/30 mx-auto mb-2" />
                <p className="text-xs text-muted-foreground">
                  No recordings yet
                </p>
              </div>
            ) : (
              <div className="p-2 space-y-0.5">
                {sessionsState.sessions.map((session) => {
                  const isActive =
                    session.id === activeSessionId && state.isRecording;
                  const isViewing =
                    session.id === sessionsState.viewingSessionId;
                  const date = new Date(session.createdAt);

                  return (
                    <button
                      key={session.id}
                      onClick={() => sessionsActions.open(session.id)}
                      className={cn(
                        "w-full text-left rounded-lg px-3 py-2.5 transition-colors group relative",
                        isViewing
                          ? "bg-primary/10 text-primary"
                          : "hover:bg-muted text-foreground",
                      )}
                    >
                      <div className="flex items-start gap-2">
                        {isActive ? (
                          <div className="h-2 w-2 rounded-full bg-red-500 animate-pulse mt-1 shrink-0" />
                        ) : (
                          <Mic className="h-3.5 w-3.5 mt-0.5 text-muted-foreground shrink-0" />
                        )}
                        <div className="min-w-0 flex-1">
                          <p className="text-xs font-medium truncate leading-tight">
                            {session.title ?? formatSessionTitle(date)}
                          </p>
                          <div className="flex items-center gap-1.5 mt-0.5">
                            <Clock className="h-2.5 w-2.5 text-muted-foreground/60 shrink-0" />
                            <span className="text-[10px] text-muted-foreground/60">
                              {formatRelativeTime(date)}
                            </span>
                            {session.charCount > 0 && (
                              <span className="text-[10px] text-muted-foreground/60">
                                · {session.charCount}c
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Main panel ── */}
      <div className="flex-1 flex flex-col h-full overflow-hidden">
        {/* Show history toggle when sidebar is hidden */}
        {!showHistory && (
          <div className="shrink-0 px-4 pt-3">
            <button
              onClick={() => setShowHistory(true)}
              className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              <ChevronRight className="h-3.5 w-3.5" />
              Show history
            </button>
          </div>
        )}

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* Active config bar */}
          <div className="rounded-xl border bg-card px-4 py-3">
            <div className="flex items-center gap-4 flex-wrap">
              <div className="flex items-center gap-2 min-w-0">
                <Cpu className="h-4 w-4 text-muted-foreground shrink-0" />
                <div className="min-w-0">
                  <p className="text-xs text-muted-foreground">Model</p>
                  <p className="text-sm font-medium truncate">
                    {state.activeModel ?? (
                      <span className="text-amber-500 italic">
                        No model loaded
                      </span>
                    )}
                  </p>
                </div>
              </div>
              <div className="w-px h-8 bg-border shrink-0 hidden sm:block" />
              <div className="flex items-center gap-2 min-w-0 flex-1">
                <Mic className="h-4 w-4 text-muted-foreground shrink-0" />
                <div className="min-w-0">
                  <p className="text-xs text-muted-foreground">
                    Microphone{state.selectedDevice ? "" : " (system default)"}
                  </p>
                  <p className="text-sm font-medium truncate">
                    {activeDeviceName}
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Permission error */}
          {permError && (
            <div className="flex items-start gap-3 rounded-lg border border-amber-500/20 bg-amber-500/5 p-3">
              <AlertCircle className="mt-0.5 h-4 w-4 text-amber-500 shrink-0" />
              <p className="text-sm text-amber-400">{permError}</p>
            </div>
          )}

          {/* Engine error */}
          {state.error && (
            <div className="flex items-start gap-3 rounded-lg border border-red-500/20 bg-red-500/5 p-3">
              <AlertCircle className="mt-0.5 h-4 w-4 text-red-500 shrink-0" />
              <p className="text-sm text-red-400">{state.error}</p>
            </div>
          )}

          {/* Recording controls */}
          <div className="rounded-xl border bg-card p-5">
            <div className="mb-3">
              <h3 className="font-semibold">
                {state.isRecording
                  ? "Recording…"
                  : state.isProcessingTail
                    ? "Processing…"
                    : "Ready to Record"}
              </h3>
              <p className="text-sm text-muted-foreground mt-0.5">
                {state.isRecording
                  ? "Listening… speak into your microphone"
                  : state.isProcessingTail
                    ? "Processing remaining audio…"
                    : "Each recording is automatically saved to history"}
              </p>
            </div>

            {/* Device quick-select */}
            {state.audioDevices.length > 0 && (
              <div className="mb-4 flex flex-wrap gap-1.5">
                <button
                  onClick={() => actions.setSelectedDevice(null)}
                  className={cn(
                    "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors",
                    !state.selectedDevice
                      ? "border-primary bg-primary/10 text-primary font-medium"
                      : "border-border text-muted-foreground hover:border-primary/40",
                  )}
                >
                  <Volume2 className="h-3 w-3" />
                  Default
                </button>
                {state.audioDevices.map((dev, i) => (
                  <button
                    key={i}
                    onClick={() => actions.setSelectedDevice(dev.name)}
                    className={cn(
                      "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors max-w-[200px]",
                      state.selectedDevice === dev.name
                        ? "border-primary bg-primary/10 text-primary font-medium"
                        : "border-border text-muted-foreground hover:border-primary/40",
                    )}
                  >
                    <Mic className="h-3 w-3 shrink-0" />
                    <span className="truncate">{dev.name}</span>
                  </button>
                ))}
              </div>
            )}

            <div className="flex flex-col items-center gap-3 py-4">
              {/* Mic permission denied banner */}
              {permissions.get("microphone")?.status === "denied" &&
                !state.isRecording && (
                  <div className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-2 text-sm text-amber-500">
                    <MicOff className="h-4 w-4 shrink-0" />
                    <span>Microphone access denied.</span>
                    <button
                      className="underline underline-offset-2 hover:text-amber-400"
                      onClick={() => openSettings("microphone")}
                    >
                      Open Settings
                    </button>
                  </div>
                )}

              {/* Main mic button */}
              <RecordingMicButton
                isRecording={state.isRecording}
                isProcessingTail={state.isProcessingTail}
                liveRms={state.liveRms}
                onToggle={
                  state.isRecording
                    ? actions.stopRecording
                    : handleStartRecording
                }
                size="lg"
              />

              {state.isProcessingTail && (
                <div className="flex flex-col items-center gap-2 text-center">
                  <p className="text-xs text-amber-500">
                    Finishing transcription of remaining audio…
                  </p>
                  <button
                    onClick={actions.forceReset}
                    className="text-xs text-muted-foreground underline underline-offset-2 hover:text-red-400 transition-colors"
                  >
                    Taking too long? Click to reset
                  </button>
                </div>
              )}

              {/* Live audio level meter */}
              {state.isRecording && (
                <div className="w-full max-w-xs">
                  <RmsLevelBar
                    liveRms={state.liveRms}
                    showDot
                    showReadout
                    label={
                      state.isCalibrating
                        ? "Calibrating mic level…"
                        : "Recording"
                    }
                    {...(state.selectedDevice ? { detail: state.selectedDevice } : {})}
                  />
                </div>
              )}
            </div>
          </div>

          {/* Viewing session / live transcript */}
          {(viewingSession || state.isRecording) && (
            <div className="rounded-xl border bg-card">
              {/* Session header */}
              {viewingSession && (
                <div className="flex items-center gap-3 px-5 py-3 border-b">
                  {editingTitleId === viewingSession.id ? (
                    <div className="flex-1 flex items-center gap-2">
                      <input
                        type="text"
                        value={titleDraft}
                        onChange={(e) => setTitleDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleSaveTitle();
                          if (e.key === "Escape") setEditingTitleId(null);
                        }}
                        placeholder="Add a title…"
                        autoFocus
                        className="flex-1 bg-transparent text-sm font-medium outline-none border-b border-primary/50 pb-0.5"
                      />
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 px-2 text-xs"
                        onClick={handleSaveTitle}
                      >
                        Save
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 px-2 text-xs text-muted-foreground"
                        onClick={() => setEditingTitleId(null)}
                      >
                        Cancel
                      </Button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 flex-1 min-w-0">
                      <h3 className="font-semibold truncate text-sm">
                        {viewingSession.title ??
                          formatSessionTitle(
                            new Date(viewingSession.createdAt),
                          )}
                      </h3>
                      {isViewingActive && (
                        <span className="flex items-center gap-1 text-xs text-red-500">
                          <div className="h-1.5 w-1.5 rounded-full bg-red-500 animate-pulse" />
                          Live
                        </span>
                      )}
                      <button
                        onClick={() => handleStartTitleEdit(viewingSession)}
                        className="h-5 w-5 flex items-center justify-center rounded hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
                        title="Edit title"
                      >
                        <Pencil className="h-3 w-3" />
                      </button>
                    </div>
                  )}

                  {/* Action buttons */}
                  <div className="flex items-center gap-1 shrink-0">
                    {/* Continue recording */}
                    {!state.isRecording &&
                      !state.isProcessingTail &&
                      viewingSession.segments.length > 0 && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 px-2 text-xs gap-1"
                          onClick={() =>
                            handleContinueRecording(viewingSession.id)
                          }
                          title="Continue recording into this session"
                        >
                          <Mic className="h-3 w-3" />
                          Continue
                        </Button>
                      )}

                    {/* Push to note */}
                    {textDraft.length > 0 && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className={cn(
                          "h-7 px-2 text-xs gap-1",
                          pushSuccess === viewingSession.id &&
                            "text-emerald-500",
                        )}
                        onClick={() =>
                          handlePushToNote(viewingSession, textDraft)
                        }
                        disabled={pushingToNote === viewingSession.id}
                        title="Save transcript as a note"
                      >
                        {pushingToNote === viewingSession.id ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : pushSuccess === viewingSession.id ? (
                          <Check className="h-3 w-3" />
                        ) : (
                          <FileText className="h-3 w-3" />
                        )}
                        {pushSuccess === viewingSession.id
                          ? "Saved!"
                          : "Push to Note"}
                      </Button>
                    )}

                    {/* Copy */}
                    {textDraft.length > 0 && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className={cn(
                          "h-7 px-2 text-xs gap-1",
                          copiedId === viewingSession.id && "text-emerald-500",
                        )}
                        onClick={() => handleCopy(textDraft, viewingSession.id)}
                        title="Copy transcript"
                      >
                        {copiedId === viewingSession.id ? (
                          <Check className="h-3 w-3" />
                        ) : (
                          <Copy className="h-3 w-3" />
                        )}
                        {copiedId === viewingSession.id ? "Copied!" : "Copy"}
                      </Button>
                    )}

                    {/* AI Polish — split button: left = run default preset, right = dropdown */}
                    {textDraft.length > 0 && !isViewingActive && (
                      <Popover
                        open={polishDropdownOpen}
                        onOpenChange={setPolishDropdownOpen}
                      >
                        <div className="flex items-center">
                          {/* Left: run default preset */}
                          <button
                            className={cn(
                              "inline-flex items-center gap-1 rounded-l-md h-7 px-2 text-xs",
                              "hover:bg-accent transition-colors",
                              polishSuccess === viewingSession.id &&
                                "text-emerald-500",
                              !llmServerRunning && "text-muted-foreground",
                              polishing && "opacity-50 cursor-not-allowed",
                            )}
                            onClick={() => void handlePolish()}
                            disabled={polishing}
                            title={
                              llmServerRunning
                                ? `AI Polish using "${polishPresets.defaultPreset.name}"`
                                : "Local LLM not running — click to start it"
                            }
                          >
                            {polishing ? (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            ) : polishSuccess === viewingSession.id ? (
                              <Check className="h-3 w-3" />
                            ) : (
                              <Sparkles className="h-3 w-3" />
                            )}
                            {polishSuccess === viewingSession.id
                              ? "Polished!"
                              : "AI Polish"}
                            {!llmServerRunning && (
                              <span className="text-[9px] text-amber-500 ml-0.5">
                                ●
                              </span>
                            )}
                          </button>
                          {/* Right: dropdown chevron */}
                          <PopoverTrigger>
                            <button
                              className={cn(
                                "inline-flex items-center justify-center rounded-r-md h-7 w-5",
                                "hover:bg-accent border-l border-border/40 transition-colors",
                                polishing && "opacity-50 cursor-not-allowed",
                              )}
                              disabled={polishing}
                              title="Choose AI Polish preset"
                            >
                              <ChevronDown className="h-3 w-3" />
                            </button>
                          </PopoverTrigger>
                        </div>

                        <PopoverContent
                          side="bottom"
                          align="end"
                          className="w-64 p-1.5 space-y-0.5"
                        >
                          <p className="px-2 py-1 text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
                            Presets
                          </p>
                          {polishPresets.presets.map((preset) => (
                            <button
                              key={preset.id}
                              className={cn(
                                "w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-xs text-left",
                                "hover:bg-accent transition-colors",
                                preset.id === polishPresets.defaultPresetId &&
                                  "font-medium",
                              )}
                              onClick={() => {
                                setPolishDropdownOpen(false);
                                void handlePolish(preset.id);
                              }}
                            >
                              <span className="flex-1 truncate">
                                {preset.name}
                              </span>
                              {preset.id === polishPresets.defaultPresetId && (
                                <Star className="h-3 w-3 text-amber-500 shrink-0" />
                              )}
                            </button>
                          ))}
                          <div className="border-t border-border/50 my-1" />
                          <button
                            className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-xs text-left text-muted-foreground hover:bg-accent transition-colors"
                            onClick={() => {
                              setPolishDropdownOpen(false);
                              setShowManagePresets(true);
                            }}
                          >
                            <Settings2 className="h-3 w-3" />
                            Manage presets…
                          </button>
                        </PopoverContent>
                      </Popover>
                    )}

                    {/* Restore original — only shown after AI polish */}
                    {viewingSession.rawText &&
                      viewingSession.rawText !== viewingSession.fullText &&
                      !isViewingActive && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 px-2 text-xs gap-1 text-muted-foreground"
                          onClick={handleRestoreRaw}
                          title="Restore original unpolished transcript"
                        >
                          <Undo2 className="h-3 w-3" />
                          Restore
                        </Button>
                      )}

                    {/* Delete */}
                    {!isViewingActive && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 px-0 text-muted-foreground hover:text-red-500"
                        onClick={() =>
                          sessionsActions.remove(viewingSession.id)
                        }
                        title="Delete recording"
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    )}
                  </div>
                </div>
              )}

              {/* Transcript content — editable textarea */}
              <div className="flex flex-col">
                {viewingSession && (
                  <div className="flex items-center gap-3 px-5 py-2 border-b border-border/40 text-xs text-muted-foreground">
                    <span>
                      {new Date(viewingSession.createdAt).toLocaleString()}
                    </span>
                    {viewingSession.durationSecs > 0 && (
                      <span>
                        · {formatDuration(viewingSession.durationSecs)}
                      </span>
                    )}
                    {viewingSession.modelUsed && (
                      <span>· {viewingSession.modelUsed}</span>
                    )}
                    {viewingSession.charCount > 0 && (
                      <span className="ml-auto">
                        {viewingSession.charCount} chars
                      </span>
                    )}
                  </div>
                )}

                {/* AI polish result banner */}
                {viewingSession?.aiProcessedAt && (
                  <div className="px-5 py-2.5 border-b border-border/40 bg-primary/5 space-y-2">
                    {viewingSession.aiDescription && (
                      <p className="text-xs text-muted-foreground leading-relaxed">
                        <span className="font-medium text-foreground">
                          Summary:
                        </span>{" "}
                        {viewingSession.aiDescription}
                      </p>
                    )}
                    {viewingSession.aiTags &&
                      viewingSession.aiTags.length > 0 && (
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <Tag className="h-3 w-3 text-muted-foreground shrink-0" />
                          {viewingSession.aiTags.map((tag) => (
                            <span
                              key={tag}
                              className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground"
                            >
                              {tag}
                            </span>
                          ))}
                        </div>
                      )}
                    <p className="text-[10px] text-muted-foreground/60">
                      AI polished ·{" "}
                      {new Date(viewingSession.aiProcessedAt).toLocaleString()}
                    </p>
                  </div>
                )}

                {/* Push-to-note error */}
                {pushError && (
                  <div className="flex items-start gap-2 px-5 py-2 border-b border-destructive/20 bg-destructive/5">
                    <AlertCircle className="h-3.5 w-3.5 text-destructive shrink-0 mt-0.5" />
                    <span className="text-xs text-destructive flex-1">
                      {pushError}
                    </span>
                    <button
                      className="text-[10px] text-destructive/70 hover:text-destructive underline"
                      onClick={() => setPushError(null)}
                    >
                      Dismiss
                    </button>
                  </div>
                )}

                {/* AI polish error */}
                {polishError && (
                  <div className="flex items-start gap-2 px-5 py-2 border-b border-destructive/20 bg-destructive/5">
                    <AlertCircle className="h-3.5 w-3.5 text-destructive shrink-0 mt-0.5" />
                    <span className="text-xs text-destructive flex-1">
                      {polishError}
                    </span>
                    <button
                      className="text-[10px] text-destructive/70 hover:text-destructive underline"
                      onClick={() => setPolishError(null)}
                    >
                      Dismiss
                    </button>
                  </div>
                )}

                {/* Live pulsing indicator between segments while recording */}
                {isViewingActive && (
                  <div className="flex items-center gap-2 px-5 py-2 border-b border-border/40 bg-red-500/5">
                    <div className="h-1.5 w-1.5 rounded-full bg-red-500 animate-pulse shrink-0" />
                    <span className="text-xs text-red-500 font-medium">
                      {state.isCalibrating
                        ? "Calibrating microphone…"
                        : "Listening — transcript updates as each chunk is processed"}
                    </span>
                  </div>
                )}
                {state.isProcessingTail &&
                  viewingSession?.id === activeSessionId && (
                    <div className="flex items-center gap-2 px-5 py-2 border-b border-border/40 bg-amber-500/5">
                      <Loader2 className="h-3.5 w-3.5 text-amber-500 animate-spin shrink-0" />
                      <span className="text-xs text-amber-500 font-medium">
                        Finishing transcription of remaining audio…
                      </span>
                    </div>
                  )}

                <textarea
                  className={cn(
                    "w-full resize-none bg-transparent px-5 py-4 text-sm leading-relaxed",
                    "focus:outline-none placeholder:text-muted-foreground/50",
                    "min-h-[200px]",
                    isViewingActive && "cursor-default select-text",
                  )}
                  value={textDraft}
                  onChange={handleTextChange}
                  readOnly={isViewingActive}
                  placeholder={
                    state.isRecording
                      ? "Listening… transcript will appear here as you speak"
                      : state.isProcessingTail
                        ? "Processing remaining audio…"
                        : "No transcript yet. Start recording to begin.\n\nYou can also type or paste text here directly."
                  }
                  spellCheck
                />

                {!isViewingActive && textDraft.length > 0 && (
                  <p className="px-5 pb-2 text-[10px] text-muted-foreground/50 text-right">
                    Click to edit · auto-saved
                  </p>
                )}
              </div>
            </div>
          )}

          {/* LLM model-picker / startup modal */}
          {showLlmModal && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
              <div className="relative mx-4 w-full max-w-md rounded-xl border bg-card shadow-2xl">
                {/* Header */}
                <div className="flex items-start gap-3 p-5 pb-4">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-amber-500/10 shrink-0 mt-0.5">
                    <Sparkles className="h-5 w-5 text-amber-500" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <h3 className="font-semibold text-sm">
                      Choose a Model for AI Polish
                    </h3>
                    <p className="text-xs text-muted-foreground mt-1 leading-relaxed">
                      {llmState.isStarting
                        ? "Starting the model — this takes a moment on first load…"
                        : "Select a downloaded model to start, then polish will run automatically."}
                    </p>
                  </div>
                  {!llmState.isStarting && (
                    <button
                      className="shrink-0 rounded-sm p-0.5 text-muted-foreground hover:text-foreground"
                      onClick={() => {
                        pendingPolishRef.current = false;
                        setShowLlmModal(false);
                      }}
                      aria-label="Close"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  )}
                </div>

                {/* Startup progress */}
                {llmState.isStarting && (
                  <div className="mx-5 mb-4 rounded-lg bg-muted/50 p-3 space-y-2">
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" />
                      <span className="truncate">
                        {llmState.startingModelName ?? "Loading model…"}
                      </span>
                    </div>
                    {llmState.serverStartProgress && (
                      <>
                        <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
                          <div
                            className="h-full rounded-full bg-amber-500 transition-all duration-300"
                            style={{
                              width: `${llmState.serverStartProgress.percent}%`,
                            }}
                          />
                        </div>
                        <p className="text-[10px] text-muted-foreground">
                          {llmState.serverStartProgress.phase} ·{" "}
                          {Math.round(llmState.serverStartProgress.percent)}%
                        </p>
                      </>
                    )}
                    {(() => {
                      const lastLog =
                        llmState.serverLogs[llmState.serverLogs.length - 1];
                      return (
                        lastLog && (
                          <p className="text-[10px] text-muted-foreground/70 truncate font-mono">
                            {lastLog.line}
                          </p>
                        )
                      );
                    })()}
                  </div>
                )}

                {/* Model list */}
                {!llmState.isStarting && (
                  <div className="mx-5 mb-4 space-y-1.5 max-h-56 overflow-y-auto">
                    {llmState.downloadedModels.length === 0 ? (
                      <div className="rounded-lg bg-muted/40 px-3 py-4 text-center">
                        <p className="text-xs text-muted-foreground">
                          No models downloaded yet.
                        </p>
                        <p className="text-[11px] text-muted-foreground/60 mt-1">
                          Go to the{" "}
                          <span className="font-medium text-foreground">
                            Models
                          </span>{" "}
                          tab to download one.
                        </p>
                      </div>
                    ) : (
                      llmState.downloadedModels.map((m) => {
                        const isSelected = modalSelectedModel === m.filename;
                        return (
                          <button
                            key={m.filename}
                            onClick={() => setModalSelectedModel(m.filename)}
                            className={cn(
                              "w-full flex items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors",
                              isSelected
                                ? "border-amber-500/50 bg-amber-500/5"
                                : "border-transparent bg-muted/40 hover:bg-muted/70",
                            )}
                          >
                            <div
                              className={cn(
                                "h-3.5 w-3.5 rounded-full border-2 shrink-0 transition-colors",
                                isSelected
                                  ? "border-amber-500 bg-amber-500"
                                  : "border-muted-foreground/40",
                              )}
                            />
                            <div className="flex-1 min-w-0">
                              <p className="text-xs font-medium truncate">
                                {m.name || m.filename}
                              </p>
                              <p className="text-[10px] text-muted-foreground">
                                {m.size_gb} GB
                              </p>
                            </div>
                            {llmState.serverStatus?.model_name === m.filename &&
                              !llmServerRunning && (
                                <span className="text-[10px] text-muted-foreground/60 shrink-0">
                                  last used
                                </span>
                              )}
                          </button>
                        );
                      })
                    )}
                  </div>
                )}

                {/* Footer */}
                <div className="flex gap-2 p-5 pt-0">
                  {!llmState.isStarting && (
                    <Button
                      variant="outline"
                      size="sm"
                      className="flex-1"
                      onClick={() => {
                        pendingPolishRef.current = false;
                        setShowLlmModal(false);
                      }}
                    >
                      Cancel
                    </Button>
                  )}
                  {!llmState.isStarting &&
                    llmState.downloadedModels.length > 0 && (
                      <Button
                        size="sm"
                        className="flex-1 bg-amber-500 hover:bg-amber-600 text-white"
                        disabled={!modalSelectedModel}
                        onClick={() => {
                          if (!modalSelectedModel) return;
                          pendingPolishRef.current = true;
                          // Determine GPU layers from hardware result or fall back to 0
                          const hw = llmState.hardwareResult;
                          const gpuLayers = hw?.recommended_gpu_layers ?? 0;
                          llmActions
                            .startServer(modalSelectedModel, gpuLayers)
                            .catch((e) => {
                              pendingPolishRef.current = false;
                              const msg =
                                e instanceof Error ? e.message : String(e);
                              setPolishError(msg);
                              setShowLlmModal(false);
                            });
                        }}
                      >
                        <Play className="h-3.5 w-3.5 mr-1.5" />
                        Start &amp; Polish
                      </Button>
                    )}
                  {llmState.isStarting && (
                    <p className="flex-1 text-center text-xs text-muted-foreground py-1">
                      Polish will run automatically once ready…
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Empty state when no session selected and not recording */}
          {!viewingSession && !state.isRecording && (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <Mic className="h-12 w-12 text-muted-foreground/20 mb-4" />
              <h3 className="text-sm font-medium text-muted-foreground">
                No recording selected
              </h3>
              <p className="text-xs text-muted-foreground/60 mt-1">
                Click "New Recording" or select one from the sidebar
              </p>
            </div>
          )}
        </div>
      </div>

      {/* ── Manage Presets modal ─────────────────────────────────────────── */}
      {showManagePresets && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="relative mx-4 w-full max-w-lg rounded-xl border bg-card shadow-2xl flex flex-col max-h-[80vh]">
            {/* Header */}
            <div className="flex items-center justify-between px-5 py-4 border-b shrink-0">
              <div>
                <h3 className="font-semibold text-sm">AI Polish Presets</h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Choose a default and create custom styles for AI Polish.
                </p>
              </div>
              <button
                className="rounded-sm p-0.5 text-muted-foreground hover:text-foreground"
                onClick={() => {
                  setShowManagePresets(false);
                  setEditingPreset(null);
                }}
                aria-label="Close"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {editingPreset === null ? (
              /* ── List view ── */
              <>
                <div className="flex-1 overflow-y-auto p-4 space-y-2">
                  {polishPresets.presets.map((preset) => (
                    <div
                      key={preset.id}
                      className={cn(
                        "flex items-start gap-3 rounded-lg border px-3 py-2.5",
                        preset.id === polishPresets.defaultPresetId
                          ? "border-amber-500/40 bg-amber-500/5"
                          : "border-transparent bg-muted/40",
                      )}
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-medium truncate">
                            {preset.name}
                          </span>
                          {preset.isBuiltIn && (
                            <span className="text-[9px] bg-muted px-1.5 py-0.5 rounded text-muted-foreground">
                              built-in
                            </span>
                          )}
                          {preset.id === polishPresets.defaultPresetId && (
                            <Star className="h-3 w-3 text-amber-500 shrink-0" />
                          )}
                        </div>
                        <p className="text-[10px] text-muted-foreground mt-0.5 line-clamp-2 leading-relaxed">
                          {preset.systemPrompt.slice(0, 120)}…
                        </p>
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        {preset.id !== polishPresets.defaultPresetId && (
                          <button
                            className="h-6 px-2 text-[10px] rounded border border-border/60 hover:bg-accent transition-colors text-muted-foreground"
                            onClick={() => polishPresets.setDefault(preset.id)}
                            title="Set as default"
                          >
                            Set default
                          </button>
                        )}
                        {!preset.isBuiltIn && (
                          <>
                            <button
                              className="h-6 w-6 flex items-center justify-center rounded hover:bg-accent transition-colors text-muted-foreground"
                              onClick={() => {
                                setEditingPreset(preset);
                                setPresetDraftName(preset.name);
                                // Strip the auto-appended JSON instruction so the user
                                // only sees and edits the meaningful part of the prompt.
                                setPresetDraftPrompt(
                                  preset.systemPrompt
                                    .replace("\n" + POLISH_JSON_INSTRUCTION, "")
                                    .trim(),
                                );
                              }}
                              title="Edit"
                            >
                              <PencilIcon className="h-3 w-3" />
                            </button>
                            <button
                              className="h-6 w-6 flex items-center justify-center rounded hover:bg-accent transition-colors text-red-500/70 hover:text-red-500"
                              onClick={() => polishPresets.remove(preset.id)}
                              title="Delete"
                            >
                              <TrashIcon className="h-3 w-3" />
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
                <div className="px-4 pb-4 pt-2 border-t shrink-0">
                  <Button
                    size="sm"
                    className="w-full"
                    onClick={() => {
                      setEditingPreset({
                        id: "",
                        name: "",
                        systemPrompt: "",
                        isBuiltIn: false,
                        updatedAt: "",
                      });
                      setPresetDraftName("");
                      setPresetDraftPrompt("");
                    }}
                  >
                    <Plus className="h-3.5 w-3.5 mr-1.5" />
                    New Custom Preset
                  </Button>
                </div>
              </>
            ) : (
              /* ── Editor view ── */
              <div className="flex flex-col flex-1 min-h-0">
                <div className="flex items-center gap-2 px-5 py-3 border-b shrink-0">
                  <button
                    className="text-xs text-muted-foreground hover:text-foreground"
                    onClick={() => setEditingPreset(null)}
                  >
                    ← Back
                  </button>
                  <span className="text-xs text-muted-foreground">/</span>
                  <span className="text-xs font-medium">
                    {editingPreset.id ? "Edit Preset" : "New Preset"}
                  </span>
                </div>
                <div className="flex-1 overflow-y-auto p-5 space-y-4">
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium">Preset Name</label>
                    <input
                      type="text"
                      value={presetDraftName}
                      onChange={(e) => setPresetDraftName(e.target.value)}
                      placeholder="e.g. Technical Notes"
                      className="w-full rounded-md border bg-background px-3 py-1.5 text-sm outline-none focus:ring-1 focus:ring-ring"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium">System Prompt</label>
                    <p className="text-[10px] text-muted-foreground leading-relaxed">
                      Write the instructions the model will follow. The JSON
                      output format is automatically appended — just describe
                      how to clean and format the text.
                    </p>
                    <textarea
                      value={presetDraftPrompt}
                      onChange={(e) => setPresetDraftPrompt(e.target.value)}
                      placeholder="You are a transcription editor. Clean up the transcript and…"
                      rows={8}
                      className="w-full rounded-md border bg-background px-3 py-2 text-xs font-mono outline-none focus:ring-1 focus:ring-ring resize-none leading-relaxed"
                    />
                    <div className="rounded-md bg-muted/50 px-3 py-2 text-[10px] text-muted-foreground leading-relaxed">
                      <span className="font-medium text-foreground">
                        Auto-appended:
                      </span>{" "}
                      {POLISH_JSON_INSTRUCTION}
                    </div>
                  </div>
                </div>
                <div className="flex gap-2 px-5 py-4 border-t shrink-0">
                  <Button
                    variant="outline"
                    size="sm"
                    className="flex-1"
                    onClick={() => setEditingPreset(null)}
                  >
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    className="flex-1"
                    disabled={
                      !presetDraftName.trim() || !presetDraftPrompt.trim()
                    }
                    onClick={() => {
                      const existingId = editingPreset?.id || null;
                      const rawPrompt = presetDraftPrompt.trim();
                      const systemPrompt = rawPrompt.includes(
                        POLISH_JSON_INSTRUCTION,
                      )
                        ? rawPrompt
                        : rawPrompt + "\n" + POLISH_JSON_INSTRUCTION;
                      const saved = polishPresets.save(
                        existingId
                          ? {
                              id: existingId,
                              name: presetDraftName.trim(),
                              systemPrompt,
                            }
                          : { name: presetDraftName.trim(), systemPrompt },
                      );
                      if (!existingId) polishPresets.setDefault(saved.id);
                      setEditingPreset(null);
                    }}
                  >
                    Save Preset
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Models Tab ─────────────────────────────────────────────────────────────

function ModelsTab({
  state,
  actions,
}: {
  state: TranscriptionState;
  actions: TranscriptionActions;
}) {
  const desktopAvailable = isTauri();
  const hw = state.hardwareResult;
  const downloaded = state.setupStatus?.downloaded_models ?? [];
  const { isDownloading, downloadingFilename, downloadQueue } = state;

  // Fire hardware detection at most once per mount — guarding with a ref
  // prevents a self-sustaining invoke loop while hw is still null.
  const didAutoDetect = useRef(false);
  useEffect(() => {
    if (
      desktopAvailable &&
      !hw &&
      !state.isDetecting &&
      !didAutoDetect.current
    ) {
      didAutoDetect.current = true;
      void actions.detectHardware();
    }
  }, [desktopAvailable, hw, state.isDetecting, actions]);

  // Live mirrors for the download-completion poll loop below — a closure
  // captured at click time would otherwise read stale pre-download values.
  const isDownloadingRef = useRef(isDownloading);
  const downloadQueueRef = useRef(downloadQueue);
  useEffect(() => {
    isDownloadingRef.current = isDownloading;
    downloadQueueRef.current = downloadQueue;
  }, [isDownloading, downloadQueue]);

  const handleDownloadAndActivate = async (model: ModelInfo) => {
    try {
      const exists = await actions.checkModelExists(model.filename);
      if (!exists) {
        // Use queueDownload so multiple clicks work correctly
        actions.queueDownload(model.filename);
        // Wait for download to complete then activate
        const waitAndActivate = async () => {
          const nowExists = await actions.checkModelExists(model.filename);
          if (nowExists) {
            await actions.initTranscription(model.filename);
          } else if (
            isDownloadingRef.current ||
            downloadQueueRef.current.length > 0
          ) {
            setTimeout(waitAndActivate, 500);
          }
        };
        setTimeout(waitAndActivate, 1000);
      } else {
        await actions.initTranscription(model.filename);
      }
    } catch {
      // Error displayed in terminal + state.error
    }
  };

  const handleDownloadAll = () => {
    const allM = hw?.all_models ?? [];
    const toDownload = allM
      .filter((m) => !downloaded.includes(m.filename))
      .map((m) => m.filename);
    actions.downloadAll(toDownload);
  };

  const allModels = hw?.all_models ?? [];

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div className="rounded-xl border bg-card p-6 space-y-4">
        <h3 className="font-semibold">Available Models</h3>
        <p className="text-sm text-muted-foreground">
          All models use the same API — switching models only changes accuracy
          and speed.
        </p>

        {/* Header row with Download All */}
        <div className="flex items-center justify-between">
          <span className="text-sm text-muted-foreground">
            {downloaded.length}/{allModels.length} downloaded
          </span>
          <div className="flex items-center gap-2">
            {downloadQueue.length > 0 && (
              <span className="text-xs bg-primary/15 text-primary rounded-full px-2 py-0.5 tabular-nums">
                {downloadQueue.length} queued
              </span>
            )}
            {allModels.some((m) => !downloaded.includes(m.filename)) && (
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs gap-1"
                onClick={handleDownloadAll}
              >
                <Download className="h-3 w-3" />
                Download All
              </Button>
            )}
          </div>
        </div>

        <div className="space-y-3">
          {allModels.map((model) => {
            const isDownloaded = downloaded.includes(model.filename);
            const isActive = state.activeModel === model.filename;
            const isDownloadingThis =
              isDownloading && downloadingFilename === model.filename;
            const isQueued =
              !isDownloadingThis &&
              downloadQueue.some((e) => e.filename === model.filename);
            const isRecommended = hw?.recommended_filename === model.filename;

            return (
              <div
                key={model.filename}
                className={cn(
                  "rounded-lg border p-4 space-y-3 transition-colors",
                  isActive && "border-primary/50 bg-primary/5",
                )}
              >
                <div className="flex items-start justify-between">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">
                        {tierLabel(model.tier)}
                      </span>
                      {isRecommended && (
                        <span className="text-xs bg-primary/10 text-primary px-2 py-0.5 rounded-full">
                          Recommended
                        </span>
                      )}
                      {isActive && (
                        <span className="text-xs bg-emerald-500/10 text-emerald-500 px-2 py-0.5 rounded-full">
                          Active
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-muted-foreground">
                      {model.description}
                    </p>
                  </div>
                </div>

                <div className="grid grid-cols-4 gap-2 text-xs">
                  <Stat
                    label="Download"
                    value={`${model.download_size_mb} MB`}
                  />
                  <Stat
                    label="RAM Usage"
                    value={`${model.ram_required_mb} MB`}
                  />
                  <Stat label="Speed" value={model.relative_speed} />
                  <Stat label="Accuracy" value={model.accuracy} />
                </div>

                <div className="flex items-center gap-2">
                  {isActive ? (
                    <span className="text-xs text-emerald-500 flex items-center gap-1">
                      <CheckCircle2 className="h-3 w-3" />
                      Currently active
                    </span>
                  ) : isDownloadingThis ? (
                    <Button size="sm" disabled>
                      <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                      {state.downloadProgress
                        ? `${Math.round(state.downloadProgress.percent)}%`
                        : "Downloading..."}
                    </Button>
                  ) : isQueued ? (
                    <span className="text-xs text-muted-foreground flex items-center gap-1">
                      <Loader2 className="h-3 w-3 animate-spin" />
                      Queued
                    </span>
                  ) : isDownloaded ? (
                    <div className="flex items-center gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => handleDownloadAndActivate(model)}
                      >
                        <ChevronRight className="mr-1 h-3 w-3" />
                        Activate
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => actions.deleteModel(model.filename)}
                        className="text-muted-foreground hover:text-red-500"
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>
                  ) : (
                    <Button
                      size="sm"
                      onClick={() => handleDownloadAndActivate(model)}
                    >
                      <Download className="mr-1 h-3 w-3" />
                      Download & Activate
                    </Button>
                  )}
                </div>

                {isDownloadingThis && (
                  <DownloadProgress
                    progress={state.downloadProgress}
                    isDownloading={state.isDownloading}
                  />
                )}
              </div>
            );
          })}
        </div>

        {allModels.length === 0 && (
          <div className="flex items-center justify-center py-8">
            <Button
              onClick={actions.detectHardware}
              disabled={state.isDetecting || !desktopAvailable}
              title={
                desktopAvailable
                  ? undefined
                  : "Hardware detection is available in the desktop app"
              }
            >
              {state.isDetecting ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="mr-2 h-4 w-4" />
              )}
              Detect Hardware to See Models
            </Button>
          </div>
        )}
      </div>

      <div className="rounded-xl border bg-card p-6 space-y-3">
        <h3 className="font-semibold text-sm">Technical Details</h3>
        <div className="space-y-2 text-xs text-muted-foreground">
          <p>
            Models are GGML format files from the whisper.cpp project.
            Downloaded from Hugging Face and stored locally.
          </p>
          <p>
            <code className="bg-muted px-1 py-0.5 rounded">.en</code> suffix
            means English-only (optimized).
          </p>
          <p>
            Switching models reinitializes the transcription context. The API is
            identical across all models.
          </p>
        </div>
      </div>
    </div>
  );
}

// ── Devices Tab ────────────────────────────────────────────────────────────

type MicTestPhase = "idle" | "testing" | "recorded" | "playing";

function DevicesTab({
  state,
  actions,
}: {
  state: TranscriptionState;
  actions: TranscriptionActions;
}) {
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Mic test state (uses Web Audio API directly — independent of Whisper pipeline)
  const [micTestPhase, setMicTestPhase] = useState<MicTestPhase>("idle");
  const [_micTestDevice, setMicTestDevice] = useState<string | null>(null);
  const [testLevelBars, setTestLevelBars] = useState<number[]>(
    Array(40).fill(0),
  );
  const [testPeakDb, setTestPeakDb] = useState<number>(-Infinity);
  const [testAvgDb, setTestAvgDb] = useState<number>(-Infinity);
  const [testRecordedBlob, setTestRecordedBlob] = useState<Blob | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [testCountdown, setTestCountdown] = useState(0);
  const [historyBars, setHistoryBars] = useState<number[]>(Array(80).fill(0));

  const analyserRef = useRef<AnalyserNode | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const animFrameRef = useRef<number | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const playbackAudioRef = useRef<HTMLAudioElement | null>(null);
  const historyRef = useRef<number[]>(Array(80).fill(0));
  const countdownTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      await actions.listAudioDevices();
    } finally {
      setIsRefreshing(false);
    }
  };

  useEffect(() => {
    handleRefresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Clean up on unmount
  useEffect(() => {
    return () => {
      stopMicTest();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stopMicTest = useCallback(() => {
    if (animFrameRef.current !== null) {
      cancelAnimationFrame(animFrameRef.current);
      animFrameRef.current = null;
    }
    if (countdownTimerRef.current !== null) {
      clearInterval(countdownTimerRef.current);
      countdownTimerRef.current = null;
    }
    if (
      mediaRecorderRef.current &&
      mediaRecorderRef.current.state !== "inactive"
    ) {
      mediaRecorderRef.current.stop();
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((t) => t.stop());
      mediaStreamRef.current = null;
    }
    if (audioContextRef.current) {
      audioContextRef.current.close().catch((e) => console.warn("[voice] AudioContext close failed:", e));
      audioContextRef.current = null;
    }
    analyserRef.current = null;
    mediaRecorderRef.current = null;
  }, []);

  const startMicTest = useCallback(async (deviceName: string | null) => {
    setTestError(null);
    setTestRecordedBlob(null);
    setTestPeakDb(-Infinity);
    setTestAvgDb(-Infinity);
    setTestLevelBars(Array(40).fill(0));
    historyRef.current = Array(80).fill(0);
    setHistoryBars(Array(80).fill(0));
    chunksRef.current = [];

    try {
      // Attempt to find the browser media device ID matching the CPAL device name.
      // CPAL uses system device names; browser uses opaque IDs. We match by label.
      let audioConstraint: MediaTrackConstraints | boolean = true;
      if (deviceName) {
        try {
          const devices = await navigator.mediaDevices.enumerateDevices();
          const match = devices.find(
            (d) =>
              d.kind === "audioinput" &&
              d.label.includes(deviceName.split(" ").slice(0, 3).join(" ")),
          );
          if (match?.deviceId) {
            audioConstraint = { deviceId: { exact: match.deviceId } };
          }
        } catch {
          // Fall back to default
        }
      }
      const constraints: MediaStreamConstraints = { audio: audioConstraint };
      const stream = await navigator.mediaDevices.getUserMedia(constraints);
      mediaStreamRef.current = stream;

      const ctx = new AudioContext({ sampleRate: 48000 });
      audioContextRef.current = ctx;

      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.6;
      source.connect(analyser);
      analyserRef.current = analyser;

      // MediaRecorder for playback
      const recorder = new MediaRecorder(stream, {
        mimeType: "audio/webm;codecs=opus",
      });
      mediaRecorderRef.current = recorder;
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        setTestRecordedBlob(blob);
        setMicTestPhase("recorded");
      };
      recorder.start();

      setMicTestDevice(deviceName);
      setMicTestPhase("testing");
      setTestCountdown(5);

      // 5-second countdown then auto-stop recording (but keep meter running)
      let secs = 5;
      countdownTimerRef.current = setInterval(() => {
        secs -= 1;
        setTestCountdown(secs);
        if (secs <= 0) {
          clearInterval(countdownTimerRef.current!);
          countdownTimerRef.current = null;
          if (mediaRecorderRef.current?.state === "recording") {
            mediaRecorderRef.current.stop();
          }
          // Stop stream & context after recorder captures final data
          setTimeout(() => {
            if (mediaStreamRef.current) {
              mediaStreamRef.current.getTracks().forEach((t) => t.stop());
              mediaStreamRef.current = null;
            }
            if (audioContextRef.current) {
              audioContextRef.current.close().catch((e) => console.warn("[voice] AudioContext close failed:", e));
              audioContextRef.current = null;
            }
            if (animFrameRef.current !== null) {
              cancelAnimationFrame(animFrameRef.current);
              animFrameRef.current = null;
            }
          }, 200);
        }
      }, 1000);

      // Animate level meter
      const freqData = new Uint8Array(analyser.frequencyBinCount);
      let peakDb = -Infinity;

      const animate = () => {
        analyser.getByteFrequencyData(freqData);

        // Compute RMS from frequency magnitudes
        let sum = 0;
        for (const value of freqData) {
          const norm = value / 255;
          sum += norm * norm;
        }
        const rms = Math.sqrt(sum / freqData.length);
        const db = rms > 0 ? 20 * Math.log10(rms) : -Infinity;

        if (db > peakDb) {
          peakDb = db;
          setTestPeakDb(db);
        }
        setTestAvgDb(db);

        // 40-bar spectrum
        const binCount = freqData.length;
        const barCount = 40;
        const barsArr: number[] = Array(barCount).fill(0);
        for (let b = 0; b < barCount; b++) {
          const startBin = Math.floor((b / barCount) * binCount);
          const endBin = Math.floor(((b + 1) / barCount) * binCount);
          let max = 0;
          for (let k = startBin; k < endBin; k++) {
            const sample = freqData[k];
            if (sample !== undefined && sample > max) max = sample;
          }
          barsArr[b] = max / 255;
        }
        setTestLevelBars(barsArr);

        // Rolling history
        historyRef.current.shift();
        historyRef.current.push(rms);
        setHistoryBars([...historyRef.current]);

        animFrameRef.current = requestAnimationFrame(animate);
      };
      animFrameRef.current = requestAnimationFrame(animate);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setTestError(msg);
      setMicTestPhase("idle");
    }
  }, []);

  const stopMicTestManually = useCallback(() => {
    if (countdownTimerRef.current !== null) {
      clearInterval(countdownTimerRef.current);
      countdownTimerRef.current = null;
    }
    if (mediaRecorderRef.current?.state === "recording") {
      mediaRecorderRef.current.stop();
    }
    setTimeout(() => {
      stopMicTest();
    }, 200);
  }, [stopMicTest]);

  const playbackRecording = useCallback(() => {
    if (!testRecordedBlob) return;
    if (playbackAudioRef.current) {
      playbackAudioRef.current.pause();
      URL.revokeObjectURL(playbackAudioRef.current.src);
    }
    const url = URL.createObjectURL(testRecordedBlob);
    const audio = new Audio(url);
    playbackAudioRef.current = audio;
    setMicTestPhase("playing");
    audio.onended = () => {
      setMicTestPhase("recorded");
      URL.revokeObjectURL(url);
    };
    audio.onerror = () => {
      setMicTestPhase("recorded");
    };
    audio.play().catch(() => setMicTestPhase("recorded"));
  }, [testRecordedBlob]);

  const stopPlayback = useCallback(() => {
    if (playbackAudioRef.current) {
      playbackAudioRef.current.pause();
      playbackAudioRef.current = null;
    }
    setMicTestPhase("recorded");
  }, []);

  const resetTest = useCallback(() => {
    stopMicTest();
    if (playbackAudioRef.current) {
      playbackAudioRef.current.pause();
      playbackAudioRef.current = null;
    }
    setMicTestPhase("idle");
    setTestRecordedBlob(null);
    setTestError(null);
    setTestPeakDb(-Infinity);
    setTestAvgDb(-Infinity);
    setTestLevelBars(Array(40).fill(0));
    historyRef.current = Array(80).fill(0);
    setHistoryBars(Array(80).fill(0));
  }, [stopMicTest]);

  // The "active" device — if user explicitly selected one, use that; else the system default
  const activeDevice = state.selectedDevice
    ? (state.audioDevices.find((d) => d.name === state.selectedDevice) ?? null)
    : (state.audioDevices.find((d) => d.is_default) ?? null);

  const formatDb = (db: number) => {
    if (!isFinite(db)) return "—";
    return `${db.toFixed(1)} dB`;
  };

  const getSignalQuality = (db: number): { label: string; color: string } => {
    if (!isFinite(db))
      return { label: "No signal", color: "text-muted-foreground" };
    if (db > -10) return { label: "Very loud", color: "text-red-500" };
    if (db > -20) return { label: "Loud", color: "text-amber-500" };
    if (db > -35) return { label: "Good", color: "text-emerald-500" };
    if (db > -50) return { label: "Quiet", color: "text-yellow-500" };
    return { label: "Very quiet", color: "text-red-400" };
  };

  const quality = getSignalQuality(testAvgDb);

  return (
    <div className="h-full flex gap-0 overflow-hidden">
      {/* ── Left panel: device list ── */}
      <div className="w-80 shrink-0 border-r flex flex-col h-full">
        <div className="flex items-center justify-between px-4 py-3 border-b shrink-0">
          <div>
            <h3 className="font-semibold text-sm">Input Devices</h3>
            <p className="text-xs text-muted-foreground mt-0.5">
              {state.audioDevices.length} device
              {state.audioDevices.length !== 1 ? "s" : ""} detected
            </p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={handleRefresh}
            disabled={isRefreshing}
            className="h-8 w-8 p-0"
            title="Refresh device list"
          >
            {isRefreshing ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
          </Button>
        </div>

        {/* Auto / System Default option */}
        <div className="px-3 py-2 border-b shrink-0">
          <button
            onClick={() => actions.setSelectedDevice(null)}
            className={cn(
              "w-full flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors text-left",
              !state.selectedDevice
                ? "bg-primary text-primary-foreground"
                : "hover:bg-muted text-foreground",
            )}
          >
            <div
              className={cn(
                "flex h-8 w-8 items-center justify-center rounded-full shrink-0",
                !state.selectedDevice ? "bg-primary-foreground/20" : "bg-muted",
              )}
            >
              <Settings2 className="h-4 w-4" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium leading-tight">
                Auto (System Default)
              </p>
              <p
                className={cn(
                  "text-xs leading-tight mt-0.5",
                  !state.selectedDevice
                    ? "text-primary-foreground/70"
                    : "text-muted-foreground",
                )}
              >
                {state.audioDevices.find((d) => d.is_default)?.name ??
                  "OS picks automatically"}
              </p>
            </div>
            {!state.selectedDevice && (
              <CheckCircle2 className="h-4 w-4 shrink-0 text-primary-foreground" />
            )}
          </button>
        </div>

        {/* Device list */}
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {isRefreshing && state.audioDevices.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 gap-2">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground/40" />
              <p className="text-xs text-muted-foreground">Scanning…</p>
            </div>
          ) : state.audioDevices.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 gap-2">
              <Volume2 className="h-6 w-6 text-muted-foreground/30" />
              <p className="text-xs text-muted-foreground">
                No devices detected
              </p>
            </div>
          ) : (
            state.audioDevices.map((device, i) => {
              const isExplicitlySelected = state.selectedDevice === device.name;

              return (
                <button
                  key={i}
                  onClick={() => actions.setSelectedDevice(device.name)}
                  className={cn(
                    "w-full flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors text-left",
                    isExplicitlySelected
                      ? "bg-primary text-primary-foreground"
                      : "hover:bg-muted text-foreground",
                  )}
                >
                  <div
                    className={cn(
                      "flex h-8 w-8 items-center justify-center rounded-full shrink-0",
                      isExplicitlySelected
                        ? "bg-primary-foreground/20"
                        : "bg-muted",
                    )}
                  >
                    <Mic className="h-4 w-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-sm font-medium truncate leading-tight">
                        {device.name}
                      </span>
                      {device.is_default && (
                        <span
                          className={cn(
                            "text-[10px] px-1.5 py-0.5 rounded-full font-medium shrink-0",
                            isExplicitlySelected
                              ? "bg-primary-foreground/20 text-primary-foreground"
                              : "bg-muted-foreground/20 text-muted-foreground",
                          )}
                        >
                          default
                        </span>
                      )}
                    </div>
                    <p
                      className={cn(
                        "text-xs mt-0.5 truncate",
                        isExplicitlySelected
                          ? "text-primary-foreground/70"
                          : "text-muted-foreground",
                      )}
                    >
                      {device.channels[0] !== undefined
                        ? `${device.channels[0]}ch`
                        : ""}
                      {device.sample_rates[0] !== undefined
                        ? ` · ${(device.sample_rates[0] / 1000).toFixed(0)}kHz`
                        : ""}
                    </p>
                  </div>
                  {isExplicitlySelected && (
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-primary-foreground" />
                  )}
                </button>
              );
            })
          )}
        </div>

        {/* Footer note */}
        <div className="px-4 py-3 border-t shrink-0">
          <p className="text-[10px] text-muted-foreground leading-relaxed">
            Selection is persisted across sessions. Whisper resamples all input
            to 16kHz mono automatically.
          </p>
        </div>
      </div>

      {/* ── Right panel: device detail + mic test ── */}
      <div className="flex-1 flex flex-col h-full overflow-y-auto">
        {activeDevice ? (
          <div className="p-6 space-y-6">
            {/* Device header */}
            <div className="flex items-start justify-between gap-4">
              <div className="flex items-center gap-4">
                <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 text-primary shrink-0">
                  <Mic className="h-7 w-7" />
                </div>
                <div>
                  <h2 className="text-lg font-semibold leading-tight">
                    {activeDevice.name}
                  </h2>
                  <div className="flex items-center gap-2 mt-1">
                    {activeDevice.is_default && (
                      <span className="inline-flex items-center gap-1 text-xs bg-muted text-muted-foreground px-2 py-0.5 rounded-full">
                        System default
                      </span>
                    )}
                    {state.selectedDevice === activeDevice.name ? (
                      <span className="inline-flex items-center gap-1 text-xs bg-primary/10 text-primary px-2 py-0.5 rounded-full font-medium">
                        <CheckCircle2 className="h-3 w-3" />
                        Manually selected
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-xs bg-emerald-500/10 text-emerald-500 px-2 py-0.5 rounded-full font-medium">
                        <CheckCircle2 className="h-3 w-3" />
                        Auto (system default)
                      </span>
                    )}
                  </div>
                </div>
              </div>
              {state.selectedDevice && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => actions.setSelectedDevice(null)}
                  className="shrink-0"
                >
                  Reset to Auto
                </Button>
              )}
            </div>

            {/* Specs grid */}
            <div className="grid grid-cols-4 gap-3">
              <div className="rounded-xl border bg-card p-4">
                <div className="flex items-center gap-2 mb-1">
                  <Activity className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="text-xs text-muted-foreground">
                    Sample Rate
                  </span>
                </div>
                <p className="text-sm font-semibold">
                  {activeDevice.sample_rates.length > 0
                    ? activeDevice.sample_rates
                        .map((r) => `${(r / 1000).toFixed(0)}kHz`)
                        .join(", ")
                    : "—"}
                </p>
                <p className="text-[10px] text-muted-foreground mt-0.5">
                  Whisper uses 16kHz
                </p>
              </div>
              <div className="rounded-xl border bg-card p-4">
                <div className="flex items-center gap-2 mb-1">
                  <AudioLines className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="text-xs text-muted-foreground">
                    Channels
                  </span>
                </div>
                <p className="text-sm font-semibold">
                  {activeDevice.channels.length > 0
                    ? activeDevice.channels
                        .map((c) =>
                          c === 1 ? "Mono" : c === 2 ? "Stereo" : `${c}ch`,
                        )
                        .join(" / ")
                    : "—"}
                </p>
                <p className="text-[10px] text-muted-foreground mt-0.5">
                  Downmixed to mono
                </p>
              </div>
              <div className="rounded-xl border bg-card p-4">
                <div className="flex items-center gap-2 mb-1">
                  <Radio className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="text-xs text-muted-foreground">
                    Connection
                  </span>
                </div>
                <p className="text-sm font-semibold">
                  {activeDevice.name.toLowerCase().includes("usb")
                    ? "USB"
                    : activeDevice.name.toLowerCase().includes("bluetooth") ||
                        activeDevice.name.toLowerCase().includes("airpod") ||
                        activeDevice.name.toLowerCase().includes("headset")
                      ? "Bluetooth"
                      : activeDevice.name.toLowerCase().includes("built") ||
                          activeDevice.name.toLowerCase().includes("internal")
                        ? "Built-in"
                        : "System"}
                </p>
                <p className="text-[10px] text-muted-foreground mt-0.5">
                  Interface type
                </p>
              </div>
              <div className="rounded-xl border bg-card p-4">
                <div className="flex items-center gap-2 mb-1">
                  <Waves className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="text-xs text-muted-foreground">
                    Compatibility
                  </span>
                </div>
                <p className="text-sm font-semibold text-emerald-500">
                  Supported
                </p>
                <p className="text-[10px] text-muted-foreground mt-0.5">
                  16kHz resampled
                </p>
              </div>
            </div>

            {/* Microphone Test Section */}
            <div className="rounded-xl border bg-card overflow-hidden">
              <div className="flex items-center justify-between px-5 py-4 border-b">
                <div>
                  <h3 className="font-semibold flex items-center gap-2">
                    <CircleDot className="h-4 w-4 text-primary" />
                    Microphone Test
                  </h3>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    Record 5 seconds of audio and play it back to verify your
                    mic is working
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {micTestPhase === "idle" && (
                    <Button
                      onClick={() => startMicTest(state.selectedDevice)}
                      size="sm"
                      className="gap-1.5"
                    >
                      <CircleDot className="h-3.5 w-3.5" />
                      Start Test
                    </Button>
                  )}
                  {micTestPhase === "testing" && (
                    <Button
                      onClick={stopMicTestManually}
                      variant="destructive"
                      size="sm"
                      className="gap-1.5"
                    >
                      <Square className="h-3 w-3 fill-current" />
                      Stop ({testCountdown}s)
                    </Button>
                  )}
                  {(micTestPhase === "recorded" ||
                    micTestPhase === "playing") && (
                    <div className="flex gap-2">
                      <Button
                        onClick={resetTest}
                        variant="outline"
                        size="sm"
                        className="gap-1.5"
                      >
                        <RefreshCw className="h-3.5 w-3.5" />
                        Re-test
                      </Button>
                    </div>
                  )}
                </div>
              </div>

              <div className="p-5 space-y-4">
                {testError && (
                  <div className="flex items-start gap-3 rounded-lg border border-red-500/20 bg-red-500/5 p-3">
                    <AlertCircle className="h-4 w-4 text-red-500 shrink-0 mt-0.5" />
                    <div className="flex-1">
                      <p className="text-sm font-medium text-red-500">
                        Microphone access failed
                      </p>
                      <p className="text-xs text-red-400 mt-0.5">{testError}</p>
                    </div>
                  </div>
                )}

                {micTestPhase === "idle" && !testError && (
                  <div className="flex flex-col items-center justify-center py-8 gap-3 text-center">
                    <div className="flex h-16 w-16 items-center justify-center rounded-full bg-muted">
                      <Mic className="h-8 w-8 text-muted-foreground/50" />
                    </div>
                    <div>
                      <p className="text-sm font-medium text-muted-foreground">
                        Ready to test
                      </p>
                      <p className="text-xs text-muted-foreground/60 mt-0.5">
                        Click "Start Test" to record 5 seconds of audio from
                        this microphone
                      </p>
                    </div>
                  </div>
                )}

                {micTestPhase === "testing" && (
                  <div className="space-y-4">
                    {/* Big animated mic indicator */}
                    <div className="flex items-center gap-4">
                      <div
                        className="flex h-16 w-16 items-center justify-center rounded-full bg-red-500 text-white shrink-0 transition-all duration-75"
                        style={{
                          boxShadow: isFinite(testAvgDb)
                            ? `0 0 ${8 + Math.max(0, (testAvgDb + 60) * 0.8)}px ${4 + Math.max(0, (testAvgDb + 60) * 0.4)}px rgba(239,68,68,${Math.min(0.2 + Math.max(0, (testAvgDb + 60) / 100), 0.7)})`
                            : undefined,
                        }}
                      >
                        <Mic className="h-8 w-8" />
                      </div>
                      <div className="flex-1">
                        <div className="flex items-center justify-between mb-1">
                          <div className="flex items-center gap-2">
                            <div className="h-2 w-2 rounded-full bg-red-500 animate-pulse" />
                            <span className="text-sm font-semibold">
                              Recording…
                            </span>
                            <span className="text-sm text-muted-foreground">
                              Speak into your microphone
                            </span>
                          </div>
                          <span className="text-2xl font-bold tabular-nums text-red-500">
                            {testCountdown}s
                          </span>
                        </div>
                        {/* Signal quality badge */}
                        <div className="flex items-center gap-2 mt-2">
                          <span
                            className={cn("text-xs font-medium", quality.color)}
                          >
                            {quality.label}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {formatDb(testAvgDb)} avg · {formatDb(testPeakDb)}{" "}
                            peak
                          </span>
                        </div>
                        {/* dB bar */}
                        <div className="mt-2 h-3 w-full rounded-full bg-muted overflow-hidden">
                          <div
                            className={cn(
                              "h-full rounded-full transition-all duration-75",
                              !isFinite(testAvgDb)
                                ? "bg-muted-foreground/20"
                                : testAvgDb > -20
                                  ? "bg-red-500"
                                  : testAvgDb > -35
                                    ? "bg-emerald-500"
                                    : testAvgDb > -50
                                      ? "bg-yellow-500"
                                      : "bg-red-400",
                            )}
                            style={{
                              width: isFinite(testAvgDb)
                                ? `${Math.max(2, Math.min(100, (testAvgDb + 70) * 1.43))}%`
                                : "2%",
                            }}
                          />
                        </div>
                      </div>
                    </div>

                    {/* Spectrum analyzer */}
                    <div className="rounded-lg bg-muted/30 border p-3">
                      <p className="text-xs text-muted-foreground mb-2">
                        Frequency spectrum
                      </p>
                      <div className="flex items-end gap-0.5 h-16">
                        {testLevelBars.map((v, i) => (
                          <div
                            key={i}
                            className={cn(
                              "flex-1 rounded-t transition-all duration-75",
                              v > 0.7
                                ? "bg-red-500"
                                : v > 0.4
                                  ? "bg-amber-500"
                                  : v > 0.1
                                    ? "bg-emerald-500"
                                    : "bg-muted-foreground/20",
                            )}
                            style={{ height: `${Math.max(4, v * 100)}%` }}
                          />
                        ))}
                      </div>
                    </div>

                    {/* Rolling waveform history */}
                    <div className="rounded-lg bg-muted/30 border p-3">
                      <p className="text-xs text-muted-foreground mb-2">
                        Input level history
                      </p>
                      <div className="flex items-center gap-0.5 h-8">
                        {historyBars.map((v, i) => (
                          <div
                            key={i}
                            className={cn(
                              "flex-1 rounded transition-all duration-150",
                              v > 0.05
                                ? "bg-primary"
                                : "bg-muted-foreground/15",
                            )}
                            style={{
                              height: `${Math.max(10, v * 200)}%`,
                              maxHeight: "100%",
                            }}
                          />
                        ))}
                      </div>
                    </div>

                    {/* Guidance tips */}
                    <div className="grid grid-cols-3 gap-2 text-xs">
                      <div
                        className={cn(
                          "rounded-lg border px-3 py-2 text-center transition-colors",
                          isFinite(testAvgDb) && testAvgDb > -50
                            ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-500"
                            : "border-border text-muted-foreground",
                        )}
                      >
                        Signal detected
                      </div>
                      <div
                        className={cn(
                          "rounded-lg border px-3 py-2 text-center transition-colors",
                          isFinite(testAvgDb) &&
                            testAvgDb > -35 &&
                            testAvgDb < -10
                            ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-500"
                            : "border-border text-muted-foreground",
                        )}
                      >
                        Good level
                      </div>
                      <div
                        className={cn(
                          "rounded-lg border px-3 py-2 text-center transition-colors",
                          isFinite(testPeakDb) && testPeakDb < -6
                            ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-500"
                            : isFinite(testPeakDb) && testPeakDb >= -6
                              ? "border-red-500/30 bg-red-500/5 text-red-500"
                              : "border-border text-muted-foreground",
                        )}
                      >
                        No clipping
                      </div>
                    </div>
                  </div>
                )}

                {(micTestPhase === "recorded" ||
                  micTestPhase === "playing") && (
                  <div className="space-y-4">
                    {/* Test result summary */}
                    <div className="flex items-center gap-4 rounded-lg border bg-muted/20 px-4 py-3">
                      <div
                        className={cn(
                          "flex h-10 w-10 items-center justify-center rounded-full shrink-0",
                          isFinite(testPeakDb) && testPeakDb > -50
                            ? "bg-emerald-500/10 text-emerald-500"
                            : "bg-amber-500/10 text-amber-500",
                        )}
                      >
                        <CheckCircle2 className="h-5 w-5" />
                      </div>
                      <div className="flex-1">
                        <p className="text-sm font-semibold">
                          Recording complete
                        </p>
                        <p className="text-xs text-muted-foreground mt-0.5">
                          Peak: {formatDb(testPeakDb)}
                          {isFinite(testPeakDb) && testPeakDb > -50
                            ? " · Microphone is working"
                            : " · Very low signal — check mic placement or permissions"}
                        </p>
                      </div>
                    </div>

                    {/* Final spectrum snapshot */}
                    <div className="rounded-lg bg-muted/30 border p-3">
                      <p className="text-xs text-muted-foreground mb-2">
                        Last recorded spectrum
                      </p>
                      <div className="flex items-end gap-0.5 h-16">
                        {testLevelBars.map((v, i) => (
                          <div
                            key={i}
                            className={cn(
                              "flex-1 rounded-t",
                              v > 0.7
                                ? "bg-red-400"
                                : v > 0.4
                                  ? "bg-amber-400"
                                  : v > 0.1
                                    ? "bg-primary/60"
                                    : "bg-muted-foreground/15",
                            )}
                            style={{ height: `${Math.max(4, v * 100)}%` }}
                          />
                        ))}
                      </div>
                    </div>

                    {/* Playback control */}
                    <div className="rounded-xl border bg-card p-4 space-y-3">
                      <div className="flex items-center justify-between">
                        <div>
                          <p className="text-sm font-semibold">
                            Listen to Your Recording
                          </p>
                          <p className="text-xs text-muted-foreground mt-0.5">
                            Play back what was captured to verify audio quality
                          </p>
                        </div>
                        {micTestPhase === "playing" ? (
                          <Button
                            onClick={stopPlayback}
                            variant="destructive"
                            size="sm"
                            className="gap-1.5"
                          >
                            <Square className="h-3 w-3 fill-current" />
                            Stop
                          </Button>
                        ) : (
                          <Button
                            onClick={playbackRecording}
                            size="sm"
                            className="gap-1.5"
                          >
                            <Play className="h-3.5 w-3.5 fill-current" />
                            Play Back
                          </Button>
                        )}
                      </div>

                      {micTestPhase === "playing" && (
                        <div className="flex items-center gap-2">
                          <div className="flex gap-0.5">
                            {Array.from({ length: 20 }).map((_, i) => (
                              <div
                                key={i}
                                className="w-1 rounded-full bg-primary animate-pulse"
                                style={{
                                  height: `${8 + Math.random() * 16}px`,
                                  animationDelay: `${i * 50}ms`,
                                  animationDuration: `${600 + Math.random() * 400}ms`,
                                }}
                              />
                            ))}
                          </div>
                          <span className="text-xs text-primary font-medium">
                            Playing…
                          </span>
                        </div>
                      )}
                    </div>

                    {/* Level analysis */}
                    <div className="grid grid-cols-3 gap-3">
                      <div className="rounded-xl border bg-card p-4 text-center">
                        <p className="text-xs text-muted-foreground mb-1">
                          Peak Level
                        </p>
                        <p
                          className={cn(
                            "text-xl font-bold tabular-nums",
                            isFinite(testPeakDb) && testPeakDb >= -6
                              ? "text-red-500"
                              : isFinite(testPeakDb) && testPeakDb > -20
                                ? "text-amber-500"
                                : isFinite(testPeakDb) && testPeakDb > -50
                                  ? "text-emerald-500"
                                  : "text-muted-foreground",
                          )}
                        >
                          {formatDb(testPeakDb)}
                        </p>
                        <p className="text-[10px] text-muted-foreground mt-1">
                          {isFinite(testPeakDb) && testPeakDb >= -6
                            ? "⚠ Clipping risk"
                            : isFinite(testPeakDb) && testPeakDb > -20
                              ? "Loud"
                              : isFinite(testPeakDb) && testPeakDb > -35
                                ? "Good range"
                                : isFinite(testPeakDb) && testPeakDb > -50
                                  ? "Quiet"
                                  : "No signal"}
                        </p>
                      </div>
                      <div className="rounded-xl border bg-card p-4 text-center">
                        <p className="text-xs text-muted-foreground mb-1">
                          Signal Quality
                        </p>
                        <p className={cn("text-xl font-bold", quality.color)}>
                          {isFinite(testPeakDb) ? quality.label : "—"}
                        </p>
                        <p className="text-[10px] text-muted-foreground mt-1">
                          {isFinite(testPeakDb)
                            ? "Transcription ready"
                            : "No audio captured"}
                        </p>
                      </div>
                      <div className="rounded-xl border bg-card p-4 text-center">
                        <p className="text-xs text-muted-foreground mb-1">
                          Recommendation
                        </p>
                        <p className="text-sm font-semibold text-foreground">
                          {!isFinite(testPeakDb)
                            ? "Check mic"
                            : testPeakDb >= -6
                              ? "Lower gain"
                              : testPeakDb > -50
                                ? "Ready to use"
                                : "Move closer"}
                        </p>
                        <p className="text-[10px] text-muted-foreground mt-1">
                          {!isFinite(testPeakDb)
                            ? "No signal detected"
                            : testPeakDb >= -6
                              ? "Reduce microphone volume"
                              : testPeakDb > -50
                                ? "Optimal for Whisper"
                                : "Signal too weak"}
                        </p>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Tips & best practices */}
            <div className="rounded-xl border bg-card p-5 space-y-4">
              <h3 className="font-semibold text-sm flex items-center gap-2">
                <Info className="h-4 w-4 text-muted-foreground" />
                Best Practices for Whisper
              </h3>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-3">
                  <div className="flex items-start gap-2.5">
                    <div className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-500 text-xs font-bold shrink-0 mt-0.5">
                      ✓
                    </div>
                    <div>
                      <p className="text-xs font-medium">
                        Dedicated microphone
                      </p>
                      <p className="text-xs text-muted-foreground">
                        External USB mics have better noise isolation than
                        built-in laptop mics
                      </p>
                    </div>
                  </div>
                  <div className="flex items-start gap-2.5">
                    <div className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-500 text-xs font-bold shrink-0 mt-0.5">
                      ✓
                    </div>
                    <div>
                      <p className="text-xs font-medium">
                        Speak clearly at –20 to –35 dB
                      </p>
                      <p className="text-xs text-muted-foreground">
                        Whisper performs best with clean, well-leveled audio in
                        this range
                      </p>
                    </div>
                  </div>
                  <div className="flex items-start gap-2.5">
                    <div className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-500 text-xs font-bold shrink-0 mt-0.5">
                      ✓
                    </div>
                    <div>
                      <p className="text-xs font-medium">Quiet environment</p>
                      <p className="text-xs text-muted-foreground">
                        Background noise degrades accuracy significantly;
                        Whisper's VAD helps but silence is better
                      </p>
                    </div>
                  </div>
                </div>
                <div className="space-y-3">
                  <div className="flex items-start gap-2.5">
                    <div className="flex h-5 w-5 items-center justify-center rounded-full bg-red-500/10 text-red-500 text-xs font-bold shrink-0 mt-0.5">
                      ✗
                    </div>
                    <div>
                      <p className="text-xs font-medium">
                        Avoid clipping (above –6 dB)
                      </p>
                      <p className="text-xs text-muted-foreground">
                        Clipped audio causes transcription artifacts and
                        distortion
                      </p>
                    </div>
                  </div>
                  <div className="flex items-start gap-2.5">
                    <div className="flex h-5 w-5 items-center justify-center rounded-full bg-red-500/10 text-red-500 text-xs font-bold shrink-0 mt-0.5">
                      ✗
                    </div>
                    <div>
                      <p className="text-xs font-medium">
                        Avoid Bluetooth for long sessions
                      </p>
                      <p className="text-xs text-muted-foreground">
                        Bluetooth mics switch to lower-quality SCO mode during
                        recording, increasing latency
                      </p>
                    </div>
                  </div>
                  <div className="flex items-start gap-2.5">
                    <div className="flex h-5 w-5 items-center justify-center rounded-full bg-amber-500/10 text-amber-500 text-xs font-bold shrink-0 mt-0.5">
                      !
                    </div>
                    <div>
                      <p className="text-xs font-medium">
                        Auto resampling is transparent
                      </p>
                      <p className="text-xs text-muted-foreground">
                        All input is resampled to 16kHz mono — no manual
                        configuration needed
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center p-12 gap-4 text-center">
            {isRefreshing ? (
              <>
                <Loader2 className="h-10 w-10 animate-spin text-muted-foreground/30" />
                <p className="text-sm text-muted-foreground">
                  Scanning for audio devices…
                </p>
              </>
            ) : state.audioDevices.length === 0 ? (
              <>
                <Volume2 className="h-12 w-12 text-muted-foreground/20" />
                <h3 className="text-base font-semibold">
                  No Audio Devices Found
                </h3>
                <p className="text-sm text-muted-foreground max-w-sm">
                  No microphones were detected. Make sure your device has a
                  microphone connected and that macOS has granted audio access.
                </p>
                <Button
                  onClick={handleRefresh}
                  variant="outline"
                  size="sm"
                  className="gap-1.5"
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  Scan Again
                </Button>
              </>
            ) : (
              <>
                <Mic className="h-12 w-12 text-muted-foreground/20" />
                <h3 className="text-base font-semibold">Select a Device</h3>
                <p className="text-sm text-muted-foreground">
                  Choose a microphone from the list to see details and run a
                  test
                </p>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Helpers ────────────────────────────────────────────────────────────────

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-center">
      <p className="text-muted-foreground">{label}</p>
      <p className="font-medium text-foreground">{value}</p>
    </div>
  );
}

function tierLabel(tier: WhisperModelTier): string {
  switch (tier) {
    case "Low":
      return "Tiny (Fast)";
    case "Default":
      return "Base (Balanced)";
    case "High":
      return "Small (Accurate)";
  }
}

function formatRam(mb: number): string {
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${mb} MB`;
}

function formatDuration(secs: number): string {
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

function formatSessionTitle(date: Date): string {
  const now = new Date();
  const isToday = date.toDateString() === now.toDateString();
  const isThisYear = date.getFullYear() === now.getFullYear();

  if (isToday) {
    return date.toLocaleTimeString(undefined, {
      hour: "numeric",
      minute: "2-digit",
    });
  }
  if (isThisYear) {
    return date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  }
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatRelativeTime(date: Date): string {
  const now = Date.now();
  const diff = Math.floor((now - date.getTime()) / 1000);
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function getGpuLabel(hw: HardwareDetectionResult): string {
  if (hw.hardware.is_apple_silicon) return "Apple Silicon (Metal)";
  if (hw.hardware.supports_cuda && hw.hardware.gpu_vram_mb) {
    return `NVIDIA (${formatRam(hw.hardware.gpu_vram_mb)} VRAM)`;
  }
  if (hw.hardware.supports_metal) return "Metal (macOS)";
  return "CPU only";
}

function getAccelLabel(hw: HardwareDetectionResult): string {
  if (hw.hardware.is_apple_silicon) return "Metal (GPU)";
  if (hw.hardware.supports_cuda) return "CUDA (GPU)";
  if (hw.hardware.supports_metal) return "Metal (GPU)";
  return "SIMD (CPU)";
}
