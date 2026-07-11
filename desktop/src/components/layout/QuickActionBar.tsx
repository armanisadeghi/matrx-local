import { useState, useCallback, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { DownloadIndicator } from "@/components/downloads/DownloadIndicator";
import {
  Mic,
  Ear,
  Cpu,
  Activity,
  Shield,
  Globe,
  Cloud,
  CloudOff,
  MessageSquare,
  MessageSquareDashed,
  StickyNote,
  Globe2,
  AudioLines,
  ArrowUpCircle,
  User,
  RotateCcw,
  RefreshCw,
  LogOut,
  Loader2,
  CircleDot,
} from "lucide-react";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { NotificationCenter } from "@/components/notifications/NotificationCenter";
import { QuickChatModal } from "@/components/quick-actions/QuickChatModal";
import { QuickLocalChatModal } from "@/components/quick-actions/QuickLocalChatModal";
import { QuickNoteModal } from "@/components/quick-actions/QuickNoteModal";
import { QuickScrapeModal } from "@/components/quick-actions/QuickScrapeModal";
import { QuickTranscriptModal } from "@/components/quick-actions/QuickTranscriptModal";
import { useLlmApp } from "@/contexts/LlmContext";
import { useWakeWordContext } from "@/contexts/WakeWordContext";
import { useServiceStatus } from "@/hooks/use-service-status";
import type { CloudSyncStatus } from "@/hooks/use-service-status";
import { isTauri, restartApp } from "@/lib/sidecar";
import { triggerPageRefresh } from "@/lib/page-refresh";
import { cn } from "@/lib/utils";
import type { EngineStatus } from "@/hooks/use-engine";
import type {
  TranscriptionState,
  TranscriptionActions,
} from "@/hooks/use-transcription";
import type {
  AutoUpdateState,
  AutoUpdateActions,
} from "@/hooks/use-auto-update";
import type { AppNotification } from "@/hooks/use-notifications";
import type { User as SupabaseUser } from "@supabase/supabase-js";

export interface QuickActionBarProps {
  isRecording: boolean;
  onRecord: () => void;
  onBackgroundRecord: () => void;
  isBackgroundRecording: boolean;
  engineStatus: EngineStatus;
  engineUrl: string | null;
  tools: string[];
  onOpenMonitor: () => void;
  transcriptionState: TranscriptionState;
  transcriptionActions: TranscriptionActions;
  user: SupabaseUser | null;
  userId: string | null;
  onSignOut: () => void;
  updateState: AutoUpdateState;
  updateActions: AutoUpdateActions;
  notifications: AppNotification[];
  unreadCount: number;
  onMarkRead: (id: string) => void;
  onMarkAllRead: () => void;
  onDismissNotification: (id: string) => void;
  onClearAllNotifications: () => void;
}

type DotColor = "green" | "amber" | "red" | "blue" | "gray";

function StatusDot({ color }: { color: DotColor }) {
  const cls: Record<DotColor, string> = {
    green: "bg-emerald-500",
    amber: "bg-amber-500 animate-pulse",
    red: "bg-red-500",
    blue: "bg-sky-500",
    gray: "bg-zinc-500",
  };
  return (
    <span
      className={`absolute -right-0.5 top-0 block h-[7px] w-[7px] rounded-full ring-1 ring-background ${cls[color]}`}
    />
  );
}

function BarButton({
  tooltip,
  onClick,
  active,
  dotColor,
  disabled,
  pulseActive,
  children,
}: {
  tooltip: string;
  onClick?: () => void;
  active?: boolean;
  dotColor?: DotColor;
  disabled?: boolean;
  pulseActive?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Tooltip delayDuration={150}>
      <TooltipTrigger asChild>
        <button
          onClick={onClick}
          disabled={disabled}
          className={cn(
            "relative flex h-7 w-7 items-center justify-center rounded-md transition-colors",
            "text-muted-foreground hover:text-foreground hover:bg-muted/50",
            active && "text-emerald-500 hover:text-emerald-400",
            pulseActive && "text-emerald-500 animate-pulse",
            disabled && "opacity-40 pointer-events-none",
          )}
        >
          {children}
          {dotColor && <StatusDot color={dotColor} />}
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-xs">
        {tooltip}
      </TooltipContent>
    </Tooltip>
  );
}

function GroupSeparator() {
  return <div className="mx-2 h-5 w-px bg-border/60" />;
}

function cloudSyncDotColor(status: CloudSyncStatus): DotColor {
  switch (status) {
    case "synced":
      return "green";
    case "syncing":
      return "amber";
    case "error":
    case "orphan":
      return "red";
    case "not-configured":
      return "blue";
    case "unknown":
      return "gray";
  }
}

function cloudSyncTooltip(
  status: CloudSyncStatus,
  lastError: string | null,
): string {
  switch (status) {
    case "synced":
      return "Cloud sync: Up to date";
    case "syncing":
      return "Cloud sync: Syncing now...";
    case "error":
      return `Cloud sync: Error${lastError ? ` — ${lastError}` : ""}`;
    case "orphan":
      return "Cloud sync: This device is not registered. Click to re-sync.";
    case "not-configured":
      return "Cloud sync: Not set up. Sign in to enable.";
    case "unknown":
      return "Cloud sync: Checking status...";
  }
}

export function QuickActionBar(props: QuickActionBarProps) {
  const {
    isRecording,
    onRecord,
    onBackgroundRecord,
    isBackgroundRecording,
    engineStatus,
    engineUrl,
    tools,
    onOpenMonitor,
    transcriptionState,
    transcriptionActions,
    user,
    userId,
    onSignOut,
    updateState,
    updateActions,
    notifications,
    unreadCount,
    onMarkRead,
    onMarkAllRead,
    onDismissNotification,
    onClearAllNotifications,
  } = props;

  const navigate = useNavigate();
  const [llmState, llmActions] = useLlmApp();
  const { state: wwState, actions: wwActions } = useWakeWordContext();
  const [serviceStatus, serviceActions] = useServiceStatus(engineStatus);

  const [chatOpen, setChatOpen] = useState(false);
  const [localChatOpen, setLocalChatOpen] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const [scrapeOpen, setScrapeOpen] = useState(false);
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);
  const [restarting, setRestarting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    if (!userMenuOpen) return;
    const handle = (e: MouseEvent) => {
      if (
        userMenuRef.current &&
        !userMenuRef.current.contains(e.target as Node)
      ) {
        setUserMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, [userMenuOpen]);

  const llmRunning = llmState.serverStatus?.running ?? false;
  const llmStarting = llmState.isStarting;
  const llmHasModels = llmState.downloadedModels.length > 0;

  const wwListening =
    wwState.uiMode === "listening" ||
    wwState.uiMode === "muted" ||
    wwState.uiMode === "active";

  const handleLlmToggle = useCallback(async () => {
    if (llmRunning) {
      await llmActions.stopServer();
    } else if (llmHasModels) {
      const model = llmState.downloadedModels[0];
      // Detected GPU layers, not hardcoded CPU-only (see QuickLocalChatModal).
      if (model) {
        await llmActions.startServer(
          model.filename,
          llmState.hardwareResult?.recommended_gpu_layers ?? 0,
        );
      }
    }
  }, [llmRunning, llmHasModels, llmState.downloadedModels, llmActions]);

  const handleWwToggle = useCallback(async () => {
    if (wwListening) {
      await wwActions.stopListening();
    } else {
      await wwActions.setup();
    }
  }, [wwListening, wwActions]);

  const handleRefreshPageData = useCallback(() => {
    if (refreshing) return;
    setRefreshing(true);
    triggerPageRefresh("*");
    setTimeout(() => setRefreshing(false), 700);
  }, [refreshing]);

  const handleRestart = useCallback(async () => {
    if (restarting) return;
    setRestarting(true);
    try {
      await restartApp();
    } finally {
      // Reset in case restartApp resolves without actually restarting
      setRestarting(false);
    }
  }, [restarting]);

  const engineDotColor: DotColor =
    engineStatus === "connected"
      ? "green"
      : engineStatus === "discovering" || engineStatus === "starting"
        ? "amber"
        : engineStatus === "error"
          ? "red"
          : "gray";

  const proxyRunning = serviceStatus.proxy?.running ?? false;
  const tunnelRunning = serviceStatus.tunnel?.running ?? false;

  const hasUpdate =
    updateState.status?.status === "available" ||
    updateState.status?.status === "installed";

  const cloudStatus = serviceStatus.cloudSyncStatus;
  const cloudDot = cloudSyncDotColor(cloudStatus);
  const cloudTip = cloudSyncTooltip(
    cloudStatus,
    serviceStatus.cloudDebug?.last_error ?? null,
  );

  const proxyTip = proxyRunning
    ? `Proxy: Active on port ${serviceStatus.proxy?.port ?? "—"}`
    : "Proxy: Not running";

  const tunnelTip = tunnelRunning
    ? `Remote access: Active — ${serviceStatus.tunnel?.url ?? "connecting..."}`
    : "Remote access: Not connected";

  const engineTip =
    engineStatus === "connected"
      ? `System engine: Connected (${engineUrl ?? ""})`
      : engineStatus === "discovering" || engineStatus === "starting"
        ? "System engine: Starting up..."
        : engineStatus === "error"
          ? "System engine: Error — click for details"
          : "System engine: Not available";

  const llmTip = llmStarting
    ? "Confidential chat: Starting up..."
    : llmRunning
      ? `Confidential chat: Active — ${llmState.serverStatus?.model_name ?? "model loaded"}`
      : llmHasModels
        ? "Confidential chat: Ready — click to start"
        : "Confidential chat: No model downloaded yet";

  return (
    <>
      <div className="no-select glass flex h-11 shrink-0 items-center gap-0.5 border-b px-3">
        {/* ═══ GROUP 1: Voice & Audio ═══ */}
        <BarButton
          tooltip={
            isRecording
              ? "Recording (compact mode)... click to enter"
              : "Compact recording — shrinks the app"
          }
          onClick={onRecord}
          active={isRecording}
          dotColor={isRecording ? "green" : "blue"}
        >
          <Mic className="h-4 w-4" />
        </BarButton>

        <BarButton
          tooltip="Quick transcription — records in a popup"
          onClick={() => setTranscriptOpen(true)}
        >
          <AudioLines className="h-4 w-4" />
        </BarButton>

        <BarButton
          tooltip={
            isBackgroundRecording
              ? "Background recording... click to stop and save"
              : "Background recording — click and just talk"
          }
          onClick={onBackgroundRecord}
          pulseActive={isBackgroundRecording}
          dotColor={isBackgroundRecording ? "green" : "blue"}
        >
          <CircleDot className="h-4 w-4" />
        </BarButton>

        <BarButton
          tooltip={
            wwListening
              ? "Wake word: Listening — click to stop"
              : "Wake word: Click to start listening"
          }
          onClick={handleWwToggle}
          active={wwListening}
          dotColor={wwListening ? "green" : "blue"}
        >
          <Ear className="h-4 w-4" />
        </BarButton>

        <GroupSeparator />

        {/* ═══ GROUP 2: AI ═══ */}
        <BarButton
          tooltip={llmTip}
          onClick={handleLlmToggle}
          active={llmRunning}
          dotColor={
            llmStarting
              ? "amber"
              : llmRunning
                ? "green"
                : llmHasModels
                  ? "blue"
                  : "gray"
          }
          disabled={llmStarting || (!llmRunning && !llmHasModels)}
        >
          {llmStarting ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Cpu className="h-4 w-4" />
          )}
        </BarButton>

        <GroupSeparator />

        {/* ═══ GROUP 3: System Status ═══ */}
        <BarButton
          tooltip={engineTip}
          onClick={onOpenMonitor}
          dotColor={engineDotColor}
        >
          <Activity className="h-4 w-4" />
        </BarButton>

        <BarButton
          tooltip={proxyTip}
          dotColor={proxyRunning ? "green" : "blue"}
        >
          <Shield className="h-4 w-4" />
        </BarButton>

        <BarButton
          tooltip={tunnelTip}
          dotColor={tunnelRunning ? "green" : "blue"}
        >
          <Globe className="h-4 w-4" />
        </BarButton>

        <BarButton
          tooltip={cloudTip}
          onClick={serviceActions.triggerCloudSync}
          dotColor={cloudDot}
        >
          {serviceStatus.cloudSyncing ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : cloudStatus === "not-configured" ? (
            <CloudOff className="h-4 w-4" />
          ) : (
            <Cloud className="h-4 w-4" />
          )}
        </BarButton>

        <GroupSeparator />

        {/* ═══ GROUP 4: Quick Actions ═══ */}
        <BarButton tooltip="Quick chat" onClick={() => setChatOpen(true)}>
          <MessageSquare className="h-4 w-4" />
        </BarButton>

        <BarButton
          tooltip="Quick confidential chat (on-device)"
          onClick={() => setLocalChatOpen(true)}
        >
          <MessageSquareDashed className="h-4 w-4" />
        </BarButton>

        <BarButton tooltip="Quick note" onClick={() => setNoteOpen(true)}>
          <StickyNote className="h-4 w-4" />
        </BarButton>

        <BarButton tooltip="Quick scrape" onClick={() => setScrapeOpen(true)}>
          <Globe2 className="h-4 w-4" />
        </BarButton>

        {/* ── Download Indicator ── */}
        <DownloadIndicator />

        {/* ── Spacer ── */}
        <div className="flex-1" />

        {/* ═══ GROUP 5: System Actions ═══ */}
        <BarButton
          tooltip="Refresh page data — reloads data for the current view without restarting"
          onClick={handleRefreshPageData}
          disabled={refreshing}
        >
          <RefreshCw
            className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`}
          />
        </BarButton>

        {hasUpdate && (
          <BarButton
            tooltip={`New version available: ${updateState.status?.version ?? ""}. Click to update.`}
            onClick={updateActions.openDialog}
          >
            <ArrowUpCircle className="h-4 w-4 text-emerald-500" />
          </BarButton>
        )}

        <div ref={userMenuRef} className="relative">
          <BarButton
            tooltip={user ? (user.email ?? "Account") : "Not signed in"}
            onClick={() => setUserMenuOpen((v) => !v)}
          >
            <User className="h-4 w-4" />
          </BarButton>
          {userMenuOpen && (
            <div className="absolute right-0 top-full z-50 mt-1 w-48 rounded-lg border bg-popover p-1 shadow-lg">
              {user && (
                <p className="truncate px-2 py-1 text-xs text-muted-foreground">
                  {user.email}
                </p>
              )}
              <button
                onClick={() => {
                  setUserMenuOpen(false);
                  onSignOut();
                }}
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              >
                <LogOut className="h-3.5 w-3.5" />
                Sign out
              </button>
            </div>
          )}
        </div>

        <NotificationCenter
          notifications={notifications}
          unreadCount={unreadCount}
          onMarkRead={onMarkRead}
          onMarkAllRead={onMarkAllRead}
          onDismiss={onDismissNotification}
          onClearAll={onClearAllNotifications}
        />

        {isTauri() && (
          <BarButton
            tooltip={
              restarting
                ? "Restarting everything..."
                : "Restart entire application"
            }
            onClick={handleRestart}
            disabled={restarting}
          >
            {restarting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RotateCcw className="h-4 w-4" />
            )}
          </BarButton>
        )}
      </div>

      {/* ── Modals ── */}
      <QuickChatModal
        open={chatOpen}
        onOpenChange={setChatOpen}
        engineStatus={engineStatus}
        engineUrl={engineUrl}
        tools={tools}
      />
      <QuickLocalChatModal
        open={localChatOpen}
        onOpenChange={setLocalChatOpen}
        engineStatus={engineStatus}
        engineUrl={engineUrl}
        tools={tools}
      />
      <QuickNoteModal
        open={noteOpen}
        onOpenChange={setNoteOpen}
        engineStatus={engineStatus}
        userId={userId}
      />
      <QuickScrapeModal open={scrapeOpen} onOpenChange={setScrapeOpen} userId={userId} />
      <QuickTranscriptModal
        userId={userId}
        open={transcriptOpen}
        onOpenChange={setTranscriptOpen}
        transcriptionState={transcriptionState}
        transcriptionActions={transcriptionActions}
        onNavigateToVoice={() => navigate("/voice")}
      />
    </>
  );
}
