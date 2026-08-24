import { useEffect, useRef, useState } from "react";
import {
  Copy,
  Check,
  RotateCcw,
  ThumbsUp,
  ThumbsDown,
  Volume2,
  VolumeX,
  AlertTriangle,
  Info,
} from "lucide-react";
import type { ChatMessage, ToolCallResult } from "@/hooks/use-chat";
import { MessageMarkdown } from "./MessageMarkdown";
import { KindBlockView } from "@/features/content-ir/render/KindBlockView";
import type { ChatMessageBlock, ChatToolBlock } from "@/lib/chat-blocks";
import { ChatToolCall } from "./ChatToolCall";

interface ChatMessagesProps {
  messages: ChatMessage[];
  isStreaming: boolean;
  ttsReadAloudEnabled?: boolean;
  readingMessageId?: string | null;
  onReadAloud?: (messageId: string, content: string) => void;
  onStopReadAloud?: () => void;
  onReferencePaths?: (paths: string[]) => void;
}

function CopyButton({ text }: { text: string }) {
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

function MessageActions({
  text,
  messageId,
  ttsEnabled,
  isReading,
  onReadAloud,
  onStopReadAloud,
}: {
  text: string;
  messageId: string;
  ttsEnabled?: boolean;
  isReading?: boolean;
  onReadAloud?: (messageId: string, content: string) => void;
  onStopReadAloud?: () => void;
}) {
  return (
    <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
      <CopyButton text={text} />
      {ttsEnabled && (
        <button
          onClick={() => {
            if (isReading) {
              onStopReadAloud?.();
            } else {
              onReadAloud?.(messageId, text);
            }
          }}
          className={`rounded-md p-1.5 transition-colors ${
            isReading
              ? "text-primary hover:text-primary/80"
              : "text-muted-foreground hover:text-foreground"
          }`}
          title={isReading ? "Stop reading" : "Read aloud"}
        >
          {isReading ? (
            <VolumeX className="h-3.5 w-3.5" />
          ) : (
            <Volume2 className="h-3.5 w-3.5" />
          )}
        </button>
      )}
      <button
        className="rounded-md p-1.5 text-muted-foreground transition-colors hover:text-foreground"
        title="Good response"
      >
        <ThumbsUp className="h-3.5 w-3.5" />
      </button>
      <button
        className="rounded-md p-1.5 text-muted-foreground transition-colors hover:text-foreground"
        title="Bad response"
      >
        <ThumbsDown className="h-3.5 w-3.5" />
      </button>
      <button
        className="rounded-md p-1.5 text-muted-foreground transition-colors hover:text-foreground"
        title="Retry"
      >
        <RotateCcw className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function UserMessage({ message }: { message: ChatMessage }) {
  return (
    <div className="group py-5 px-4 md:px-0">
      <div className="mx-auto max-w-3xl">
        {/* Label */}
        <div className="mb-1.5 flex items-center gap-2">
          <span className="text-xs font-semibold">You</span>
        </div>
        {/* Content */}
        <div className="rounded-2xl bg-muted px-4 py-3">
          <p className="whitespace-pre-wrap text-[0.9375rem] leading-relaxed">
            {message.content}
          </p>
        </div>
      </div>
    </div>
  );
}

/** Map an ordered tool block to the ToolExecutionCard contract, preferring the
 * richer legacy tool_results entry (image/artifact/action_needed) by call_id. */
function toolBlockResult(
  block: ChatToolBlock,
  richResults: ToolCallResult[] | undefined,
): ToolCallResult | undefined {
  const rich = richResults?.find((r) => r.tool_call_id === block.callId);
  if (rich) return rich;
  if (block.phase === "complete") {
    return {
      tool_call_id: block.callId,
      type: "success",
      output:
        typeof block.output === "string"
          ? block.output
          : JSON.stringify(block.output ?? "", null, 2),
    };
  }
  if (block.phase === "error") {
    return {
      tool_call_id: block.callId,
      type: "error",
      output: block.errorMessage ?? "Tool execution failed",
    };
  }
  return undefined;
}

/** Ordered stream blocks: text / thinking / tool cards / errors rendered at
 * their true arrival position — never grouped by type. */
function MessageBlocks({
  blocks,
  message,
  onReferencePaths,
}: {
  blocks: ChatMessageBlock[];
  message: ChatMessage;
  onReferencePaths?: (paths: string[]) => void;
}) {
  return (
    <div className="space-y-2">
      {blocks.map((block, index) => {
        const key = `${message.id}-block-${index}`;
        switch (block.type) {
          case "text":
            return (
              <div
                key={key}
                className="chat-prose text-[0.9375rem] leading-[1.7]"
              >
                <MessageMarkdown text={block.content} />
              </div>
            );
          case "kind":
            // Server-built structured content — routed through the SHARED
            // kind route to a bundled component, or the honest generic floor.
            return (
              <KindBlockView
                key={key}
                blockId={block.blockId}
                type={block.blockType}
                content={block.content}
                metadata={block.metadata}
                complete={block.complete}
              />
            );
          case "thinking":
            return (
              <details
                key={key}
                className="rounded-lg border border-border/70 bg-muted/20 px-3 py-2 text-xs text-muted-foreground"
              >
                <summary className="cursor-pointer font-medium text-foreground/80">
                  Reasoning
                </summary>
                <div className="mt-2 whitespace-pre-wrap leading-relaxed">
                  {block.content}
                </div>
              </details>
            );
          case "tool_call": {
            const result = toolBlockResult(block, message.tool_results);
            const statusMessage = block.progress[block.progress.length - 1];
            return (
              <ChatToolCall
                key={block.callId}
                toolCall={{
                  id: block.callId,
                  name: block.toolName,
                  input: block.input,
                }}
                {...(result ? { result } : {})}
                {...(statusMessage ? { statusMessage } : {})}
                isDelegated={block.phase === "delegated"}
                {...(onReferencePaths ? { onReferencePaths } : {})}
              />
            );
          }
          case "error":
            return (
              <div
                key={key}
                className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive"
              >
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span className="whitespace-pre-wrap">{block.message}</span>
              </div>
            );
        }
      })}
    </div>
  );
}

function AssistantMessage({
  message,
  ttsEnabled,
  isReading,
  onReadAloud,
  onStopReadAloud,
  onReferencePaths,
}: {
  message: ChatMessage;
  ttsEnabled?: boolean;
  isReading?: boolean;
  onReadAloud?: (messageId: string, content: string) => void;
  onStopReadAloud?: () => void;
  onReferencePaths?: (paths: string[]) => void;
}) {
  const hasBlocks = !!message.blocks?.length;
  return (
    <div className="group py-5 px-4 md:px-0">
      <div className="mx-auto max-w-3xl">
        {/* Label row */}
        <div className="mb-1.5 flex items-center gap-2">
          {/* AI Matrx logo mark */}
          <div className="flex h-5 w-5 items-center justify-center rounded-full bg-primary/85">
            <svg
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="white"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
            </svg>
          </div>
          <span className="text-xs font-semibold">AI Matrx</span>
          {message.model && !message.isStreaming && (
            <span className="text-[10px] text-muted-foreground">
              {message.model}
            </span>
          )}
        </div>

        {/* Content — ordered stream blocks when available (live cloud chat),
            legacy flat content otherwise (hydrated / local chat). */}
        {hasBlocks ? (
          <div>
            <MessageBlocks
              blocks={message.blocks!}
              message={message}
              {...(onReferencePaths ? { onReferencePaths } : {})}
            />
            {message.isStreaming && (
              <span className="ml-0.5 inline-block h-[1.1em] w-[2px] animate-pulse bg-primary align-text-bottom" />
            )}
          </div>
        ) : (
          <div className="chat-prose text-[0.9375rem] leading-[1.7]">
            {message.content ? (
              <MessageMarkdown text={message.content} />
            ) : message.streamStatus ? (
              <p className="text-sm text-muted-foreground">
                {message.streamStatus}
              </p>
            ) : null}

            {/* Streaming cursor */}
            {message.isStreaming && (
              <span className="ml-0.5 inline-block h-[1.1em] w-[2px] animate-pulse bg-primary align-text-bottom" />
            )}
          </div>
        )}

        {hasBlocks &&
          !message.content &&
          message.isStreaming &&
          message.streamStatus && (
            <p className="mt-1 text-sm text-muted-foreground">
              {message.streamStatus}
            </p>
          )}

        {!hasBlocks && message.reasoning && (
          <details className="mt-3 rounded-lg border border-border/70 bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
            <summary className="cursor-pointer font-medium text-foreground/80">
              Reasoning
            </summary>
            <div className="mt-2 whitespace-pre-wrap leading-relaxed">
              {message.reasoning}
            </div>
          </details>
        )}

        {(message.error || message.streamDiagnostics?.length) && (
          <div className="mt-3 space-y-1.5">
            {message.error && (
              <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span className="whitespace-pre-wrap">{message.error}</span>
              </div>
            )}
            {message.streamDiagnostics?.map((item, index) => (
              <div
                key={`${message.id}-diag-${index}`}
                className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300"
              >
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span className="whitespace-pre-wrap">{item}</span>
              </div>
            ))}
          </div>
        )}

        {/* Tool calls (legacy flat list — block-rendered messages place tool
            cards inline at their stream position instead) */}
        {!hasBlocks && message.tool_calls && message.tool_calls.length > 0 && (
          <div className="mt-3 space-y-2">
            {message.tool_calls.map((tc) => {
              const result = message.tool_results?.find(
                (r) => r.tool_call_id === tc.id,
              );
              return (
                <ChatToolCall
                  key={tc.id}
                  toolCall={tc}
                  {...(result !== undefined ? { result } : {})}
                  {...(onReferencePaths ? { onReferencePaths } : {})}
                />
              );
            })}
          </div>
        )}

        {/* Action buttons */}
        {!message.isStreaming && message.content && (
          <div className="mt-2">
            <MessageActions
              text={message.content}
              messageId={message.id}
              {...(ttsEnabled !== undefined ? { ttsEnabled } : {})}
              {...(isReading !== undefined ? { isReading } : {})}
              {...(onReadAloud !== undefined ? { onReadAloud } : {})}
              {...(onStopReadAloud !== undefined ? { onStopReadAloud } : {})}
            />
          </div>
        )}
      </div>
    </div>
  );
}

export function TypingIndicator() {
  return (
    <div className="py-5 px-4 md:px-0">
      <div className="mx-auto max-w-3xl">
        <div className="mb-1.5 flex items-center gap-2">
          <div className="flex h-5 w-5 items-center justify-center rounded-full bg-primary/85">
            <svg
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="white"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
            </svg>
          </div>
          <span className="text-xs font-semibold">AI Matrx</span>
        </div>
        <div className="flex items-center gap-1.5 py-1">
          <div className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/50 [animation-delay:0ms]" />
          <div className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/50 [animation-delay:150ms]" />
          <div className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/50 [animation-delay:300ms]" />
        </div>
      </div>
    </div>
  );
}

export function ChatMessages({
  messages,
  isStreaming,
  ttsReadAloudEnabled,
  readingMessageId,
  onReadAloud,
  onStopReadAloud,
  onReferencePaths,
}: ChatMessagesProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "instant" });
  }, [messages, isStreaming]);

  if (messages.length === 0) {
    return null;
  }

  return (
    <div className="py-2">
      {messages.map((msg) =>
        msg.role === "user" ? (
          <UserMessage key={msg.id} message={msg} />
        ) : (
          <AssistantMessage
            key={msg.id}
            message={msg}
            {...(ttsReadAloudEnabled !== undefined
              ? { ttsEnabled: ttsReadAloudEnabled }
              : {})}
            isReading={readingMessageId === msg.id}
            {...(onReadAloud !== undefined ? { onReadAloud } : {})}
            {...(onStopReadAloud !== undefined ? { onStopReadAloud } : {})}
            {...(onReferencePaths ? { onReferencePaths } : {})}
          />
        ),
      )}
      <div ref={bottomRef} />
    </div>
  );
}
