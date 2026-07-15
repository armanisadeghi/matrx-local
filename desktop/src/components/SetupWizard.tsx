/**
 * SetupWizard — First-run setup experience for Matrx Local.
 *
 * Shows installation status, runs automatic setup (browser engine, storage dirs),
 * and offers optional configuration (transcription, LLM models).
 *
 * Embedded in the Dashboard page. Shows automatically when setup is incomplete,
 * and can be re-opened via a "Setup" button when complete.
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { isTauri } from "@/lib/sidecar";
import type { UnlistenFn } from "@tauri-apps/api/event";
import {
  CheckCircle2,
  Circle,
  Loader2,
  AlertCircle,
  ChevronRight,
  Chrome,
  FolderOpen,
  Shield,
  Mic,
  Cpu,
  Sparkles,
  Settings2,
  Download,
  Play,
  RotateCcw,
  ChevronDown,
  ChevronUp,
  X,
  ExternalLink,
  BrainCircuit,
  AlertTriangle,
  ShieldCheck,
  Globe,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { engine } from "@/lib/api";
import type {
  SetupStatus,
  SetupComponentStatus,
  SetupProgressEvent,
  SetupCompleteEvent,
} from "@/lib/api";
import { PermissionsModal } from "@/components/PermissionsModal";
import { usePermissionsContext } from "@/contexts/PermissionsContext";
import type { EngineStatus } from "@/hooks/use-engine";
import type { VoiceSetupStatus } from "@/lib/transcription/types";
import type {
  LlmSetupStatus,
  LlmHardwareResult,
  LlmDownloadProgress,
} from "@/lib/llm/types";
import { emitClientLog } from "@/hooks/use-client-log";
import { overlayLlmCatalog } from "@/lib/llm/catalog";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SETUP_DISMISSED_KEY = "matrx-setup-dismissed";

const COMPONENT_ICONS: Record<string, React.ReactNode> = {
  core_packages: <Cpu className="h-5 w-5" />,
  browser_engine: <Chrome className="h-5 w-5" />,
  storage_dirs: <FolderOpen className="h-5 w-5" />,
  tts_model: <Sparkles className="h-5 w-5" />,
  cloudflared: <Globe className="h-5 w-5" />,
  permissions: <ShieldCheck className="h-5 w-5" />,
  transcription: <Mic className="h-5 w-5" />,
  local_llm: <BrainCircuit className="h-5 w-5" />,
};

// Components that must be "ready" for setup to be considered complete
const BLOCKING_IDS = new Set([
  "core_packages",
  "browser_engine",
  "storage_dirs",
  "tts_model",
  "cloudflared",
]);

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type WizardPhase =
  | "checking"
  | "ready"
  | "installing"
  | "complete"
  | "configure";

interface ComponentProgress {
  status: string;
  message: string;
  percent: number;
  deep_link?: string | null;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface SetupWizardProps {
  engineStatus: EngineStatus;
  onSetupComplete?: () => void;
}

export function SetupWizard({
  engineStatus,
  onSetupComplete,
}: SetupWizardProps) {
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
  const [phase, setPhase] = useState<WizardPhase>("checking");
  const [progress, setProgress] = useState<Record<string, ComponentProgress>>(
    {},
  );
  const [overallPercent, setOverallPercent] = useState(0);
  const [installError, setInstallError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);
  const [dismissed, setDismissed] = useState(() => {
    return localStorage.getItem(SETUP_DISMISSED_KEY) === "true";
  });
  const [statusError, setStatusError] = useState<string | null>(null);

  // Tauri-driven optional component states
  const [voiceStatus, setVoiceStatus] = useState<VoiceSetupStatus | null>(null);
  const [llmStatus, setLlmStatus] = useState<LlmSetupStatus | null>(null);
  const [llmHardware, setLlmHardware] = useState<LlmHardwareResult | null>(
    null,
  );
  const [_llmDownloadProgress, setLlmDownloadProgress] =
    useState<LlmDownloadProgress | null>(null);
  const [isDownloadingModel, setIsDownloadingModel] = useState(false);
  const [isDownloadingTranscription, setIsDownloadingTranscription] =
    useState(false);

  const [permissionsModalOpen, setPermissionsModalOpen] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const llmUnlistenRef = useRef<UnlistenFn | null>(null);
  const runInstallRef = useRef<(() => void) | null>(null);
  const autoInstallFiredRef = useRef(false);
  // checkStatus reads `dismissed` via a ref so that dismissing the wizard
  // doesn't change checkStatus's identity and re-trigger the status check
  // (which would set phase back to "checking" and re-expand the wizard).
  const dismissedRef = useRef(dismissed);
  useEffect(() => {
    dismissedRef.current = dismissed;
  }, [dismissed]);

  // Stable log helpers — emitClientLog is a module-level singleton, no deps needed
  const logLine = useCallback(
    (level: Parameters<typeof emitClientLog>[0], msg: string) =>
      emitClientLog(level, msg, "setup"),
    [],
  );
  const logData = useCallback((label: string, payload: unknown) => {
    try {
      emitClientLog(
        "data",
        `${label}: ${typeof payload === "string" ? payload : JSON.stringify(payload)}`,
        "setup",
      );
    } catch {
      emitClientLog("data", `${label}: [unserializable]`, "setup");
    }
  }, []);
  const { permissions: permissionStates } = usePermissionsContext();

  // ── Load Tauri optional status ─────────────────────────────────────────

  const loadTauriStatus = useCallback(async () => {
    if (!isTauri()) return;
    const { invoke } = await import("@tauri-apps/api/core");
    try {
      const v = await invoke<VoiceSetupStatus>("get_voice_setup_status");
      setVoiceStatus(v);
      logLine(
        "data",
        `Voice setup: model=${v.selected_model ?? "none"}, complete=${v.setup_complete}`,
      );
    } catch (e) {
      logLine("warn", `Could not load voice setup status: ${e}`);
    }
    try {
      const l = await invoke<LlmSetupStatus>("get_llm_setup_status");
      setLlmStatus(l);
      logLine(
        "data",
        `LLM setup: models=${l.downloaded_models.join(", ") || "none"}, server=${l.server_running}`,
      );
    } catch (e) {
      logLine("warn", `Could not load LLM setup status: ${e}`);
    }
    try {
      const hw = await overlayLlmCatalog(
        await invoke<LlmHardwareResult>("detect_llm_hardware"),
      );
      setLlmHardware(hw);
      logLine(
        "info",
        `Hardware: ${hw.hardware.is_apple_silicon ? "Apple Silicon" : `${hw.hardware.total_ram_mb}MB RAM`} — recommended model: ${hw.recommended_filename}`,
      );
    } catch (e) {
      logLine("warn", `Hardware detection failed: ${e}`);
    }
  }, [logLine]);

  // ── Load setup status ──────────────────────────────────────────────────

  const checkStatus = useCallback(async () => {
    if (engineStatus !== "connected") return;
    setPhase("checking");
    setStatusError(null);
    logLine("info", "Checking system setup status...");

    // Retry up to 5 times with exponential backoff. The engine may have just
    // finished booting and its internal services (permissions checker, etc.)
    // need a moment to settle — this is why "Re-Check" always worked but the
    // first automatic check failed with "Load failed".
    let status: SetupStatus | null = null;
    let lastError = "";
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        status = await engine.getSetupStatus();
        break;
      } catch (e) {
        lastError = e instanceof Error ? e.message : String(e);
        if (attempt < 4) {
          const delayMs = [500, 1000, 2000, 3000][attempt] ?? 3000;
          logLine(
            "warn",
            `Status check attempt ${attempt + 1} failed — retrying in ${delayMs}ms...`,
          );
          await new Promise((r) => setTimeout(r, delayMs));
        }
      }
    }

    if (!status) {
      setStatusError(lastError);
      setPhase("ready");
      logLine("error", `Failed to check status after 5 attempts: ${lastError}`);
      return;
    }

    setSetupStatus(status);
    logLine("info", `Platform: ${status.platform} ${status.architecture}`);
    if (status.gpu_available)
      logLine("info", `GPU detected: ${status.gpu_name}`);

    const initial: Record<string, ComponentProgress> = {};
    for (const comp of status.components) {
      initial[comp.id] = {
        status: comp.status,
        message: comp.detail || "",
        percent: comp.status === "ready" ? 100 : 0,
        deep_link: comp.deep_link,
      };
      const level =
        comp.status === "ready"
          ? "success"
          : comp.status === "error"
            ? "error"
            : comp.status === "warning"
              ? "warn"
              : "warn";
      logLine(
        level,
        `${comp.label}: ${comp.status}${comp.detail ? ` — ${comp.detail}` : ""}`,
      );
    }
    setProgress(initial);

    if (status.setup_complete) {
      setPhase("complete");
      logLine("success", "All core components are ready");
    } else {
      const notReady = status.components.filter(
        (c) => BLOCKING_IDS.has(c.id) && c.status !== "ready",
      );
      logLine(
        "warn",
        `${notReady.length} required component(s) need setup — starting automatically`,
      );

      // Auto-start installation on genuine first run only — i.e. the wizard has
      // never been dismissed before.  Existing users who previously completed
      // setup (dismissed=true) will see the wizard reopen with TTS/cloudflared
      // listed as "not ready" and a manual "Set Up Now" button, so they choose
      // when to download ~370 MB rather than having it start silently.
      if (!autoInstallFiredRef.current && !dismissedRef.current) {
        autoInstallFiredRef.current = true;
        setPhase("ready");
        // Small delay so the UI renders the component list before the
        // install progress starts streaming in.
        setTimeout(() => {
          runInstallRef.current?.();
        }, 600);
      } else {
        setPhase("ready");
      }
    }

    // Load Tauri optional status after engine status loads
    await loadTauriStatus();
  }, [engineStatus, logLine, loadTauriStatus]);

  useEffect(() => {
    checkStatus();
  }, [checkStatus]);

  // ── Run setup installation ─────────────────────────────────────────────

  const runInstall = useCallback(async () => {
    setPhase("installing");
    setInstallError(null);
    setOverallPercent(0);
    logLine("info", "Starting setup installation...");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await engine.runSetupInstall({
        mode: "first_run",
        signal: controller.signal,
        onRawLine: (line) => {
          if (
            line.trim() &&
            !line.startsWith("event:") &&
            !line.startsWith("data:")
          ) {
            logLine("data", line);
          }
        },
        onProgress: (data: SetupProgressEvent) => {
          setProgress((prev) => ({
            ...prev,
            [data.component]: {
              status: data.status,
              message: data.message,
              percent: data.percent,
              ...(data.deep_link !== undefined ? { deep_link: data.deep_link } : {}),
            },
          }));

          const level =
            data.status === "ready"
              ? "success"
              : data.status === "error"
                ? "error"
                : data.status === "warning"
                  ? "warn"
                  : "info";
          logLine(
            level,
            `[${data.component}] ${data.message} (${data.percent}%)`,
          );
          if (
            data.bytes_downloaded !== undefined &&
            data.total_bytes !== undefined
          ) {
            logData(`[${data.component}] download`, {
              bytes: data.bytes_downloaded,
              total: data.total_bytes,
              pct: data.percent,
            });
          }
        },
        onTotalProgress: (pct: number) => {
          setOverallPercent(pct);
        },
        onComplete: (data: SetupCompleteEvent) => {
          setOverallPercent(100);
          if (data.had_errors) {
            logLine(
              "warn",
              `Setup finished with errors: ${data.errors.join("; ")}`,
            );
            logLine(
              "warn",
              "Some components may not be ready — check details above",
            );
            setPhase("ready");
          } else {
            logLine("success", "Setup complete — all core systems ready");
            setPhase("complete");
            checkStatus().then(() => {
              onSetupComplete?.();
            });
          }
        },
        onError: (error: string) => {
          setInstallError(error);
          setPhase("ready");
          logLine("error", `Setup error: ${error}`);
          // Auto-recheck status so we know what actually succeeded
          setTimeout(() => checkStatus(), 1500);
        },
      });
    } catch (e) {
      if ((e as Error).name === "AbortError") {
        logLine("warn", "Setup cancelled by user");
        setPhase("ready");
      } else {
        const msg = (e as Error).message;
        setInstallError(msg);
        setPhase("ready");
        logLine("error", `Setup exception: ${msg}`);
        setTimeout(() => checkStatus(), 1500);
      }
    }
  }, [setupStatus, checkStatus, onSetupComplete, logLine, logData]);

  // Keep ref current so auto-install can call it without circular deps.
  runInstallRef.current = runInstall;

  const cancelInstall = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setPhase("ready");
    logLine("warn", "Setup cancelled by user");
  }, [logLine]);

  // ── Transcription (Python model download) ─────────────────────────────

  const installTranscription = useCallback(async () => {
    setIsDownloadingTranscription(true);
    logLine("info", "Starting whisper model download from HuggingFace...");

    const controller = new AbortController();
    try {
      await engine.runTranscriptionInstall("base.en", {
        signal: controller.signal,
        onRawLine: (line) => {
          if (
            line.trim() &&
            !line.startsWith("event:") &&
            !line.startsWith("data:")
          ) {
            logLine("data", line);
          }
        },
        onProgress: (data: SetupProgressEvent) => {
          setProgress((prev) => ({
            ...prev,
            transcription: {
              status: data.status,
              message: data.message,
              percent: data.percent,
            },
          }));
          const level =
            data.status === "error"
              ? "error"
              : data.status === "ready"
                ? "success"
                : "info";
          logLine(level, `[transcription] ${data.message} (${data.percent}%)`);
          if (data.bytes_downloaded !== undefined) {
            logData("[transcription] download", {
              bytes: data.bytes_downloaded,
              total: data.total_bytes,
              pct: data.percent,
            });
          }
        },
        onComplete: (data) => {
          if (data.had_errors) {
            logLine(
              "error",
              `Transcription download failed: ${data.errors?.join("; ")}`,
            );
          } else {
            logLine("success", "Transcription model ready");
            setProgress((prev) => ({
              ...prev,
              transcription: {
                status: "ready",
                message: "Model ready",
                percent: 100,
              },
            }));
          }
        },
        onError: (error: string) => {
          logLine("error", `Transcription install error: ${error}`);
          setProgress((prev) => ({
            ...prev,
            transcription: { status: "error", message: error, percent: 0 },
          }));
        },
      });
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        const msg = (e as Error).message;
        logLine("error", `Transcription exception: ${msg}`);
      }
    } finally {
      setIsDownloadingTranscription(false);
    }
  }, [logLine, logData]);

  // ── LLM model download ─────────────────────────────────────────────────

  const downloadLlmModel = useCallback(async () => {
    if (!llmHardware) return;
    const filename = llmHardware.recommended_filename;
    const modelInfo = llmHardware.all_models.find(
      (m) => m.filename === filename,
    );
    if (!modelInfo) {
      logLine("error", `No model info found for ${filename}`);
      return;
    }
    const urls: string[] = modelInfo.all_part_urls;

    setIsDownloadingModel(true);
    setLlmDownloadProgress(null);
    logLine("info", `Starting LLM model download: ${filename}`);
    logLine(
      "cmd",
      `Parts: ${urls.length} — ${urls[0]}${urls.length > 1 ? ` (+${urls.length - 1} more)` : ""}`,
    );

    const { invoke } = await import("@tauri-apps/api/core");
    const { listen } = await import("@tauri-apps/api/event");

    const unlisten = await listen<LlmDownloadProgress>(
      "llm-download-progress",
      (event) => {
        const p = event.payload;
        setLlmDownloadProgress(p);
        const mbDone = (p.bytes_downloaded / 1e6).toFixed(0);
        const mbTotal =
          p.total_bytes > 0 ? `/ ${(p.total_bytes / 1e6).toFixed(0)} MB` : "";
        const partNote =
          p.total_parts > 1 ? ` (part ${p.part}/${p.total_parts})` : "";
        setProgress((prev) => ({
          ...prev,
          local_llm: {
            status: "installing",
            message: `Downloading${partNote}: ${mbDone} ${mbTotal}`,
            percent: Math.round(p.percent),
          },
        }));
        logData("[local_llm] download-progress", p);
      },
    );
    llmUnlistenRef.current = unlisten;

    try {
      logLine("cmd", `invoke download_llm_model: ${filename}`);
      const hfTok = await engine.getHuggingfaceTokenForDownloads();
      await invoke("download_llm_model", { filename, urls, hfToken: hfTok });
      logLine("success", `LLM model downloaded: ${filename}`);
      setProgress((prev) => ({
        ...prev,
        local_llm: { status: "ready", message: filename, percent: 100 },
      }));
      const updated = await invoke<LlmSetupStatus>("get_llm_setup_status");
      setLlmStatus(updated);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      logLine("error", `LLM download failed: ${msg}`);
      setProgress((prev) => ({
        ...prev,
        local_llm: { status: "error", message: msg, percent: 0 },
      }));
    } finally {
      setIsDownloadingModel(false);
      setLlmDownloadProgress(null);
      unlisten();
      llmUnlistenRef.current = null;
    }
  }, [llmHardware, logLine, logData]);

  // Cleanup LLM listener on unmount
  useEffect(() => {
    return () => {
      llmUnlistenRef.current?.();
    };
  }, []);

  // ── Dismiss handler ────────────────────────────────────────────────────

  const dismiss = useCallback(() => {
    setDismissed(true);
    localStorage.setItem(SETUP_DISMISSED_KEY, "true");
  }, []);

  const undismiss = useCallback(() => {
    setDismissed(false);
    localStorage.removeItem(SETUP_DISMISSED_KEY);
  }, []);

  // ── Open macOS Settings deep link ──────────────────────────────────────

  const openDeepLink = useCallback(
    async (link: string) => {
      try {
        logLine("cmd", `Opening settings: ${link}`);
        if (isTauri()) {
          const { open } = await import("@tauri-apps/plugin-shell");
          await open(link);
        } else {
          window.open(link, "_blank");
        }
      } catch (e) {
        logLine("error", `Could not open settings link: ${e}`);
      }
    },
    [logLine],
  );

  // ── Don't render if engine not connected ───────────────────────────────

  if (engineStatus !== "connected") return null;

  // ── Collapsed state when setup is complete and dismissed ───────────────

  if (phase === "complete" && dismissed) {
    return (
      <button
        onClick={undismiss}
        className="flex items-center gap-2 rounded-lg border border-border/50 bg-card/50 px-4 py-2 text-xs text-muted-foreground hover:bg-card hover:text-foreground transition-all"
      >
        <Settings2 className="h-3.5 w-3.5" />
        <span>Setup & Configuration</span>
      </button>
    );
  }

  // ── Component categorisation ───────────────────────────────────────────

  const allComponents = setupStatus?.components || [];

  // Override transcription status with Tauri authoritative value
  const effectiveComponents = allComponents.map((c) => {
    if (c.id === "transcription" && voiceStatus !== null) {
      return {
        ...c,
        status: voiceStatus.setup_complete ? "ready" : c.status,
        detail: voiceStatus.setup_complete
          ? `Model: ${voiceStatus.selected_model ?? "loaded"}`
          : c.detail,
      } as SetupComponentStatus;
    }
    return c;
  });

  // Build LLM component from Tauri status
  const llmComponent: SetupComponentStatus | null = llmStatus
    ? {
        id: "local_llm",
        label: "Confidential Chat",
        description:
          "Optional private AI on your device for offline, fully local conversations — no cloud required while you chat.",
        status: llmStatus.downloaded_models.length > 0 ? "ready" : "not_ready",
        detail:
          llmStatus.downloaded_models.length > 0
            ? `${llmStatus.downloaded_models.length} model(s) downloaded`
            : llmHardware
              ? `Recommended: ${llmHardware.recommended_filename} (~${llmHardware.all_models.find((m) => m.filename === llmHardware.recommended_filename)?.disk_size_gb ?? "?"}GB)`
              : "No models downloaded",
        optional: true,
        size_hint: llmHardware
          ? `~${llmHardware.all_models.find((m) => m.filename === llmHardware.recommended_filename)?.disk_size_gb ?? "?"}GB`
          : null,
        deep_link: null,
      }
    : null;

  const requiredComponents = effectiveComponents.filter((c) =>
    BLOCKING_IDS.has(c.id),
  );
  const optionalComponents = [
    ...effectiveComponents.filter((c) => c.optional),
    ...(llmComponent ? [llmComponent] : []),
  ];

  const allRequiredReady = requiredComponents.every(
    (c) => (progress[c.id]?.status || c.status) === "ready",
  );

  return (
    <Card className="relative overflow-hidden border-primary/20">
      {/* Gradient accent bar */}
      <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-blue-500 via-violet-500 to-emerald-500" />

      <CardHeader className="pb-2 pt-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-blue-500/20 to-violet-500/20">
              {phase === "checking" ? (
                <Loader2 className="h-5 w-5 text-blue-400 animate-spin" />
              ) : (
                <Sparkles className="h-5 w-5 text-blue-400" />
              )}
            </div>
            <div>
              <CardTitle className="text-lg font-semibold">
                {phase === "complete"
                  ? "Matrx Local is Ready"
                  : "Welcome to Matrx Local"}
              </CardTitle>
              <p className="text-xs text-muted-foreground mt-0.5">
                {phase === "checking" && "Checking your system..."}
                {phase === "ready" && "Let's get everything set up for you"}
                {phase === "installing" &&
                  "Setting up your system — this only happens once"}
                {phase === "complete" &&
                  "All core systems are configured and operational"}
                {phase === "configure" && "Customize your setup"}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {phase === "complete" && (
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                onClick={dismiss}
                title="Dismiss"
              >
                <X className="h-4 w-4" />
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={() => setExpanded((e) => !e)}
            >
              {expanded ? (
                <ChevronUp className="h-4 w-4" />
              ) : (
                <ChevronDown className="h-4 w-4" />
              )}
            </Button>
          </div>
        </div>

        {/* Overall progress bar during installation */}
        {phase === "installing" && (
          <div className="mt-3 space-y-1">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>Setting up...</span>
              <span className="tabular-nums">{overallPercent}%</span>
            </div>
            <Progress value={overallPercent} className="h-2" />
          </div>
        )}

        {/* Action button when collapsed */}
        {phase === "ready" && !expanded && (
          <div className="mt-3">
            <Button size="sm" className="gap-1.5" onClick={runInstall}>
              <Play className="h-3.5 w-3.5" /> Set Up Now
            </Button>
          </div>
        )}
      </CardHeader>

      {expanded && (
        <CardContent className="space-y-4 pb-5">
          {/* ── Loading/checking state ─────────────────────────── */}
          {phase === "checking" && (
            <div className="flex items-center justify-center gap-2 py-6 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Analyzing your system...
            </div>
          )}

          {/* ── Status check error ─────────────────────────────── */}
          {statusError && phase === "ready" && !setupStatus && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 space-y-2">
              <div className="flex items-center gap-2 text-sm text-amber-400">
                <AlertCircle className="h-4 w-4 shrink-0" />
                <span>Could not check setup status: {statusError}</span>
              </div>
              <p className="text-xs text-muted-foreground ml-6">
                Check the debug terminal below for details, or try running setup
                directly.
              </p>
              <div className="flex gap-2 ml-6">
                <Button
                  size="sm"
                  variant="outline"
                  className="gap-1.5"
                  onClick={checkStatus}
                >
                  <RotateCcw className="h-3.5 w-3.5" /> Re-check
                </Button>
                <Button size="sm" className="gap-1.5" onClick={runInstall}>
                  <Play className="h-3.5 w-3.5" /> Try Setup Anyway
                </Button>
              </div>
            </div>
          )}

          {/* ── Required components ────────────────────────────── */}
          {phase !== "checking" && requiredComponents.length > 0 && (
            <div className="space-y-1">
              <p className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide px-1 mb-1">
                Required
              </p>
              {requiredComponents.map((comp) => (
                <ComponentRow
                  key={comp.id}
                  component={comp}
                  {...(progress[comp.id] !== undefined ? { progress: progress[comp.id] } : {})}
                  {...(comp.deep_link
                    ? { onOpenSettings: () => openDeepLink(comp.deep_link!) }
                    : {})}
                />
              ))}
            </div>
          )}

          {/* ── Permissions ────────────────────────────────────── */}
          {phase !== "checking" && (
            <div className="space-y-1">
              <p className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide px-1 mb-1">
                Permissions
              </p>
              <div className="flex items-center justify-between rounded-lg border bg-muted/30 px-3 py-2.5">
                <div className="flex items-center gap-2.5 text-sm">
                  <ShieldCheck className="h-4 w-4 text-muted-foreground" />
                  <span>
                    {(() => {
                      const granted = Array.from(
                        permissionStates.values(),
                      ).filter((s) => s.status === "granted").length;
                      const total = Array.from(
                        permissionStates.values(),
                      ).filter(
                        (s) =>
                          s.status !== "unavailable" && s.status !== "loading",
                      ).length;
                      if (total === 0) return "Checking permissions…";
                      return granted === total
                        ? `All ${total} permissions granted`
                        : `${granted} of ${total} permissions granted`;
                    })()}
                  </span>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 gap-1.5 text-xs"
                  onClick={() => setPermissionsModalOpen(true)}
                >
                  <Shield className="h-3 w-3" />
                  Review & Grant
                </Button>
              </div>
            </div>
          )}

          <PermissionsModal
            open={permissionsModalOpen}
            onOpenChange={setPermissionsModalOpen}
          />

          {/* ── Action buttons ─────────────────────────────────── */}
          {phase === "ready" && setupStatus && (
            <div className="flex items-center gap-3 pt-1">
              <Button className="gap-2" onClick={runInstall}>
                <Download className="h-4 w-4" />
                {allRequiredReady ? "Verify Setup" : "Set Up Now"}
              </Button>
              {installError && (
                <div className="flex items-center gap-1.5 text-xs text-red-400 max-w-sm">
                  <AlertCircle className="h-3.5 w-3.5 shrink-0" />
                  <span className="truncate">{installError}</span>
                </div>
              )}
            </div>
          )}

          {phase === "installing" && (
            <div className="flex items-center gap-3 pt-1">
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5"
                onClick={cancelInstall}
              >
                <X className="h-3.5 w-3.5" /> Cancel
              </Button>
              <span className="text-xs text-muted-foreground">
                Keep the app open while setup completes
              </span>
            </div>
          )}

          {(phase === "complete" ||
            (phase === "ready" && allRequiredReady)) && (
            <div className="flex items-center gap-3 pt-1">
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5"
                onClick={checkStatus}
              >
                <RotateCcw className="h-3.5 w-3.5" /> Re-check
              </Button>
            </div>
          )}

          {/* ── Optional Enhancements ──────────────────────────── */}
          {(phase === "complete" ||
            phase === "ready" ||
            phase === "configure") &&
            optionalComponents.length > 0 && (
              <div className="border-t border-border/50 pt-4 mt-4 space-y-3">
                <div className="flex items-center gap-2 mb-1">
                  <Sparkles className="h-4 w-4 text-amber-400" />
                  <span className="text-sm font-medium">
                    Optional Enhancements
                  </span>
                </div>

                {optionalComponents.map((comp) => {
                  const p = progress[comp.id];
                  const effectiveStatus = p?.status || comp.status;
                  const isReady = effectiveStatus === "ready";
                  const isInstalling = effectiveStatus === "installing";
                  const isError = effectiveStatus === "error";

                  if (comp.id === "transcription") {
                    return (
                      <div key={comp.id} className="space-y-2">
                        <ComponentRow component={comp} {...(p ? { progress: p } : {})} />
                        {!isReady && !isInstalling && (
                          <div className="ml-9 space-y-1.5">
                            <p className="text-xs text-muted-foreground">
                              {setupStatus?.gpu_available
                                ? `GPU detected (${setupStatus.gpu_name}) — transcription will use hardware acceleration.`
                                : "No GPU detected — transcription will run on CPU (functional but slower)."}
                            </p>
                            <Button
                              size="sm"
                              variant="outline"
                              className="gap-1.5"
                              onClick={installTranscription}
                              disabled={isDownloadingTranscription}
                            >
                              {isDownloadingTranscription ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <Download className="h-3.5 w-3.5" />
                              )}
                              {isError ? "Retry" : "Download"} Whisper Model (
                              {comp.size_hint || "~150 MB"})
                            </Button>
                          </div>
                        )}
                        {isInstalling && p && (
                          <div className="ml-9 space-y-1">
                            <div className="flex items-center justify-between text-xs text-muted-foreground">
                              <span className="truncate">{p.message}</span>
                              <span className="tabular-nums ml-2">
                                {p.percent}%
                              </span>
                            </div>
                            <Progress value={p.percent} className="h-1.5" />
                          </div>
                        )}
                      </div>
                    );
                  }

                  if (comp.id === "local_llm") {
                    return (
                      <div key={comp.id} className="space-y-2">
                        <ComponentRow component={comp} {...(p ? { progress: p } : {})} />
                        {!isReady && !isInstalling && llmHardware && (
                          <div className="ml-9 space-y-1.5">
                            <p className="text-xs text-muted-foreground">
                              Recommended for your hardware:{" "}
                              <span className="text-foreground font-medium">
                                {llmHardware.recommended_filename}
                              </span>{" "}
                              — {llmHardware.reason}
                            </p>
                            <Button
                              size="sm"
                              variant="outline"
                              className="gap-1.5"
                              onClick={downloadLlmModel}
                              disabled={isDownloadingModel}
                            >
                              {isDownloadingModel ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <Download className="h-3.5 w-3.5" />
                              )}
                              {isError ? "Retry" : "Download"} Model (
                              {comp.size_hint || "varies"})
                            </Button>
                          </div>
                        )}
                        {isInstalling && p && (
                          <div className="ml-9 space-y-1">
                            <div className="flex items-center justify-between text-xs text-muted-foreground">
                              <span className="truncate">{p.message}</span>
                              <span className="tabular-nums ml-2">
                                {p.percent}%
                              </span>
                            </div>
                            <Progress value={p.percent} className="h-1.5" />
                          </div>
                        )}
                      </div>
                    );
                  }

                  return (
                    <ComponentRow key={comp.id} component={comp} {...(p ? { progress: p } : {})} />
                  );
                })}
              </div>
            )}

          {/* ── System info footer ────────────────────────────── */}
          {setupStatus && (
            <div className="flex items-center gap-4 text-[11px] text-muted-foreground/60 pt-1 border-t border-border/30">
              <span>
                {setupStatus.platform} {setupStatus.architecture}
              </span>
              {setupStatus.gpu_available && (
                <span className="flex items-center gap-1">
                  <Cpu className="h-3 w-3" /> {setupStatus.gpu_name}
                </span>
              )}
            </div>
          )}
        </CardContent>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// ComponentRow — shows status for one setup component
// ---------------------------------------------------------------------------

function ComponentRow({
  component,
  progress: p,
  onOpenSettings,
}: {
  component: SetupComponentStatus & { status: string };
  progress?: ComponentProgress;
  onOpenSettings?: () => void;
}) {
  const effectiveStatus = p?.status || component.status;
  const effectiveMessage = p?.message || component.detail || "";
  const icon = COMPONENT_ICONS[component.id] || <Circle className="h-5 w-5" />;

  const isWarning = effectiveStatus === "warning";
  const isReady = effectiveStatus === "ready";
  const isInstalling = effectiveStatus === "installing";
  const isError = effectiveStatus === "error";
  const isNotReady = effectiveStatus === "not_ready";

  return (
    <div className="flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors hover:bg-muted/30">
      {/* Status indicator */}
      <div className="shrink-0">
        {isReady ? (
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-emerald-500/10">
            <CheckCircle2 className="h-5 w-5 text-emerald-500" />
          </div>
        ) : isInstalling ? (
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-blue-500/10">
            <Loader2 className="h-5 w-5 text-blue-400 animate-spin" />
          </div>
        ) : isError ? (
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-red-500/10">
            <AlertCircle className="h-5 w-5 text-red-400" />
          </div>
        ) : isWarning ? (
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-amber-500/10">
            <AlertTriangle className="h-5 w-5 text-amber-400" />
          </div>
        ) : (
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-muted/50 text-muted-foreground">
            {icon}
          </div>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-medium">{component.label}</span>
          {component.optional && (
            <Badge
              variant="outline"
              className="text-[10px] px-1.5 py-0 h-4 text-muted-foreground border-muted-foreground/30"
            >
              Optional
            </Badge>
          )}
          {isWarning && (
            <Badge
              variant="outline"
              className="text-[10px] px-1.5 py-0 h-4 text-amber-400 border-amber-400/30"
            >
              Advisory
            </Badge>
          )}
          {component.size_hint && !isReady && (
            <span className="text-[10px] text-muted-foreground">
              {component.size_hint}
            </span>
          )}
        </div>
        <p className="text-xs text-muted-foreground truncate mt-0.5">
          {isInstalling ? effectiveMessage : component.description}
        </p>
        {isReady && effectiveMessage && (
          <p className="text-[11px] text-emerald-500/70 mt-0.5">
            {effectiveMessage}
          </p>
        )}
        {isError && effectiveMessage && (
          <p className="text-[11px] text-red-400/80 mt-0.5 whitespace-normal line-clamp-2">
            {effectiveMessage}
          </p>
        )}
        {isWarning && effectiveMessage && (
          <p className="text-[11px] text-amber-400/70 mt-0.5 whitespace-normal line-clamp-2">
            {effectiveMessage}
          </p>
        )}
      </div>

      {/* Progress bar for installing state */}
      {isInstalling && p && (
        <div className="w-16 shrink-0">
          <Progress value={p.percent} className="h-1.5" />
        </div>
      )}

      {/* Right-side action / indicator */}
      <div className="shrink-0 flex items-center gap-1">
        {isWarning && onOpenSettings && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-[11px] text-amber-400 hover:text-amber-300 gap-1"
            onClick={onOpenSettings}
          >
            <ExternalLink className="h-3 w-3" />
            Open Settings
          </Button>
        )}
        {isReady && <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
        {isNotReady && (
          <ChevronRight className="h-4 w-4 text-muted-foreground/50" />
        )}
      </div>
    </div>
  );
}
