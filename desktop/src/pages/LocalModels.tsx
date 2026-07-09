"use client";

import {
  useState,
  useRef,
  useCallback,
  createContext,
  useContext,
  useEffect,
} from "react";
import type {
  LlmState,
  LlmActions,
  ServerStartProgress,
  ServerLogLine,
} from "@/hooks/use-llm";
import { useLlmApp } from "@/contexts/LlmContext";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Download,
  Trash2,
  Play,
  Pause,
  Square,
  RotateCcw,
  Zap,
  ZapOff,
  Send,
  Cpu,
  HardDrive,
  Activity,
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Copy,
  Check,
  MessageSquare,
  Wrench,
  Code,
  Server,
  Settings,
  X,
  FolderOpen,
  Plus,
  PackagePlus,
  ExternalLink,
  Loader2,
  Pencil,
  ThumbsUp,
  ThumbsDown,
  GitFork,
  Image,
  KeyRound,
  UserCheck,
  UserPlus,
  ArrowRight,
  ShieldCheck,
  Eye,
  EyeOff,
  RefreshCw,
  BookmarkPlus,
  Folder,
  FolderPlus,
  FileText,
  Volume2,
  VolumeX,
  Mic,
  ChevronRight,
  TriangleAlert,
  ListChecks,
  OctagonX,
  CheckCheck,
  Timer,
} from "lucide-react";
import { isTauri } from "@/lib/sidecar";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Slider } from "@/components/ui/slider";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  streamCompletion,
  runAgenticLoop,
  ContextSizeError,
} from "@/lib/llm/api";
import type {
  ChatMessage,
  LlmModelInfo,
  ToolDefinition,
} from "@/lib/llm/types";
import type { EngineToolSchema } from "@/lib/api";
import { useNavigate } from "react-router-dom";
import { systemPrompts, BUILTIN_PROMPTS } from "@/lib/system-prompts";
import { loadSettings } from "@/lib/settings";
import type { SystemPrompt } from "@/lib/system-prompts";
import { usePageRefreshHandler } from "@/hooks/use-page-refresh";
import { ModelRepoAnalyzer } from "@/components/llm/ModelRepoAnalyzer";
import { engine } from "@/lib/api";
import type { DocFolder, DocTree } from "@/lib/api";
import { MediaGenTab } from "@/components/media-gen/MediaGenTab";
import { useAuth } from "@/hooks/use-auth";
import { useTtsApp, useTtsAppOptional } from "@/contexts/TtsContext";
import { useChatTts } from "@/hooks/use-chat-tts";
import { logError } from "@/lib/error-reporting";
import { useTranscriptionApp } from "@/contexts/TranscriptionContext";
import { useWakeWordContext } from "@/contexts/WakeWordContext";
import { useVoiceChat } from "@/hooks/use-voice-chat";
import { VoiceChatBar } from "@/components/voice/VoiceChatBar";
import { WakeWordActivePopup } from "@/components/voice/WakeWordActivePopup";
import { RecordingMicButton } from "@/components/recording/RecordingMicButton";

// ── Shared LLM context (single hook instance for all tabs) ───────────────

const LlmContext = createContext<[LlmState, LlmActions] | null>(null);

function useLlmContext(): [LlmState, LlmActions] {
  const ctx = useContext(LlmContext);
  if (!ctx) throw new Error("useLlmContext used outside LlmContext.Provider");
  return ctx;
}

