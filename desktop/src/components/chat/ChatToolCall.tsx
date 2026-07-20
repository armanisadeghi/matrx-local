import type { ToolCall, ToolCallResult } from "@/hooks/use-chat";
import { ToolExecutionCard } from "@/components/tools/ToolExecutionCard";

interface ChatToolCallProps {
  toolCall: ToolCall;
  result?: ToolCallResult;
  statusMessage?: string;
  isDelegated?: boolean;
  onReferencePaths?: (paths: string[]) => void;
}

export function ChatToolCall({
  toolCall,
  result,
  statusMessage,
  isDelegated,
  onReferencePaths,
}: ChatToolCallProps) {
  return (
    <ToolExecutionCard
      toolCall={toolCall}
      {...(result ? { result } : {})}
      {...(statusMessage ? { statusMessage } : {})}
      {...(isDelegated !== undefined ? { isDelegated } : {})}
      {...(onReferencePaths ? { onReferencePaths } : {})}
    />
  );
}
