import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { HashRouter, Routes, Route } from "react-router-dom";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppLayout, type PageEntry } from "@/components/layout/AppLayout";
import { Dashboard } from "@/pages/Dashboard";
import { Documents } from "@/pages/Documents";
import { Files } from "@/pages/Files";
import { Scraping } from "@/pages/Scraping";
import { Tools } from "@/pages/Tools";
import { Activity } from "@/pages/Activity";
import { Ports } from "@/pages/Ports";
import { Settings } from "@/pages/Settings";
import { Devices } from "@/pages/Devices";
import { Chat } from "@/pages/Chat";
import { CloudChat } from "@/pages/CloudChat";
import { Login } from "@/pages/Login";
import { OAuthPending } from "@/pages/OAuthPending";
import { AuthCallback } from "@/pages/AuthCallback";
import { AiMatrx } from "@/pages/AiMatrx";
import { BrowserLab } from "@/pages/BrowserLab";
import { Voice } from "@/pages/Voice";
import { LocalModels } from "@/pages/LocalModels";
import { MediaGeneration } from "@/pages/MediaGeneration";
import { TextToSpeech } from "@/pages/TextToSpeech";
import { TranscriptOverlay } from "@/components/TranscriptOverlay";
import { SystemPrompts } from "@/pages/SystemPrompts";
import { Configurations } from "@/pages/Configurations";
import { TauriFetchBrowser } from "@/pages/TauriFetchBrowser";
import { BridgeTest } from "@/pages/BridgeTest";
import { useEngine } from "@/hooks/use-engine";
import { useAuth } from "@/hooks/use-auth";
import { useTheme } from "@/hooks/use-theme";
import { useNotifications } from "@/hooks/use-notifications";
import { useAutoUpdate } from "@/hooks/use-auto-update";
import {
  TranscriptionProvider,
  useTranscriptionApp,
} from "@/contexts/TranscriptionContext";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { EngineMonitor } from "@/components/EngineRecoveryModal";
import { UpdateDialog } from "@/components/UpdateDialog";
import { UpdateBanner } from "@/components/UpdateBanner";
import { RestartingOverlay } from "@/components/RestartingOverlay";
import { NotificationToastContainer } from "@/components/notifications/NotificationCenter";
import { StartupScreen } from "@/components/StartupScreen";
import { FirstRunScreen } from "@/components/FirstRunScreen";
import {
  DevTerminalPanel,
  DevTerminalProvider,
} from "@/components/DevTerminalPanel";
import { CompactRecorderWindow } from "@/components/CompactRecorderWindow";
import { MenuEventBridge } from "@/components/MenuEventBridge";
import { PermissionsProvider } from "@/contexts/PermissionsContext";
import { AccessHealthProvider } from "@/contexts/AccessHealthContext";
import { AudioDevicesProvider } from "@/contexts/AudioDevicesContext";
import { LlmProvider } from "@/contexts/LlmContext";
import { WakeWordProvider } from "@/contexts/WakeWordContext";
import {
  TranscriptionSessionsProvider,
  useSessionsContext,
} from "@/contexts/TranscriptionSessionsContext";
import { DownloadManagerProvider } from "@/contexts/DownloadManagerContext";
import { MediaGenProvider } from "@/contexts/MediaGenContext";
import { PromptMatrixProvider } from "@/contexts/PromptMatrixContext";
import { MediaLibraryProvider } from "@/contexts/MediaLibraryContext";
import { ListLibraryProvider } from "@/contexts/ListLibraryContext";
import { SavedPromptsProvider } from "@/contexts/SavedPromptsContext";
import { VariationBatchesProvider } from "@/contexts/VariationBatchesContext";
import { MediaVaultProvider } from "@/contexts/MediaVaultContext";
import { MediaActionsProvider } from "@/components/media/MediaActionsProvider";
import { TtsProvider } from "@/contexts/TtsContext";
import { DownloadManagerModal } from "@/components/downloads/DownloadManagerModal";
import { engine } from "@/lib/api";
import { isTauri } from "@/lib/sidecar";
import {
  initUnifiedLog,
  initTauriLogStream,
  initConsoleCapture,
  stopEngineStreams,
  stopTauriStream,
} from "@/hooks/use-unified-log";
import supabase from "@/lib/supabase";
import {
  ActionNeededNavigationBridge,
  ActionNeededSources,
  actionNeededStore,
} from "@/features/action-needed";

