"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import {
  CheckCircle2,
  AlertCircle,
  Info,
  XCircle,
  Download,
  ChevronDown,
  ChevronUp,
  HelpCircle,
  Loader2,
  ExternalLink,
  Star,
  Cpu,
  HardDrive,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  analyzeModelRepo,
  hardwarePayload,
  formatRepoBytes,
  statusColor,
  statusLabel,
  QUANT_DESCRIPTIONS,
  type RepoAnalysisResult,
  type ModelFileEntry,
  type CompatibilityStatus,
} from "@/lib/llm/repoAnalyzer";
import type { LlmHardwareResult, LlmDownloadProgress } from "@/lib/llm/types";

// ── Props ─────────────────────────────────────────────────────────────────

interface ModelRepoAnalyzerProps {
  hardwareResult: LlmHardwareResult | null;
  isDownloading: boolean;
  downloadProgress: LlmDownloadProgress | null;
  /** Called synchronously to enqueue the download. */
  onDownload: (filename: string, urls: string[]) => void;
}

// ── Status icon ───────────────────────────────────────────────────────────

function StatusIcon({ status, recommended }: { status: CompatibilityStatus; recommended: boolean }) {
  if (recommended) return <Star className="h-3.5 w-3.5 text-yellow-500 fill-yellow-500" />;
  switch (status) {
    case "works":
      return <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />;
    case "needs_more_ram":
      return <AlertCircle className="h-3.5 w-3.5 text-yellow-500" />;
    case "accessory_only":
      return <Info className="h-3.5 w-3.5 text-blue-700 dark:text-blue-400" />;
    case "incompatible_format":
      return <XCircle className="h-3.5 w-3.5 text-muted-foreground" />;
  }
}

// ── Quant badge with tooltip ──────────────────────────────────────────────

