import { ToolExecutionCard } from "@/components/tools/ToolExecutionCard";
import { safeToolOutput } from "@/features/filesystem/tool-results";
import type { ToolCallResult } from "@/hooks/use-chat";
import type { ToolImageData, ToolMediaArtifact } from "@/lib/api";

interface ToolOutputProps {
  result: unknown;
  /** Kept for callers whose schemas describe presentation; canonical rendering is data-driven. */
  outputType?: string;
  elapsedMs?: number;
  toolName?: string;
}

function normalizeResult(result: unknown): ToolCallResult {
  const item = result && typeof result === "object" && !Array.isArray(result)
    ? (result as Record<string, unknown>)
    : null;
  const type = item?.type === "error" ? "error" : "success";
  return {
    tool_call_id: "tool-playground",
    type,
    output: safeToolOutput(item?.output ?? result),
    ...(item?.metadata && typeof item.metadata === "object" && !Array.isArray(item.metadata)
      ? { metadata: item.metadata as Record<string, unknown> }
      : {}),
    ...(item?.artifact ? { artifact: item.artifact as ToolMediaArtifact } : {}),
    ...(item?.image ? { image: item.image as ToolImageData } : {}),
  };
}

/** Tool playground adapter over the same execution card used by chat. */
export function ToolOutput({ result, elapsedMs, toolName = "Tool" }: ToolOutputProps) {
  if (!result) return null;
  return (
    <ToolExecutionCard
      toolCall={{ id: "tool-playground", name: toolName, input: {} }}
      result={normalizeResult(result)}
      {...(elapsedMs != null ? { elapsedMs } : {})}
      defaultExpanded
    />
  );
}