const SETUP_DISMISSED_KEY = "matrx-setup-dismissed";

// ---------------------------------------------------------------------------
// HashRouter + OAuth callback bridge
if (
  typeof window !== "undefined" &&
  window.location.pathname === "/auth/callback" &&
  !window.location.hash.includes("/auth/callback")
) {
  const search = window.location.search;
  window.location.replace(`/#/auth/callback${search}`);
}

/**
 * Outer App — renders the full provider stack so that AppInner (and every
 * other consumer) reads the real context instances instead of the no-op
 * context defaults. AppInner must stay inside these providers.
 */
export default function App() {
  return (
    <ErrorBoundary>
      <DevTerminalProvider>
        <DownloadManagerProvider>
          {/* Media: ONE library store, ONE vault store, ONE action set + the
              app-wide lightbox / info dialog / context menu. Every image in the
              app — icon, thumbnail, tile, result — routes through these, so
              deleting or vaulting an item updates every surface at once. */}
          <MediaGenProvider>
            {/* ONE prompt matrix, app-wide: a half-built sweep (ten hand-typed
                options, a tuned strategy) must survive a tab switch. Inside
                MediaGenProvider because it reads the model/LoRA catalog to
                validate what each variable is allowed to sweep. */}
            <PromptMatrixProvider>
              <SavedPromptsProvider>
                <VariationBatchesProvider>
                  <ListLibraryProvider>
                    <MediaLibraryProvider>
                      <MediaVaultProvider>
                        <MediaActionsProvider>
                          <TtsProvider>
                            <LlmProvider>
                              <WakeWordProvider>
                                <TranscriptionSessionsProvider>
                                  <PermissionsProvider>
                                    <AudioDevicesProvider>
                                      <TranscriptionProvider>
                                        <AppInner />
                                      </TranscriptionProvider>
                                    </AudioDevicesProvider>
                                  </PermissionsProvider>
                                </TranscriptionSessionsProvider>
                              </WakeWordProvider>
                            </LlmProvider>
                          </TtsProvider>
                        </MediaActionsProvider>
                      </MediaVaultProvider>
                    </MediaLibraryProvider>
                  </ListLibraryProvider>
                </VariationBatchesProvider>
              </SavedPromptsProvider>
            </PromptMatrixProvider>
          </MediaGenProvider>
        </DownloadManagerProvider>
      </DevTerminalProvider>
    </ErrorBoundary>
  );
}

