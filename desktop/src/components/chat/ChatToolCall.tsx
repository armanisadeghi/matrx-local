import type { ToolCall, ToolCallResult } from "@/hooks/use-chat";
import { ToolExecutionCard } from "@/components/tools/ToolExecutionCard";

interface ChatToolCallProps {
  toolCall: ToolCall;
  result?: ToolCallResult;
  onReferencePaths?: (paths: string[]) => void;
}

export function ChatToolCall({ toolCall, result, onReferencePaths }: ChatToolCallProps) {
  return (
    <ToolExecutionCard
      toolCall={toolCall}
      {...(result ? { result } : {})}
      {...(onReferencePaths ? { onReferencePaths } : {})}
    />
  );
}
