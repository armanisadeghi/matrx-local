/**
 * lib/chat-blocks.ts
 *
 * Ordered content blocks for streamed assistant messages — the desktop port of
 * matrx-frontend's `lib/chat-protocol` canonical-block model.
 *
 * DESIGN (mirrors matrx-frontend, the reference implementation):
 * • ONE ordered block list per message. Text, thinking, tool calls, render
 *   blocks, and errors are interleaved in true arrival order — never grouped
 *   into parallel accumulators that lose interleaving.
 * • A tool call is a SINGLE block keyed by `call_id`, anchored at the position
 *   where it first appeared. Every later event for that call (progress,
 *   result, error) patches the SAME block in place by ID — order of results
 *   never matters.
 * • Tool phase is an explicit per-call state machine
 *   (pending → running/delegated → complete | error), so each card resolves
 *   independently instead of sharing one message-global status string.
 * • Render blocks upsert in place by `blockId`, anchored at first appearance.
 */

import type {
  RenderBlockPayload,
  ToolEventPayload,
  ErrorPayload,
} from "@/types/python-generated/stream-events";

export type ToolBlockPhase =
  | "pending"
  | "running"
  | "delegated"
  | "complete"
  | "error";

export interface ChatTextBlock {
  type: "text";
  /** Set when this block came from a server render_block (upserted by ID). */
  blockId?: string;
  content: string;
}

export interface ChatThinkingBlock {
  type: "thinking";
  blockId?: string;
  content: string;
}

export interface ChatToolBlock {
  type: "tool_call";
  callId: string;
  toolName: string;
  input: Record<string, unknown>;
  /** Raw tool result once tool_completed arrives (string or JSON object). */
  output?: string | Record<string, unknown> | null;
  errorMessage?: string;
  /** Transient progress lines (tool_progress / tool_step / tool_result_preview). */
  progress: string[];
  phase: ToolBlockPhase;
}

export interface ChatErrorBlock {
  type: "error";
  errorType: string;
  message: string;
}

export type ChatMessageBlock =
  | ChatTextBlock
  | ChatThinkingBlock
  | ChatToolBlock
  | ChatErrorBlock;

/**
 * Stateful, append-only builder driven event-by-event from the stream loop.
 * `snapshot()` returns a fresh immutable-by-convention array safe to put in
 * React state (blocks are shallow-copied so in-place patches never mutate a
 * previously published snapshot).
 */
export class StreamBlockBuilder {
  private blocks: ChatMessageBlock[] = [];
  private toolIndex = new Map<string, number>();
  private renderIndex = new Map<string, number>();
  private sawChunkText = false;

  /** Plain text delta — merges into the trailing anonymous text block. */
  addText(text: string): void {
    if (!text) return;
    this.sawChunkText = true;
    const last = this.blocks[this.blocks.length - 1];
    if (last && last.type === "text" && !last.blockId) {
      last.content += text;
    } else {
      this.blocks.push({ type: "text", content: text });
    }
  }

  /** Reasoning delta — merges into the trailing anonymous thinking block. */
  addThinking(text: string): void {
    if (!text) return;
    const last = this.blocks[this.blocks.length - 1];
    if (last && last.type === "thinking" && !last.blockId) {
      last.content += text;
    } else {
      this.blocks.push({ type: "thinking", content: text });
    }
  }

  /** Standalone markdown (media outputs) — its own text block, never merged. */
  addStandaloneMarkdown(markdown: string): void {
    if (!markdown) return;
    this.blocks.push({ type: "text", blockId: `standalone-${this.blocks.length}`, content: markdown });
  }

  addError(payload: ErrorPayload): void {
    this.blocks.push({
      type: "error",
      errorType: payload.error_type,
      message: payload.user_message || payload.message,
    });
  }

  /**
   * Fold one tool_event into the single block for its call_id, creating it at
   * the current stream position on first sight.
   */
  applyToolEvent(payload: ToolEventPayload): void {
    const callId = payload.call_id;
    let idx = this.toolIndex.get(callId);
    if (idx === undefined) {
      idx = this.blocks.length;
      this.blocks.push({
        type: "tool_call",
        callId,
        toolName: payload.tool_name || "tool",
        input: {},
        progress: [],
        phase: "pending",
      });
      this.toolIndex.set(callId, idx);
    }
    const block = this.blocks[idx] as ChatToolBlock;
    const data = payload.data ?? {};

    switch (payload.event) {
      case "tool_started": {
        const args = data.arguments;
        if (args && typeof args === "object" && !Array.isArray(args)) {
          block.input = args as Record<string, unknown>;
        }
        block.phase = "running";
        if (payload.message) block.progress.push(payload.message);
        break;
      }
      case "tool_delegated": {
        const args = data.arguments;
        if (args && typeof args === "object" && !Array.isArray(args)) {
          block.input = args as Record<string, unknown>;
        }
        block.phase = "delegated";
        if (payload.message) block.progress.push(payload.message);
        break;
      }
      case "tool_progress":
      case "tool_step":
      case "tool_result_preview": {
        if (block.phase === "pending") block.phase = "running";
        if (payload.message) block.progress.push(payload.message);
        break;
      }
      case "tool_completed": {
        const result = data.result;
        block.output =
          typeof result === "string"
            ? result
            : result && typeof result === "object" && !Array.isArray(result)
              ? (result as Record<string, unknown>)
              : result == null
                ? null
                : String(result);
        block.phase = "complete";
        if (payload.message) block.progress.push(payload.message);
        break;
      }
      case "tool_error": {
        block.errorMessage = payload.message ?? "Tool execution failed";
        block.phase = "error";
        break;
      }
    }
  }

  /**
   * Upsert a server render_block at its first-appearance position.
   * `markdown` is the already-rendered text for this block (renderBlockText).
   * Reasoning-flavored blocks become thinking blocks; text/markdown blocks are
   * skipped when chunk text is also streaming (the server mirrors chunk text
   * as render blocks on some paths — same dedup rule as the legacy handler).
   */
  applyRenderBlock(
    payload: RenderBlockPayload,
    markdown: string | null,
    isReasoningType: boolean,
  ): void {
    if (!markdown) return;
    if (
      !isReasoningType &&
      this.sawChunkText &&
      (payload.type === "text" || payload.type === "markdown")
    ) {
      return;
    }
    const blockId = payload.blockId || `${payload.blockIndex}-${payload.type}`;
    const existing = this.renderIndex.get(blockId);
    if (existing !== undefined) {
      const block = this.blocks[existing] as ChatTextBlock | ChatThinkingBlock;
      block.content = markdown;
      return;
    }
    this.renderIndex.set(blockId, this.blocks.length);
    this.blocks.push({
      type: isReasoningType ? "thinking" : "text",
      blockId,
      content: markdown,
    });
  }

  /** Force-terminate every non-terminal tool block (stream died / aborted). */
  failPendingTools(message: string): void {
    for (const block of this.blocks) {
      if (block.type !== "tool_call") continue;
      if (block.phase === "complete" || block.phase === "error") continue;
      block.phase = "error";
      block.errorMessage = block.errorMessage ?? message;
    }
  }

  /** Fresh snapshot safe for React state. */
  snapshot(): ChatMessageBlock[] {
    return this.blocks.map((block) =>
      block.type === "tool_call"
        ? { ...block, progress: [...block.progress] }
        : { ...block },
    );
  }
}

export function isToolBlock(b: ChatMessageBlock): b is ChatToolBlock {
  return b.type === "tool_call";
}
