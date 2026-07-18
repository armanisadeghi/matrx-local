import { useMemo, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clipboard,
  Loader2,
  Wrench,
  XCircle,
} from "lucide-react";
import { MediaThumb } from "@/components/media/MediaThumb";
import {
  descriptorFromToolArtifact,
  descriptorFromToolImage,
} from "@/components/media/types";
import { EnginePlaces, FilesystemResultView } from "@/features/filesystem/FilesystemResultView";
import {
  isFilesystemTool,
  normalizeFilesystemResult,
  parseJsonValue,
  safeToolOutput,
} from "@/features/filesystem/tool-results";
import type { ToolCall, ToolCallResult } from "@/hooks/use-chat";
import { engine, type ToolMediaArtifact } from "@/lib/api";
import { cn } from "@/lib/utils";

export interface ToolExecutionCardProps {
  toolCall: ToolCall;
  result?: ToolCallResult;
  elapsedMs?: number;
  defaultExpanded?: boolean;
  onReferencePaths?: (paths: string[]) => void;
}

function artifactFromOutput(output: string): ToolMediaArtifact | null {
  const value = parseJsonValue(output);
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const item = value as Record<string, unknown>;
  return item.kind === "image_ref" && typeof item.artifact_id === "string"
    ? (item as unknown as ToolMediaArtifact)
    : null;
}

function GenericOutput({ output, error }: { output: string; error: boolean }) {
  const parsed = parseJsonValue(output);
  return (
    <pre
      className={cn(
        "max-h-80 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/50 p-2 font-mono text-[11px] leading-relaxed",
        error && "bg-destructive/5 text-destructive",
      )}
    >
      {safeToolOutput(parsed)}
    </pre>
  );
}

/** The canonical card for live chat, hydrated chat, and the tool playground. */
export function ToolExecutionCard({
  toolCall,
  result,
  elapsedMs,
  defaultExpanded = false,
  onReferencePaths,
}: ToolExecutionCardProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const isSuccess = result?.type === "success";
  const isError = result?.type === "error";
  const filesystemTool = isFilesystemTool(toolCall.name);
  const filesystem = result ? normalizeFilesystemResult(result, toolCall.name) : null;
  const artifact = result?.artifact ?? (result ? artifactFromOutput(result.output) : null);
  const media = useMemo(() => {
    if (artifact && engine.engineUrl) {
      return descriptorFromToolArtifact(
        artifact,
        `${engine.engineUrl}/artifacts/${encodeURIComponent(artifact.artifact_id)}/content`,
      );
    }
    if (result?.image) return descriptorFromToolImage(result.image, toolCall.id);
    return null;
  }, [artifact, result?.image, toolCall.id]);

  return (
    <section
      className={cn(
        "overflow-hidden rounded-lg border text-xs",
        isSuccess && "border-emerald-500/25 bg-emerald-500/5",
        isError && "border-destructive/25 bg-destructive/5",
        !result && "border-border bg-muted/30",
      )}
      aria-label={`${toolCall.name} tool execution`}
    >
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
      >
        {!result && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
        {isSuccess && <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />}
        {isError && <XCircle className="h-3.5 w-3.5 text-destructive" />}
        <Wrench className="h-3 w-3 text-muted-foreground" />
        <span className="min-w-0 truncate font-medium">{toolCall.name}</span>
        {elapsedMs != null && (
          <span className="text-[10px] tabular-nums text-muted-foreground">
            {elapsedMs < 1000 ? `${elapsedMs} ms` : `${(elapsedMs / 1000).toFixed(1)} s`}
          </span>
        )}
        <span className="ml-auto text-muted-foreground">
          {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        </span>
      </button>

      {media && (
        <div className="border-t p-2">
          <MediaThumb item={media} variant="gallery" className="max-h-72 rounded border bg-black/20" />
        </div>
      )}

      {result && filesystem && (
        <div className="border-t">
          <FilesystemResultView result={filesystem} {...(onReferencePaths ? { onReference: onReferencePaths } : {})} />
        </div>
      )}

      {filesystemTool && result && (
        <details className="border-t bg-background/50">
          <summary className="cursor-pointer px-3 py-2 text-[11px] font-medium text-muted-foreground">
            Places on this computer
          </summary>
          <EnginePlaces {...(onReferencePaths ? { onReference: onReferencePaths } : {})} />
        </details>
      )}

      {expanded && (
        <div className="space-y-2 border-t px-3 py-2">
          <div>
            <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Input</span>
            <pre className="mt-1 max-h-48 overflow-auto rounded bg-muted/50 p-2 font-mono text-[11px] leading-relaxed">
              {JSON.stringify(toolCall.input, null, 2)}
            </pre>
          </div>
          {result && !filesystem && !media && (
            <div>
              <div className="mb-1 flex items-center justify-between">
                <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Output</span>
                <button
                  type="button"
                  className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                  title="Copy output"
                  onClick={() => void navigator.clipboard.writeText(result.output)}
                >
                  <Clipboard className="h-3.5 w-3.5" />
                </button>
              </div>
              <GenericOutput output={result.output} error={isError} />
            </div>
          )}
        </div>
      )}

      {filesystemTool && !result && expanded && (
        <div className="border-t px-3 py-2 text-[11px] text-muted-foreground">Waiting for filesystem results…</div>
      )}
    </section>
  );
}