function AppInner() {
  const auth = useAuth();
  const themeCtx = useTheme();
  const {
    status,
    url,
    tools,
    systemInfo,
    browserStatus,
    engineVersion,
    error: engineError,
    refresh,
    restartEngine,
  } = useEngine();

  useEffect(() => {
    if (status !== "connected") actionNeededStore.reset();
  }, [status]);

  const notif = useNotifications();
  const [updateState, updateActions] = useAutoUpdate();

  // ---------------------------------------------------------------------------
  // Compact recorder mode
  // ---------------------------------------------------------------------------
  const [isCompact, setIsCompact] = useState(false);
  const { state: transcriptionState, actions: transcriptionActions } =
    useTranscriptionApp();
  // Use the shared sessions context — a raw useTranscriptionSessions() call
  // here would create a second store instance and clobber the provider
  // instance's global flush callback.
  const { actions: bgSessActions } = useSessionsContext();

  const [compactTranscript, setCompactTranscript] = useState("");
  useEffect(() => {
    if (isCompact && transcriptionState.segments.length > 0) {
      setCompactTranscript(
        transcriptionState.segments
          .map((s) => s.text)
          .join(" ")
          .trim(),
      );
    }
  }, [transcriptionState.segments, isCompact]);

  // ---------------------------------------------------------------------------
  // Background recording mode — uses real transcription sessions
  // ---------------------------------------------------------------------------
  const [bgRecording, setBgRecording] = useState(false);
  const bgSessionIdRef = useRef<string | null>(null);
  const bgSegmentCountRef = useRef(0);
  const bgStartTimeRef = useRef(0);

  useEffect(() => {
    if (!bgRecording || !bgSessionIdRef.current) return;
    if (transcriptionState.segments.length > bgSegmentCountRef.current) {
      const newSegs = transcriptionState.segments.slice(
        bgSegmentCountRef.current,
      );
      bgSegmentCountRef.current = transcriptionState.segments.length;
      bgSessActions.append(bgSessionIdRef.current, newSegs);
    }
  }, [bgRecording, transcriptionState.segments, bgSessActions]);

  const addNotification = notif.addNotification;
  useEffect(() => {
    if (
      !bgRecording &&
      bgSessionIdRef.current &&
      !transcriptionState.isRecording &&
      !transcriptionState.isProcessingTail
    ) {
      const durationSecs = Math.round(
        (Date.now() - bgStartTimeRef.current) / 1000,
      );
      const mins = Math.floor(durationSecs / 60);
      const secs = durationSecs % 60;
      bgSessActions.finalize(bgSessionIdRef.current, durationSecs);
      addNotification(
        "Recording Saved",
        `${mins}m ${secs}s transcription saved. Open Speech to Text → Transcripts to review.`,
        "success",
      );
      bgSessionIdRef.current = null;
      bgSegmentCountRef.current = 0;
    }
  }, [
    bgRecording,
    transcriptionState.isRecording,
    transcriptionState.isProcessingTail,
    addNotification,
    bgSessActions,
  ]);

  const toggleBackgroundRecording = useCallback(async () => {
    if (bgRecording || transcriptionState.isRecording) {
      setBgRecording(false);
      await transcriptionActions.stopRecording();
    } else {
      const session = bgSessActions.startNew(
        transcriptionState.activeModel,
        transcriptionState.selectedDevice,
      );
      bgSessionIdRef.current = session.id;
      bgSegmentCountRef.current = transcriptionState.segments.length;
      bgStartTimeRef.current = Date.now();
      setBgRecording(true);
      await transcriptionActions.startRecording();
    }
  }, [
    bgRecording,
    transcriptionState.isRecording,
    transcriptionState.segments.length,
    transcriptionState.activeModel,
    transcriptionState.selectedDevice,
    transcriptionActions,
    bgSessActions,
  ]);

  const invokeSetCompactMode = useCallback(async (enabled: boolean) => {
    if (!isTauri()) return;
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("set_compact_mode", { enabled });
    } catch (e) {
      console.warn("[compact-mode] set_compact_mode failed:", e);
    }
  }, []);

  const enterCompactMode = useCallback(async () => {
    setCompactTranscript("");
    setIsCompact(true);
    await invokeSetCompactMode(true);
    if (
      !transcriptionState.isRecording &&
      !transcriptionState.isProcessingTail
    ) {
      setTimeout(() => {
        transcriptionActions.startRecording().catch(() => {});
      }, 150);
    }
  }, [
    invokeSetCompactMode,
    transcriptionState.isRecording,
    transcriptionState.isProcessingTail,
    transcriptionActions,
  ]);

  const exitCompactMode = useCallback(async () => {
    if (transcriptionState.isRecording) {
      await transcriptionActions.stopRecording();
    }
    setIsCompact(false);
    await invokeSetCompactMode(false);
  }, [
    invokeSetCompactMode,
    transcriptionState.isRecording,
    transcriptionActions,
  ]);

  // ---------------------------------------------------------------------------
  // Unified log streams
  // ---------------------------------------------------------------------------
  useEffect(() => {
    const restoreConsole = initConsoleCapture();
    initTauriLogStream();
    return () => {
      restoreConsole();
      stopTauriStream();
    };
  }, []);

  useEffect(() => {
    if (status === "connected" && url) {
      const getToken = async () => {
        const {
          data: { session },
        } = await supabase.auth.getSession();
        return session?.access_token ?? null;
      };
      initUnifiedLog(url, getToken);
    } else if (status !== "connected") {
      stopEngineStreams();
    }
    return () => {
      stopEngineStreams();
    };
  }, [status, url]);

  const toasts = notif.toasts;

  // First-run detection
  const [setupComplete, setSetupComplete] = useState<boolean | null>(null);
  const setupCheckedRef = useRef(false);

  useEffect(() => {
    if (status === "connected" && url && !setupCheckedRef.current) {
      setupCheckedRef.current = true;
      engine
        .getSetupStatus()
        .then((s) => {
          setSetupComplete(s?.setup_complete ?? true);
        })
        .catch(() => {
          setSetupComplete(true);
        });
    }
  }, [status, url]);

  const isFirstRun =
    auth.isAuthenticated &&
    status === "connected" &&
    url !== null &&
    setupComplete === false &&
    !localStorage.getItem(SETUP_DISMISSED_KEY);

  const handleFirstRunComplete = useCallback(() => {
    localStorage.setItem(SETUP_DISMISSED_KEY, "1");
    setSetupComplete(true);
  }, []);

  // Engine Monitor
  const [monitorOpen, setMonitorOpen] = useState(false);
  const prevStatusRef = useRef(status);

  useEffect(() => {
    if (
      auth.isAuthenticated &&
      status === "error" &&
      prevStatusRef.current !== "error"
    ) {
      setMonitorOpen(true);
    }
    prevStatusRef.current = status;
  }, [status, auth.isAuthenticated]);

  const handleOpenMonitor = useCallback(() => setMonitorOpen(true), []);

  // Persistent pages
  const appPages: PageEntry[] = useMemo(
    () => [
      {
        path: "/",
        element: (
          <Dashboard
            engineStatus={status}
            engineUrl={url}
            tools={tools}
            systemInfo={systemInfo}
            browserStatus={browserStatus}
            onRefresh={refresh}
            user={auth.user}
            onSignOut={auth.signOut}
          />
        ),
      },
      {
        path: "/chat",
        element: <Chat engineStatus={status} engineUrl={url} tools={tools} />,
      },
      {
        path: "/cloud-chat",
        element: <CloudChat engineStatus={status} engineUrl={url} />,
      },
      {
        path: "/notes",
        element: (
          <Documents engineStatus={status} userId={auth.user?.id ?? null} />
        ),
      },
      { path: "/files", element: <Files engineStatus={status} /> },
      {
        path: "/scraping",
        element: <Scraping engineStatus={status} engineUrl={url} />,
      },
      {
        path: "/tools",
        element: <Tools engineStatus={status} engineUrl={url} tools={tools} />,
      },
      {
        path: "/activity",
        element: <Activity engineStatus={status} engineUrl={url} />,
      },
      {
        path: "/ports",
        element: <Ports engineStatus={status} engineUrl={url} />,
      },
      {
        path: "/devices",
        element: <Devices engineStatus={status} engineUrl={url} />,
      },
      { path: "/voice", element: <Voice /> },
      { path: "/tts", element: <TextToSpeech /> },
      { path: "/local-models", element: <LocalModels /> },
      { path: "/media-generation", element: <MediaGeneration /> },
      { path: "/system-prompts", element: <SystemPrompts /> },
      { path: "/aimatrx", element: <AiMatrx /> },
      { path: "/browser", element: <BrowserLab /> },
      { path: "/browser/tauri", element: <TauriFetchBrowser /> },
      { path: "/configurations", element: <Configurations /> },
      {
        path: "/bridge-test",
        element: (
          <BridgeTest engineStatus={status} engineUrl={url} user={auth.user} />
        ),
      },
      {
        path: "/settings",
        element: (
          <Settings
            engineStatus={status}
            engineUrl={url}
            engineVersion={engineVersion}
            onRefresh={refresh}
            auth={auth}
            theme={themeCtx.theme}
            setTheme={themeCtx.setTheme}
            updateState={updateState}
            updateActions={updateActions}
          />
        ),
      },
    ],
    [
      status,
      url,
      tools,
      systemInfo,
      browserStatus,
      refresh,
      auth,
      engineVersion,
      themeCtx.theme,
      themeCtx.setTheme,
      updateState,
      updateActions,
    ],
  );

  const isCallbackRoute =
    typeof window !== "undefined" &&
    window.location.hash.startsWith("#/auth/callback");

  if (auth.oauthPending) {
    return (
      <ErrorBoundary>
        <OAuthPending
          onCancel={auth.cancelOAuth}
          completeOAuthExchange={auth.completeOAuthExchange}
        />
      </ErrorBoundary>
    );
  }

  const isEngineStarting =
    auth.isAuthenticated &&
    (status === "discovering" || status === "starting") &&
    !isCallbackRoute;

  if ((auth.loading && !isCallbackRoute) || isEngineStarting) {
    return <StartupScreen authLoading={auth.loading} engineStatus={status} />;
  }

  if (isFirstRun) {
    return (
      <ErrorBoundary>
        <FirstRunScreen engineUrl={url!} onComplete={handleFirstRunComplete} />
      </ErrorBoundary>
    );
  }

  if (isCompact) {
    return (
      <ErrorBoundary>
        <CompactRecorderWindow
          isRecording={transcriptionState.isRecording}
          isProcessingTail={transcriptionState.isProcessingTail}
          isCalibrating={transcriptionState.isCalibrating}
          liveRms={transcriptionState.liveRms}
          transcript={compactTranscript}
          onStartRecording={transcriptionActions.startRecording}
          onStopRecording={transcriptionActions.stopRecording}
          onExpand={exitCompactMode}
        />
      </ErrorBoundary>
    );
  }

  return (
    <TooltipProvider delayDuration={150}>
      {/* ONE access-health store app-wide (banner, Documents, Recovery,
          Settings) — single poll owner, generation-fenced updates. Lives here
          (not in the provider stack) because it needs engineStatus. */}
      <AccessHealthProvider engineStatus={status}>
        <HashRouter>
          <ActionNeededSources />
          <ActionNeededNavigationBridge />
          <Routes>
            <Route path="/overlay" element={<TranscriptOverlay />} />
            <Route path="/auth/callback" element={<AuthCallback />} />

            {!auth.isAuthenticated ? (
              <Route path="*" element={<Login auth={auth} />} />
            ) : (
              <>
                <Route
                  path="/*"
                  element={
                    <AppLayout
                      engineStatus={status}
                      engineUrl={url}
                      engineVersion={engineVersion}
                      onRefresh={refresh}
                      onRestartEngine={restartEngine}
                      user={auth.user}
                      onSignOut={auth.signOut}
                      isRecording={transcriptionState.isRecording}
                      onRecord={enterCompactMode}
                      onBackgroundRecord={toggleBackgroundRecording}
                      isBackgroundRecording={bgRecording}
                      transcriptionState={transcriptionState}
                      transcriptionActions={transcriptionActions}
                      tools={tools}
                      updateState={updateState}
                      updateActions={updateActions}
                      notifications={notif.notifications}
                      unreadCount={notif.unreadCount}
                      onMarkRead={notif.markRead}
                      onMarkAllRead={notif.markAllRead}
                      onDismissNotification={notif.dismiss}
                      onClearAllNotifications={notif.clearAll}
                      onOpenMonitor={handleOpenMonitor}
                      pages={appPages}
                    />
                  }
                />
              </>
            )}
          </Routes>
          {/* These live INSIDE HashRouter (though outside Routes): several of
            them — DownloadManagerModal's action dialog especially — call
            useNavigate(). Hoisting them out crashes the app on boot. */}
          <MenuEventBridge />
          <NotificationToastContainer
            toasts={toasts}
            onHide={notif.hideToast}
            onDismiss={notif.dismiss}
          />
          <EngineMonitor
            open={monitorOpen}
            onOpenChange={setMonitorOpen}
            engineStatus={status}
            engineError={engineError}
            onRestartEngine={restartEngine}
            onRefresh={refresh}
          />
          <RestartingOverlay visible={updateState.restarting} />
          <UpdateBanner state={updateState} actions={updateActions} />
          <UpdateDialog state={updateState} actions={updateActions} />
          <DownloadManagerModal />
          <DevTerminalPanel />
        </HashRouter>
      </AccessHealthProvider>
    </TooltipProvider>
  );
}