function QuantBadge({ quant }: { quant: string | null }) {
  if (!quant) return null;
  const desc = QUANT_DESCRIPTIONS[quant] ?? quant;
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge variant="outline" className="text-[10px] px-1.5 py-0 font-mono cursor-help shrink-0">
            {quant}
          </Badge>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-[220px] text-center">
          {desc}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

// ── Split parts indicator ─────────────────────────────────────────────────

function SplitBadge({ totalParts }: { totalParts: number }) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-blue-500 border-blue-500/30 cursor-help shrink-0">
            {totalParts} parts
          </Badge>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-[240px] text-center">
          This model is split across {totalParts} files. All parts download automatically — you only click once.
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

// ── Single file row ───────────────────────────────────────────────────────

interface FileRowProps {
  entry: ModelFileEntry;
  effectiveCapacityGb: number | null;
  isDownloading: boolean;
  activeDownloadFile: string | null;
  downloadProgress: LlmDownloadProgress | null;
  onDownload: (entry: ModelFileEntry) => void;
}

function FileRow({
  entry,
  effectiveCapacityGb,
  isDownloading,
  activeDownloadFile,
  downloadProgress,
  onDownload,
}: FileRowProps) {
  const isThisDownloading = isDownloading && activeDownloadFile === entry.filename;
  const canDownload =
    (entry.compatibility_status === "works" || entry.recommended) &&
    entry.role === "main_model" &&
    entry.format === "gguf";

  const ramNeeded = entry.ram_required_gb;
  const ramAvailable = effectiveCapacityGb;

  return (
    <div
      className={`
        flex items-center gap-3 px-4 py-3 border-b last:border-b-0 text-sm transition-colors
        ${entry.recommended ? "bg-yellow-500/5 border-yellow-500/10" : ""}
        ${entry.compatibility_status === "incompatible_format" ? "opacity-50" : ""}
      `}
    >
      {/* Status icon */}
      <div className="shrink-0 mt-0.5">
        <StatusIcon status={entry.compatibility_status} recommended={entry.recommended} />
      </div>

      {/* File info */}
      <div className="flex-1 min-w-0 space-y-0.5">
        <div className="flex items-center gap-1.5 flex-wrap">
          {entry.recommended && (
            <Badge className="text-[10px] px-1.5 py-0 bg-yellow-500/15 text-yellow-600 border-yellow-500/30 shrink-0">
              Best for your machine
            </Badge>
          )}
          <span
            className={`font-mono text-xs truncate ${
              entry.compatibility_status === "incompatible_format"
                ? "text-muted-foreground"
                : "text-foreground"
            }`}
            title={entry.filename}
          >
            {entry.filename}
          </span>
          {entry.quant && <QuantBadge quant={entry.quant} />}
          {entry.is_split && entry.total_parts && (
            <SplitBadge totalParts={entry.total_parts} />
          )}
        </div>

        {/* Compatibility reason */}
        <p className="text-[11px] text-muted-foreground leading-relaxed">
          {entry.compatibility_reason}
        </p>

        {/* Download progress bar */}
        {isThisDownloading && downloadProgress && (
          <div className="pt-1">
            <div className="flex items-center justify-between text-[10px] text-muted-foreground mb-1">
              <span>
                {downloadProgress.total_parts > 1
                  ? `Part ${downloadProgress.part}/${downloadProgress.total_parts}`
                  : "Downloading"}
              </span>
              <span>{downloadProgress.percent.toFixed(0)}%</span>
            </div>
            <div className="h-1 w-full rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full bg-primary transition-all duration-300"
                style={{ width: `${downloadProgress.percent}%` }}
              />
            </div>
          </div>
        )}
      </div>

      {/* Size + RAM */}
      <div className="text-right shrink-0 space-y-0.5 hidden sm:block">
        <p className="text-xs font-medium tabular-nums">
          {formatRepoBytes(entry.total_size_bytes)}
        </p>
        {ramAvailable !== null && entry.format === "gguf" && entry.role === "main_model" && (
          <p
            className={`text-[10px] tabular-nums ${
              ramNeeded <= ramAvailable ? "text-green-600" : "text-yellow-600"
            }`}
          >
            ~{ramNeeded.toFixed(1)} GB RAM
          </p>
        )}
      </div>

      {/* Status badge */}
      <div className="shrink-0 hidden md:block">
        <Badge
          variant="outline"
          className={`text-[10px] px-1.5 py-0 ${statusColor(entry.compatibility_status)}`}
        >
          {statusLabel(entry.compatibility_status)}
        </Badge>
      </div>

      {/* Download button */}
      <div className="shrink-0">
        {canDownload ? (
          <Button
            size="sm"
            variant={entry.recommended ? "default" : "outline"}
            className="h-7 px-2.5 text-xs gap-1"
            onClick={() => onDownload(entry)}
            disabled={isDownloading}
          >
            {isThisDownloading ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Download className="h-3 w-3" />
            )}
            {isThisDownloading ? "Downloading…" : "Download"}
          </Button>
        ) : (
          <div className="h-7 w-20" />
        )}
      </div>
    </div>
  );
}

// ── Collapsed accessory / incompatible section ────────────────────────────