// ── Helpers ──────────────────────────────────────────────────────────────

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const gb = bytes / (1024 * 1024 * 1024);
  if (gb >= 1) return `${gb.toFixed(2)} GB`;
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(1)} MB`;
}

// ── Open external URL via Tauri shell ────────────────────────────────────

async function openUrl(url: string) {
  if (
    typeof window !== "undefined" &&
    (window as unknown as Record<string, unknown>).__TAURI__
  ) {
    const { open } = await import("@tauri-apps/plugin-shell");
    await open(url);
  } else {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

// ── Rating scale description ──────────────────────────────────────────────

const RATING_LABELS: Record<number, string> = {
  0: "Not recommended",
  1: "Limited (size/quant compromised)",
  2: "Good enough",
  3: "Great — top local",
  4: "Excellent — top open-source",
  5: "Near-frontier",
};

// A single numeric rating cell — color-coded, full-size, easy to scan
function RatingCell({ value, label }: { value: number; label: string }) {
  const color =
    value === 0
      ? "text-muted-foreground/30"
      : value <= 2
        ? "text-amber-400"
        : value <= 4
          ? "text-blue-400"
          : "text-green-400";
  const tooltip = `${label}: ${value}/5 — ${RATING_LABELS[value] ?? ""}`;
  return (
    <span
      className={`tabular-nums font-bold text-base leading-none ${color}`}
      title={tooltip}
    >
      {value > 0 ? value : "—"}
    </span>
  );
}

function CopyButton({ text, label }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <Button variant="ghost" size="sm" className="h-7 px-2 gap-1" onClick={copy}>
      <Copy className="h-3 w-3" />
      {copied ? "Copied!" : (label ?? "Copy")}
    </Button>
  );
}

function MsgCopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button
      onClick={copy}
      className={`rounded-md p-1.5 transition-colors ${copied ? "text-emerald-500" : "text-muted-foreground hover:text-foreground"}`}
      title="Copy"
    >
      {copied ? (
        <Check className="h-3.5 w-3.5" />
      ) : (
        <Copy className="h-3.5 w-3.5" />
      )}
    </button>
  );
}

// ── Model Loading Progress Card ───────────────────────────────────────────

function ModelLoadingCard({
  modelName,
  progress,
  logs,
}: {
  modelName: string | null;
  progress: ServerStartProgress | null;
  logs: ServerLogLine[];
}) {
  const [elapsed, setElapsed] = useState(0);
  const [showLogs, setShowLogs] = useState(false);

  // Local timer that ticks every second regardless of Tauri events
  useEffect(() => {
    setElapsed(0);
    const id = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [modelName]);

  const pct = progress?.percent ?? Math.min((elapsed / 120) * 60, 55);
  const phase =
    progress?.phase ?? (elapsed < 3 ? "initializing" : "loading model");
  const elapsedDisplay = progress?.elapsed_secs ?? elapsed;

  // Meaningful log lines to show
  const visibleLogs = logs.filter((l) => l.kind !== "noise").slice(-8);

  return (
    <div className="rounded-xl border bg-card shadow-sm overflow-hidden">
      {/* Header */}
      <div className="px-4 pt-4 pb-3 space-y-3">
        <div className="flex items-center gap-2.5">
          <Activity className="h-4 w-4 text-blue-500 animate-pulse shrink-0" />
          <p className="text-sm font-semibold">Loading model into memory</p>
          <span className="ml-auto text-xs text-muted-foreground tabular-nums">
            {elapsedDisplay}s
          </span>
        </div>

        {modelName && (
          <p className="text-xs text-muted-foreground font-mono truncate pl-6">
            {modelName}
          </p>
        )}

        {/* Progress bar */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground capitalize">{phase}</span>
            <span className="text-muted-foreground">{pct.toFixed(0)}%</span>
          </div>
          <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
            <div
              className="h-full rounded-full bg-blue-500 transition-all duration-1000 ease-linear"
              style={{ width: `${pct}%` }}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            Large models take 10–90 seconds to map into GPU memory.
          </p>
        </div>
      </div>

      {/* Log toggle */}
      {visibleLogs.length > 0 && (
        <div className="border-t">
          <button
            className="w-full flex items-center gap-2 px-4 py-2 text-xs text-muted-foreground hover:text-foreground hover:bg-muted/30 transition-colors"
            onClick={() => setShowLogs((v) => !v)}
          >
            {showLogs ? (
              <ChevronUp className="h-3 w-3" />
            ) : (
              <ChevronDown className="h-3 w-3" />
            )}
            {showLogs ? "Hide" : "Show"} server output
          </button>
          {showLogs && (
            <div className="px-4 pb-3 space-y-0.5 max-h-40 overflow-y-auto">
              {visibleLogs.map((l, i) => (
                <p
                  key={i}
                  className={`text-xs font-mono leading-relaxed ${
                    l.kind === "error"
                      ? "text-destructive"
                      : l.kind === "ready"
                        ? "text-green-600"
                        : "text-muted-foreground"
                  }`}
                >
                  {l.line}
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Setup Tab ─────────────────────────────────────────────────────────────

function SetupTab() {
  const [state, actions] = useLlmContext();
  const {
    hardwareResult,
    downloadProgress,
    isDetecting,
    isDownloading,
    isStarting,
    startingModelName,
    serverStartProgress,
    serverLogs,
    downloadCancelled,
    downloadedModels,
    serverStatus,
    error,
  } = state;
  const { detectHardware, quickSetup, cancelDownload, clearError } = actions;

  useEffect(() => {
    if (!hardwareResult && !isDetecting) {
      detectHardware().catch((e) => console.warn("[local-models] detectHardware failed:", e));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const isModelDownloaded = hardwareResult
    ? downloadedModels.some((m) => {
        if (m.filename !== hardwareResult.recommended_filename) return false;
        // Split models are only usable when every part is on disk.
        const catalog = hardwareResult.all_models.find(
          (c) => c.filename === m.filename,
        );
        return catalog?.is_split ? m.all_parts_present : true;
      })
    : false;

  const handleQuickSetup = async () => {
    clearError();
    await quickSetup();
  };

  const progressPercent = downloadProgress?.percent ?? 0;
  const partLabel =
    downloadProgress && downloadProgress.total_parts > 1
      ? ` — Part ${downloadProgress.part} of ${downloadProgress.total_parts}`
      : "";
  const bytesLabel = downloadProgress
    ? `${formatBytes(downloadProgress.bytes_downloaded)} / ${formatBytes(downloadProgress.total_bytes || (hardwareResult?.all_models.find((m) => m.filename === downloadProgress.filename)?.expected_size_bytes ?? 0))}`
    : "";

  return (
    <div className="space-y-6 max-w-2xl">
      {/* Hardware Detection Card */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base flex items-center gap-2">
              <Cpu className="h-4 w-4 text-blue-500" />
              Hardware Profile
            </CardTitle>
            <Button
              size="sm"
              variant="outline"
              disabled={isDetecting}
              onClick={detectHardware}
            >
              {isDetecting ? "Detecting…" : "Re-detect"}
            </Button>
          </div>
        </CardHeader>
        {hardwareResult && (
          <CardContent className="pt-0">
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <p className="text-muted-foreground text-xs">RAM</p>
                <p className="font-medium">
                  {(hardwareResult.hardware.total_ram_mb / 1024).toFixed(0)} GB
                </p>
              </div>
              <div>
                <p className="text-muted-foreground text-xs">CPU Threads</p>
                <p className="font-medium">
                  {hardwareResult.hardware.cpu_threads}
                </p>
              </div>
              <div>
                <p className="text-muted-foreground text-xs">GPU</p>
                <p className="font-medium">
                  {hardwareResult.hardware.is_apple_silicon
                    ? "Apple Silicon (Metal)"
                    : hardwareResult.hardware.supports_cuda
                      ? `CUDA${hardwareResult.hardware.gpu_name ? ` — ${hardwareResult.hardware.gpu_name}` : ""} — ${((hardwareResult.hardware.gpu_vram_mb ?? 0) / 1024).toFixed(0)} GB VRAM`
                      : hardwareResult.hardware.supports_vulkan
                        ? `Vulkan${hardwareResult.hardware.gpu_name ? ` — ${hardwareResult.hardware.gpu_name}` : ""}${hardwareResult.hardware.gpu_vram_mb ? ` — ${(hardwareResult.hardware.gpu_vram_mb / 1024).toFixed(0)} GB VRAM` : ""}`
                        : "No GPU detected — CPU inference"}
                </p>
                {!hardwareResult.hardware.is_apple_silicon &&
                  !hardwareResult.hardware.supports_cuda &&
                  !hardwareResult.hardware.supports_vulkan && (
                    <p className="text-xs text-amber-500 mt-0.5">
                      GPU not found. Ensure GPU drivers are installed and try
                      Re-detect.
                    </p>
                  )}
              </div>
              <div>
                <p className="text-muted-foreground text-xs">Recommended</p>
                <p className="font-medium text-blue-500">
                  {hardwareResult.recommended_name}
                </p>
              </div>
            </div>
            <p className="text-xs text-muted-foreground mt-3 bg-muted/40 rounded px-3 py-2">
              {hardwareResult.reason}
            </p>
          </CardContent>
        )}
      </Card>

      {/* Quick Setup Card */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Zap className="h-4 w-4 text-yellow-500" />
            Quick Setup
          </CardTitle>
          <CardDescription>
            Download the recommended model in one step, then start confidential
            chat on your device.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {hardwareResult && (
            <div className="flex items-center gap-3 rounded-lg border bg-muted/30 px-4 py-3">
              <div className="flex-1">
                <p className="font-medium text-sm">
                  {hardwareResult.recommended_name}
                </p>
                <p className="text-xs text-muted-foreground">
                  {hardwareResult.recommended_size_gb.toFixed(1)} GB •{" "}
                  {hardwareResult.recommended_gpu_layers === 99
                    ? "Full GPU offload"
                    : hardwareResult.recommended_gpu_layers === 0
                      ? "CPU inference"
                      : `${hardwareResult.recommended_gpu_layers} GPU layers`}
                </p>
              </div>
              {isModelDownloaded ? (
                <Badge className="bg-green-500/20 text-green-600 border-green-500/30">
                  <CheckCircle2 className="h-3 w-3 mr-1" />
                  Downloaded
                </Badge>
              ) : (
                <Badge variant="outline">Not downloaded</Badge>
              )}
            </div>
          )}

          {/* Download progress */}
          {isDownloading && downloadProgress && (
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span className="font-medium">Downloading{partLabel}</span>
                <span>{bytesLabel}</span>
              </div>
              <Progress value={progressPercent} className="h-2" />
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">
                  {progressPercent.toFixed(1)}%
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-6 px-2 text-xs text-destructive border-destructive/30"
                  onClick={cancelDownload}
                >
                  <X className="h-3 w-3 mr-1" />
                  Cancel
                </Button>
              </div>
            </div>
          )}

          {downloadCancelled && (
            <div className="text-xs text-amber-500 flex items-center gap-2">
              <AlertCircle className="h-3 w-3" />
              Download cancelled. Partial files have been cleaned up.
            </div>
          )}

          {/* Model loading progress — shown while server is starting */}
          {isStarting && (
            <ModelLoadingCard
              modelName={startingModelName}
              progress={serverStartProgress}
              logs={serverLogs}
            />
          )}

          {serverStatus?.running && (
            <div className="flex items-center gap-2 text-sm text-green-600">
              <CheckCircle2 className="h-4 w-4" />
              Confidential chat is running on port {serverStatus.port} — model:{" "}
              {serverStatus.model_name}
            </div>
          )}

          {error && (
            <div className="text-xs text-destructive flex items-start gap-2 bg-destructive/10 rounded px-3 py-2">
              <AlertCircle className="h-3 w-3 mt-0.5 shrink-0" />
              <span className="whitespace-pre-wrap">{error}</span>
            </div>
          )}

          <Button
            className="w-full"
            disabled={
              isDetecting || isDownloading || isStarting || !hardwareResult
            }
            onClick={handleQuickSetup}
          >
            {isDetecting
              ? "Detecting hardware…"
              : isDownloading
                ? "Downloading…"
                : isStarting
                  ? "Starting confidential chat…"
                  : serverStatus?.running && isModelDownloaded
                    ? "Restart with Recommended Model"
                    : isModelDownloaded
                      ? "Start confidential chat"
                      : "Download & Start"}
          </Button>
          {!hardwareResult && isDetecting && (
            <p className="text-xs text-center text-muted-foreground">
              Detecting hardware…
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ── Custom Model Inline Row ───────────────────────────────────────────────

function CustomModelRow({ onAdded }: { onAdded: () => void }) {
  const [state, actions] = useLlmContext();
  const { isDownloading, downloadProgress, downloadCancelled } = state;

  const [mode, setMode] = useState<"url" | "local">("url");
  const [url, setUrl] = useState("");
  const [customFilename, setCustomFilename] = useState("");
  const [isImporting, setIsImporting] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  /** Filename of the download THIS row initiated — used to gate the
   *  progress/cancel UI so other downloads (e.g. catalog models) aren't
   *  hijacked by this row. */
  const [initiatedFilename, setInitiatedFilename] = useState<string | null>(
    null,
  );

  const fileInputRef = useRef<HTMLInputElement>(null);

  const deriveFilenameFromUrl = (u: string) => {
    try {
      const segments = new URL(u).pathname.split("/");
      const last = segments[segments.length - 1];
      if (last.endsWith(".gguf")) return last;
    } catch {
      /* ignore */
    }
    return "";
  };

  const handleUrlChange = (u: string) => {
    setUrl(u);
    if (!customFilename) setCustomFilename(deriveFilenameFromUrl(u));
  };

  const handleUrlDownload = async () => {
    setLocalError(null);
    setSuccessMsg(null);
    const trimmedUrl = url.trim();
    if (!trimmedUrl) return;
    let filename =
      customFilename.trim() ||
      deriveFilenameFromUrl(trimmedUrl) ||
      "custom-model.gguf";
    if (!filename.endsWith(".gguf")) filename += ".gguf";
    setInitiatedFilename(filename);
    try {
      await actions.downloadModel(filename, [trimmedUrl]);
      setSuccessMsg(`Downloaded: ${filename}`);
      setUrl("");
      setCustomFilename("");
      onAdded();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (!msg.toLowerCase().includes("cancel")) setLocalError(msg);
    } finally {
      setInitiatedFilename(null);
    }
  };

  const handleLocalFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    setLocalError(null);
    setSuccessMsg(null);
    const file = e.target.files?.[0];
    if (!file) return;
    const filePath = (file as File & { path?: string }).path;
    if (!isTauri() || !filePath) {
      setLocalError("Local file import only works in the desktop app.");
      return;
    }
    if (!file.name.endsWith(".gguf")) {
      setLocalError("Only .gguf files are supported.");
      return;
    }
    setIsImporting(true);
    try {
      const saved = await actions.importLocalModel(
        filePath,
        customFilename.trim(),
      );
      setSuccessMsg(`Imported: ${saved}`);
      setCustomFilename("");
      onAdded();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setLocalError(msg);
    } finally {
      setIsImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const progressPercent = downloadProgress?.percent ?? 0;
  // Only treat the active download as ours when it's the one this row started.
  const isCustomDownloading =
    isDownloading &&
    downloadProgress &&
    initiatedFilename !== null &&
    downloadProgress.filename === initiatedFilename;

  return (
    <div className="rounded-lg border border-dashed bg-muted/10 p-4 space-y-3">
      <div className="flex items-center gap-3">
        <PackagePlus className="h-4 w-4 text-muted-foreground shrink-0" />
        <span className="text-sm font-medium text-muted-foreground">
          Add Custom Model
        </span>
        <div className="flex gap-0.5 rounded-md border p-0.5 bg-muted/30 ml-auto">
          <button
            className={`px-2 py-0.5 rounded text-xs font-medium transition-colors ${
              mode === "url"
                ? "bg-background shadow text-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
            onClick={() => setMode("url")}
          >
            URL
          </button>
          <button
            className={`px-2 py-0.5 rounded text-xs font-medium transition-colors ${
              mode === "local"
                ? "bg-background shadow text-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
            onClick={() => setMode("local")}
          >
            Local File
          </button>
        </div>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept=".gguf"
        className="hidden"
        onChange={handleLocalFile}
      />

      <div className="flex items-end gap-2">
        {mode === "url" ? (
          <div className="flex-1">
            <Input
              placeholder="https://huggingface.co/.../model.gguf"
              value={url}
              onChange={(e) => handleUrlChange(e.target.value)}
              className="text-xs h-8 font-mono"
            />
          </div>
        ) : (
          <Button
            size="sm"
            variant="outline"
            className="gap-1.5 h-8"
            disabled={isImporting}
            onClick={() => fileInputRef.current?.click()}
          >
            <FolderOpen className="h-3 w-3" />
            {isImporting ? "Importing…" : "Choose .gguf"}
          </Button>
        )}
        <Input
          placeholder="Save as (optional)"
          value={customFilename}
          onChange={(e) => setCustomFilename(e.target.value)}
          className="text-xs h-8 w-44"
        />
        {mode === "url" && (
          <Button
            size="sm"
            className="gap-1 h-8 px-3"
            disabled={!url.trim() || isDownloading}
            onClick={handleUrlDownload}
          >
            <Download className="h-3 w-3" />
            {isDownloading ? "Downloading…" : "Add"}
          </Button>
        )}
      </div>

      {isCustomDownloading && downloadProgress && (
        <div className="flex items-center gap-3">
          <Progress value={progressPercent} className="h-1.5 flex-1" />
          <span className="text-xs text-muted-foreground tabular-nums w-12 text-right">
            {progressPercent.toFixed(0)}%
          </span>
          <button
            className="text-xs text-destructive hover:underline"
            onClick={actions.cancelDownload}
          >
            Cancel
          </button>
        </div>
      )}

      {downloadCancelled && (
        <p className="text-xs text-amber-500 flex items-center gap-1">
          <AlertCircle className="h-3 w-3" />
          Cancelled.
        </p>
      )}
      {localError && (
        <p className="text-xs text-destructive flex items-center gap-1">
          <AlertCircle className="h-3 w-3 shrink-0" />
          {localError}
        </p>
      )}
      {successMsg && (
        <p className="text-xs text-green-600 flex items-center gap-1">
          <CheckCircle2 className="h-3 w-3 shrink-0" />
          {successMsg}
        </p>
      )}
    </div>
  );
}

// ── Hugging Face Access Modal ─────────────────────────────────────────────
//
// Shown only when a download fails because the model requires a free HF token.
// Tries Playwright automation first. Falls back to guided manual steps if unavailable.

type HfStep =
  | "ask_account"
  | "browser_working"
  | "browser_opened"
  | "has_account"
  | "no_account";

function HuggingFaceAccessModal() {
  const navigate = useNavigate();
  const [state, actions] = useLlmContext();
  const { xetTokenRequired, xetPendingFilename } = state;
  const { saveHfTokenAndRetry, dismissXetModal, refreshHfTokenConfigured } =
    actions;

  const [step, setStep] = useState<HfStep>("ask_account");
  const [token, setToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Reset wizard each time the modal opens
  useEffect(() => {
    if (xetTokenRequired) {
      setStep("ask_account");
      setToken("");
      setSaveError(null);
    }
  }, [xetTokenRequired]);

  // Refresh HF token status when user returns to window (they may have set it in Settings)
  useEffect(() => {
    const onFocus = () => void refreshHfTokenConfigured();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refreshHfTokenConfigured]);

  const modelShortName = xetPendingFilename
    ? xetPendingFilename.replace(/\.gguf$/, "").replace(/-/g, " ")
    : "this model";

  /** Call the backend Playwright flow and react to the result. */
  const runBrowserAssist = async (hasAccount: boolean) => {
    setStep("browser_working");
    try {
      const result = await engine.hfTokenAssist(hasAccount);
      if (result.status === "token_ready" && result.token) {
        setToken(result.token);
        setStep("has_account"); // jump straight to token entry with field pre-filled
      } else if (result.status === "opened") {
        setStep("browser_opened");
      } else {
        // "manual" — Playwright not available or failed silently
        setStep(hasAccount ? "has_account" : "no_account");
      }
    } catch {
      setStep(hasAccount ? "has_account" : "no_account");
    }
  };

  const handleSaveAndRetry = async () => {
    if (!token.trim()) return;
    setSaving(true);
    setSaveError(null);
    try {
      await saveHfTokenAndRetry(token.trim());
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const openHfSignup = () => openUrl("https://huggingface.co/join");
  const openHfTokens = () =>
    openUrl("https://huggingface.co/settings/tokens/new?tokenType=read");
  const openSettingsKeys = () => {
    dismissXetModal();
    navigate("/settings?tab=api-keys");
  };

  return (
    <Dialog
      open={xetTokenRequired}
      onOpenChange={(open) => {
        if (!open && step !== "browser_working") dismissXetModal();
      }}
    >
      <DialogContent className="max-w-lg p-0 gap-0 overflow-hidden">
        {/* Header */}
        <div className="bg-gradient-to-br from-orange-500/10 via-amber-500/5 to-transparent px-6 pt-6 pb-4 border-b">
          <div className="flex items-center gap-3 mb-2">
            <div className="rounded-full bg-amber-500/15 p-2">
              <KeyRound className="h-5 w-5 text-amber-500" />
            </div>
            <DialogHeader className="space-y-0.5">
              <DialogTitle className="text-base font-semibold leading-tight">
                One small step before downloading
              </DialogTitle>
              <DialogDescription className="text-xs text-muted-foreground">
                <span className="font-medium text-foreground/80">
                  {modelShortName}
                </span>{" "}
                is hosted on Hugging Face and needs a free access token.
              </DialogDescription>
            </DialogHeader>
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            This is completely normal — it&apos;s just Hugging Face&apos;s way
            of tracking who downloads their models. It&apos;s free, takes 2
            minutes, and you only need to do it once.
          </p>
        </div>

        <div className="px-6 py-5 space-y-5">
          {/* Step: Ask if they have an account */}
          {step === "ask_account" && (
            <div className="space-y-4">
              <p className="text-sm font-medium text-foreground">
                Do you already have a Hugging Face account?
              </p>
              <div className="grid grid-cols-2 gap-3">
                <button
                  type="button"
                  onClick={() => void runBrowserAssist(true)}
                  className="flex flex-col items-center gap-2 rounded-lg border-2 border-border hover:border-primary/50 hover:bg-primary/5 p-4 transition-all group"
                >
                  <UserCheck className="h-6 w-6 text-muted-foreground group-hover:text-primary transition-colors" />
                  <span className="text-sm font-medium">Yes, I have one</span>
                </button>
                <button
                  type="button"
                  onClick={() => void runBrowserAssist(false)}
                  className="flex flex-col items-center gap-2 rounded-lg border-2 border-border hover:border-primary/50 hover:bg-primary/5 p-4 transition-all group"
                >
                  <UserPlus className="h-6 w-6 text-muted-foreground group-hover:text-primary transition-colors" />
                  <span className="text-sm font-medium">No, I need one</span>
                </button>
              </div>
            </div>
          )}

          {/* Step: Browser automation in progress */}
          {step === "browser_working" && (
            <div className="flex flex-col items-center gap-4 py-6 text-center">
              <div className="rounded-full bg-primary/10 p-4">
                <Loader2 className="h-8 w-8 text-primary animate-spin" />
              </div>
              <div className="space-y-1">
                <p className="text-sm font-medium text-foreground">
                  Opening Hugging Face for you…
                </p>
                <p className="text-xs text-muted-foreground">
                  We&apos;re launching a browser and navigating to the right
                  page.
                </p>
              </div>
            </div>
          )}

          {/* Step: Browser opened — token was extracted automatically */}
          {step === "browser_opened" && (
            <div className="space-y-4">
              <div className="rounded-lg bg-green-500/10 border border-green-500/20 p-3 flex items-start gap-2">
                <CheckCircle2 className="h-4 w-4 text-green-500 mt-0.5 shrink-0" />
                <div className="space-y-0.5">
                  <p className="text-sm font-medium text-foreground">
                    Browser is open
                  </p>
                  <p className="text-xs text-muted-foreground">
                    We&apos;ve opened Hugging Face and navigated to the right
                    page. If you&apos;re signed in, just check the browser
                    window — the form may already be filled in.
                  </p>
                </div>
              </div>

              <p className="text-sm font-medium text-foreground">
                Once you have your token, paste it here:
              </p>

              <div className="space-y-2">
                <div className="relative">
                  <Input
                    id="hf-token-input-opened"
                    type={showToken ? "text" : "password"}
                    placeholder="hf_xxxxxxxxxxxxxxxxxxxxxxxx"
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    className="pr-9 font-mono text-xs"
                    autoComplete="off"
                    spellCheck={false}
                    autoFocus
                  />
                  <button
                    type="button"
                    onClick={() => setShowToken((v) => !v)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                    tabIndex={-1}
                  >
                    {showToken ? (
                      <EyeOff className="h-3.5 w-3.5" />
                    ) : (
                      <Eye className="h-3.5 w-3.5" />
                    )}
                  </button>
                </div>
                {saveError && (
                  <p className="text-xs text-destructive flex items-center gap-1.5">
                    <AlertCircle className="h-3 w-3 shrink-0" />
                    {saveError}
                  </p>
                )}
              </div>

              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setStep("ask_account")}
                  className="h-8 text-xs"
                >
                  ← Back
                </Button>
                <Button
                  size="sm"
                  className="flex-1 h-8 gap-2"
                  disabled={!token.trim() || saving}
                  onClick={handleSaveAndRetry}
                >
                  {saving ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin" /> Saving
                      &amp; downloading…
                    </>
                  ) : (
                    <>
                      <Download className="h-3.5 w-3.5" /> Save token &amp;
                      download
                    </>
                  )}
                </Button>
              </div>

              <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <ShieldCheck className="h-3 w-3 shrink-0" />
                <span>Your token is stored locally on this machine only.</span>
              </div>
            </div>
          )}

          {/* Step: Has account — manual fallback (Playwright not available or needs sign-in) */}
          {step === "has_account" && (
            <div className="space-y-4">
              <div className="space-y-3">
                {token.startsWith("hf_") ? (
                  <div className="rounded-lg bg-green-500/10 border border-green-500/20 p-3 flex items-start gap-2">
                    <CheckCircle2 className="h-4 w-4 text-green-500 mt-0.5 shrink-0" />
                    <div>
                      <p className="text-sm font-medium text-foreground">
                        Token found automatically!
                      </p>
                      <p className="text-xs text-muted-foreground">
                        We grabbed it from the browser for you. Just click
                        download below.
                      </p>
                    </div>
                  </div>
                ) : (
                  <>
                    <p className="text-sm font-medium text-foreground">
                      Let&apos;s grab your access token
                    </p>
                    <ol className="space-y-3">
                      {[
                        {
                          num: 1,
                          text: "Click the button below — it opens Hugging Face in your browser.",
                        },
                        {
                          num: 2,
                          text: "If you're not signed in yet, sign in first.",
                        },
                        {
                          num: 3,
                          text: 'The page will show a form. Leave the type as "Read" and click "Create token".',
                        },
                        {
                          num: 4,
                          text: "Copy the token that appears (it starts with hf_…).",
                        },
                        {
                          num: 5,
                          text: "Paste it in the box below and click Download.",
                        },
                      ].map((item) => (
                        <li
                          key={item.num}
                          className="flex items-start gap-3 text-sm"
                        >
                          <span className="flex-shrink-0 flex items-center justify-center h-5 w-5 rounded-full bg-primary/15 text-primary text-xs font-bold mt-0.5">
                            {item.num}
                          </span>
                          <span className="text-muted-foreground leading-relaxed">
                            {item.text}
                          </span>
                        </li>
                      ))}
                    </ol>
                    <Button
                      variant="outline"
                      size="sm"
                      className="w-full gap-2 h-9 border-amber-500/40 hover:border-amber-500/70 hover:bg-amber-500/5 text-amber-600 dark:text-amber-400"
                      onClick={openHfTokens}
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                      Open Hugging Face → Create Token
                    </Button>
                  </>
                )}
              </div>

              <div className="space-y-2">
                <Label htmlFor="hf-token-input" className="text-xs font-medium">
                  {token.startsWith("hf_")
                    ? "Your token (auto-filled)"
                    : "Paste your token here"}
                </Label>
                <div className="relative">
                  <Input
                    id="hf-token-input"
                    type={showToken ? "text" : "password"}
                    placeholder="hf_xxxxxxxxxxxxxxxxxxxxxxxx"
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    className="pr-9 font-mono text-xs"
                    autoComplete="off"
                    spellCheck={false}
                  />
                  <button
                    type="button"
                    onClick={() => setShowToken((v) => !v)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                    tabIndex={-1}
                  >
                    {showToken ? (
                      <EyeOff className="h-3.5 w-3.5" />
                    ) : (
                      <Eye className="h-3.5 w-3.5" />
                    )}
                  </button>
                </div>
                {saveError && (
                  <p className="text-xs text-destructive flex items-center gap-1.5">
                    <AlertCircle className="h-3 w-3 shrink-0" />
                    {saveError}
                  </p>
                )}
              </div>

              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setStep("ask_account")}
                  className="h-8 text-xs"
                >
                  ← Back
                </Button>
                <Button
                  size="sm"
                  className="flex-1 h-8 gap-2"
                  disabled={!token.trim() || saving}
                  onClick={handleSaveAndRetry}
                >
                  {saving ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin" /> Saving
                      &amp; downloading…
                    </>
                  ) : (
                    <>
                      <Download className="h-3.5 w-3.5" /> Save token &amp;
                      download
                    </>
                  )}
                </Button>
              </div>

              <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <ShieldCheck className="h-3 w-3 shrink-0" />
                <span>
                  Your token is stored locally on this machine only. It is never
                  sent to Matrx servers.
                </span>
              </div>

              <div className="border-t pt-3">
                <p className="text-[11px] text-muted-foreground">
                  Prefer to save it in Settings?{" "}
                  <button
                    type="button"
                    onClick={openSettingsKeys}
                    className="text-primary hover:underline font-medium"
                  >
                    Open Settings → API Keys
                  </button>
                </p>
              </div>
            </div>
          )}

          {/* Step: No account — create one first */}
          {step === "no_account" && (
            <div className="space-y-4">
              <div className="space-y-3">
                <p className="text-sm font-medium text-foreground">
                  No problem — creating an account is free and fast
                </p>
                <p className="text-xs text-muted-foreground">
                  We&apos;ve opened the Hugging Face signup page in a browser
                  window. Fill it out there, then come back here.
                </p>
                <ol className="space-y-3">
                  {[
                    {
                      num: 1,
                      text: "In the browser window we just opened, enter your email and choose a password. No credit card needed.",
                    },
                    {
                      num: 2,
                      text: "Check your email and click the confirmation link.",
                    },
                    {
                      num: 3,
                      text: 'Once confirmed, come back here and click "I\'m signed in".',
                    },
                  ].map((item) => (
                    <li
                      key={item.num}
                      className="flex items-start gap-3 text-sm"
                    >
                      <span className="flex-shrink-0 flex items-center justify-center h-5 w-5 rounded-full bg-primary/15 text-primary text-xs font-bold mt-0.5">
                        {item.num}
                      </span>
                      <span className="text-muted-foreground leading-relaxed">
                        {item.text}
                      </span>
                    </li>
                  ))}
                </ol>
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full gap-2 h-9 border-border hover:bg-muted/30"
                  onClick={openHfSignup}
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  Didn&apos;t open? Click here to open signup
                </Button>
              </div>

              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setStep("ask_account")}
                  className="h-8 text-xs"
                >
                  ← Back
                </Button>
                <Button
                  size="sm"
                  className="flex-1 h-8 gap-2"
                  onClick={() => void runBrowserAssist(true)}
                >
                  I&apos;m signed in — get my token
                  <ArrowRight className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          )}
        </div>

        {/* Footer dismiss */}
        <div className="px-6 py-3 border-t bg-muted/30 flex items-center justify-between">
          <p className="text-[11px] text-muted-foreground">
            You can also skip this and download other models that don&apos;t
            need a token.
          </p>
          {step !== "browser_working" && (
            <Button
              variant="ghost"
              size="sm"
              onClick={dismissXetModal}
              className="h-7 text-xs text-muted-foreground"
            >
              Skip for now
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ── Hugging Face token status hint (small, shown at bottom of Models tab) ─

function HuggingFaceTokenStatusHint() {
  const navigate = useNavigate();
  const [state, actions] = useLlmContext();
  const { hfTokenConfigured } = state;
  const { refreshHfTokenConfigured } = actions;

  useEffect(() => {
    const onFocus = () => void refreshHfTokenConfigured();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refreshHfTokenConfigured]);

  const openApiKeys = () => navigate("/settings?tab=api-keys");

  return (
    <div className="rounded-lg border border-border bg-muted/10 px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <KeyRound className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="font-medium text-foreground">Hugging Face token</span>
        {hfTokenConfigured ? (
          <span className="text-green-600 dark:text-green-500 flex items-center gap-1">
            <CheckCircle2 className="h-3 w-3" /> Configured
          </span>
        ) : (
          <span className="text-muted-foreground">
            Not set — only needed for some models
          </span>
        )}
        <span className="text-muted-foreground hidden sm:inline">·</span>
        <button
          type="button"
          className="text-primary hover:underline font-medium"
          onClick={openApiKeys}
        >
          Manage in Settings → API Keys
        </button>
      </div>
    </div>
  );
}

// ── Shared row props ──────────────────────────────────────────────────────

interface ModelRowProps {
  model: LlmModelInfo;
  downloadedModels: import("@/hooks/use-llm").LlmState["downloadedModels"];
  isDownloading: boolean;
  downloadingFilename: string | null;
  downloadProgress:
    | import("@/hooks/use-llm").LlmState["downloadProgress"]
    | null;
  downloadQueue: import("@/hooks/use-llm").LlmState["downloadQueue"];
  isRunning: boolean;
  runningModelPath: string;
  isStarting: boolean;
  hardwareResult: import("@/lib/llm/types").LlmHardwareResult | null;
  expandedRow: string | null;
  setExpandedRow: (f: string | null) => void;
  onDownload: (model: LlmModelInfo, andRun: boolean) => void;
  onLoad: (model: LlmModelInfo) => void;
  onDelete: (filename: string) => void;
  cancelDownload: () => void;
  /** Per-filename download errors — the row looks up its own (variant-aware) filename. */
  downloadErrors?: Record<string, string> | null;
  onClearDownloadError?: (filename: string) => void;
}

// ── Column layout (shared between header + rows) ──────────────────────────
// provider | name | text | code | vision | tools | size | RAM | cutoff | actions
const TABLE_COLS =
  "grid-cols-[96px_1fr_44px_44px_44px_44px_72px_60px_72px_112px]";

function ModelRow({
  model,
  downloadedModels,
  isDownloading,
  downloadingFilename,
  downloadProgress,
  downloadQueue,
  isRunning,
  runningModelPath,
  isStarting,
  hardwareResult,
  expandedRow,
  setExpandedRow,
  onDownload,
  onLoad,
  onDelete,
  cancelDownload,
  downloadErrors,
  onClearDownloadError,
  rowIndex,
}: ModelRowProps & { rowIndex: number }) {
  const isThisRunning = isRunning && runningModelPath.includes(model.filename);
  const isExpanded = expandedRow === model.filename;
  const isRecommended = hardwareResult?.recommended_filename === model.filename;

  const [selectedVariantIdx, setSelectedVariantIdx] = useState<number>(0);
  const hasVariants = model.variants && model.variants.length > 1;

  const effectiveFilename = hasVariants
    ? model.variants[selectedVariantIdx].filename
    : model.filename;
  const effectiveIsSplit = hasVariants
    ? model.variants[selectedVariantIdx].is_split
    : model.is_split;
  // Split models only count as downloaded when every part is present —
  // otherwise an incomplete download would show as "Downloaded" and could
  // never be re-downloaded.
  const effectiveDownloaded = downloadedModels.some(
    (m) =>
      m.filename === effectiveFilename &&
      (!effectiveIsSplit || m.all_parts_present),
  );

  // Errors are stored under the filename that actually failed (which may be a
  // variant filename) — look up by the row's effective filename.
  const downloadError = downloadErrors?.[effectiveFilename] ?? null;

  // All filenames that belong to this model (default + all variants).
  // Used to detect when any variant of this model is currently downloading or
  // queued so the UI shows the correct button state regardless of which variant
  // tab the user has currently selected.
  const allModelFilenames = hasVariants
    ? model.variants.map((v) => v.filename)
    : [model.filename];

  // True when the SELECTED variant is actively downloading (shows cancel button).
  const effectiveDownloadingThis =
    isDownloading && downloadingFilename === effectiveFilename;

  // True when ANY variant of this model is actively downloading — used to show
  // a spinner on the row even if the user has switched to viewing another variant.
  const anyVariantDownloading =
    isDownloading &&
    !!downloadingFilename &&
    allModelFilenames.includes(downloadingFilename);

  const effectiveQueued =
    !anyVariantDownloading &&
    downloadQueue.some((e) => allModelFilenames.includes(e.filename));

  // Row background: running > alternating stripes
  const stripeBg = rowIndex % 2 === 0 ? "" : "bg-muted/20";
  const rowBg = isThisRunning ? "bg-green-500/5" : stripeBg;

  return (
    <div className="border-b last:border-b-0">
      <div
        className={`grid ${TABLE_COLS} gap-x-3 items-center px-3 py-2.5 text-sm transition-colors hover:bg-primary/5 ${rowBg}`}
      >
        {/* Provider */}
        <span
          className="text-xs text-foreground/70 truncate"
          title={model.provider}
        >
          {model.provider}
        </span>

        {/* Name — downloaded state is the primary signal */}
        <div className="flex items-center gap-2 min-w-0">
          {/* Status dot: green = downloaded, empty = not yet */}
          <div
            className="shrink-0 w-2 h-2 rounded-full mt-px"
            title={effectiveDownloaded ? "Downloaded" : "Not downloaded"}
            style={{
              background: effectiveDownloaded
                ? "rgb(34 197 94)"
                : "rgb(100 116 139 / 0.3)",
            }}
          />
          <button
            className="flex items-center gap-1 min-w-0 text-left group"
            onClick={() => setExpandedRow(isExpanded ? null : model.filename)}
          >
            <span
              className={`font-medium truncate ${effectiveDownloaded ? "text-foreground" : "text-foreground/60"}`}
            >
              {model.name}
            </span>
            <ChevronDown
              className={`h-3 w-3 text-muted-foreground shrink-0 transition-transform group-hover:text-foreground ${isExpanded ? "rotate-180" : ""}`}
            />
          </button>
          {isRecommended && (
            <Badge
              className="text-[10px] px-1.5 py-0 h-4 bg-blue-500/15 text-blue-400 border-blue-500/30 shrink-0"
              title="Recommended for your hardware"
            >
              ★
            </Badge>
          )}
          {isThisRunning && (
            <div className="h-1.5 w-1.5 rounded-full bg-green-500 animate-pulse shrink-0" />
          )}
        </div>

        {/* Rating: Text */}
        <div className="flex justify-center">
          <RatingCell value={model.text_rating} label="Text / Chat" />
        </div>

        {/* Rating: Code */}
        <div className="flex justify-center">
          <RatingCell value={model.code_rating} label="Code" />
        </div>

        {/* Rating: Vision */}
        <div className="flex justify-center">
          <RatingCell value={model.vision_rating} label="Vision" />
        </div>

        {/* Rating: Tools */}
        <div className="flex justify-center">
          <RatingCell value={model.tool_calling_rating} label="Tool calling" />
        </div>

        {/* Size */}
        <span className="text-xs text-foreground/80 text-right tabular-nums">
          {hasVariants
            ? `${model.variants[selectedVariantIdx].disk_size_gb.toFixed(1)} GB`
            : `${model.disk_size_gb.toFixed(1)} GB`}
        </span>

        {/* RAM */}
        <span className="text-xs text-foreground/80 text-right tabular-nums">
          {hasVariants
            ? `${model.variants[selectedVariantIdx].ram_required_gb.toFixed(0)} GB`
            : `${model.ram_required_gb.toFixed(0)} GB`}
        </span>

        {/* Knowledge cutoff */}
        <span
          className="text-xs text-foreground/80 text-center tabular-nums"
          title={`Knowledge cutoff: ${model.knowledge_cutoff}`}
        >
          {model.knowledge_cutoff}
        </span>

        {/* Actions — always at stable positions; disabled when not applicable */}
        <div className="flex justify-end items-center">
          {/* HuggingFace model card link */}
          <Button
            size="sm"
            variant="ghost"
            className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
            onClick={() => openUrl(model.hf_model_card_url)}
            title="View model card on HuggingFace"
          >
            <ExternalLink className="h-3.5 w-3.5" />
          </Button>

          {/* Download button — 5-state machine: idle | error | downloading | queued | done */}
          {effectiveDownloadingThis ? (
            // This exact variant is downloading — show cancel X
            <Button
              size="sm"
              variant="ghost"
              className="h-7 w-7 p-0 text-amber-500 hover:text-amber-400"
              onClick={cancelDownload}
              title="Downloading — click to cancel"
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          ) : anyVariantDownloading ? (
            // A different variant of this model is downloading — show spinner (no cancel, switch variant tab to cancel)
            <span
              className="h-7 w-7 flex items-center justify-center"
              title={`Downloading ${downloadingFilename} — switch to that variant to cancel`}
            >
              <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-400" />
            </span>
          ) : effectiveQueued ? (
            <span
              className="h-7 w-7 flex items-center justify-center"
              title="Queued for download — waiting for current download to finish"
            >
              <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
            </span>
          ) : downloadError && !effectiveDownloaded ? (
            <Button
              size="sm"
              variant="ghost"
              className="h-7 w-7 p-0 text-destructive hover:text-destructive/80"
              onClick={() => {
                onClearDownloadError?.(effectiveFilename);
                if (hasVariants) {
                  const v = model.variants[selectedVariantIdx];
                  onDownload(
                    {
                      ...model,
                      filename: v.filename,
                      hf_url: v.hf_url,
                      hf_parts: v.hf_parts,
                      all_part_urls: v.all_part_urls,
                    },
                    false,
                  );
                } else {
                  onDownload(model, false);
                }
              }}
              title={`Download failed: ${downloadError} — click to retry`}
            >
              <AlertCircle className="h-3.5 w-3.5" />
            </Button>
          ) : !effectiveDownloaded ? (
            <Button
              size="sm"
              variant="ghost"
              className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
              onClick={() => {
                if (hasVariants) {
                  const v = model.variants[selectedVariantIdx];
                  onDownload(
                    {
                      ...model,
                      filename: v.filename,
                      hf_url: v.hf_url,
                      hf_parts: v.hf_parts,
                      all_part_urls: v.all_part_urls,
                    },
                    false,
                  );
                } else {
                  onDownload(model, false);
                }
              }}
              title="Download model"
            >
              <Download className="h-3.5 w-3.5" />
            </Button>
          ) : (
            <div className="h-7 w-7" />
          )}

          {/* Play — enabled when downloaded and not running */}
          <Button
            size="sm"
            variant="ghost"
            className={`h-7 w-7 p-0 ${effectiveDownloaded && !isThisRunning ? "text-green-500 hover:text-green-400" : "text-muted-foreground/20 cursor-not-allowed"}`}
            onClick={() => {
              if (!effectiveDownloaded || isThisRunning) return;
              if (hasVariants) {
                const v = model.variants[selectedVariantIdx];
                onLoad({ ...model, filename: v.filename, hf_url: v.hf_url });
              } else {
                onLoad(model);
              }
            }}
            disabled={!effectiveDownloaded || isThisRunning || isStarting}
            title={
              isThisRunning
                ? "Model is running"
                : effectiveDownloaded
                  ? "Load and run model"
                  : "Download the model first"
            }
          >
            <Play className="h-3.5 w-3.5" />
          </Button>

          {/* Delete — enabled when downloaded */}
          <Button
            size="sm"
            variant="ghost"
            className={`h-7 w-7 p-0 ${effectiveDownloaded ? "text-muted-foreground hover:text-destructive" : "text-muted-foreground/20 cursor-not-allowed"}`}
            onClick={() => {
              if (effectiveDownloaded) onDelete(effectiveFilename);
            }}
            disabled={!effectiveDownloaded}
            title={
              effectiveDownloaded ? "Delete model file" : "No file to delete"
            }
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Download progress — show for the active variant or any variant of this model */}
      {anyVariantDownloading && (
        <div className="px-4 pb-2 flex items-center gap-3">
          {downloadProgress ? (
            <>
              {anyVariantDownloading &&
                !effectiveDownloadingThis &&
                downloadProgress.filename && (
                  <span className="text-xs text-amber-400/80 tabular-nums shrink-0">
                    {downloadProgress.filename
                      .replace(/\.gguf$/, "")
                      .slice(-20)}
                  </span>
                )}
              <Progress
                value={downloadProgress.percent}
                className="h-1.5 flex-1"
              />
              <span className="text-xs text-foreground/70 tabular-nums w-24 text-right">
                {downloadProgress.percent.toFixed(0)}% ·{" "}
                {formatBytes(downloadProgress.bytes_downloaded)}
              </span>
            </>
          ) : (
            <>
              <div className="flex-1 h-1.5 rounded-full bg-muted/40 overflow-hidden">
                <div className="h-full bg-primary/40 rounded-full animate-pulse w-1/3" />
              </div>
              <span className="text-xs text-muted-foreground w-24 text-right flex items-center justify-end gap-1">
                <Loader2 className="h-3 w-3 animate-spin" />
                Starting…
              </span>
            </>
          )}
        </div>
      )}

      {/* Expanded detail + variant picker */}
      {isExpanded && (
        <div className="px-4 pb-3 pl-10 space-y-2 bg-muted/10 border-t">
          {model.is_uncensored && (
            <div className="flex items-center gap-1.5 text-xs text-amber-400 pt-2">
              <AlertCircle className="h-3.5 w-3.5 shrink-0" />
              Uncensored — no content filtering
            </div>
          )}
          <p className="text-sm text-foreground/80 leading-relaxed">
            {model.description}
          </p>
          <p className="text-xs text-foreground/60">
            <span className="font-medium text-foreground/80">
              {model.provider}
            </span>
            {" · "}
            {model.context_length.toLocaleString()} token context
            {model.is_split &&
              ` · split download (${model.hf_parts.length + 1} parts)`}
          </p>

          {/* Variant picker for multi-quant models */}
          {hasVariants && (
            <div className="mt-2">
              <p className="text-xs font-medium text-foreground/70 mb-2">
                Choose quantization
              </p>
              <div className="flex gap-2 flex-wrap">
                {model.variants.map((v, idx) => {
                  const vDownloaded = downloadedModels.some(
                    (d) => d.filename === v.filename,
                  );
                  const fitsHardware =
                    hardwareResult &&
                    v.ram_required_gb <=
                      hardwareResult.hardware.total_ram_mb / 1024;
                  return (
                    <button
                      key={v.filename}
                      onClick={() => setSelectedVariantIdx(idx)}
                      className={`flex flex-col items-start rounded-md border px-3 py-2 text-left transition-colors text-xs ${
                        idx === selectedVariantIdx
                          ? "border-primary bg-primary/10 text-foreground"
                          : "border-border bg-background hover:bg-muted/40 text-foreground/80"
                      }`}
                    >
                      <div className="flex items-center gap-1.5 mb-0.5">
                        <span className="font-semibold">{v.label}</span>
                        {fitsHardware && (
                          <span className="text-[10px] bg-green-500/15 text-green-400 rounded px-1.5 py-0.5">
                            fits your machine
                          </span>
                        )}
                        {vDownloaded && (
                          <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
                        )}
                      </div>
                      <span className="text-foreground/60">
                        {v.disk_size_gb.toFixed(1)} GB disk ·{" "}
                        {v.ram_required_gb.toFixed(0)} GB RAM
                      </span>
                      <span className="text-foreground/40 text-[10px] mt-0.5">
                        {v.quant}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Server-grade collapsible section ─────────────────────────────────────

function ServerGradeSection(
  props: Omit<
    ModelRowProps & { rowIndex: number },
    "model" | "rowIndex" | "downloadErrors"
  > & {
    models: LlmModelInfo[];
    startIndex: number;
    modelDownloadErrors?: Record<string, string>;
  },
) {
  const { models, startIndex, modelDownloadErrors, ...rowProps } = props;
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border-t">
      <button
        className="w-full flex items-center gap-2 px-4 py-2.5 text-sm text-foreground/70 hover:text-foreground hover:bg-muted/20 transition-colors"
        onClick={() => setExpanded((e) => !e)}
      >
        <ChevronDown
          className={`h-3.5 w-3.5 transition-transform ${expanded ? "rotate-180" : ""}`}
        />
        <Server className="h-3.5 w-3.5" />
        <span className="font-medium">Server-grade models</span>
        <Badge variant="outline" className="text-xs px-2 py-0 ml-1">
          {models.length}
        </Badge>
        <span className="ml-2 text-foreground/40 text-xs">
          40 GB+ VRAM · multi-GPU
        </span>
      </button>
      {expanded && (
        <div>
          {models.map((model, idx) => (
            <ModelRow
              key={model.filename}
              model={model}
              rowIndex={startIndex + idx}
              downloadErrors={modelDownloadErrors ?? null}
              {...rowProps}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Models Tab ────────────────────────────────────────────────────────────

function ModelsTab() {
  const [state, actions] = useLlmContext();
  const {
    hardwareResult,
    downloadProgress,
    isDownloading,
    downloadingFilename,
    downloadQueue,
    isStarting,
    isDetecting,
    startingModelName,
    serverStartProgress,
    serverLogs,
    downloadedModels,
    serverStatus,
    error,
    downloadCancelled,
  } = state;
  const {
    detectHardware,
    startServer,
    deleteModel,
    cancelDownload,
    queueDownload,
    downloadAll,
    listModels,
    clearError,
  } = actions;
  const { activeCount: dmActiveCount, openModal: openDownloadModal } =
    useDownloadManager();

  // Refs for always-fresh values inside async polling callbacks
  const downloadedModelsRef = useRef(downloadedModels);
  const errorRef = useRef(error);
  const downloadCancelledRef = useRef(downloadCancelled);
  useEffect(() => {
    downloadedModelsRef.current = downloadedModels;
  }, [downloadedModels]);
  useEffect(() => {
    errorRef.current = error;
  }, [error]);
  useEffect(() => {
    downloadCancelledRef.current = downloadCancelled;
  }, [downloadCancelled]);

  const [localError, setLocalError] = useState<string | null>(null);
  const [modelDownloadErrors, setModelDownloadErrors] = useState<
    Record<string, string>
  >({});
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [expandedCustomRow, setExpandedCustomRow] = useState<string | null>(
    null,
  );
  const [editingCustomRow, setEditingCustomRow] = useState<string | null>(null);
  const [editingCustomName, setEditingCustomName] = useState("");
  const [customModelNames, setCustomModelNames] = useState<
    Record<string, string>
  >(() => {
    try {
      return JSON.parse(localStorage.getItem("custom-model-names") ?? "{}");
    } catch {
      return {};
    }
  });

  // Track last known downloading filename so we can attribute errors to the right model.
  const lastDownloadingFilenameRef = useRef<string | null>(null);
  useEffect(() => {
    if (downloadingFilename) {
      lastDownloadingFilenameRef.current = downloadingFilename;
    }
  }, [downloadingFilename]);

  // Ref mirrors of the download-in-flight state. NOTE: the error-recording
  // effect below is declared BEFORE the mirror-update effect on purpose —
  // effects run in declaration order, so when a failing download sets `error`
  // and clears `isDownloading` in the same batch, the recording effect still
  // reads the previous committed values (download WAS in flight, and which
  // file it was).
  const isDownloadingRef = useRef(isDownloading);
  const downloadingFilenameRef = useRef(downloadingFilename);

  // When a download error appears, record it against the model that was
  // downloading. Only record when a download was actually in flight —
  // otherwise unrelated hook errors (startServer/stopServer/deleteModel/
  // detectHardware) get stamped onto the last-downloaded model row.
  useEffect(() => {
    if (!error) return;
    const failedFilename =
      downloadingFilenameRef.current ?? lastDownloadingFilenameRef.current;
    if (isDownloadingRef.current && failedFilename) {
      setModelDownloadErrors((prev) => ({ ...prev, [failedFilename]: error }));
    }
  }, [error]);

  useEffect(() => {
    isDownloadingRef.current = isDownloading;
    downloadingFilenameRef.current = downloadingFilename;
  }, [isDownloading, downloadingFilename]);

  useEffect(() => {
    if (!hardwareResult && !isDetecting) {
      detectHardware().catch((e) => console.warn("[local-models] detectHardware failed:", e));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const ensureHardware = useCallback(async () => {
    if (!hardwareResult) return await detectHardware();
    return hardwareResult;
  }, [hardwareResult, detectHardware]);

  const handleDownload = (model: LlmModelInfo, andRun: boolean) => {
    setLocalError(null);
    // Clear any prior error for this specific model on a fresh attempt
    setModelDownloadErrors((prev) => {
      const next = { ...prev };
      delete next[model.filename];
      return next;
    });
    if (andRun) {
      const run = async () => {
        try {
          const hw = await ensureHardware();
          queueDownload(model.filename, model.all_part_urls);
          // Poll until the model appears in downloadedModels, then start.
          // Uses refs so callbacks always see latest state, not stale closures.
          // Bail out after 30 min (3600 × 500 ms) to prevent infinite loops.
          await new Promise<void>((resolve, reject) => {
            let attempts = 0;
            const maxAttempts = 3600;
            const check = () => {
              const downloaded = downloadedModelsRef.current.some(
                (m) => m.filename === model.filename,
              );
              if (downloaded) {
                resolve();
                return;
              }
              // If an error appeared or download was cancelled, stop waiting.
              if (errorRef.current || downloadCancelledRef.current) {
                reject(new Error(errorRef.current ?? "Download cancelled"));
                return;
              }
              if (++attempts >= maxAttempts) {
                reject(new Error("Download timed out waiting to complete"));
                return;
              }
              setTimeout(check, 500);
            };
            setTimeout(check, 1000);
          });
          await startServer(
            model.filename,
            hw.recommended_gpu_layers,
            model.context_length,
          );
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          if (!msg.toLowerCase().includes("cancel")) setLocalError(msg);
        }
      };
      void run();
    } else {
      queueDownload(model.filename, model.all_part_urls);
    }
  };

  const handleDownloadAll = () => {
    const allModels = hardwareResult?.all_models ?? [];
    const entries = allModels
      .filter((m) => !downloadedModels.some((d) => d.filename === m.filename))
      .map((m) => ({ filename: m.filename, urls: m.all_part_urls }));
    downloadAll(entries);
  };

  const handleLoad = async (model: LlmModelInfo) => {
    setLocalError(null);
    try {
      const hw = await ensureHardware();
      await startServer(
        model.filename,
        hw.recommended_gpu_layers,
        model.context_length,
      );
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setLocalError(msg);
    }
  };

  const handleDelete = async (filename: string) => {
    setLocalError(null);
    try {
      await deleteModel(filename);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setLocalError(msg);
    }
  };

  const handleClearModelDownloadError = useCallback(
    (filename: string) => {
      setModelDownloadErrors((prev) => {
        const next = { ...prev };
        delete next[filename];
        return next;
      });
      clearError();
    },
    [clearError],
  );

  const handleLoadCustom = async (filename: string) => {
    setLocalError(null);
    try {
      const hw = await ensureHardware();
      await startServer(filename, hw.recommended_gpu_layers, 4096);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setLocalError(msg);
    }
  };

  const saveCustomModelName = (filename: string, name: string) => {
    const updated = {
      ...customModelNames,
      [filename]: name.trim() || filename,
    };
    setCustomModelNames(updated);
    try {
      localStorage.setItem("custom-model-names", JSON.stringify(updated));
    } catch {
      // storage full — ignore
    }
  };

  const allModels = hardwareResult?.all_models ?? [];
  const customModels = downloadedModels.filter(
    (dm) => !allModels.some((m) => m.filename === dm.filename),
  );
  const isRunning = !!serverStatus?.running;
  const runningModelPath = serverStatus?.model_path ?? "";

  return (
    <div className="space-y-4">
      {/* Download manager status badge */}
      {dmActiveCount > 0 && (
        <button
          onClick={openDownloadModal}
          className="flex items-center gap-2 w-full rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-sm text-primary hover:bg-primary/10 transition-colors"
        >
          <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" />
          <span className="flex-1 text-left font-medium">
            {dmActiveCount} download{dmActiveCount !== 1 ? "s" : ""} in progress
          </span>
          <span className="text-xs text-muted-foreground">View progress →</span>
        </button>
      )}

      {(error || localError) && (
        <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-2 text-sm text-destructive">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <span className="whitespace-pre-wrap flex-1">
            {error ?? localError}
          </span>
          <button
            className="text-xs underline shrink-0"
            onClick={() => {
              setLocalError(null);
              clearError();
            }}
          >
            Dismiss
          </button>
        </div>
      )}

      {isStarting && (
        <ModelLoadingCard
          modelName={startingModelName}
          progress={serverStartProgress}
          logs={serverLogs}
        />
      )}

      {allModels.length === 0 && !isDetecting && (
        <div className="rounded-lg border bg-muted/20 px-6 py-8 text-center text-sm text-muted-foreground">
          <Cpu className="h-6 w-6 mx-auto mb-2 opacity-30" />
          <p>Detecting hardware to find compatible models…</p>
          <Button
            size="sm"
            variant="outline"
            className="mt-3"
            onClick={detectHardware}
          >
            Retry Detection
          </Button>
        </div>
      )}

      {isDetecting && allModels.length === 0 && (
        <div className="rounded-lg border bg-muted/20 px-6 py-8 text-center text-sm text-muted-foreground">
          <Activity className="h-5 w-5 mx-auto mb-2 animate-pulse text-blue-500" />
          Detecting hardware…
        </div>
      )}

      {allModels.length > 0 && (
        <div className="rounded-lg border overflow-hidden">
          {/* Sticky table header */}
          <div
            className={`grid ${TABLE_COLS} gap-x-3 items-center px-3 py-2 bg-muted/60 text-xs font-semibold text-foreground/60 border-b sticky top-0 z-10`}
          >
            <span>Provider</span>
            <span>Model</span>
            <span className="text-center" title="Text / Chat quality (0–5)">
              Text
            </span>
            <span className="text-center" title="Code quality (0–5)">
              Code
            </span>
            <span className="text-center" title="Vision capability (0–5)">
              Vision
            </span>
            <span className="text-center" title="Tool calling capability (0–5)">
              Tools
            </span>
            <span className="text-right">Size</span>
            <span className="text-right">RAM</span>
            <span className="text-center">Cutoff</span>
            <div className="flex items-center justify-end gap-1">
              <span>Actions</span>
              {allModels.some(
                (m) => !downloadedModels.some((d) => d.filename === m.filename),
              ) && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-5 px-1.5 text-[10px] gap-1 ml-1"
                  onClick={handleDownloadAll}
                  title="Download all missing models"
                  disabled={isDownloading}
                >
                  <Download className="h-2.5 w-2.5" />
                  All
                </Button>
              )}
              {downloadQueue.length > 0 && (
                <span className="text-[10px] bg-primary/15 text-primary rounded-full px-1.5 py-0.5 tabular-nums">
                  {downloadQueue.length}
                </span>
              )}
            </div>
          </div>

          {/* Model rows — consumer models */}
          {(() => {
            const consumerModels = allModels.filter((m) => !m.is_server_grade);
            return consumerModels.map((model, idx) => (
              <ModelRow
                key={model.filename}
                model={model}
                rowIndex={idx}
                downloadedModels={downloadedModels}
                isDownloading={isDownloading}
                downloadingFilename={downloadingFilename ?? null}
                downloadProgress={downloadProgress ?? null}
                downloadQueue={downloadQueue}
                isRunning={isRunning}
                runningModelPath={runningModelPath}
                isStarting={isStarting}
                hardwareResult={hardwareResult ?? null}
                expandedRow={expandedRow}
                setExpandedRow={setExpandedRow}
                onDownload={handleDownload}
                onLoad={handleLoad}
                onDelete={handleDelete}
                cancelDownload={cancelDownload}
                downloadErrors={modelDownloadErrors}
                onClearDownloadError={handleClearModelDownloadError}
              />
            ));
          })()}

          {/* Server-grade models — collapsible */}
          {(() => {
            const consumerCount = allModels.filter(
              (m) => !m.is_server_grade,
            ).length;
            const serverModels = allModels.filter((m) => m.is_server_grade);
            if (serverModels.length === 0) return null;
            return (
              <ServerGradeSection
                models={serverModels}
                startIndex={consumerCount}
                downloadedModels={downloadedModels}
                isDownloading={isDownloading}
                downloadingFilename={downloadingFilename ?? null}
                downloadProgress={downloadProgress ?? null}
                downloadQueue={downloadQueue}
                isRunning={isRunning}
                runningModelPath={runningModelPath}
                isStarting={isStarting}
                hardwareResult={hardwareResult ?? null}
                expandedRow={expandedRow}
                setExpandedRow={setExpandedRow}
                onDownload={handleDownload}
                onLoad={handleLoad}
                onDelete={handleDelete}
                cancelDownload={cancelDownload}
                modelDownloadErrors={modelDownloadErrors}
                onClearDownloadError={handleClearModelDownloadError}
              />
            );
          })()}

          {/* Custom downloaded models */}
          {customModels.map((dm, idx) => {
            const isThisRunning =
              isRunning && runningModelPath.includes(dm.filename);
            const totalConsumer = allModels.filter(
              (m) => !m.is_server_grade,
            ).length;
            const stripe = (totalConsumer + idx) % 2 === 0 ? "" : "bg-muted/20";
            const isExpanded = expandedCustomRow === dm.filename;
            const isEditing = editingCustomRow === dm.filename;
            const displayName = customModelNames[dm.filename] ?? dm.name;

            return (
              <div key={dm.filename} className="border-b last:border-b-0">
                <div
                  className={`grid ${TABLE_COLS} gap-x-3 items-center px-3 py-2.5 text-sm transition-colors hover:bg-primary/5 ${isThisRunning ? "bg-green-500/5" : stripe}`}
                >
                  <span className="text-xs text-foreground/50">Custom</span>

                  {/* Name with expand chevron */}
                  <div className="flex items-center gap-2 min-w-0">
                    <div className="w-2 h-2 rounded-full bg-green-500 shrink-0" />
                    <button
                      className="flex items-center gap-1 min-w-0 text-left group"
                      onClick={() =>
                        setExpandedCustomRow(isExpanded ? null : dm.filename)
                      }
                    >
                      <span className="font-medium truncate text-foreground">
                        {displayName}
                      </span>
                      <ChevronDown
                        className={`h-3 w-3 text-muted-foreground shrink-0 transition-transform group-hover:text-foreground ${isExpanded ? "rotate-180" : ""}`}
                      />
                    </button>
                    {isThisRunning && (
                      <div className="h-1.5 w-1.5 rounded-full bg-green-500 animate-pulse shrink-0" />
                    )}
                  </div>

                  {/* Rating placeholders */}
                  <span className="text-foreground/30 text-xs text-center">
                    —
                  </span>
                  <span className="text-foreground/30 text-xs text-center">
                    —
                  </span>
                  <span className="text-foreground/30 text-xs text-center">
                    —
                  </span>
                  <span className="text-foreground/30 text-xs text-center">
                    —
                  </span>

                  {/* Size */}
                  <span className="text-xs text-foreground/80 text-right tabular-nums">
                    {dm.size_gb} GB
                  </span>
                  <span className="text-foreground/30 text-xs text-right">
                    —
                  </span>
                  <span className="text-foreground/30 text-xs text-center">
                    —
                  </span>

                  {/* Actions — stable positions */}
                  <div className="flex justify-end items-center">
                    {/* Edit/rename */}
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
                      onClick={() => {
                        if (isEditing) {
                          setEditingCustomRow(null);
                        } else {
                          setEditingCustomName(displayName);
                          setEditingCustomRow(dm.filename);
                        }
                      }}
                      title="Rename model"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>

                    {/* Download — not applicable for custom; greyed out */}
                    <div className="h-7 w-7" />

                    {/* Play */}
                    <Button
                      size="sm"
                      variant="ghost"
                      className={`h-7 w-7 p-0 ${!isThisRunning ? "text-green-500 hover:text-green-400" : "text-muted-foreground/20 cursor-not-allowed"}`}
                      onClick={() => {
                        if (!isThisRunning) handleLoadCustom(dm.filename);
                      }}
                      disabled={isThisRunning || isStarting}
                      title={
                        isThisRunning
                          ? "Model is running"
                          : "Load and run model"
                      }
                    >
                      <Play className="h-3.5 w-3.5" />
                    </Button>

                    {/* Delete */}
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                      onClick={() => handleDelete(dm.filename)}
                      title="Delete model file"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>

                {/* Inline rename */}
                {isEditing && (
                  <div className="px-4 pb-3 pt-2 pl-10 bg-muted/10 border-t flex items-center gap-2">
                    <Input
                      className="h-7 text-xs flex-1 max-w-xs"
                      value={editingCustomName}
                      onChange={(e) => setEditingCustomName(e.target.value)}
                      placeholder="Enter a name for this model"
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          saveCustomModelName(dm.filename, editingCustomName);
                          setEditingCustomRow(null);
                        }
                        if (e.key === "Escape") setEditingCustomRow(null);
                      }}
                      autoFocus
                    />
                    <Button
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={() => {
                        saveCustomModelName(dm.filename, editingCustomName);
                        setEditingCustomRow(null);
                      }}
                    >
                      Save
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 px-2 text-xs"
                      onClick={() => setEditingCustomRow(null)}
                    >
                      Cancel
                    </Button>
                  </div>
                )}

                {/* Expanded details */}
                {isExpanded && !isEditing && (
                  <div className="px-4 pb-3 pl-10 space-y-1.5 bg-muted/10 border-t">
                    <p className="text-xs text-foreground/60 pt-2">
                      <span className="font-medium text-foreground/80">
                        File:
                      </span>{" "}
                      <span className="font-mono">{dm.filename}</span>
                    </p>
                    <p className="text-xs text-foreground/60">
                      <span className="font-medium text-foreground/80">
                        Size:
                      </span>{" "}
                      {dm.size_gb} GB
                    </p>
                    {isThisRunning && (
                      <p className="text-xs text-green-400 flex items-center gap-1">
                        <div className="h-1.5 w-1.5 rounded-full bg-green-500 animate-pulse" />
                        Currently running
                      </p>
                    )}
                    <p className="text-xs text-muted-foreground">
                      Custom model — no metadata available. Use the rename
                      button to give it a friendly name.
                    </p>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Always-visible custom model input */}
      <CustomModelRow onAdded={listModels} />

      {/* Intelligent repository analyzer */}
      <ModelRepoAnalyzer
        hardwareResult={hardwareResult}
        isDownloading={isDownloading}
        downloadProgress={downloadProgress}
        onDownload={queueDownload}
      />

      <HuggingFaceTokenStatusHint />
    </div>
  );
}

// ── Agentic Tool Call Card ─────────────────────────────────────────────────

function AgenticToolCallCard({
  toolName,
  arguments: args,
  result,
  isError,
  elapsedMs,
}: {
  toolName: string;
  arguments: Record<string, unknown>;
  result: string;
  isError: boolean;
  elapsedMs: number;
}) {
  const [showArgs, setShowArgs] = useState(false);
  const [showResult, setShowResult] = useState(false);

  return (
    <div
      className={`rounded-lg border text-xs ${
        isError
          ? "border-destructive/40 bg-destructive/5"
          : "border-blue-500/20 bg-blue-500/5"
      }`}
    >
      {/* Header row */}
      <div className="flex items-center gap-2 px-3 py-2">
        <div
          className={`h-5 w-5 rounded-full flex items-center justify-center shrink-0 ${
            isError ? "bg-destructive/20" : "bg-blue-500/20"
          }`}
        >
          {isError ? (
            <AlertCircle className="h-3 w-3 text-destructive" />
          ) : (
            <Wrench className="h-3 w-3 text-blue-500" />
          )}
        </div>
        <span className="font-mono font-semibold">{toolName}</span>
        <span className="ml-auto text-muted-foreground flex items-center gap-1">
          <Timer className="h-2.5 w-2.5" />
          {elapsedMs < 1000
            ? `${elapsedMs}ms`
            : `${(elapsedMs / 1000).toFixed(1)}s`}
        </span>
      </div>

      {/* Expandable args */}
      <div className="border-t">
        <button
          className="flex items-center gap-1 w-full px-3 py-1.5 text-muted-foreground hover:text-foreground transition-colors"
          onClick={() => setShowArgs((v) => !v)}
        >
          <ChevronRight
            className={`h-3 w-3 transition-transform ${showArgs ? "rotate-90" : ""}`}
          />
          Arguments
        </button>
        {showArgs && (
          <pre className="px-3 pb-2 font-mono text-[10px] whitespace-pre-wrap break-all text-muted-foreground overflow-hidden">
            {JSON.stringify(args, null, 2)}
          </pre>
        )}
      </div>

      {/* Expandable result */}
      <div className="border-t">
        <button
          className={`flex items-center gap-1 w-full px-3 py-1.5 transition-colors ${
            isError
              ? "text-destructive hover:text-destructive/80"
              : "text-muted-foreground hover:text-foreground"
          }`}
          onClick={() => setShowResult((v) => !v)}
        >
          <ChevronRight
            className={`h-3 w-3 transition-transform ${showResult ? "rotate-90" : ""}`}
          />
          {isError ? "Error" : "Result"}
        </button>
        {showResult && (
          <pre className="px-3 pb-2 font-mono text-[10px] whitespace-pre-wrap break-all max-h-48 overflow-y-auto text-muted-foreground">
            {result.length > 4000
              ? result.slice(0, 4000) + "\n…(truncated)"
              : result}
          </pre>
        )}
      </div>
    </div>
  );
}

// ── Audio Chat Mode ───────────────────────────────────────────────────────

interface AudioChatModeProps {
  voiceChatState: import("@/hooks/use-voice-chat").VoiceChatState;
  voiceChatActions: import("@/hooks/use-voice-chat").VoiceChatActions;
  transcriptionState: import("@/hooks/use-transcription").TranscriptionState;
  isGenerating: boolean;
  stopGeneration: () => void;
  ttsPlaybackState: import("@/hooks/use-tts").TtsPlaybackState;
  messages: {
    id: string;
    role: string;
    content: string;
    isStreaming?: boolean;
  }[];
  ttsAvailable: boolean;
}

function AudioChatMode({
  voiceChatState,
  voiceChatActions,
  transcriptionState,
  isGenerating,
  stopGeneration,
  ttsPlaybackState,
  messages,
  ttsAvailable,
}: AudioChatModeProps) {
  const { phase, autoMode, pendingTranscript } = voiceChatState;
  const isRecording = transcriptionState.isRecording;
  const isProcessingTail = transcriptionState.isProcessingTail;
  const liveRms = transcriptionState.liveRms;
  const isSpeaking =
    ttsPlaybackState === "playing" || ttsPlaybackState === "synthesizing";

  const lastAssistantMsg = [...messages]
    .reverse()
    .find((m) => m.role === "assistant");
  const lastUserMsg = [...messages].reverse().find((m) => m.role === "user");

  const phaseColor: Record<typeof phase, string> = {
    idle: "text-muted-foreground",
    recording: "text-red-500",
    processing: "text-amber-500",
    transcribed: "text-blue-500",
    generating: "text-primary",
    speaking: "text-green-500",
  };

  const phaseLabel: Record<typeof phase, string> = {
    idle: "Tap the mic to speak",
    recording: "Listening…",
    processing: "Processing speech…",
    transcribed: "Ready — tap Send or speak again",
    generating: "Model is thinking…",
    speaking: "Speaking…",
  };

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Status bar */}
      <div className="shrink-0 flex items-center justify-between px-4 py-2 border-b bg-muted/10">
        <div className="flex items-center gap-2">
          <span className={`text-sm font-medium ${phaseColor[phase]}`}>
            {phaseLabel[phase]}
          </span>
          {!ttsAvailable && (
            <span className="text-xs text-amber-500">
              (TTS not available — text responses only)
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {/* Auto mode toggle */}
          <button
            onClick={() => voiceChatActions.setAutoMode(!autoMode)}
            className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium border transition-colors ${
              autoMode
                ? "bg-primary/15 text-primary border-primary/30"
                : "text-muted-foreground border-border hover:text-foreground"
            }`}
            title={
              autoMode
                ? "Auto mode: silence auto-submits"
                : "Manual mode: press Send to submit"
            }
          >
            {autoMode ? (
              <Zap className="h-3 w-3" />
            ) : (
              <ZapOff className="h-3 w-3" />
            )}
            {autoMode ? "Auto" : "Manual"}
          </button>
        </div>
      </div>

      {/* Conversation preview — last exchange */}
      <div className="flex-1 min-h-0 overflow-y-auto flex flex-col items-center justify-end px-6 py-6 gap-4">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center gap-3 text-center max-w-sm">
            <div className="h-14 w-14 rounded-full bg-primary/10 flex items-center justify-center">
              <Mic className="h-7 w-7 text-primary/60" />
            </div>
            <p className="text-sm text-muted-foreground">
              No messages yet.{" "}
              {autoMode
                ? "Start speaking — I'll auto-submit when you pause."
                : "Tap the mic, speak, then press Send."}
            </p>
          </div>
        ) : (
          <div className="w-full max-w-2xl space-y-4">
            {/* Last user message */}
            {lastUserMsg && (
              <div className="flex justify-end">
                <div className="max-w-[80%] rounded-2xl bg-primary px-4 py-2.5 text-sm text-primary-foreground">
                  {lastUserMsg.content}
                </div>
              </div>
            )}
            {/* Last assistant message */}
            {lastAssistantMsg && (
              <div className="flex justify-start items-start gap-3">
                <div
                  className={`h-8 w-8 rounded-full flex items-center justify-center shrink-0 ${
                    isSpeaking ? "bg-green-500/20" : "bg-muted"
                  }`}
                >
                  {isSpeaking ? (
                    <SpeakingWave />
                  ) : (
                    <Volume2 className="h-4 w-4 text-muted-foreground" />
                  )}
                </div>
                <div className="max-w-[80%] rounded-2xl bg-muted px-4 py-2.5 text-sm">
                  {lastAssistantMsg.isStreaming ? (
                    <>
                      {lastAssistantMsg.content}
                      <span className="inline-block h-3.5 w-0.5 bg-primary opacity-70 animate-pulse ml-1 align-middle" />
                    </>
                  ) : (
                    lastAssistantMsg.content
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Live transcript preview */}
        {(phase === "recording" ||
          phase === "processing" ||
          phase === "transcribed") && (
          <div className="w-full max-w-2xl">
            <div className="flex justify-end">
              <div
                className={`max-w-[80%] rounded-2xl border px-4 py-2.5 text-sm ${
                  phase === "transcribed"
                    ? "border-blue-500/40 bg-blue-500/5 text-foreground"
                    : "border-dashed border-muted-foreground/30 text-muted-foreground italic"
                }`}
              >
                {phase === "recording"
                  ? transcriptionState.fullTranscript
                      .slice(voiceChatState.sessionBaseTranscript.length)
                      .trim() || "Listening…"
                  : pendingTranscript || "Processing…"}
                {phase === "recording" && (
                  <span className="inline-block h-3 w-0.5 bg-current animate-pulse ml-0.5" />
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Bottom controls */}
      <div className="shrink-0 border-t bg-background px-6 py-4">
        <div className="max-w-2xl mx-auto flex flex-col items-center gap-4">
          {/* Main mic button */}
          <div className="flex items-center gap-6">
            {/* Stop TTS / generation */}
            {(isSpeaking || isGenerating) && (
              <button
                onClick={() => {
                  if (isSpeaking) voiceChatActions.stopSpeaking();
                  if (isGenerating) stopGeneration();
                }}
                className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/15 text-destructive hover:bg-destructive/25 transition-colors"
                title={isSpeaking ? "Stop speaking" : "Stop generation"}
              >
                <Square className="h-5 w-5 fill-current" />
              </button>
            )}

            {/* Big mic button */}
            <RecordingMicButton
              isRecording={isRecording}
              isProcessingTail={isProcessingTail}
              liveRms={liveRms}
              onToggle={voiceChatActions.toggleRecording}
              disabled={
                isGenerating || isSpeaking || !transcriptionState.activeModel
              }
              size="lg"
              stopIcon="square"
            />

            {/* Send (manual mode) */}
            {!autoMode && (
              <button
                onClick={voiceChatActions.sendPendingTranscript}
                disabled={
                  phase !== "transcribed" ||
                  !pendingTranscript.trim() ||
                  isGenerating
                }
                className={`flex h-12 w-12 items-center justify-center rounded-full transition-colors ${
                  phase === "transcribed" &&
                  pendingTranscript.trim() &&
                  !isGenerating
                    ? "bg-primary text-primary-foreground hover:bg-primary/90"
                    : "bg-muted text-muted-foreground opacity-40 cursor-not-allowed"
                }`}
                title="Send transcript"
              >
                <Send className="h-5 w-5" />
              </button>
            )}
          </div>

          {/* Status hint */}
          <p className="text-xs text-muted-foreground text-center">
            {!transcriptionState.activeModel ? (
              <span className="text-amber-500">
                Transcription model not ready — go to Speech to Text tab to set
                up
              </span>
            ) : autoMode ? (
              "Speak, then pause for ~1.5s — I'll send automatically. Response plays back, speak any time to interrupt."
            ) : (
              "Tap mic → speak → tap mic again or wait → tap Send → listen to response."
            )}
          </p>
        </div>
      </div>
    </div>
  );
}

function SpeakingWave() {
  return (
    <div className="flex items-center gap-[2px]">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="block w-[3px] rounded-full bg-green-500"
          style={{
            height: `${8 + (i % 2) * 6}px`,
            animation: `speaking-bar 0.7s ease-in-out ${i * 0.18}s infinite alternate`,
          }}
        />
      ))}
      <style>{`
        @keyframes speaking-bar {
          from { transform: scaleY(0.3); opacity: 0.5; }
          to   { transform: scaleY(1);   opacity: 1; }
        }
      `}</style>
    </div>
  );
}

// ── Inference / Playground Tab ────────────────────────────────────────────

type InferenceMode = "chat" | "tools" | "raw" | "voice";

interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  isStreaming?: boolean;
  // Agentic tool step data
  toolSteps?: AgenticStepDisplay[];
}

/** Display model for a single agentic step shown in the chat */
interface AgenticStepDisplay {
  stepIndex: number;
  assistantContent: string | null;
  toolCalls: {
    toolCallId: string;
    toolName: string;
    arguments: Record<string, unknown>;
    result: string;
    isError: boolean;
    elapsedMs: number;
  }[];
  finishReason: string;
}

interface SavedConversation {
  id: string;
  title: string;
  messages: ConversationMessage[];
  systemPrompt: string;
  createdAt: number;
  updatedAt: number;
  modelName?: string;
}

const CONVERSATIONS_STORE_KEY = "llm-playground-conversations";
const MAX_CONVERSATIONS = 50;

function makeTitle(firstUserMsg: string): string {
  return firstUserMsg.length > 40
    ? firstUserMsg.slice(0, 40).trimEnd() + "…"
    : firstUserMsg;
}

function loadConversations(): SavedConversation[] {
  try {
    const raw = localStorage.getItem(CONVERSATIONS_STORE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveConversations(convs: SavedConversation[]): void {
  try {
    localStorage.setItem(
      CONVERSATIONS_STORE_KEY,
      JSON.stringify(convs.slice(0, MAX_CONVERSATIONS)),
    );
  } catch {
    // storage full — ignore silently
  }
}

// Model switcher used inside the inference header bar
function ModelSwitcher() {
  const [state, actions] = useLlmContext();
  const { serverStatus, downloadedModels, isStarting, hardwareResult } = state;
  const { startServer, stopServer, detectHardware, listModels } = actions;
  const [switching, setSwitching] = useState(false);

  useEffect(() => {
    listModels();
  }, [listModels]);

  const matchedModel = downloadedModels.find((m) =>
    serverStatus?.model_path?.includes(m.filename)
  );

  const currentModel =
    matchedModel?.name ??
    serverStatus?.model_name ??
    serverStatus?.model_path?.split("/").pop() ??
    "";

  const handleSwitch = async (filename: string) => {
    if (switching || isStarting) return;
    setSwitching(true);
    try {
      const hw = hardwareResult ?? (await detectHardware());
      const ctx = 8192;
      await stopServer();
      await startServer(filename, hw.recommended_gpu_layers, ctx);
    } catch {
      // error surfaced by server tab
    } finally {
      setSwitching(false);
    }
  };

  if (downloadedModels.length <= 1) {
    return (
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <div className="h-1.5 w-1.5 rounded-full bg-green-500 animate-pulse" />
        <span className="font-medium truncate max-w-[160px]">
          {currentModel || "Running"}
        </span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-1.5 rounded-full bg-green-500 animate-pulse shrink-0" />
      <select
        className="text-xs bg-transparent border-0 outline-none cursor-pointer font-medium text-foreground max-w-[200px] truncate"
        value={matchedModel?.filename ?? ""}
        onChange={(e) => handleSwitch(e.target.value)}
        disabled={switching || isStarting}
        title="Switch model"
      >
        {!matchedModel && (
          <option value="" disabled className="bg-background">
            {currentModel || "Select model"}
          </option>
        )}
        {downloadedModels.map((m) => (
          <option key={m.filename} value={m.filename} className="bg-background">
            {m.name} ({m.size_gb} GB)
          </option>
        ))}
      </select>
      {(switching || isStarting) && (
        <span className="text-xs text-muted-foreground animate-pulse">
          switching…
        </span>
      )}
    </div>
  );
}

// ── System Prompt Selector ────────────────────────────────────────────────
// A proper full-width selector for the settings panel.
// Shows a dropdown of all saved + builtin prompts, a textarea for the
// current content, and a link to the full System Prompts management page.

function SystemPromptSelector({
  value,
  onChange,
}: {
  value: string;
  onChange: (content: string) => void;
}) {
  const navigate = useNavigate();
  const [userPrompts, setUserPrompts] = useState<SystemPrompt[]>(() =>
    systemPrompts.list(),
  );
  const [isRefreshing, setIsRefreshing] = useState(false);
  const allPrompts = [...BUILTIN_PROMPTS, ...userPrompts];
  const activePrompt = allPrompts.find((p) => p.content === value);

  const reload = () => {
    setUserPrompts(systemPrompts.list());
  };

  const handleRefresh = () => {
    setIsRefreshing(true);
    reload();
    setTimeout(() => setIsRefreshing(false), 600);
  };

  // Reload on mount and whenever prompts change (cross-tab storage event or same-tab custom event)
  useEffect(() => {
    reload();
    const handleStorage = (e: StorageEvent) => {
      if (e.key === "matrx-system-prompts") reload();
    };
    window.addEventListener("storage", handleStorage);
    window.addEventListener("matrx-prompts-changed", reload);
    return () => {
      window.removeEventListener("storage", handleStorage);
      window.removeEventListener("matrx-prompts-changed", reload);
    };
  }, []);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label className="text-xs font-medium">System Prompt</Label>
        <div className="flex items-center gap-1.5">
          <button
            onClick={handleRefresh}
            title="Refresh prompt list"
            className="text-muted-foreground hover:text-foreground transition-colors"
          >
            <RefreshCw
              className={`h-3 w-3 ${isRefreshing ? "animate-spin" : ""}`}
            />
          </button>
          <button
            className="flex items-center gap-1 text-xs text-primary hover:underline"
            onClick={() => navigate("/system-prompts")}
            title="Open System Prompts library"
          >
            <ExternalLink className="h-3 w-3" />
            Manage
          </button>
        </div>
      </div>

      {/* Prompt picker dropdown */}
      <div className="space-y-1.5">
        <select
          className="w-full h-8 rounded-md border border-input bg-background px-2.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          value={activePrompt?.id ?? "__custom__"}
          onChange={(e) => {
            if (e.target.value === "__custom__") return;
            if (e.target.value === "__manage__") {
              navigate("/system-prompts");
              return;
            }
            const found = allPrompts.find((p) => p.id === e.target.value);
            if (found) onChange(found.content);
          }}
        >
          <option value="__custom__">
            {activePrompt ? activePrompt.name : "— custom / paste below —"}
          </option>
          {userPrompts.length > 0 && (
            <optgroup label="My Prompts">
              {userPrompts.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </optgroup>
          )}
          <optgroup label="Built-in">
            {BUILTIN_PROMPTS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </optgroup>
          <option value="__manage__">→ Open Prompts Library…</option>
        </select>

        <Textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="You are a helpful assistant… (or select from the library above)"
          className="text-xs resize-none h-32"
        />
        <p className="text-[10px] text-muted-foreground text-right">
          {value.length} chars
        </p>
      </div>
    </div>
  );
}

// ── Save-to-Note modal ────────────────────────────────────────────────────

type SaveScope = "message" | "conversation";

interface SaveToNoteModalProps {
  open: boolean;
  onClose: () => void;
  scope: SaveScope;
  content: string; // pre-formatted markdown to save
  userId: string | null;
  enginePort: number | null;
}

function SaveToNoteModal({
  open,
  onClose,
  scope,
  content,
  userId,
  enginePort,
}: SaveToNoteModalProps) {
  const [tree, setTree] = useState<DocTree | null>(null);
  const [loadingTree, setLoadingTree] = useState(false);
  const [selectedFolderId, setSelectedFolderId] = useState<string | null>(null);
  const [selectedFolderName, setSelectedFolderName] =
    useState<string>("General");
  const [noteTitle, setNoteTitle] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [newFolderMode, setNewFolderMode] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [creatingFolder, setCreatingFolder] = useState(false);

  // Load folder tree when modal opens
  useEffect(() => {
    if (!open || !enginePort) return;
    setSaved(false);
    setSaveError(null);
    setNewFolderMode(false);
    setNewFolderName("");
    setNoteTitle(
      scope === "conversation"
        ? `Conversation — ${new Date().toLocaleDateString()}`
        : `Note — ${new Date().toLocaleDateString()}`,
    );

    const uid = userId ?? "local";
    setLoadingTree(true);
    engine
      .getDocTree(uid)
      .then((t) => {
        setTree(t);
        // Pre-select first folder if any
        if (t.folders.length > 0) {
          setSelectedFolderId(t.folders[0].id);
          setSelectedFolderName(t.folders[0].name);
        } else {
          setSelectedFolderId(null);
          setSelectedFolderName("General");
        }
      })
      .catch(() => {
        setTree({ folders: [], total_notes: 0, unfiled_notes: 0 });
      })
      .finally(() => setLoadingTree(false));
  }, [open, enginePort, userId, scope]);

  const handleFolderSelect = (folder: DocFolder | null) => {
    setSelectedFolderId(folder?.id ?? null);
    setSelectedFolderName(folder?.name ?? "General");
  };

  const handleCreateFolder = async () => {
    if (!newFolderName.trim() || !enginePort) return;
    setCreatingFolder(true);
    try {
      const uid = userId ?? "local";
      const created = await engine.createFolder(uid, {
        name: newFolderName.trim(),
      });
      setTree((prev) =>
        prev
          ? { ...prev, folders: [...prev.folders, created] }
          : { folders: [created], total_notes: 0, unfiled_notes: 0 },
      );
      setSelectedFolderId(created.id);
      setSelectedFolderName(created.name);
      setNewFolderMode(false);
      setNewFolderName("");
    } catch {
      // silently ignore — user can retry
    } finally {
      setCreatingFolder(false);
    }
  };

  const handleSave = async () => {
    if (!enginePort || !noteTitle.trim()) return;
    setSaving(true);
    setSaveError(null);
    try {
      const uid = userId ?? "local";
      await engine.createNote(uid, {
        label: noteTitle.trim(),
        content,
        folder_name: selectedFolderName,
        folder_id: selectedFolderId ?? undefined,
      });
      setSaved(true);
      setTimeout(onClose, 900);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Failed to save note.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <BookmarkPlus className="h-4 w-4 text-primary" />
            Save to Notes
          </DialogTitle>
          <DialogDescription className="text-xs">
            {scope === "message"
              ? "Save this response as a note."
              : "Save the full conversation as a note."}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 pt-1">
          {/* Note title */}
          <div className="space-y-1.5">
            <Label className="text-xs">Title</Label>
            <Input
              value={noteTitle}
              onChange={(e) => setNoteTitle(e.target.value)}
              placeholder="Note title…"
              className="text-sm"
              autoFocus
            />
          </div>

          {/* Folder picker */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label className="text-xs">Folder</Label>
              <button
                className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                onClick={() => setNewFolderMode((v) => !v)}
              >
                <FolderPlus className="h-3 w-3" />
                New folder
              </button>
            </div>

            {newFolderMode && (
              <div className="flex gap-2">
                <Input
                  value={newFolderName}
                  onChange={(e) => setNewFolderName(e.target.value)}
                  placeholder="Folder name…"
                  className="text-sm h-8"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void handleCreateFolder();
                    if (e.key === "Escape") {
                      setNewFolderMode(false);
                      setNewFolderName("");
                    }
                  }}
                  autoFocus
                />
                <Button
                  size="sm"
                  className="h-8 text-xs px-3 shrink-0"
                  disabled={!newFolderName.trim() || creatingFolder}
                  onClick={() => void handleCreateFolder()}
                >
                  {creatingFolder ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    "Create"
                  )}
                </Button>
              </div>
            )}

            {loadingTree ? (
              <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
                <Loader2 className="h-3 w-3 animate-spin" />
                Loading folders…
              </div>
            ) : (
              <div className="rounded-md border divide-y max-h-48 overflow-y-auto">
                {/* Unfiled option */}
                <button
                  className={`w-full flex items-center gap-2 px-3 py-2 text-sm text-left transition-colors ${
                    selectedFolderId === null
                      ? "bg-primary/10 text-primary"
                      : "hover:bg-muted/50"
                  }`}
                  onClick={() => handleFolderSelect(null)}
                >
                  <FileText className="h-3.5 w-3.5 shrink-0 opacity-60" />
                  <span className="truncate">Unfiled</span>
                </button>
                {(tree?.folders ?? []).map((f) => (
                  <button
                    key={f.id}
                    className={`w-full flex items-center gap-2 px-3 py-2 text-sm text-left transition-colors ${
                      selectedFolderId === f.id
                        ? "bg-primary/10 text-primary"
                        : "hover:bg-muted/50"
                    }`}
                    onClick={() => handleFolderSelect(f)}
                  >
                    <Folder className="h-3.5 w-3.5 shrink-0 opacity-60" />
                    <span className="truncate">{f.name}</span>
                    {f.note_count !== undefined && (
                      <span className="ml-auto text-[10px] text-muted-foreground">
                        {f.note_count}
                      </span>
                    )}
                  </button>
                ))}
                {(tree?.folders ?? []).length === 0 && !loadingTree && (
                  <p className="px-3 py-2 text-xs text-muted-foreground">
                    No folders yet — note will be saved as Unfiled.
                  </p>
                )}
              </div>
            )}
          </div>

          {saveError && (
            <p className="text-xs text-destructive flex items-center gap-1.5">
              <AlertCircle className="h-3.5 w-3.5 shrink-0" />
              {saveError}
            </p>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <Button
              variant="ghost"
              size="sm"
              className="text-xs"
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              className="text-xs min-w-[90px]"
              disabled={saving || saved || !noteTitle.trim()}
              onClick={() => void handleSave()}
            >
              {saved ? (
                <>
                  <Check className="h-3.5 w-3.5 mr-1.5" />
                  Saved!
                </>
              ) : saving ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                  Saving…
                </>
              ) : (
                <>
                  <BookmarkPlus className="h-3.5 w-3.5 mr-1.5" />
                  Save Note
                </>
              )}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function InferenceTab() {
  const [state, actions] = useLlmContext();
  const { serverStatus, isStarting, hardwareResult, downloadedModels } = state;
  const { startServer, stopServer, detectHardware, listModels } = actions;
  const { user } = useAuth();
  const userId = user?.id ?? null;

  // ── Prompt list (kept in sync with localStorage) ──────────────────────
  const [inferenceUserPrompts, setInferenceUserPrompts] = useState<
    SystemPrompt[]
  >(() => systemPrompts.list());
  useEffect(() => {
    const reloadPrompts = () => setInferenceUserPrompts(systemPrompts.list());
    reloadPrompts();
    const handleStorage = (e: StorageEvent) => {
      if (e.key === "matrx-system-prompts") reloadPrompts();
    };
    window.addEventListener("storage", handleStorage);
    window.addEventListener("matrx-prompts-changed", reloadPrompts);
    return () => {
      window.removeEventListener("storage", handleStorage);
      window.removeEventListener("matrx-prompts-changed", reloadPrompts);
    };
  }, []);

  // ── Conversation list ──────────────────────────────────────────────────
  const [conversations, setConversations] = useState<SavedConversation[]>(() =>
    loadConversations(),
  );
  const [activeConvId, setActiveConvId] = useState<string | null>(
    () => loadConversations()[0]?.id ?? null,
  );

  // ── Active conversation state ──────────────────────────────────────────
  const activeConv = conversations.find((c) => c.id === activeConvId) ?? null;
  const messages: ConversationMessage[] = activeConv?.messages ?? [];
  const [input, setInput] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stopRef = useRef(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollAreaRef = useRef<HTMLDivElement>(null);
  const userScrolledUpRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // ── Message actions state ─────────────────────────────────────────────
  const [editingMsgId, setEditingMsgId] = useState<string | null>(null);
  const [editingContent, setEditingContent] = useState("");
  const editTextareaRef = useRef<HTMLTextAreaElement>(null);
  const [reactions, setReactions] = useState<
    Record<string, "up" | "down" | null>
  >({});

  // ── Save-to-note modal state ──────────────────────────────────────────
  const [saveNoteOpen, setSaveNoteOpen] = useState(false);
  const [saveNoteScope, setSaveNoteScope] = useState<SaveScope>("message");
  const [saveNoteContent, setSaveNoteContent] = useState("");

  // ── TTS read-aloud ────────────────────────────────────────────────────
  const [readingMsgId, setReadingMsgId] = useState<string | null>(null);
  const [autoReadAloud, setAutoReadAloud] = useState(false);

  // ── Dictation mic (standalone, separate from voice chat mode) ─────────
  // Tracks whether the standalone mic button (in the toolbar) started recording
  const dictationActiveRef = useRef(false);
  const dictationBaseTranscriptRef = useRef("");
  // Read-aloud is optional here. Use the non-throwing accessor so the hook is
  // always called unconditionally (rules-of-hooks safe) — the previous
  // try/catch around useTtsApp() both risked a rules-of-hooks violation and
  // hid a missing provider silently. When TTS is unavailable we log loudly
  // once and render the read-aloud controls disabled with a tooltip.
  const ttsCtx = useTtsAppOptional();
  const ttsState: ReturnType<typeof useTtsApp>[0] | null = ttsCtx?.[0] ?? null;
  const ttsActions: ReturnType<typeof useTtsApp>[1] | null =
    ttsCtx?.[1] ?? null;
  const ttsUnavailableLoggedRef = useRef(false);
  useEffect(() => {
    if (!ttsCtx && !ttsUnavailableLoggedRef.current) {
      ttsUnavailableLoggedRef.current = true;
      logError(
        "local-models",
        "read-aloud unavailable",
        new Error(
          "TtsProvider is not mounted — chat read-aloud controls are disabled.",
        ),
      );
    }
  }, [ttsCtx]);
  // The currently-streaming assistant message (for real-time TTS sentence chunking)
  const streamingMsg =
    messages.find((m) => m.role === "assistant" && m.isStreaming) ?? null;
  const chatTts = useChatTts(ttsActions, streamingMsg, isGenerating);
  const ttsPlaybackState = ttsState?.playbackState ?? "idle";

  // ── Transcription (singleton) ─────────────────────────────────────────
  const { state: transcriptionState, actions: transcriptionActions } =
    useTranscriptionApp();

  // ── Wake word context (read-only) ─────────────────────────────────────
  const { state: wwState } = useWakeWordContext();

  // ── Voice chat state machine ──────────────────────────────────────────
  // Stable getter for the last completed assistant message — used by the hook
  // to start TTS after generation finishes.  We use a ref so the hook never
  // holds a stale closure over `messages`.
  const lastAssistantContentRef = useRef<string | null>(null);
  useEffect(() => {
    if (!isGenerating) {
      const last = [...messages]
        .reverse()
        .find((m) => m.role === "assistant" && !m.isStreaming);
      lastAssistantContentRef.current = last?.content ?? null;
    }
  }, [isGenerating, messages]);

  const [voiceChatState, voiceChatActions] = useVoiceChat({
    transcriptionState,
    transcriptionActions,
    chatTts,
    ttsPlaybackState,
    onSend: (text: string) => {
      setInput(text);
      // Use a micro-task so setInput propagates before handleSend reads it
      setTimeout(() => {
        void handleSendText(text);
      }, 0);
    },
    onStartTts: () => lastAssistantContentRef.current,
    isGenerating,
    modelReady: !!transcriptionState.activeModel,
  });

  // Auto-read aloud: start TTS when generation finishes
  const prevIsGeneratingRef = useRef(false);
  useEffect(() => {
    const wasGenerating = prevIsGeneratingRef.current;
    prevIsGeneratingRef.current = isGenerating;

    if (!wasGenerating && isGenerating && autoReadAloud) {
      // LLM just started — reset readingMsgId so the stop button appears
      setReadingMsgId("auto");
    }

    if (wasGenerating && !isGenerating) {
      if (autoReadAloud) {
        // Generation just finished — speak the last assistant message
        const lastAssistant = [...messages]
          .reverse()
          .find((m) => m.role === "assistant");
        if (lastAssistant?.content) {
          setReadingMsgId(lastAssistant.id);
          chatTts.readCompleteMessage(lastAssistant.content);
        } else {
          // Nothing to read — clear state
          chatTts.stopReadAloud();
          setReadingMsgId(null);
        }
      } else {
        // Generation stopped but autoReadAloud is off — stop any silent TTS
        if (chatTts.isReadingAloud) {
          chatTts.stopReadAloud();
          setReadingMsgId(null);
        }
      }
    }
  }, [isGenerating]); // eslint-disable-line react-hooks/exhaustive-deps

  // When TTS finishes, clear the readingMsgId
  useEffect(() => {
    if (!chatTts.isReadingAloud && readingMsgId !== null) {
      setReadingMsgId(null);
    }
  }, [chatTts.isReadingAloud, readingMsgId]);

  // Dictation: when standalone mic recording stops and tail is done, append delta to input
  useEffect(() => {
    if (!dictationActiveRef.current) return;
    if (transcriptionState.isRecording || transcriptionState.isProcessingTail)
      return;
    // Recording fully finished — append delta
    dictationActiveRef.current = false;
    const delta = transcriptionState.fullTranscript
      .slice(dictationBaseTranscriptRef.current.length)
      .trim();
    if (delta) {
      setInput((prev) => (prev ? prev + " " + delta : delta));
    }
  }, [
    transcriptionState.isRecording,
    transcriptionState.isProcessingTail,
    transcriptionState.fullTranscript,
  ]);

  // ── Voice assistant system prompt ────────────────────────────────────────
  // When entering voice mode (wake word or clicking the Speech to Text tab), automatically
  // apply the configured voice assistant system prompt.  When leaving, restore
  // the previous prompt (if voiceRestorePromptOnExit is set).
  // We use refs to avoid forward-reference issues with systemPrompt/setSystemPrompt
  // which are declared later in the component body.
  const preVoiceSystemPromptRef = useRef<string | null>(null);
  // Stable ref to setSystemPrompt so callbacks don't go stale
  const setSystemPromptRef = useRef<((v: string) => void) | null>(null);
  const getCurrentSystemPromptRef = useRef<(() => string) | null>(null);

  const applyVoiceSystemPrompt = useCallback(async () => {
    const settings = await loadSettings();
    const promptId =
      settings.voiceAssistantSystemPromptId || "builtin-voice-assistant";
    const content = systemPrompts.resolve(promptId) ?? "";
    if (content && setSystemPromptRef.current) {
      // Only save pre-voice prompt once per activation
      if (preVoiceSystemPromptRef.current === null) {
        preVoiceSystemPromptRef.current =
          getCurrentSystemPromptRef.current?.() ?? "";
      }
      setSystemPromptRef.current(content);
    }
  }, []);

  const restoreVoiceSystemPrompt = useCallback(async () => {
    const settings = await loadSettings();
    if (
      settings.voiceRestorePromptOnExit &&
      preVoiceSystemPromptRef.current !== null &&
      setSystemPromptRef.current
    ) {
      setSystemPromptRef.current(preVoiceSystemPromptRef.current);
    }
    preVoiceSystemPromptRef.current = null;
  }, []);

  // ── Voice popup (shown from any tab when wake word fires or voice mode active) ─
  const [voicePopupVisible, setVoicePopupVisible] = useState(false);

  // Track current assistant content for the popup (streaming + completed)
  const [popupAssistantContent, setPopupAssistantContent] = useState<
    string | null
  >(null);
  useEffect(() => {
    if (!voicePopupVisible) return;
    // Show streaming content live, then the completed message
    const streaming = messages.find(
      (m) => m.role === "assistant" && m.isStreaming,
    );
    if (streaming) {
      setPopupAssistantContent(streaming.content);
      return;
    }
    const last = [...messages].reverse().find((m) => m.role === "assistant");
    setPopupAssistantContent(last?.content ?? null);
  }, [voicePopupVisible, messages, isGenerating]);

  // pendingStartRecordingRef: set to true when we want to start recording
  // as soon as voice chat becomes active.  Avoids the 50ms setTimeout race
  // where activate() might not have committed before toggleRecording() fires.
  const pendingStartRecordingRef = useRef(false);

  // When isActive flips to true and we have a pending start, kick off recording.
  useEffect(() => {
    if (voiceChatState.isActive && pendingStartRecordingRef.current) {
      pendingStartRecordingRef.current = false;
      voiceChatActions.toggleRecording();
    }
  }, [voiceChatState.isActive, voiceChatActions]);

  // Wake word → show popup + switch to Speech to Text tab + start recording
  const prevWwUiModeRef = useRef(wwState.uiMode);
  useEffect(() => {
    const prev = prevWwUiModeRef.current;
    prevWwUiModeRef.current = wwState.uiMode;
    if (prev !== "active" && wwState.uiMode === "active") {
      // Show the floating popup so the user sees live activity from any tab
      setVoicePopupVisible(true);
      // Switch to the voice tab so the full UI is visible
      setMode("voice");
      // Apply the voice assistant system prompt
      void applyVoiceSystemPrompt();
      if (voiceChatState.isActive) {
        // Already active — start recording immediately
        voiceChatActions.toggleRecording();
      } else {
        // Activate first; the pendingStart effect above will kick off
        // recording once isActive commits (no magic number timeout).
        pendingStartRecordingRef.current = true;
        voiceChatActions.activate();
      }
    }
    // When wake word goes back to listening/idle, hide the popup
    if (prev === "active" && wwState.uiMode !== "active") {
      setVoicePopupVisible(false);
    }
  }, [wwState.uiMode]); // eslint-disable-line react-hooks/exhaustive-deps

  // A ref we update with the current mode so the popup-visibility effect
  // below can read it without `mode` being in scope yet.
  const modeRef = useRef<string>("chat");
  const [switchingModel, setSwitchingModel] = useState(false);
  const [showPromptPicker, setShowPromptPicker] = useState(false);
  const [showModelPicker, setShowModelPicker] = useState(false);
  const promptPickerRef = useRef<HTMLDivElement>(null);
  const modelPickerRef = useRef<HTMLDivElement>(null);

  // ── Settings panel ─────────────────────────────────────────────────────
  const [showSettings, setShowSettings] = useState(false);
  const [systemPrompt, setSystemPrompt] = useState(
    activeConv?.systemPrompt ?? "",
  );
  // Wire the voice system prompt helpers (declared earlier) to these setters.
  setSystemPromptRef.current = setSystemPrompt;
  getCurrentSystemPromptRef.current = () => systemPrompt;
  const [temperature, setTemperature] = useState(0.7);
  const [topP, setTopP] = useState(0.8);
  const [maxTokens, setMaxTokens] = useState(8000);
  const [mode, setMode] = useState<InferenceMode>("chat");
  modeRef.current = mode;

  // Show popup whenever voice mode is active and voice chat is running
  useEffect(() => {
    if (mode === "voice" && voiceChatState.isActive) {
      setVoicePopupVisible(true);
    } else if (wwState.uiMode !== "active") {
      // Hide popup only when not triggered by wake word
      setVoicePopupVisible(false);
    }
  }, [mode, voiceChatState.isActive, wwState.uiMode]);

  // ── Agentic tools mode state ──────────────────────────────────────────
  const [toolSchemasByCategory, setToolSchemasByCategory] = useState<
    Record<string, EngineToolSchema[]>
  >({});
  const [loadingToolSchemas, setLoadingToolSchemas] = useState(false);
  const [toolSchemasError, setToolSchemasError] = useState<string | null>(null);
  // Selected tool names for the agentic loop
  const [selectedToolNames, setSelectedToolNames] = useState<Set<string>>(
    new Set(),
  );
  const [toolSearchQuery, setToolSearchQuery] = useState("");
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(
    new Set(),
  );
  // Agentic chat messages for tools mode
  const [agentMessages, setAgentMessages] = useState<ConversationMessage[]>([]);
  // Full message history (including tool result messages) for accurate context on follow-up turns
  const agentHistoryRef = useRef<ChatMessage[]>([]);
  const [agentInput, setAgentInput] = useState("");
  const [isAgentRunning, setIsAgentRunning] = useState(false);
  const [agentError, setAgentError] = useState<string | null>(null);
  const [maxAgentSteps, setMaxAgentSteps] = useState(10);
  const agentAbortRef = useRef<AbortController | null>(null);
  const agentScrollRef = useRef<HTMLDivElement>(null);

  // Load tool schemas when entering tools mode
  useEffect(() => {
    if (mode !== "tools" || Object.keys(toolSchemasByCategory).length > 0)
      return;
    setLoadingToolSchemas(true);
    setToolSchemasError(null);
    engine
      .getToolSchemasByCategory()
      .then((schemas) => {
        setToolSchemasByCategory(schemas);
        // Expand first two categories by default
        const cats = Object.keys(schemas).sort();
        setExpandedCategories(new Set(cats.slice(0, 2)));
      })
      .catch((e) => {
        setToolSchemasError(
          e instanceof Error ? e.message : "Failed to load tools",
        );
      })
      .finally(() => setLoadingToolSchemas(false));
  }, [mode]);

  const toggleToolSelection = (toolName: string) => {
    setSelectedToolNames((prev) => {
      const next = new Set(prev);
      if (next.has(toolName)) {
        next.delete(toolName);
      } else {
        next.add(toolName);
      }
      return next;
    });
  };

  const toggleCategory = (cat: string) => {
    setExpandedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) {
        next.delete(cat);
      } else {
        next.add(cat);
      }
      return next;
    });
  };

  const selectAllInCategory = (cat: string) => {
    const tools = toolSchemasByCategory[cat] ?? [];
    setSelectedToolNames((prev) => {
      const next = new Set(prev);
      tools.forEach((t) => next.add(t.name));
      return next;
    });
  };

  const deselectAllInCategory = (cat: string) => {
    const tools = toolSchemasByCategory[cat] ?? [];
    setSelectedToolNames((prev) => {
      const next = new Set(prev);
      tools.forEach((t) => next.delete(t.name));
      return next;
    });
  };

  const clearAllTools = () => setSelectedToolNames(new Set());

  /** Convert selected engine tool schemas to llama-server ToolDefinition format. */
  const buildToolDefinitions = (): ToolDefinition[] => {
    const allSchemas = Object.values(toolSchemasByCategory).flat();
    return allSchemas
      .filter((s) => selectedToolNames.has(s.name))
      .map((s) => ({
        type: "function" as const,
        function: {
          name: s.name,
          description: s.description,
          parameters: {
            type: "object" as const,
            properties: Object.fromEntries(
              Object.entries(s.input_schema.properties).map(([k, v]) => [
                k,
                { type: v.type, description: v.description ?? k },
              ]),
            ),
            required: s.input_schema.required,
          },
        },
      }));
  };

  const handleAgentAbort = () => {
    if (agentAbortRef.current) {
      agentAbortRef.current.abort();
    }
  };

  const handleAgentSend = async () => {
    if (!port || !agentInput.trim() || isAgentRunning) return;
    if (selectedToolNames.size === 0) {
      setAgentError("Select at least one tool from the panel before sending.");
      return;
    }

    const userMsg = agentInput.trim();
    setAgentInput("");
    setAgentError(null);

    const userMsgEntry: ConversationMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: userMsg,
    };
    const assistantPlaceholderId = crypto.randomUUID();
    const assistantEntry: ConversationMessage = {
      id: assistantPlaceholderId,
      role: "assistant",
      content: "",
      isStreaming: true,
      toolSteps: [],
    };

    setAgentMessages((prev) => [...prev, userMsgEntry, assistantEntry]);
    setIsAgentRunning(true);

    const controller = new AbortController();
    agentAbortRef.current = controller;

    const tools = buildToolDefinitions();

    // Use the accumulated full history (includes tool result messages) so the
    // model has proper context on follow-up turns.
    const chatHistory: ChatMessage[] = [
      ...agentHistoryRef.current,
      { role: "user" as const, content: userMsg },
    ];

    const accSteps: AgenticStepDisplay[] = [];

    try {
      const result = await runAgenticLoop(
        port,
        chatHistory,
        tools,
        async (toolName, args) => {
          try {
            const res = await engine.invokeTool(
              toolName,
              args as Record<string, unknown>,
            );
            return {
              output: res.output ?? "",
              isError: res.type === "error",
            };
          } catch (e) {
            return {
              output: `Failed to invoke ${toolName}: ${(e as Error).message}`,
              isError: true,
            };
          }
        },
        (step) => {
          accSteps.push(step);
          setAgentMessages((prev) =>
            prev.map((m) =>
              m.id === assistantPlaceholderId
                ? {
                    ...m,
                    content: step.assistantContent ?? m.content,
                    toolSteps: [...accSteps],
                  }
                : m,
            ),
          );
          // Scroll to bottom after each step
          setTimeout(
            () =>
              agentScrollRef.current?.scrollTo({
                top: agentScrollRef.current.scrollHeight,
                behavior: "smooth",
              }),
            50,
          );
        },
        controller.signal,
        maxAgentSteps,
      );

      const finalMsg =
        result.finalContent ||
        (result.stoppedByUser
          ? "⚠️ Stopped by user."
          : result.stoppedByMaxSteps
            ? `⚠️ Reached the maximum of ${maxAgentSteps} steps.`
            : "Done.");

      // Persist the full history (including tool results) for accurate context
      // on subsequent turns. Also append the final assistant message.
      agentHistoryRef.current = [...result.fullHistory];

      setAgentMessages((prev) =>
        prev.map((m) =>
          m.id === assistantPlaceholderId
            ? {
                ...m,
                content: finalMsg,
                isStreaming: false,
                toolSteps: accSteps,
              }
            : m,
        ),
      );
    } catch (e) {
      const errMsg = e instanceof Error ? e.message : String(e);
      setAgentError(errMsg);
      setAgentMessages((prev) =>
        prev.map((m) =>
          m.id === assistantPlaceholderId
            ? { ...m, content: "", isStreaming: false, toolSteps: accSteps }
            : m,
        ),
      );
    } finally {
      setIsAgentRunning(false);
      agentAbortRef.current = null;
    }
  };

  // Tool / Raw state
  const [rawJson, setRawJson] = useState(
    JSON.stringify(
      {
        model: "local",
        messages: [{ role: "user", content: "Hello!" }],
        max_tokens: 256,
        temperature: 0.7,
        stream: false,
      },
      null,
      2,
    ),
  );
  const [rawResult, setRawResult] = useState<string | null>(null);

  // ── Server launch state ────────────────────────────────────────────────
  const [gpuLayersOverride, setGpuLayersOverride] = useState(99);
  const [gpuLayersRaw, setGpuLayersRaw] = useState("99");
  const [contextLengthOverride, setContextLengthOverride] = useState(8192);
  const [selectedModel, setSelectedModel] = useState("");

  useEffect(() => {
    listModels();
  }, [listModels]);

  useEffect(() => {
    if (hardwareResult?.recommended_gpu_layers !== undefined) {
      setGpuLayersOverride(hardwareResult.recommended_gpu_layers);
      setGpuLayersRaw(String(hardwareResult.recommended_gpu_layers));
    }
  }, [hardwareResult?.recommended_gpu_layers]);

  // Sync system prompt when switching conversations
  useEffect(() => {
    setSystemPrompt(activeConv?.systemPrompt ?? "");
  }, [activeConvId]);

  const port = serverStatus?.running ? serverStatus.port : null;

  // ── Save-to-note helpers ───────────────────────────────────────────────
  const openSaveNote = (scope: SaveScope, msgContent?: string) => {
    if (scope === "message" && msgContent !== undefined) {
      setSaveNoteContent(msgContent);
    } else {
      // Format the full conversation as markdown
      const model = serverStatus?.model_name ?? "On-device model";
      const lines: string[] = [
        `# ${activeConv?.title ?? "Conversation"}`,
        ``,
        `*Model: ${model} — ${new Date().toLocaleString()}*`,
        ``,
        `---`,
        ``,
        ...messages.flatMap((m) => [
          `**${m.role === "user" ? "You" : "Assistant"}:**`,
          ``,
          m.content,
          ``,
          `---`,
          ``,
        ]),
      ];
      setSaveNoteContent(lines.join("\n"));
    }
    setSaveNoteScope(scope);
    setSaveNoteOpen(true);
  };

  // ── Conversation helpers ───────────────────────────────────────────────
  const persistConvs = (updated: SavedConversation[]) => {
    setConversations(updated);
    saveConversations(updated);
  };

  const newConversation = () => {
    const conv: SavedConversation = {
      id: crypto.randomUUID(),
      title: "New conversation",
      messages: [],
      systemPrompt: "",
      createdAt: Date.now(),
      updatedAt: Date.now(),
      modelName: serverStatus?.model_name,
    };
    const updated = [conv, ...conversations];
    persistConvs(updated);
    setActiveConvId(conv.id);
    setSystemPrompt("");
    setInput("");
    setError(null);
  };

  const deleteConversation = (id: string) => {
    const updated = conversations.filter((c) => c.id !== id);
    persistConvs(updated);
    if (activeConvId === id) {
      setActiveConvId(updated[0]?.id ?? null);
    }
  };

  const updateMessages = (
    id: string,
    msgs: ConversationMessage[],
    sysPrompt?: string,
  ) => {
    setConversations((prev) => {
      const updated = prev.map((c) =>
        c.id === id
          ? {
              ...c,
              messages: msgs,
              systemPrompt:
                sysPrompt !== undefined ? sysPrompt : c.systemPrompt,
              title: msgs.find((m) => m.role === "user")
                ? makeTitle(msgs.find((m) => m.role === "user")!.content)
                : c.title,
              updatedAt: Date.now(),
            }
          : c,
      );
      saveConversations(updated);
      return updated;
    });
  };

  const scrollToBottom = (force = false) => {
    if (!force && userScrolledUpRef.current) return;
    setTimeout(
      () => bottomRef.current?.scrollIntoView({ behavior: "smooth" }),
      50,
    );
  };

  // ── Auto-resize composer textarea ────────────────────────────────────
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 168)}px`;
  }, [input]);

  // ── Auto-resize edit textarea (grows to fit full content) ─────────────
  useEffect(() => {
    const el = editTextareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [editingContent, editingMsgId]);

  // ── Sync readingMsgId with TTS playing state ──────────────────────────
  useEffect(() => {
    if (!chatTts.isReadingAloud && readingMsgId) {
      setReadingMsgId(null);
    }
  }, [chatTts.isReadingAloud, readingMsgId]);

  // ── Detect user scrolling up to pause auto-scroll ─────────────────────
  useEffect(() => {
    const root = scrollAreaRef.current;
    if (!root) return;
    const viewport = root.querySelector(
      "[data-radix-scroll-area-viewport]",
    ) as HTMLElement | null;
    if (!viewport) return;
    const onScroll = () => {
      const distFromBottom =
        viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
      userScrolledUpRef.current = distFromBottom > 80;
    };
    viewport.addEventListener("scroll", onScroll, { passive: true });
    return () => viewport.removeEventListener("scroll", onScroll);
  }, [mode]); // re-attach when switching to chat mode

  // ── Close pickers on outside click ────────────────────────────────────
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (
        promptPickerRef.current &&
        !promptPickerRef.current.contains(e.target as Node)
      ) {
        setShowPromptPicker(false);
      }
      if (
        modelPickerRef.current &&
        !modelPickerRef.current.contains(e.target as Node)
      ) {
        setShowModelPicker(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // ── Edit & resend a user message ──────────────────────────────────────
  const handleEditAndResend = async (msgId: string, newContent: string) => {
    if (!port || !newContent.trim() || isGenerating) return;
    setEditingMsgId(null);
    setEditingContent("");
    const msgIndex = messages.findIndex((m) => m.id === msgId);
    if (msgIndex === -1) return;

    const truncated = messages.slice(0, msgIndex);
    const editedUserMsg: ConversationMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: newContent.trim(),
    };
    const assistantId = crypto.randomUUID();
    const newMessages = [
      ...truncated,
      editedUserMsg,
      {
        id: assistantId,
        role: "assistant" as const,
        content: "",
        isStreaming: true,
      },
    ];

    if (activeConvId) updateMessages(activeConvId, newMessages, systemPrompt);
    setIsGenerating(true);
    stopRef.current = false;
    userScrolledUpRef.current = false;
    scrollToBottom(true);

    const chatMessages: ChatMessage[] = [
      ...(systemPrompt.trim()
        ? [{ role: "system" as const, content: systemPrompt }]
        : []),
      ...truncated.map((m) => ({
        role: m.role as "user" | "assistant",
        content: m.content,
      })),
      { role: "user" as const, content: newContent.trim() },
    ];

    let accumulated = "";
    try {
      const stream = streamCompletion(port, chatMessages, {
        temperature,
        maxTokens,
      });
      for await (const token of stream) {
        if (stopRef.current) break;
        accumulated += token;
        setConversations((prev) => {
          const updated = prev.map((c) =>
            c.id === activeConvId
              ? {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === assistantId ? { ...m, content: accumulated } : m,
                  ),
                }
              : c,
          );
          saveConversations(updated);
          return updated;
        });
        scrollToBottom();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setConversations((prev) => {
        const updated = prev.map((c) =>
          c.id === activeConvId
            ? {
                ...c,
                messages: c.messages.map((m) =>
                  m.id === assistantId ? { ...m, isStreaming: false } : m,
                ),
              }
            : c,
        );
        saveConversations(updated);
        return updated;
      });
      setIsGenerating(false);
    }
  };

  // ── Retry an assistant message (re-run the preceding user turn) ───────
  const handleRetry = async (assistantMsgId: string) => {
    if (!port || isGenerating || !activeConvId) return;
    const msgIndex = messages.findIndex((m) => m.id === assistantMsgId);
    if (msgIndex === -1) return;

    // Everything before the assistant message — find the preceding user turn
    const historyBeforeAssistant = messages.slice(0, msgIndex);
    const precedingUserMsg = [...historyBeforeAssistant]
      .reverse()
      .find((m) => m.role === "user");
    if (!precedingUserMsg) return;

    // History sent to the model: all messages up to (not including) the assistant msg
    const chatMessages: ChatMessage[] = [
      ...(systemPrompt.trim()
        ? [{ role: "system" as const, content: systemPrompt }]
        : []),
      ...historyBeforeAssistant.map((m) => ({
        role: m.role as "user" | "assistant",
        content: m.content,
      })),
    ];

    // Replace the existing assistant message with an empty streaming placeholder
    const newMessages = messages.map((m) =>
      m.id === assistantMsgId ? { ...m, content: "", isStreaming: true } : m,
    );
    updateMessages(activeConvId, newMessages, systemPrompt);
    setIsGenerating(true);
    stopRef.current = false;
    userScrolledUpRef.current = false;
    scrollToBottom(true);

    let accumulated = "";
    try {
      const stream = streamCompletion(port, chatMessages, {
        temperature,
        maxTokens,
      });
      for await (const token of stream) {
        if (stopRef.current) break;
        accumulated += token;
        setConversations((prev) => {
          const updated = prev.map((c) =>
            c.id === activeConvId
              ? {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === assistantMsgId
                      ? { ...m, content: accumulated }
                      : m,
                  ),
                }
              : c,
          );
          saveConversations(updated);
          return updated;
        });
        scrollToBottom();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setConversations((prev) => {
        const updated = prev.map((c) =>
          c.id === activeConvId
            ? {
                ...c,
                messages: c.messages.map((m) =>
                  m.id === assistantMsgId ? { ...m, isStreaming: false } : m,
                ),
              }
            : c,
        );
        saveConversations(updated);
        return updated;
      });
      setIsGenerating(false);
    }
  };

  // ── Edit assistant message (in-place correction) ──────────────────────
  const handleEditAssistant = (msgId: string, newContent: string) => {
    if (!activeConvId) return;
    setEditingMsgId(null);
    setEditingContent("");
    const updatedMsgs = messages.map((m) =>
      m.id === msgId ? { ...m, content: newContent } : m,
    );
    updateMessages(activeConvId, updatedMsgs, systemPrompt);
  };

  // ── Fork conversation from a specific assistant message ───────────────
  const forkFromMessage = (msgId: string) => {
    if (!activeConv) return;
    const msgIndex = messages.findIndex((m) => m.id === msgId);
    if (msgIndex === -1) return;
    const forkedMessages = messages.slice(0, msgIndex + 1).map((m) => ({
      ...m,
      id: crypto.randomUUID(),
      isStreaming: false,
    }));
    const fork: SavedConversation = {
      id: crypto.randomUUID(),
      title: activeConv.title + " (fork)",
      messages: forkedMessages,
      systemPrompt: activeConv.systemPrompt,
      createdAt: Date.now(),
      updatedAt: Date.now(),
      modelName: activeConv.modelName,
    };
    const updated = [fork, ...conversations];
    persistConvs(updated);
    setActiveConvId(fork.id);
  };

  // ── Toggle reaction ───────────────────────────────────────────────────
  const toggleReaction = (msgId: string, type: "up" | "down") => {
    setReactions((prev) => ({
      ...prev,
      [msgId]: prev[msgId] === type ? null : type,
    }));
  };

  // ── Switch model from composer ────────────────────────────────────────
  const handleModelSwitch = async (filename: string) => {
    if (switchingModel || isStarting) return;
    setShowModelPicker(false);
    setSwitchingModel(true);
    try {
      const hw = hardwareResult ?? (await detectHardware());
      const ctx = 8192;
      await stopServer();
      await startServer(filename, hw.recommended_gpu_layers, ctx);
    } catch {
      // error surfaced elsewhere
    } finally {
      setSwitchingModel(false);
    }
  };

  // Prompt/model display helpers
  const allPrompts = [...BUILTIN_PROMPTS, ...inferenceUserPrompts];
  const activePromptName =
    allPrompts.find((p) => p.content === systemPrompt)?.name ?? null;

  const matchedModel = downloadedModels.find((m) =>
    serverStatus?.model_path?.includes(m.filename)
  );
  const currentModelName =
    matchedModel?.name ??
    serverStatus?.model_name ??
    serverStatus?.model_path?.split("/").pop() ??
    "Model";

  // ── Send message ───────────────────────────────────────────────────────
  const handleSend = async () => {
    if (!port || !input.trim() || isGenerating) return;
    const userMsg = input.trim();
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    setInput("");
    setError(null);
    stopRef.current = false;

    // Ensure we have an active conversation
    let convId = activeConvId;
    if (!convId) {
      const conv: SavedConversation = {
        id: crypto.randomUUID(),
        title: makeTitle(userMsg),
        messages: [],
        systemPrompt,
        createdAt: Date.now(),
        updatedAt: Date.now(),
        modelName: serverStatus?.model_name,
      };
      persistConvs([conv, ...conversations]);
      convId = conv.id;
      setActiveConvId(convId);
    }

    const chatMessages: ChatMessage[] = [
      ...(systemPrompt.trim()
        ? [{ role: "system" as const, content: systemPrompt }]
        : []),
      ...messages.map((m) => ({
        role: m.role as "user" | "assistant",
        content: m.content,
      })),
      { role: "user" as const, content: userMsg },
    ];

    const assistantId = crypto.randomUUID();
    const newMessages: ConversationMessage[] = [
      ...messages,
      { id: crypto.randomUUID(), role: "user", content: userMsg },
      { id: assistantId, role: "assistant", content: "", isStreaming: true },
    ];

    updateMessages(convId, newMessages, systemPrompt);
    setIsGenerating(true);
    userScrolledUpRef.current = false;
    scrollToBottom(true);

    let accumulated = "";
    try {
      const stream = streamCompletion(port, chatMessages, {
        temperature,
        maxTokens,
      });
      for await (const token of stream) {
        if (stopRef.current) break;
        accumulated += token;
        setConversations((prev) => {
          const updated = prev.map((c) =>
            c.id === convId
              ? {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === assistantId ? { ...m, content: accumulated } : m,
                  ),
                }
              : c,
          );
          saveConversations(updated);
          return updated;
        });
        scrollToBottom();
      }
    } catch (e) {
      if (e instanceof ContextSizeError) {
        // Remove the empty assistant placeholder so the conversation stays clean
        setConversations((prev) => {
          const updated = prev.map((c) =>
            c.id === convId
              ? {
                  ...c,
                  messages: c.messages.filter((m) => m.id !== assistantId),
                }
              : c,
          );
          saveConversations(updated);
          return updated;
        });
        const overBy = e.promptTokens - e.contextSize;
        setError(
          `Your conversation is too long for this model's context window.\n\n` +
            `• Used: ${e.promptTokens.toLocaleString()} tokens  •  Limit: ${e.contextSize.toLocaleString()} tokens  •  Over by: ${overBy.toLocaleString()} tokens\n\n` +
            `To fix this, you can:\n` +
            `  1. Start a new conversation (the + button above).\n` +
            `  2. Shorten your message or clear some history.\n` +
            `  3. Increase the Context Length in Server Settings (currently ${contextLengthOverride.toLocaleString()}) and restart the server.`,
        );
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setConversations((prev) => {
        const updated = prev.map((c) =>
          c.id === convId
            ? {
                ...c,
                messages: c.messages.map((m) =>
                  m.id === assistantId ? { ...m, isStreaming: false } : m,
                ),
              }
            : c,
        );
        saveConversations(updated);
        return updated;
      });
      setIsGenerating(false);
    }
  };

  // Variant of handleSend that accepts text directly (used by voice chat hook)
  const handleSendText = async (text: string) => {
    if (!port || !text.trim() || isGenerating) return;
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    setInput("");
    setError(null);
    stopRef.current = false;

    let convId = activeConvId;
    if (!convId) {
      const conv: SavedConversation = {
        id: crypto.randomUUID(),
        title: makeTitle(text),
        messages: [],
        systemPrompt,
        createdAt: Date.now(),
        updatedAt: Date.now(),
        modelName: serverStatus?.model_name,
      };
      persistConvs([conv, ...conversations]);
      convId = conv.id;
      setActiveConvId(convId);
    }

    const chatMessages: ChatMessage[] = [
      ...(systemPrompt.trim()
        ? [{ role: "system" as const, content: systemPrompt }]
        : []),
      ...messages.map((m) => ({
        role: m.role as "user" | "assistant",
        content: m.content,
      })),
      { role: "user" as const, content: text },
    ];

    const assistantId = crypto.randomUUID();
    const newMessages: ConversationMessage[] = [
      ...messages,
      { id: crypto.randomUUID(), role: "user", content: text },
      { id: assistantId, role: "assistant", content: "", isStreaming: true },
    ];

    updateMessages(convId, newMessages, systemPrompt);
    setIsGenerating(true);
    userScrolledUpRef.current = false;
    scrollToBottom(true);

    let accumulated = "";
    try {
      const stream = streamCompletion(port, chatMessages, {
        temperature,
        maxTokens,
      });
      for await (const token of stream) {
        if (stopRef.current) break;
        accumulated += token;
        setConversations((prev) => {
          const updated = prev.map((c) =>
            c.id === convId
              ? {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === assistantId ? { ...m, content: accumulated } : m,
                  ),
                }
              : c,
          );
          saveConversations(updated);
          return updated;
        });
        scrollToBottom();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setConversations((prev) => {
        const updated = prev.map((c) =>
          c.id === convId
            ? {
                ...c,
                messages: c.messages.map((m) =>
                  m.id === assistantId ? { ...m, isStreaming: false } : m,
                ),
              }
            : c,
        );
        saveConversations(updated);
        return updated;
      });
      setIsGenerating(false);
    }
  };

  const handleRawJson = async () => {
    if (!port) return;
    setError(null);
    setRawResult(null);
    setIsGenerating(true);
    try {
      const body = JSON.parse(rawJson);
      const response = await fetch(
        `http://127.0.0.1:${port}/v1/chat/completions`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      const data = await response.json();
      setRawResult(JSON.stringify(data, null, 2));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsGenerating(false);
    }
  };

  const handleStartServer = async () => {
    const modelToLoad = selectedModel || downloadedModels[0]?.filename;
    if (!modelToLoad) {
      setError(
        "No model selected. Download a model from the Models tab first.",
      );
      return;
    }
    try {
      if (!hardwareResult) await detectHardware();
      await startServer(modelToLoad, gpuLayersOverride, contextLengthOverride);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  // ── No server running — launch screen ─────────────────────────────────
  if (!port) {
    return (
      <div className="flex items-center justify-center h-full">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <Server className="h-4 w-4" />
              Start confidential chat
            </CardTitle>
            <CardDescription>
              Load a model to open the private chat playground.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {error && (
              <div className="flex items-start gap-2 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
                <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
                <span>{error}</span>
              </div>
            )}
            {isStarting && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <div className="h-2 w-2 rounded-full bg-blue-500 animate-pulse" />
                Starting confidential chat…
              </div>
            )}
            {downloadedModels.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No models downloaded yet. Go to the <strong>Models</strong> tab
                to download one.
              </p>
            ) : (
              <>
                <div className="space-y-2">
                  <Label className="text-xs">Model</Label>
                  <select
                    className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                    value={selectedModel || downloadedModels[0]?.filename}
                    onChange={(e) => setSelectedModel(e.target.value)}
                  >
                    {downloadedModels.map((m) => (
                      <option key={m.filename} value={m.filename}>
                        {m.name} ({m.size_gb} GB)
                      </option>
                    ))}
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label
                      className="text-xs"
                      title="Transformer layers to offload to GPU. 99 = all layers (Metal/CUDA). 0 = CPU only."
                    >
                      GPU Layers
                    </Label>
                    <Input
                      type="number"
                      value={gpuLayersRaw}
                      onChange={(e) => {
                        setGpuLayersRaw(e.target.value);
                        const n = parseInt(e.target.value);
                        if (!isNaN(n)) setGpuLayersOverride(n);
                      }}
                      onBlur={() => {
                        const n = parseInt(gpuLayersRaw);
                        const c = isNaN(n) ? 0 : n;
                        setGpuLayersOverride(c);
                        setGpuLayersRaw(String(c));
                      }}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-xs">Context Length</Label>
                    <select
                      className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                      value={contextLengthOverride}
                      onChange={(e) =>
                        setContextLengthOverride(parseInt(e.target.value))
                      }
                    >
                      {[2048, 4096, 8192, 16384, 32768].map((n) => (
                        <option key={n} value={n}>
                          {n.toLocaleString()}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                <Button
                  className="w-full"
                  disabled={isStarting}
                  onClick={handleStartServer}
                >
                  {isStarting ? "Starting…" : "Start Server"}
                </Button>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    );
  }

  // ── Full playground layout ─────────────────────────────────────────────
  return (
    <div className="flex h-full gap-0 rounded-xl border overflow-hidden bg-background relative">
      {/* ── Voice Assistant floating popup (visible from any tab when voice active) ── */}
      <WakeWordActivePopup
        visible={voicePopupVisible}
        voiceChatState={voiceChatState}
        voiceChatActions={voiceChatActions}
        transcriptionState={transcriptionState}
        isGenerating={isGenerating}
        stopGeneration={() => {
          stopRef.current = true;
        }}
        ttsPlaybackState={ttsPlaybackState}
        assistantContent={popupAssistantContent}
        onDismiss={() => {
          setVoicePopupVisible(false);
          if (voiceChatState.isActive) voiceChatActions.deactivate();
          void restoreVoiceSystemPrompt();
        }}
      />
      {/* ── Left sidebar: conversation list ── */}
      <div className="w-56 shrink-0 flex flex-col border-r bg-muted/20">
        <div className="flex items-center justify-between px-3 py-2.5 border-b">
          <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
            Conversations
          </span>
          <Button
            size="icon"
            variant="ghost"
            className="h-6 w-6"
            onClick={newConversation}
            title="New conversation"
          >
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </div>
        <ScrollArea className="flex-1">
          <div className="p-1.5 space-y-0.5">
            {conversations.length === 0 && (
              <p className="text-xs text-muted-foreground text-center py-6 px-2">
                No conversations yet. Start chatting!
              </p>
            )}
            {conversations.map((conv) => (
              <div
                key={conv.id}
                className={`group flex items-center gap-1 min-w-0 rounded-lg px-2 py-2 cursor-pointer text-sm transition-colors ${
                  conv.id === activeConvId
                    ? "bg-primary/10 text-primary"
                    : "hover:bg-muted/60 text-foreground"
                }`}
                onClick={() => {
                  setActiveConvId(conv.id);
                  setError(null);
                }}
              >
                <MessageSquare className="h-3.5 w-3.5 shrink-0 opacity-60" />
                <span className="flex-1 min-w-0 truncate text-xs leading-tight">
                  {conv.title}
                </span>
                <button
                  className="opacity-0 group-hover:opacity-100 p-0.5 hover:text-destructive transition-opacity shrink-0"
                  onClick={(e) => {
                    e.stopPropagation();
                    deleteConversation(conv.id);
                  }}
                  title="Delete"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        </ScrollArea>
      </div>

      {/* ── Main chat area ── */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header bar */}
        <div className="flex items-center justify-between px-4 py-2 border-b bg-background shrink-0">
          <div className="flex items-center gap-3">
            {/* Mode switcher */}
            <div className="flex gap-0.5 rounded-md border p-0.5 bg-muted/30">
              {(["chat", "voice", "tools", "raw"] as InferenceMode[]).map(
                (m) => (
                  <button
                    key={m}
                    className={`flex items-center gap-1 rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                      mode === m
                        ? "bg-background shadow-sm text-foreground"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                    onClick={() => {
                      setMode(m);
                      // Entering voice mode → auto-activate voice chat + apply voice system prompt
                      if (m === "voice" && !voiceChatState.isActive) {
                        voiceChatActions.activate();
                        void applyVoiceSystemPrompt();
                      }
                      // Leaving voice mode → deactivate + restore previous system prompt.
                      // Guard: if the wake word is currently active, the voice session is
                      // running independently (popup still visible) — don't kill it just
                      // because the user navigated to another tab.
                      if (
                        m !== "voice" &&
                        voiceChatState.isActive &&
                        wwState.uiMode !== "active"
                      ) {
                        voiceChatActions.deactivate();
                        void restoreVoiceSystemPrompt();
                      }
                    }}
                  >
                    {m === "chat" && <MessageSquare className="h-3 w-3" />}
                    {m === "voice" && <Mic className="h-3 w-3" />}
                    {m === "tools" && <Wrench className="h-3 w-3" />}
                    {m === "raw" && <Code className="h-3 w-3" />}
                    {m.charAt(0).toUpperCase() + m.slice(1)}
                  </button>
                ),
              )}
            </div>
            <ModelSwitcher />
          </div>
          <div className="flex items-center gap-2">
            {(mode === "chat" || mode === "voice") && (
              <Button
                size="sm"
                variant="ghost"
                className="h-7 text-xs text-muted-foreground gap-1"
                onClick={newConversation}
                title="New conversation"
              >
                <Plus className="h-3 w-3" />
                New
              </Button>
            )}
            {(mode === "chat" || mode === "voice") &&
              activeConv &&
              messages.length > 0 && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 text-xs text-muted-foreground gap-1"
                  onClick={() => {
                    updateMessages(activeConvId!, [], systemPrompt);
                    setError(null);
                  }}
                  title="Clear messages"
                >
                  <RotateCcw className="h-3 w-3" />
                  Clear
                </Button>
              )}
            <Button
              size="sm"
              variant="ghost"
              className={`h-7 w-7 ${showSettings ? "bg-muted" : ""}`}
              onClick={() => setShowSettings((v) => !v)}
              title="Settings"
            >
              <Settings className="h-3.5 w-3.5" />
            </Button>

          </div>
        </div>

        {error && (
          <div className="flex gap-2 px-4 py-3 text-sm text-destructive bg-destructive/10 border-b shrink-0">
            <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
            <span className="flex-1 whitespace-pre-wrap leading-relaxed">
              {error}
            </span>
            <button
              className="ml-2 shrink-0 text-xs underline self-start"
              onClick={() => setError(null)}
            >
              Dismiss
            </button>
          </div>
        )}

        {/* ── Chat mode ── */}
        {mode === "chat" && (
          <>
            <ScrollArea className="flex-1" ref={scrollAreaRef}>
              <div className="p-4 space-y-6 max-w-3xl mx-auto w-full">
                {messages.length === 0 && (
                  <div className="flex flex-col items-center justify-center py-16 gap-3 text-center">
                    <div className="h-12 w-12 rounded-full bg-primary/10 flex items-center justify-center">
                      <MessageSquare className="h-6 w-6 text-primary/60" />
                    </div>
                    <p className="text-sm text-muted-foreground max-w-xs">
                      Start a confidential chat. Messages stay on this device,
                      and your history is saved automatically.
                    </p>
                  </div>
                )}
                {messages.map((msg) => (
                  <div key={msg.id} className="group/msg">
                    {msg.role === "assistant" ? (
                      /* ── Assistant message: full-width, no avatar ── */
                      <div className="w-full min-w-0">
                        {editingMsgId === msg.id ? (
                          <div className="space-y-2">
                            <textarea
                              ref={editTextareaRef}
                              className="w-full rounded-xl border bg-background px-4 py-3 text-[0.9375rem] leading-[1.75] resize-none overflow-hidden focus:outline-none focus:ring-2 focus:ring-primary/50"
                              style={{
                                fontFamily:
                                  "var(--font-sans, ui-sans-serif, system-ui, -apple-system, sans-serif)",
                              }}
                              value={editingContent}
                              onChange={(e) =>
                                setEditingContent(e.target.value)
                              }
                              autoFocus
                            />
                            <div className="flex gap-2 justify-end">
                              <Button
                                size="sm"
                                variant="ghost"
                                className="h-7 text-xs"
                                onClick={() => {
                                  setEditingMsgId(null);
                                  setEditingContent("");
                                }}
                              >
                                Cancel
                              </Button>
                              <Button
                                size="sm"
                                className="h-7 text-xs"
                                disabled={!editingContent.trim()}
                                onClick={() =>
                                  handleEditAssistant(msg.id, editingContent)
                                }
                              >
                                Save
                              </Button>
                            </div>
                          </div>
                        ) : (
                          <div className="rounded-xl px-4 py-3 bg-muted/40">
                            <div
                              className="chat-prose leading-[1.75]"
                              style={{
                                fontFamily:
                                  "var(--font-sans, ui-sans-serif, system-ui, -apple-system, sans-serif)",
                                fontSize: "0.9375rem",
                              }}
                            >
                              <ReactMarkdown
                                remarkPlugins={[remarkGfm]}
                                components={{
                                  pre: ({ children }) => (
                                    <pre className="overflow-x-auto rounded-md bg-muted p-3 text-[0.8125rem] font-mono">
                                      {children}
                                    </pre>
                                  ),
                                  code: ({ className, children, ...props }) => {
                                    const isInline = !className;
                                    if (isInline) {
                                      return (
                                        <code
                                          className="rounded bg-muted px-1.5 py-0.5 text-[0.8125rem] font-mono"
                                          {...props}
                                        >
                                          {children}
                                        </code>
                                      );
                                    }
                                    return (
                                      <code
                                        className={`${className ?? ""} font-mono`}
                                        {...props}
                                      >
                                        {children}
                                      </code>
                                    );
                                  },
                                }}
                              >
                                {msg.content}
                              </ReactMarkdown>
                              {msg.isStreaming && (
                                <span className="inline-block h-4 w-0.5 bg-primary opacity-70 animate-pulse ml-0.5 align-middle" />
                              )}
                            </div>
                          </div>
                        )}
                        {!msg.isStreaming &&
                          editingMsgId !== msg.id &&
                          msg.content && (
                            <div className="flex items-center gap-0.5 mt-1 opacity-0 transition-opacity group-hover/msg:opacity-100 justify-start">
                              <MsgCopyButton text={msg.content} />
                              <button
                                className="rounded-md p-1.5 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-40 disabled:cursor-not-allowed"
                                title="Retry — regenerate this response"
                                disabled={isGenerating}
                                onClick={() => handleRetry(msg.id)}
                              >
                                <RefreshCw className="h-3.5 w-3.5" />
                              </button>
                              <button
                                className="rounded-md p-1.5 text-muted-foreground transition-colors hover:text-foreground"
                                title="Edit"
                                onClick={() => {
                                  setEditingMsgId(msg.id);
                                  setEditingContent(msg.content);
                                }}
                              >
                                <Pencil className="h-3.5 w-3.5" />
                              </button>
                              <button
                                className={`rounded-md p-1.5 transition-colors ${reactions[msg.id] === "up" ? "text-green-500" : "text-muted-foreground hover:text-foreground"}`}
                                title="Good response"
                                onClick={() => toggleReaction(msg.id, "up")}
                              >
                                <ThumbsUp className="h-3.5 w-3.5" />
                              </button>
                              <button
                                className={`rounded-md p-1.5 transition-colors ${reactions[msg.id] === "down" ? "text-red-500" : "text-muted-foreground hover:text-foreground"}`}
                                title="Bad response"
                                onClick={() => toggleReaction(msg.id, "down")}
                              >
                                <ThumbsDown className="h-3.5 w-3.5" />
                              </button>
                              <button
                                className="rounded-md p-1.5 text-muted-foreground transition-colors hover:text-foreground"
                                title="Fork conversation from here"
                                onClick={() => forkFromMessage(msg.id)}
                              >
                                <GitFork className="h-3.5 w-3.5" />
                              </button>

                              {/* Read aloud — play / pause / stop */}
                              {!ttsActions ? (
                                <button
                                  disabled
                                  className="rounded-md p-1.5 text-muted-foreground/40 cursor-not-allowed"
                                  title="TTS unavailable"
                                >
                                  <Volume2 className="h-3.5 w-3.5" />
                                </button>
                              ) : (
                                <>
                                  {readingMsgId === msg.id ? (
                                    <>
                                      {/* Pause / Resume */}
                                      <button
                                        className="rounded-md p-1.5 text-primary transition-colors hover:text-primary/70"
                                        title={
                                          chatTts.isPaused
                                            ? "Resume reading"
                                            : "Pause reading"
                                        }
                                        onClick={() => {
                                          if (chatTts.isPaused) {
                                            chatTts.resumeReadAloud();
                                          } else {
                                            chatTts.pauseReadAloud();
                                          }
                                        }}
                                      >
                                        {chatTts.isPaused ? (
                                          <Play className="h-3.5 w-3.5" />
                                        ) : (
                                          <Pause className="h-3.5 w-3.5" />
                                        )}
                                      </button>
                                      {/* Stop */}
                                      <button
                                        className="rounded-md p-1.5 text-destructive transition-colors hover:text-destructive/70"
                                        title="Stop reading"
                                        onClick={() => {
                                          chatTts.stopReadAloud();
                                          setReadingMsgId(null);
                                        }}
                                      >
                                        <VolumeX className="h-3.5 w-3.5" />
                                      </button>
                                    </>
                                  ) : (
                                    /* Play */
                                    <button
                                      className="rounded-md p-1.5 text-muted-foreground transition-colors hover:text-foreground"
                                      title="Read aloud"
                                      onClick={() => {
                                        setReadingMsgId(msg.id);
                                        chatTts.readCompleteMessage(
                                          msg.content,
                                        );
                                      }}
                                    >
                                      <Volume2 className="h-3.5 w-3.5" />
                                    </button>
                                  )}
                                </>
                              )}

                              {/* Divider */}
                              <span className="w-px h-3.5 bg-border mx-0.5 self-center" />

                              {/* Save this response as a note */}
                              <button
                                className="rounded-md p-1.5 text-muted-foreground transition-colors hover:text-foreground"
                                title="Save response to Notes"
                                onClick={() =>
                                  openSaveNote("message", msg.content)
                                }
                              >
                                <BookmarkPlus className="h-3.5 w-3.5" />
                              </button>

                              {/* Save full conversation as a note */}
                              <button
                                className="rounded-md p-1.5 text-muted-foreground transition-colors hover:text-foreground"
                                title="Save full conversation to Notes"
                                onClick={() => openSaveNote("conversation")}
                              >
                                <FileText className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          )}
                      </div>
                    ) : (
                      /* ── User message: right-aligned bubble with avatar ── */
                      <div className="flex gap-3 justify-end">
                        <div className="max-w-[78%] min-w-0">
                          {editingMsgId === msg.id ? (
                            <div className="space-y-2">
                              <textarea
                                ref={editTextareaRef}
                                className="w-full rounded-xl border bg-background px-4 py-3 text-[0.9375rem] leading-relaxed resize-none overflow-hidden focus:outline-none focus:ring-2 focus:ring-primary/50"
                                value={editingContent}
                                onChange={(e) =>
                                  setEditingContent(e.target.value)
                                }
                                autoFocus
                              />
                              <div className="flex gap-2 justify-end">
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  className="h-7 text-xs"
                                  onClick={() => {
                                    setEditingMsgId(null);
                                    setEditingContent("");
                                  }}
                                >
                                  Cancel
                                </Button>
                                <Button
                                  size="sm"
                                  className="h-7 text-xs"
                                  disabled={!editingContent.trim()}
                                  onClick={() =>
                                    handleEditAndResend(msg.id, editingContent)
                                  }
                                >
                                  Save & Resend
                                </Button>
                              </div>
                            </div>
                          ) : (
                            <div className="rounded-2xl px-4 py-3 text-[0.9375rem] leading-relaxed bg-primary text-primary-foreground rounded-tr-sm">
                              <p className="whitespace-pre-wrap break-words">
                                {msg.content}
                              </p>
                              {msg.isStreaming && (
                                <span className="inline-block h-4 w-0.5 bg-current opacity-70 animate-pulse ml-0.5 align-middle" />
                              )}
                            </div>
                          )}
                          {!msg.isStreaming &&
                            editingMsgId !== msg.id &&
                            msg.content && (
                              <div className="flex items-center gap-0.5 mt-1 opacity-0 transition-opacity group-hover/msg:opacity-100 justify-end">
                                <MsgCopyButton text={msg.content} />
                                <button
                                  className="rounded-md p-1.5 text-muted-foreground transition-colors hover:text-foreground"
                                  title="Edit"
                                  onClick={() => {
                                    setEditingMsgId(msg.id);
                                    setEditingContent(msg.content);
                                  }}
                                >
                                  <Pencil className="h-3.5 w-3.5" />
                                </button>
                              </div>
                            )}
                        </div>
                        <div className="h-7 w-7 rounded-full bg-muted flex items-center justify-center shrink-0 mt-0.5">
                          <span className="text-xs font-semibold text-muted-foreground">
                            U
                          </span>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
                <div ref={bottomRef} />
              </div>
            </ScrollArea>

            {/* Input area — composer with prompt/model selectors */}
            <div className="border-t p-3 shrink-0 bg-background">
              <div className="max-w-3xl mx-auto">
                {switchingModel && (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground mb-2 px-1">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Switching model…
                  </div>
                )}
                {chatTts.readAloudError && (
                  <div
                    role="alert"
                    className="flex items-start gap-2 text-xs text-destructive bg-destructive/10 rounded px-3 py-2 mb-2"
                  >
                    <AlertCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                    <span className="flex-1">
                      {chatTts.readAloudError.code === "model_not_downloaded" &&
                      ttsState?.status?.is_downloading
                        ? `TTS voice model is downloading (${Math.round(
                            ttsState.status.download_progress,
                          )}%)…`
                        : chatTts.readAloudError.message}
                    </span>
                    <button
                      className="shrink-0 text-destructive/70 hover:text-destructive underline"
                      onClick={() => chatTts.clearReadAloudError()}
                      title="Dismiss"
                    >
                      Dismiss
                    </button>
                  </div>
                )}
                <div className="glass relative rounded-xl transition-shadow focus-within:shadow-md">
                  <textarea
                    ref={textareaRef}
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        handleSend();
                      }
                    }}
                    placeholder={
                      switchingModel
                        ? "Switching model…"
                        : "Type a confidential chat message… (Enter to send, Shift+Enter for newline)"
                    }
                    className="min-h-[2.5rem] w-full resize-none bg-transparent px-3 py-2 text-sm leading-relaxed text-foreground placeholder:text-muted-foreground focus:outline-none"
                    disabled={isGenerating || switchingModel}
                    rows={1}
                  />
                  <div className="flex items-center justify-between px-2 pb-1.5">
                    <div className="flex items-center gap-1">
                      {/* Prompt selector */}
                      <div className="relative" ref={promptPickerRef}>
                        <button
                          onClick={() => {
                            setShowPromptPicker(!showPromptPicker);
                            setShowModelPicker(false);
                          }}
                          className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
                          title="Select system prompt"
                        >
                          <span className="max-w-[140px] truncate">
                            {activePromptName ?? "No prompt"}
                          </span>
                          <ChevronDown className="h-3 w-3" />
                        </button>
                        {showPromptPicker && (
                          <div className="glass absolute bottom-full left-0 mb-1.5 min-w-[240px] max-h-64 overflow-y-auto rounded-lg p-1.5 z-50">
                            <button
                              className={`flex w-full items-center rounded-md px-3 py-2 text-left text-xs transition-colors ${
                                !systemPrompt
                                  ? "bg-accent text-accent-foreground"
                                  : "text-foreground hover:bg-accent/50"
                              }`}
                              onClick={() => {
                                setSystemPrompt("");
                                if (activeConvId)
                                  updateMessages(activeConvId, messages, "");
                                setShowPromptPicker(false);
                              }}
                            >
                              <span className="text-muted-foreground">
                                No prompt
                              </span>
                            </button>
                            {allPrompts.map((p) => (
                              <button
                                key={p.id}
                                className={`flex w-full items-center rounded-md px-3 py-2 text-left text-xs transition-colors ${
                                  p.content === systemPrompt
                                    ? "bg-accent text-accent-foreground"
                                    : "text-foreground hover:bg-accent/50"
                                }`}
                                onClick={() => {
                                  setSystemPrompt(p.content);
                                  if (activeConvId)
                                    updateMessages(
                                      activeConvId,
                                      messages,
                                      p.content,
                                    );
                                  setShowPromptPicker(false);
                                }}
                              >
                                <span className="font-medium">{p.name}</span>
                                <span className="ml-auto text-[10px] text-muted-foreground">
                                  {p.category}
                                </span>
                              </button>
                            ))}
                          </div>
                        )}
                      </div>

                      {/* Model selector */}
                      <div className="relative" ref={modelPickerRef}>
                        <button
                          onClick={() => {
                            setShowModelPicker(!showModelPicker);
                            setShowPromptPicker(false);
                          }}
                          className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
                          title="Select model"
                        >
                          <Cpu className="h-3 w-3 text-blue-500 shrink-0" />
                          <span className="max-w-[140px] truncate">
                            {currentModelName}
                          </span>
                          <ChevronDown className="h-3 w-3" />
                        </button>
                        {showModelPicker && (
                          <div className="glass absolute bottom-full left-0 mb-1.5 min-w-[240px] max-h-64 overflow-y-auto rounded-lg p-1.5 z-50">
                            {downloadedModels.map((m) => {
                              const isCurrent =
                                serverStatus?.model_path?.includes(m.filename);
                              return (
                                <button
                                  key={m.filename}
                                  className={`flex w-full items-center rounded-md px-3 py-2 text-left text-xs transition-colors ${
                                    isCurrent
                                      ? "bg-accent text-accent-foreground"
                                      : "text-foreground hover:bg-accent/50"
                                  }`}
                                  onClick={() => {
                                    if (!isCurrent)
                                      handleModelSwitch(m.filename);
                                    setShowModelPicker(false);
                                  }}
                                >
                                  <Cpu className="h-3 w-3 text-blue-500 mr-2 shrink-0" />
                                  <span className="font-medium truncate">
                                    {m.name}
                                  </span>
                                  <span className="ml-auto text-[10px] text-muted-foreground shrink-0">
                                    {m.size_gb} GB
                                  </span>
                                  {isCurrent && (
                                    <span className="ml-1.5 text-[10px] text-green-500 shrink-0">
                                      running
                                    </span>
                                  )}
                                </button>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-1.5">
                      {/* Mic dictation button */}
                      <RecordingMicButton
                        isRecording={
                          transcriptionState.isRecording &&
                          !voiceChatState.isActive
                        }
                        isProcessingTail={
                          transcriptionState.isProcessingTail &&
                          !voiceChatState.isActive
                        }
                        liveRms={
                          voiceChatState.isActive
                            ? 0
                            : transcriptionState.liveRms
                        }
                        onToggle={() => {
                          if (voiceChatState.isActive) return; // let VoiceChatBar handle mic in voice mode
                          if (
                            transcriptionState.isRecording ||
                            transcriptionState.isProcessingTail
                          ) {
                            void transcriptionActions.stopRecording();
                          } else {
                            dictationBaseTranscriptRef.current =
                              transcriptionState.fullTranscript;
                            dictationActiveRef.current = true;
                            void transcriptionActions.startRecording();
                          }
                        }}
                        disabled={
                          isGenerating ||
                          switchingModel ||
                          !transcriptionState.activeModel ||
                          voiceChatState.isActive
                        }
                        size="xs"
                        stopIcon="square"
                      />

                      {/* Voice chat toggle */}
                      <button
                        onClick={() => {
                          if (voiceChatState.isActive) {
                            voiceChatActions.deactivate();
                          } else {
                            voiceChatActions.activate();
                          }
                        }}
                        className={`flex h-7 w-7 items-center justify-center rounded-full transition-colors ${
                          voiceChatState.isActive
                            ? "bg-primary text-primary-foreground"
                            : "text-muted-foreground hover:text-foreground hover:bg-muted/60"
                        }`}
                        title={
                          voiceChatState.isActive
                            ? "Exit voice chat"
                            : "Enter voice chat mode"
                        }
                      >
                        <Mic className="h-3.5 w-3.5" />
                      </button>

                      {/* Auto-read-aloud toggle */}
                      {ttsActions ? (
                        <button
                          onClick={() => setAutoReadAloud((v) => !v)}
                          className={`flex h-7 w-7 items-center justify-center rounded-full transition-colors ${
                            autoReadAloud
                              ? "bg-primary/15 text-primary"
                              : "text-muted-foreground hover:text-foreground hover:bg-muted/60"
                          }`}
                          title={
                            autoReadAloud
                              ? "Auto read-aloud ON (click to disable)"
                              : "Auto read-aloud OFF (click to enable)"
                          }
                        >
                          {autoReadAloud ? (
                            <Volume2 className="h-3.5 w-3.5" />
                          ) : (
                            <VolumeX className="h-3.5 w-3.5" />
                          )}
                        </button>
                      ) : (
                        <button
                          disabled
                          className="flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground/40 cursor-not-allowed"
                          title="TTS unavailable"
                        >
                          <VolumeX className="h-3.5 w-3.5" />
                        </button>
                      )}

                      {/* Send / Stop */}
                      {isGenerating ? (
                        <button
                          onClick={() => (stopRef.current = true)}
                          className="flex h-7 w-7 items-center justify-center rounded-full bg-primary text-primary-foreground transition-colors"
                          title="Stop generation"
                        >
                          <Square className="h-3 w-3" />
                        </button>
                      ) : (
                        <button
                          onClick={handleSend}
                          disabled={!input.trim() || switchingModel}
                          title="Send"
                          className={`flex h-7 w-7 items-center justify-center rounded-full transition-all duration-200 ${
                            !input.trim() || switchingModel
                              ? "bg-muted-foreground/30 text-primary-foreground opacity-30 cursor-not-allowed"
                              : "bg-primary text-primary-foreground active:scale-[0.96]"
                          }`}
                        >
                          <Play className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Voice Chat Bar — appears below the input area (chat mode only) */}
            <VoiceChatBar
              voiceChatState={voiceChatState}
              voiceChatActions={voiceChatActions}
              transcriptionState={transcriptionState}
              isGenerating={isGenerating}
              stopGeneration={() => {
                stopRef.current = true;
              }}
              isSpeaking={
                ttsPlaybackState === "playing" ||
                ttsPlaybackState === "synthesizing"
              }
              sessionBaseTranscript={voiceChatState.sessionBaseTranscript}
            />
          </>
        )}

        {/* ── Voice Chat mode ── */}
        {mode === "voice" && (
          <AudioChatMode
            voiceChatState={voiceChatState}
            voiceChatActions={voiceChatActions}
            transcriptionState={transcriptionState}
            isGenerating={isGenerating}
            stopGeneration={() => {
              stopRef.current = true;
            }}
            ttsPlaybackState={ttsPlaybackState}
            messages={messages}
            ttsAvailable={!!ttsActions}
          />
        )}

        {/* ── Tools / Agentic mode ── */}
        {mode === "tools" && (
          <div className="flex flex-1 min-h-0 overflow-hidden">
            {/* ── Left: Tool selector panel ── */}
            <div className="w-64 shrink-0 flex flex-col border-r bg-muted/10">
              {/* Panel header */}
              <div className="flex items-center justify-between px-3 py-2.5 border-b shrink-0">
                <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
                  <ListChecks className="h-3.5 w-3.5" />
                  Tools
                  {selectedToolNames.size > 0 && (
                    <Badge
                      variant="secondary"
                      className="text-[10px] h-4 px-1.5 ml-1"
                    >
                      {selectedToolNames.size}
                    </Badge>
                  )}
                </span>
                {selectedToolNames.size > 0 && (
                  <button
                    className="text-[10px] text-muted-foreground hover:text-destructive transition-colors"
                    onClick={clearAllTools}
                  >
                    Clear all
                  </button>
                )}
              </div>

              {/* Max steps control */}
              <div className="flex items-center gap-2 px-3 py-2 border-b shrink-0">
                <Label className="text-xs text-muted-foreground whitespace-nowrap">
                  Max steps
                </Label>
                <Input
                  type="number"
                  min={1}
                  max={50}
                  value={maxAgentSteps}
                  onChange={(e) => {
                    const n = parseInt(e.target.value);
                    if (!isNaN(n) && n >= 1) setMaxAgentSteps(Math.min(n, 50));
                  }}
                  className="h-6 text-xs w-16"
                />
              </div>

              {/* Search */}
              <div className="px-3 py-2 border-b shrink-0">
                <Input
                  value={toolSearchQuery}
                  onChange={(e) => setToolSearchQuery(e.target.value)}
                  placeholder="Search tools…"
                  className="h-7 text-xs"
                />
              </div>

              {/* Tool list */}
              <ScrollArea className="flex-1">
                {loadingToolSchemas && (
                  <div className="flex items-center justify-center py-8 gap-2 text-xs text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Loading…
                  </div>
                )}
                {toolSchemasError && (
                  <div className="px-3 py-3 text-xs text-destructive flex items-start gap-1.5">
                    <AlertCircle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                    {toolSchemasError}
                  </div>
                )}
                {!loadingToolSchemas && !toolSchemasError && (
                  <div className="p-1.5 space-y-0.5">
                    {Object.entries(toolSchemasByCategory)
                      .sort(([a], [b]) => a.localeCompare(b))
                      .map(([cat, tools]) => {
                        const q = toolSearchQuery.toLowerCase();
                        const filtered = q
                          ? tools.filter(
                              (t) =>
                                t.name.toLowerCase().includes(q) ||
                                t.description.toLowerCase().includes(q),
                            )
                          : tools;
                        if (filtered.length === 0) return null;

                        const allSelected = filtered.every((t) =>
                          selectedToolNames.has(t.name),
                        );
                        const someSelected = filtered.some((t) =>
                          selectedToolNames.has(t.name),
                        );
                        const isExpanded =
                          expandedCategories.has(cat) || !!toolSearchQuery;

                        return (
                          <div key={cat}>
                            {/* Category header */}
                            <div className="flex items-center gap-1 rounded-md px-2 py-1.5 hover:bg-muted/40 cursor-pointer group/cat">
                              <button
                                className="flex items-center gap-1 flex-1 text-left"
                                onClick={() => toggleCategory(cat)}
                              >
                                <ChevronRight
                                  className={`h-3 w-3 text-muted-foreground transition-transform ${isExpanded ? "rotate-90" : ""}`}
                                />
                                <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">
                                  {cat}
                                </span>
                                <span className="text-[10px] text-muted-foreground/60 ml-0.5">
                                  ({filtered.length})
                                </span>
                              </button>
                              {/* Select all / deselect all for category */}
                              <button
                                className="opacity-0 group-hover/cat:opacity-100 text-[10px] text-muted-foreground hover:text-foreground transition-opacity px-1"
                                onClick={() =>
                                  allSelected
                                    ? deselectAllInCategory(cat)
                                    : selectAllInCategory(cat)
                                }
                                title={
                                  allSelected ? "Deselect all" : "Select all"
                                }
                              >
                                {allSelected ? (
                                  <CheckCheck className="h-3 w-3 text-primary" />
                                ) : someSelected ? (
                                  <ListChecks className="h-3 w-3" />
                                ) : (
                                  <ListChecks className="h-3 w-3" />
                                )}
                              </button>
                            </div>

                            {/* Tool list for category */}
                            {isExpanded && (
                              <div className="ml-3 space-y-0.5 mb-1">
                                {filtered.map((tool) => {
                                  const isSelected = selectedToolNames.has(
                                    tool.name,
                                  );
                                  return (
                                    <button
                                      key={tool.name}
                                      onClick={() =>
                                        toggleToolSelection(tool.name)
                                      }
                                      className={`flex items-start gap-2 w-full rounded-md px-2 py-1.5 text-left transition-colors ${
                                        isSelected
                                          ? "bg-primary/10 text-primary"
                                          : "hover:bg-muted/40 text-foreground"
                                      }`}
                                    >
                                      <div
                                        className={`mt-0.5 h-3 w-3 shrink-0 rounded border transition-colors ${
                                          isSelected
                                            ? "bg-primary border-primary"
                                            : "border-muted-foreground/40"
                                        }`}
                                      >
                                        {isSelected && (
                                          <Check className="h-3 w-3 text-primary-foreground" />
                                        )}
                                      </div>
                                      <div className="min-w-0">
                                        <div className="text-[11px] font-medium leading-tight truncate">
                                          {tool.name}
                                        </div>
                                        <div className="text-[10px] text-muted-foreground leading-tight line-clamp-2 mt-0.5">
                                          {tool.description}
                                        </div>
                                      </div>
                                    </button>
                                  );
                                })}
                              </div>
                            )}
                          </div>
                        );
                      })}
                  </div>
                )}
              </ScrollArea>
            </div>

            {/* ── Right: Agentic chat area ── */}
            <div className="flex-1 flex flex-col min-w-0">
              {/* Abort / status bar */}
              {isAgentRunning && (
                <div className="flex items-center justify-between px-4 py-2 bg-amber-500/10 border-b shrink-0">
                  <div className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    <span>Agent running…</span>
                  </div>
                  <Button
                    size="sm"
                    variant="destructive"
                    className="h-7 text-xs gap-1 font-semibold"
                    onClick={handleAgentAbort}
                  >
                    <OctagonX className="h-3.5 w-3.5" />
                    Abort Now
                  </Button>
                </div>
              )}

              {agentError && (
                <div className="flex gap-2 px-4 py-3 text-sm text-destructive bg-destructive/10 border-b shrink-0">
                  <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
                  <span className="flex-1">{agentError}</span>
                  <button
                    className="text-xs underline"
                    onClick={() => setAgentError(null)}
                  >
                    Dismiss
                  </button>
                </div>
              )}

              {selectedToolNames.size === 0 && !isAgentRunning && (
                <div className="flex items-center gap-2 px-4 py-2 bg-muted/30 border-b text-xs text-muted-foreground shrink-0">
                  <TriangleAlert className="h-3.5 w-3.5 shrink-0" />
                  Select tools from the left panel to enable the agentic loop.
                </div>
              )}

              {/* Conversation scroll area */}
              <div
                ref={agentScrollRef}
                className="flex-1 overflow-y-auto p-4 space-y-6"
              >
                {agentMessages.length === 0 && (
                  <div className="flex flex-col items-center justify-center h-full py-16 gap-3 text-center">
                    <div className="h-12 w-12 rounded-full bg-primary/10 flex items-center justify-center">
                      <Wrench className="h-6 w-6 text-primary/60" />
                    </div>
                    <div className="max-w-xs space-y-1">
                      <p className="text-sm font-medium">Agentic Tool Loop</p>
                      <p className="text-xs text-muted-foreground">
                        Select tools on the left, then send a message. The model
                        will call tools and continue until it has an answer or
                        you abort.
                      </p>
                    </div>
                  </div>
                )}

                {agentMessages.map((msg) => (
                  <div key={msg.id} className="space-y-2">
                    {msg.role === "user" ? (
                      /* User message */
                      <div className="flex justify-end">
                        <div className="max-w-[70%] rounded-2xl rounded-br-sm bg-primary px-4 py-2.5 text-sm text-primary-foreground">
                          {msg.content}
                        </div>
                      </div>
                    ) : (
                      /* Assistant message — shows tool steps inline */
                      <div className="space-y-3">
                        {/* Tool step cards */}
                        {(msg.toolSteps ?? []).map((step) => (
                          <div key={step.stepIndex} className="space-y-2">
                            {step.toolCalls.map((tc) => (
                              <AgenticToolCallCard
                                key={tc.toolCallId}
                                toolName={tc.toolName}
                                arguments={tc.arguments}
                                result={tc.result}
                                isError={tc.isError}
                                elapsedMs={tc.elapsedMs}
                              />
                            ))}
                            {step.assistantContent &&
                              step.toolCalls.length > 0 && (
                                <div className="text-xs text-muted-foreground px-2 italic">
                                  {step.assistantContent}
                                </div>
                              )}
                          </div>
                        ))}

                        {/* Final assistant content */}
                        {msg.content && (
                          <div className="rounded-xl px-4 py-3 bg-muted/40">
                            <div
                              className="chat-prose leading-[1.75]"
                              style={{
                                fontFamily:
                                  "var(--font-sans, ui-sans-serif, system-ui, -apple-system, sans-serif)",
                                fontSize: "0.9375rem",
                              }}
                            >
                              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                {msg.content}
                              </ReactMarkdown>
                              {msg.isStreaming && (
                                <span className="inline-block h-4 w-0.5 bg-primary opacity-70 animate-pulse ml-0.5 align-middle" />
                              )}
                            </div>
                          </div>
                        )}

                        {/* Streaming spinner when no content yet */}
                        {msg.isStreaming &&
                          !msg.content &&
                          (msg.toolSteps ?? []).length === 0 && (
                            <div className="flex items-center gap-2 px-4 py-2 text-xs text-muted-foreground">
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              Thinking…
                            </div>
                          )}
                      </div>
                    )}
                  </div>
                ))}
              </div>

              {/* Composer */}
              <div className="border-t px-4 py-3 shrink-0">
                <div className="flex gap-2 items-end">
                  <Textarea
                    value={agentInput}
                    onChange={(e) => setAgentInput(e.target.value)}
                    placeholder={
                      selectedToolNames.size === 0
                        ? "Select tools first…"
                        : "Ask the agent to do something…"
                    }
                    disabled={isAgentRunning || selectedToolNames.size === 0}
                    className="flex-1 resize-none min-h-[64px] max-h-[160px] text-sm"
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        void handleAgentSend();
                      }
                    }}
                  />
                  <div className="flex flex-col gap-1.5">
                    {isAgentRunning ? (
                      <button
                        onClick={handleAgentAbort}
                        className="flex h-9 w-9 items-center justify-center rounded-full bg-destructive text-destructive-foreground hover:bg-destructive/90 transition-colors"
                        title="Abort agent"
                      >
                        <OctagonX className="h-4 w-4" />
                      </button>
                    ) : (
                      <button
                        onClick={() => void handleAgentSend()}
                        disabled={
                          !agentInput.trim() ||
                          selectedToolNames.size === 0 ||
                          isAgentRunning
                        }
                        title="Run agent"
                        className={`flex h-9 w-9 items-center justify-center rounded-full transition-all duration-200 ${
                          !agentInput.trim() ||
                          selectedToolNames.size === 0 ||
                          isAgentRunning
                            ? "bg-muted-foreground/30 text-primary-foreground opacity-30 cursor-not-allowed"
                            : "bg-primary text-primary-foreground active:scale-95"
                        }`}
                      >
                        <Zap className="h-4 w-4" />
                      </button>
                    )}
                    {agentMessages.length > 0 && (
                      <button
                        onClick={() => {
                          setAgentMessages([]);
                          agentHistoryRef.current = [];
                        }}
                        className="flex h-9 w-9 items-center justify-center rounded-full text-muted-foreground hover:text-foreground hover:bg-muted/40 transition-colors"
                        title="Clear conversation"
                      >
                        <RotateCcw className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── Raw JSON mode ── */}
        {mode === "raw" && (
          <div className="flex-1 overflow-auto p-4">
            <div className="grid grid-cols-2 gap-4 max-w-4xl mx-auto">
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <Label className="text-sm font-medium">Request Body</Label>
                  <span className="text-xs text-muted-foreground font-mono">
                    POST /v1/chat/completions
                  </span>
                </div>
                <Textarea
                  value={rawJson}
                  onChange={(e) => setRawJson(e.target.value)}
                  className="font-mono text-xs h-64 resize-none"
                />
                <Button
                  disabled={isGenerating}
                  onClick={handleRawJson}
                  className="w-full"
                >
                  {isGenerating ? "Running…" : "Send Request"}
                </Button>
              </div>
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label className="text-sm font-medium">Raw Response</Label>
                  {rawResult && <CopyButton text={rawResult} />}
                </div>
                {rawResult ? (
                  <ScrollArea className="h-96 rounded-lg border bg-muted/30">
                    <pre className="p-4 text-xs font-mono whitespace-pre-wrap">
                      {rawResult}
                    </pre>
                  </ScrollArea>
                ) : (
                  <div className="h-96 rounded-lg border bg-muted/10 flex items-center justify-center text-sm text-muted-foreground">
                    Response will appear here
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ── Right settings panel ── */}
      {showSettings && (
        <div className="w-64 shrink-0 flex flex-col border-l bg-muted/10">
          <div className="flex items-center justify-between px-3 py-2.5 border-b">
            <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
              Settings
            </span>
            <button
              onClick={() => setShowSettings(false)}
              className="text-muted-foreground hover:text-foreground"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <ScrollArea className="flex-1">
            <div className="p-3 space-y-5">
              {/* System prompt */}
              <SystemPromptSelector
                value={systemPrompt}
                onChange={(content) => {
                  setSystemPrompt(content);
                  if (activeConvId)
                    updateMessages(activeConvId, messages, content);
                }}
              />

              <Separator />

              {/* Sampling params */}
              <div className="space-y-4">
                <p className="text-xs font-medium">Sampling</p>
                <div className="space-y-2">
                  <div className="flex justify-between">
                    <Label className="text-xs text-muted-foreground">
                      Temperature
                    </Label>
                    <span className="text-xs tabular-nums">
                      {temperature.toFixed(2)}
                    </span>
                  </div>
                  <Slider
                    min={0.01}
                    max={2}
                    step={0.01}
                    value={[temperature]}
                    onValueChange={([v]) => setTemperature(v)}
                  />
                  <p className="text-xs text-muted-foreground">
                    0.7 balanced · 0.1 precise · 1.5 creative
                  </p>
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between">
                    <Label className="text-xs text-muted-foreground">
                      Top-P
                    </Label>
                    <span className="text-xs tabular-nums">
                      {topP.toFixed(2)}
                    </span>
                  </div>
                  <Slider
                    min={0.1}
                    max={1}
                    step={0.05}
                    value={[topP]}
                    onValueChange={([v]) => setTopP(v)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground">
                    Max Tokens
                  </Label>
                  <Input
                    type="number"
                    value={maxTokens}
                    onChange={(e) => {
                      const n = parseInt(e.target.value);
                      if (!isNaN(n) && n > 0) setMaxTokens(n);
                    }}
                    className="h-8 text-xs"
                  />
                </div>
              </div>

              <Separator />

              {/* Server info */}
              <div className="space-y-2">
                <p className="text-xs font-medium">Server</p>
                <div className="space-y-1 text-xs text-muted-foreground">
                  <div className="flex justify-between">
                    <span>Port</span>
                    <span className="font-mono">{port}</span>
                  </div>
                  {serverStatus?.gpu_layers !== undefined && (
                    <div className="flex justify-between">
                      <span>GPU layers</span>
                      <span className="font-mono">
                        {serverStatus.gpu_layers}
                      </span>
                    </div>
                  )}
                  {serverStatus?.context_length !== undefined && (
                    <div className="flex justify-between">
                      <span>Context</span>
                      <span className="font-mono">
                        {serverStatus.context_length.toLocaleString()}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </ScrollArea>
        </div>
      )}

      {/* ── Save-to-note modal ── */}
      <SaveToNoteModal
        open={saveNoteOpen}
        onClose={() => setSaveNoteOpen(false)}
        scope={saveNoteScope}
        content={saveNoteContent}
        userId={userId}
        enginePort={port}
      />
    </div>
  );
}

// ── Server Tab ────────────────────────────────────────────────────────────

function ServerTab() {
  const [state, actions] = useLlmContext();
  const {
    serverStatus,
    isStarting,
    startingModelName,
    serverStartProgress,
    serverLogs,
    downloadedModels,
    hardwareResult,
    isDetecting,
  } = state;
  const {
    startServer,
    stopServer,
    getServerStatus,
    healthCheck,
    detectHardware,
  } = actions;

  const [healthOk, setHealthOk] = useState<boolean | null>(null);
  const [healthLatency, setHealthLatency] = useState<number | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  // Null means "not yet user-edited" — will be set from hardware detection
  const [gpuLayers, setGpuLayers] = useState<number | null>(null);
  const [contextLen, setContextLen] = useState(8192);
  const [selectedModel, setSelectedModel] = useState(
    downloadedModels[0]?.filename ?? "",
  );
  // Persisted logs shown after a failed start attempt
  const [failureLogs, setFailureLogs] = useState<ServerLogLine[]>([]);
  const [showFailureLogs, setShowFailureLogs] = useState(true);

  // Auto-detect hardware on mount so GPU layers have a real default
  useEffect(() => {
    if (!hardwareResult && !isDetecting) {
      detectHardware().catch(() => {
        /* non-critical */
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync gpuLayers default from hardware detection result (only when not yet user-edited)
  useEffect(() => {
    if (hardwareResult && gpuLayers === null) {
      setGpuLayers(hardwareResult.recommended_gpu_layers ?? 99);
    }
  }, [hardwareResult, gpuLayers]);

  const effectiveGpuLayers = gpuLayers ?? 99;

  const runHealthCheck = async () => {
    setLocalError(null);
    const start = Date.now();
    const ok = await healthCheck();
    setHealthOk(ok);
    setHealthLatency(Date.now() - start);
  };

  const handleStart = async () => {
    setLocalError(null);
    setFailureLogs([]);
    const model = selectedModel || downloadedModels[0]?.filename;
    if (!model) return;
    try {
      if (!hardwareResult) await detectHardware();
      await startServer(model, effectiveGpuLayers, contextLen);
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : String(e));
      // Capture whatever logs accumulated during the failed start attempt
      setFailureLogs(serverLogs.slice());
      setShowFailureLogs(true);
    }
  };

  const handleStop = async () => {
    try {
      await stopServer();
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleRefresh = async () => {
    try {
      await getServerStatus();
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : String(e));
    }
  };

  const endpointUrl = serverStatus?.running
    ? `http://127.0.0.1:${serverStatus.port}/v1`
    : null;

  return (
    <div className="space-y-4 max-w-2xl">
      {/* Error banner */}
      {localError && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive space-y-1">
          <div className="flex items-start gap-2">
            <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
            <pre className="whitespace-pre-wrap font-sans break-words flex-1">
              {localError}
            </pre>
          </div>
        </div>
      )}

      {/* Live loading card — shown while server is starting */}
      {isStarting && (
        <ModelLoadingCard
          modelName={startingModelName}
          progress={serverStartProgress}
          logs={serverLogs}
        />
      )}

      {/* Post-failure log panel — persists after a failed start */}
      {!isStarting && failureLogs.length > 0 && (
        <div className="rounded-xl border bg-card shadow-sm overflow-hidden">
          <button
            className="w-full flex items-center gap-2 px-4 py-3 text-sm font-medium hover:bg-muted/30 transition-colors"
            onClick={() => setShowFailureLogs((v) => !v)}
          >
            {showFailureLogs ? (
              <ChevronUp className="h-4 w-4 text-muted-foreground" />
            ) : (
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            )}
            <span>Server output</span>
            <span className="ml-auto text-xs text-muted-foreground">
              {failureLogs.length} line{failureLogs.length !== 1 ? "s" : ""}
            </span>
          </button>
          {showFailureLogs && (
            <div className="border-t px-4 py-3 space-y-0.5 max-h-64 overflow-y-auto">
              {failureLogs.map((l, i) => (
                <p
                  key={i}
                  className={`text-xs font-mono leading-relaxed ${
                    l.kind === "error"
                      ? "text-destructive"
                      : l.kind === "ready"
                        ? "text-green-600"
                        : "text-muted-foreground"
                  }`}
                >
                  {l.line}
                </p>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Status Card */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base flex items-center gap-2">
              <Activity className="h-4 w-4" />
              Server Status
            </CardTitle>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleRefresh}
              disabled={isStarting}
            >
              <RotateCcw className="h-4 w-4" />
            </Button>
          </div>
        </CardHeader>
        <CardContent className="pt-0 space-y-4">
          {serverStatus?.running ? (
            <>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <p className="text-muted-foreground text-xs">Status</p>
                  <div className="flex items-center gap-1.5 mt-0.5">
                    <div className="h-2 w-2 rounded-full bg-green-500" />
                    <p className="font-medium text-green-600">Running</p>
                  </div>
                </div>
                <div>
                  <p className="text-muted-foreground text-xs">Port</p>
                  <p className="font-medium">{serverStatus.port}</p>
                </div>
                <div>
                  <p className="text-muted-foreground text-xs">Model</p>
                  <p className="font-medium truncate">
                    {serverStatus.model_name}
                  </p>
                </div>
                <div>
                  <p className="text-muted-foreground text-xs">GPU Layers</p>
                  <p className="font-medium">{serverStatus.gpu_layers}</p>
                </div>
              </div>

              {endpointUrl && (
                <div className="flex items-center gap-2 rounded-lg bg-muted/40 px-3 py-2">
                  <code className="text-xs flex-1 overflow-hidden text-ellipsis">
                    {endpointUrl}
                  </code>
                  <CopyButton text={endpointUrl} label="Copy URL" />
                </div>
              )}

              {/* Health check */}
              <div className="flex items-center gap-3">
                <Button size="sm" variant="outline" onClick={runHealthCheck}>
                  Check Health
                </Button>
                {healthOk !== null && (
                  <span
                    className={`text-sm flex items-center gap-1.5 ${
                      healthOk ? "text-green-600" : "text-destructive"
                    }`}
                  >
                    {healthOk ? (
                      <CheckCircle2 className="h-4 w-4" />
                    ) : (
                      <AlertCircle className="h-4 w-4" />
                    )}
                    {healthOk ? `OK — ${healthLatency}ms` : "Unhealthy"}
                  </span>
                )}
              </div>

              <Separator />

              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  className="text-destructive border-destructive/30"
                  onClick={handleStop}
                >
                  <Square className="h-3 w-3 mr-1" />
                  Stop Server
                </Button>
              </div>
            </>
          ) : (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <div className="h-2 w-2 rounded-full bg-muted-foreground/40" />
              {isStarting ? "Starting…" : "Server not running"}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Start Server Card — hidden while starting or already running */}
      {!serverStatus?.running && !isStarting && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <Settings className="h-4 w-4" />
              Start Server
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {downloadedModels.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No models downloaded. Go to the Models tab first.
              </p>
            ) : (
              <>
                <div className="space-y-2">
                  <Label className="text-xs">Model</Label>
                  <select
                    className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                    value={selectedModel || downloadedModels[0]?.filename}
                    onChange={(e) => setSelectedModel(e.target.value)}
                  >
                    {downloadedModels.map((m) => (
                      <option key={m.filename} value={m.filename}>
                        {m.name} ({m.size_gb} GB)
                      </option>
                    ))}
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <div className="flex justify-between items-center">
                      <Label
                        className="text-xs"
                        title="Transformer layers to offload to the GPU accelerator. 99 = offload all layers (fastest, uses GPU memory). 0 = CPU-only. Auto-set from your hardware."
                      >
                        GPU Layers (offload to accelerator)
                      </Label>
                      <span className="text-xs text-muted-foreground tabular-nums">
                        {effectiveGpuLayers}
                        {isDetecting && " (detecting…)"}
                        {!isDetecting &&
                          hardwareResult &&
                          gpuLayers !== null && (
                            <span className="text-green-600 ml-1">✓ auto</span>
                          )}
                      </span>
                    </div>
                    <Slider
                      min={0}
                      max={99}
                      step={1}
                      value={[effectiveGpuLayers]}
                      onValueChange={([v]) => setGpuLayers(v)}
                      disabled={isDetecting}
                    />
                    <p className="text-xs text-muted-foreground">
                      {hardwareResult
                        ? hardwareResult.recommended_gpu_layers === 99
                          ? "Full GPU offload recommended for your hardware"
                          : hardwareResult.recommended_gpu_layers === 0
                            ? "CPU-only mode recommended (no compatible GPU)"
                            : `Partial offload recommended for your hardware`
                        : "99 = full GPU offload · 0 = CPU only"}
                    </p>
                  </div>
                  <div className="space-y-2">
                    <Label className="text-xs">Context Length</Label>
                    <select
                      className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                      value={contextLen}
                      onChange={(e) => setContextLen(parseInt(e.target.value))}
                    >
                      <option value={2048}>2048</option>
                      <option value={4096}>4096</option>
                      <option value={8192}>8192 (recommended)</option>
                      <option value={16384}>16384</option>
                      <option value={32768}>32768</option>
                    </select>
                  </div>
                </div>
                <Button
                  className="w-full"
                  disabled={isStarting || isDetecting}
                  onClick={handleStart}
                >
                  {isStarting ? "Starting…" : "Start Server"}
                </Button>
              </>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ── Hardware Tab ──────────────────────────────────────────────────────────

function HardwareTab() {
  const [state, actions] = useLlmContext();
  const { hardwareResult, isDetecting, downloadedModels } = state;
  const { detectHardware, listModels } = actions;

  useEffect(() => {
    listModels();
    if (!hardwareResult && !isDetecting) {
      detectHardware().catch((e) => console.warn("[local-models] detectHardware failed:", e));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listModels]);

  return (
    <div className="space-y-4 max-w-2xl">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-medium">System Hardware</h2>
        <Button
          size="sm"
          variant="outline"
          disabled={isDetecting}
          onClick={detectHardware}
        >
          {isDetecting ? "Detecting…" : "Refresh"}
        </Button>
      </div>

      {!hardwareResult ? (
        <Card>
          <CardContent className="pt-6 text-center text-muted-foreground text-sm">
            <HardDrive className="h-8 w-8 mx-auto mb-3 opacity-30" />
            <p>Click Refresh to detect your hardware capabilities.</p>
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Memory</CardTitle>
              </CardHeader>
              <CardContent className="pt-0 space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Total RAM</span>
                  <span className="font-medium">
                    {(hardwareResult.hardware.total_ram_mb / 1024).toFixed(1)}{" "}
                    GB
                  </span>
                </div>
                {hardwareResult.hardware.gpu_vram_mb && (
                  <div className="flex justify-between text-sm">
                    <span className="text-muted-foreground">GPU VRAM</span>
                    <span className="font-medium">
                      {(hardwareResult.hardware.gpu_vram_mb / 1024).toFixed(1)}{" "}
                      GB
                    </span>
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Compute</CardTitle>
              </CardHeader>
              <CardContent className="pt-0 space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">CPU Threads</span>
                  <span className="font-medium">
                    {hardwareResult.hardware.cpu_threads}
                  </span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Acceleration</span>
                  <span className="font-medium">
                    {hardwareResult.hardware.is_apple_silicon
                      ? "Apple Metal"
                      : hardwareResult.hardware.supports_cuda
                        ? "CUDA"
                        : "CPU only"}
                  </span>
                </div>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Model Recommendation</CardTitle>
            </CardHeader>
            <CardContent className="pt-0 space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <p className="font-medium">
                    {hardwareResult.recommended_name}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {hardwareResult.recommended_size_gb.toFixed(1)} GB •{" "}
                    {hardwareResult.recommended_gpu_layers === 99
                      ? "Full GPU offload"
                      : hardwareResult.recommended_gpu_layers === 0
                        ? "CPU inference"
                        : `${hardwareResult.recommended_gpu_layers} GPU layers`}
                  </p>
                </div>
                {hardwareResult.can_upgrade && (
                  <Badge variant="outline" className="text-xs">
                    Can upgrade
                  </Badge>
                )}
              </div>
              <p className="text-xs text-muted-foreground bg-muted/40 rounded px-3 py-2">
                {hardwareResult.reason}
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">All Compatible Models</CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              <div className="space-y-2">
                {hardwareResult.all_models.map((m) => {
                  const isRecommended =
                    m.filename === hardwareResult.recommended_filename;
                  const ramOk =
                    hardwareResult.hardware.total_ram_mb / 1024 >=
                    m.ram_required_gb;
                  const downloaded = m.is_split
                    ? downloadedModels.some(
                        (d) => d.filename === m.filename && d.all_parts_present,
                      )
                    : downloadedModels.some((d) => d.filename === m.filename);
                  return (
                    <div
                      key={m.filename}
                      className={`flex items-center justify-between py-2 text-sm ${
                        !ramOk ? "opacity-50" : ""
                      }`}
                    >
                      <div>
                        <span className="font-medium">{m.name}</span>
                        {isRecommended && (
                          <Badge className="ml-2 text-xs bg-blue-500/20 text-blue-600 border-blue-500/30">
                            Recommended
                          </Badge>
                        )}
                      </div>
                      <div className="flex items-center gap-3 text-xs text-muted-foreground">
                        <span>{m.disk_size_gb.toFixed(1)} GB</span>
                        <span>{m.ram_required_gb.toFixed(0)} GB RAM</span>
                        {downloaded ? (
                          <Badge
                            variant="outline"
                            className="text-xs text-green-600 border-green-500/40 gap-1"
                          >
                            <CheckCircle2 className="h-3 w-3" />
                            Downloaded
                          </Badge>
                        ) : (
                          <Badge
                            variant="outline"
                            className="text-xs text-muted-foreground"
                          >
                            Not downloaded
                          </Badge>
                        )}
                        {!ramOk && (
                          <Badge
                            variant="outline"
                            className="text-xs text-amber-500 border-amber-500/30"
                          >
                            Low RAM
                          </Badge>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

// ── Page Root ─────────────────────────────────────────────────────────────

export function LocalModels() {
  // Use the shared app-level LLM context so Voice and other pages see the same server state
  const llm = useLlmApp();

  return (
    <LlmContext.Provider value={llm}>
      <LocalModelsInner />
    </LlmContext.Provider>
  );
}

function LocalModelsInner() {
  const [state, actions] = useLlmContext();
  const { serverStatus } = state;
  const [activeTab, setActiveTab] = useState(() =>
    serverStatus?.running ? "inference" : "setup",
  );

  useEffect(() => {
    if (serverStatus?.running && activeTab === "setup") {
      setActiveTab("inference");
    }
  }, [serverStatus?.running]);

  // Respond to global/tab refresh — reload server status, model list, and prompts
  const handleRefresh = useCallback(() => {
    void actions.getServerStatus();
    void actions.listModels();
    window.dispatchEvent(new CustomEvent("matrx-prompts-changed"));
  }, [actions]);

  usePageRefreshHandler("/local-models", handleRefresh);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Global HF token modal — shown when a download requires a token */}
      <HuggingFaceAccessModal />

      <div className="shrink-0 px-6 pt-6 pb-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <Cpu className="h-6 w-6 text-blue-500" />
              Confidential Chat
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              Private AI chat that runs on your device. Conversations stay on
              your machine and are not sent to the cloud — so you can work with
              sensitive material more comfortably.
            </p>
          </div>
          {serverStatus?.running && (
            <Badge className="bg-green-500/20 text-green-600 border-green-500/30 gap-1.5">
              <Activity className="h-3 w-3" />
              Confidential chat active · port {serverStatus.port}
            </Badge>
          )}
        </div>
      </div>

      <Tabs
        value={activeTab}
        onValueChange={setActiveTab}
        className="flex-1 flex flex-col min-h-0 px-6"
      >
        <TabsList className="w-fit shrink-0">
          <TabsTrigger value="inference" className="gap-1.5">
            <MessageSquare className="h-3.5 w-3.5" />
            Inference
          </TabsTrigger>
          <TabsTrigger value="setup" className="gap-1.5">
            <Zap className="h-3.5 w-3.5" />
            Setup
          </TabsTrigger>
          <TabsTrigger value="models" className="gap-1.5">
            <HardDrive className="h-3.5 w-3.5" />
            Models
          </TabsTrigger>
          <TabsTrigger value="server" className="gap-1.5">
            <Server className="h-3.5 w-3.5" />
            Server
          </TabsTrigger>
          <TabsTrigger value="hardware" className="gap-1.5">
            <Cpu className="h-3.5 w-3.5" />
            Hardware
          </TabsTrigger>
          <TabsTrigger value="media" className="gap-1.5">
            <Image className="h-3.5 w-3.5" />
            Image & Video
          </TabsTrigger>
        </TabsList>

        <div className="flex-1 min-h-0 pt-6 overflow-hidden">
          <TabsContent value="setup" className="m-0 h-full overflow-auto">
            <SetupTab />
          </TabsContent>
          <TabsContent value="models" className="m-0 h-full overflow-auto">
            <ModelsTab />
          </TabsContent>
          <TabsContent value="inference" className="m-0 h-full">
            <InferenceTab />
          </TabsContent>
          <TabsContent value="server" className="m-0 h-full overflow-auto">
            <ServerTab />
          </TabsContent>
          <TabsContent value="hardware" className="m-0 h-full overflow-auto">
            <HardwareTab />
          </TabsContent>
          <TabsContent value="media" className="m-0 h-full overflow-auto">
            <MediaGenTab />
          </TabsContent>
        </div>
      </Tabs>
    </div>
  );
}