function CollapsedSection({
  label,
  entries,
}: {
  label: string;
  entries: ModelFileEntry[];
}) {
  const [open, setOpen] = useState(false);
  if (entries.length === 0) return null;
  return (
    <div className="border-t">
      <button
        className="w-full flex items-center gap-2 px-4 py-2 text-xs text-muted-foreground hover:text-foreground hover:bg-muted/20 transition-colors"
        onClick={() => setOpen((v) => !v)}
      >
        {open ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        <span>
          {label} ({entries.length})
        </span>
      </button>
      {open && (
        <div className="divide-y divide-border/50">
          {entries.map((e) => (
            <div
              key={e.filename}
              className="flex items-center gap-3 px-4 py-2 text-xs text-muted-foreground opacity-60"
            >
              <StatusIcon status={e.compatibility_status} recommended={false} />
              <span className="font-mono flex-1 truncate">{e.filename}</span>
              <span className="shrink-0">{formatRepoBytes(e.size_bytes)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Help panel ─────────────────────────────────────────────────────────────

function HelpPanel({
  hardwareLabel,
  effectiveCapacityGb,
}: {
  hardwareLabel: string | null;
  effectiveCapacityGb: number | null;
}) {
  return (
    <div className="rounded-lg border bg-muted/20 px-4 py-3 space-y-3 text-xs text-muted-foreground">
      {/* Machine summary */}
      {hardwareLabel && (
        <div className="flex items-start gap-2 pb-2 border-b border-border/50">
          <Cpu className="h-3.5 w-3.5 mt-0.5 shrink-0 text-foreground" />
          <div>
            <p className="font-medium text-foreground">{hardwareLabel}</p>
            {effectiveCapacityGb !== null && (
              <p className="mt-0.5">
                Effective capacity: <span className="font-medium text-foreground">~{effectiveCapacityGb} GB</span> — models up to this size will run on your machine.
              </p>
            )}
          </div>
        </div>
      )}

      <div className="space-y-2 leading-relaxed">
        <p>
          <span className="font-medium text-foreground">What works:</span> Only{" "}
          <code className="bg-muted px-0.5 rounded">.gguf</code> files can be loaded. Safetensors,
          .bin, and .onnx files are for other runtimes and will not work here.
        </p>

        <p>
          <span className="font-medium text-foreground">Quant levels:</span>{" "}
          Q4_K_M is the best starting point for most users — good quality, reasonable size.
          Q8_0 is higher quality but needs roughly twice the RAM.
          BF16/F16 are full-precision and enormous — only practical on workstations with 64+ GB VRAM.
        </p>

        <p>
          <span className="font-medium text-foreground">Split models:</span> Large models are
          split into multiple parts (e.g. <code className="bg-muted px-0.5 rounded">-00001-of-00003</code>).
          Click Download once — all parts are fetched automatically.
        </p>

        <p>
          <span className="font-medium text-foreground">mmproj / vision files:</span> These are
          accessories for image input. They aren&apos;t needed for text chat and cannot be used as
          standalone models.
        </p>

        <p>
          <span className="font-medium text-foreground">RAM estimate:</span> The app calculates
          ~1.35× the file size as the RAM needed (for model buffers and KV cache). Your effective
          capacity already accounts for any GPU you have.
        </p>
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

export function ModelRepoAnalyzer({
  hardwareResult,
  isDownloading,
  downloadProgress,
  onDownload,
}: ModelRepoAnalyzerProps) {
  const [url, setUrl] = useState("");
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [result, setResult] = useState<RepoAnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showHelp, setShowHelp] = useState(false);
  const [elapsedSec, setElapsedSec] = useState(0);
  // Track which file is actively downloading by watching downloadProgress from the hook
  const activeDownloadFile = isDownloading && downloadProgress ? downloadProgress.filename : null;

  const hw = hardwareResult ? hardwarePayload(hardwareResult) : null;

  // Client-side ceiling on the analyze fetch. The backend HF call has its own
  // 20s timeout; 45s sits comfortably above it so a genuine slow-but-alive
  // request survives while a truly hung fetch can't spin forever.
  const ANALYZE_TIMEOUT_MS = 45_000;
  const abortRef = useRef<AbortController | null>(null);
  const timedOutRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Elapsed-seconds ticker — runs only while analyzing.
  useEffect(() => {
    if (!isAnalyzing) return;
    const started = Date.now();
    setElapsedSec(0);
    const id = setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, [isAnalyzing]);

  // Abort any in-flight request if the component unmounts.
  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      abortRef.current?.abort();
    };
  }, []);

  const handleCancel = useCallback(() => {
    // User-initiated abort — not a timeout, so handleAnalyze resets silently.
    timedOutRef.current = false;
    abortRef.current?.abort();
  }, []);

  const handleAnalyze = useCallback(async () => {
    const trimmed = url.trim();
    if (!trimmed) return;

    // Fresh controller + timeout for this run.
    const controller = new AbortController();
    abortRef.current = controller;
    timedOutRef.current = false;
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      timedOutRef.current = true;
      controller.abort();
    }, ANALYZE_TIMEOUT_MS);

    setIsAnalyzing(true);
    setError(null);
    setResult(null);
    try {
      const res = await analyzeModelRepo(trimmed, hw, controller.signal);
      setResult(res);
    } catch (e) {
      const aborted =
        controller.signal.aborted ||
        (e instanceof DOMException && e.name === "AbortError") ||
        (e instanceof Error && e.name === "AbortError");
      if (aborted && timedOutRef.current) {
        setError(
          `Analysis timed out after ${ANALYZE_TIMEOUT_MS / 1000}s. The engine may be busy or unreachable — check that it's running, then retry.`,
        );
      } else if (aborted) {
        // User cancelled — leave the form clean for another attempt.
        setError(null);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      abortRef.current = null;
      setIsAnalyzing(false);
    }
  }, [url, hw]);

  const handleDownload = useCallback(
    (entry: ModelFileEntry) => {
      setError(null);
      onDownload(entry.filename, entry.download_urls);
      // onDownloadComplete (listModels) is called by the queue system when done;
      // no need to call it here since queueDownload is synchronous.
    },
    [onDownload]
  );

  // Partition entries for display
  const mainEntries = result?.files.filter(
    (f) => f.compatibility_status === "works" || f.compatibility_status === "needs_more_ram"
  ) ?? [];
  const accessoryEntries = result?.files.filter((f) => f.compatibility_status === "accessory_only") ?? [];
  const incompatibleEntries = result?.files.filter(
    (f) => f.compatibility_status === "incompatible_format"
  ) ?? [];

  const hardwareLabel: string | null = result?.hardware_label ?? (
    hw
      ? hw.is_apple_silicon
        ? `Apple Silicon, ${(hw.total_ram_mb / 1024).toFixed(0)} GB`
        : hw.supports_cuda && hw.gpu_vram_mb
        ? `NVIDIA GPU, ${(hw.gpu_vram_mb / 1024).toFixed(0)} GB VRAM`
        : `CPU, ${(hw.total_ram_mb / 1024).toFixed(0)} GB RAM`
      : null
  );

  return (
    <div className="rounded-lg border bg-card shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b bg-muted/20">
        <Download className="h-4 w-4 text-muted-foreground shrink-0" />
        <span className="text-sm font-medium">Model Repository Analyzer</span>
        <button
          className="ml-auto text-muted-foreground hover:text-foreground transition-colors"
          onClick={() => setShowHelp((v) => !v)}
          title="How it works"
        >
          <HelpCircle className="h-4 w-4" />
        </button>
      </div>

      <div className="p-4 space-y-3">
        {/* Help panel */}
        {showHelp && (
          <HelpPanel
            hardwareLabel={result?.hardware_label ?? hardwareLabel}
            effectiveCapacityGb={result?.effective_capacity_gb ?? null}
          />
        )}

        {/* URL input + machine context */}
        <div className="space-y-1.5">
          <div className="flex gap-2">
            <Input
              placeholder="https://huggingface.co/owner/model-name"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleAnalyze()}
              className="text-xs h-8 font-mono flex-1"
              disabled={isAnalyzing}
            />
            {isAnalyzing ? (
              <>
                <Button
                  size="sm"
                  className="h-8 px-3 shrink-0 gap-1.5"
                  disabled
                >
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  <span className="tabular-nums">{elapsedSec}s</span>
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 px-3 shrink-0"
                  onClick={handleCancel}
                >
                  Cancel
                </Button>
              </>
            ) : (
              <Button
                size="sm"
                className="h-8 px-3 shrink-0"
                onClick={handleAnalyze}
                disabled={!url.trim()}
              >
                Analyze
              </Button>
            )}
          </div>

          {/* Analyzing status line */}
          {isAnalyzing && (
            <p className="text-[10px] text-muted-foreground flex items-center gap-1">
              <Loader2 className="h-3 w-3 animate-spin" />
              Analyzing repository… {elapsedSec}s elapsed (times out at{" "}
              {ANALYZE_TIMEOUT_MS / 1000}s)
            </p>
          )}

          {/* Hardware context line */}
          {hw && !result && (
            <p className="text-[10px] text-muted-foreground flex items-center gap-1">
              <HardDrive className="h-3 w-3" />
              Results will be personalized for:{" "}
              <span className="font-medium text-foreground/70">{hardwareLabel}</span>
            </p>
          )}
        </div>

        {/* Error */}
        {error && !isAnalyzing && (
          <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            <AlertCircle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            <span className="flex-1">{error}</span>
            <Button
              size="sm"
              variant="outline"
              className="h-6 px-2 text-[11px] shrink-0"
              onClick={handleAnalyze}
              disabled={!url.trim()}
            >
              Retry
            </Button>
          </div>
        )}
      </div>

      {/* Results */}
      {result && (
        <>
          {/* Repo summary */}
          <div className="px-4 pb-3 pt-0 flex items-start justify-between gap-3">
            <div className="space-y-0.5 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <p className="text-sm font-semibold truncate">{result.model_name}</p>
                {result.author && (
                  <span className="text-xs text-muted-foreground">by {result.author}</span>
                )}
                {result.architecture && (
                  <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                    {result.architecture}
                  </Badge>
                )}
              </div>
              {result.hardware_label && (
                <p className="text-[10px] text-muted-foreground flex items-center gap-1">
                  <Cpu className="h-3 w-3" />
                  Analyzed for: <span className="font-medium">{result.hardware_label}</span>
                  {result.effective_capacity_gb !== null && (
                    <> &mdash; ~{result.effective_capacity_gb} GB capacity</>
                  )}
                </p>
              )}
            </div>
            <a
              href={result.repo_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-muted-foreground hover:text-foreground transition-colors shrink-0"
              title="Open on HuggingFace"
            >
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          </div>

          <Separator />

          {/* Column headers */}
          <div className="grid px-4 py-1.5 text-[10px] font-medium text-muted-foreground uppercase tracking-wide border-b bg-muted/10">
            <div className="flex items-center gap-3">
              <div className="w-3.5" />
              <span className="flex-1">File</span>
              <span className="shrink-0 w-16 text-right hidden sm:block">Size</span>
              <span className="shrink-0 w-20 text-right hidden md:block">Status</span>
              <span className="shrink-0 w-20 text-right">Action</span>
            </div>
          </div>

          {/* Compatible + needs-more-ram entries */}
          {mainEntries.length === 0 ? (
            <div className="px-4 py-6 text-center space-y-2">
              <XCircle className="h-6 w-6 mx-auto text-muted-foreground/40" />
              <p className="text-sm font-medium text-foreground/70">No GGUF files found</p>
              <p className="text-xs text-muted-foreground max-w-sm mx-auto leading-relaxed">
                This repository doesn&apos;t contain any <code className="bg-muted px-0.5 rounded">.gguf</code> files that can be loaded here.
                It may use safetensors or another format. Try searching for a GGUF conversion of this model — many popular models have community GGUF versions on HuggingFace.
              </p>
            </div>
          ) : (
            <div className="divide-y divide-border/50">
              {mainEntries.map((entry) => (
                <FileRow
                  key={entry.filename}
                  entry={entry}
                  effectiveCapacityGb={result.effective_capacity_gb}
                  isDownloading={isDownloading}
                  activeDownloadFile={activeDownloadFile}
                  downloadProgress={downloadProgress}
                  onDownload={handleDownload}
                />
              ))}
            </div>
          )}

          {/* Accessory files — collapsed */}
          <CollapsedSection
            label="Accessory files (vision, adapters)"
            entries={accessoryEntries}
          />

          {/* Incompatible files — collapsed */}
          <CollapsedSection
            label="Incompatible formats (safetensors, etc.)"
            entries={incompatibleEntries}
          />

          {/* Footer stats */}
          <div className="px-4 py-2 border-t bg-muted/10 text-[10px] text-muted-foreground flex items-center justify-between">
            <span>
              {result.total_files} total file{result.total_files !== 1 ? "s" : ""} in repo &mdash;{" "}
              {mainEntries.filter((e) => e.compatibility_status === "works").length} compatible
            </span>
            <span className="capitalize">{result.provider}</span>
          </div>
        </>
      )}
    </div>
  );
}
