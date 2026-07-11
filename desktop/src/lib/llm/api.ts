import type {
  ChatMessage,
  ChatCompletionResponse,
  ToolDefinition,
  ToolCall,
} from "./types";
import { loadSettings, type AppSettings } from "@/lib/settings";

// ── Typed Errors ──────────────────────────────────────────────────────────

export class ContextSizeError extends Error {
  readonly promptTokens: number;
  readonly contextSize: number;

  constructor(promptTokens: number, contextSize: number) {
    super(
      `Request requires ${promptTokens.toLocaleString()} tokens but the model's context window is only ${contextSize.toLocaleString()} tokens.`,
    );
    this.name = "ContextSizeError";
    this.promptTokens = promptTokens;
    this.contextSize = contextSize;
  }
}

function throwIfContextError(status: number, body: string): void {
  if (status !== 400) return;
  try {
    const json = JSON.parse(body) as {
      error?: { type?: string; n_prompt_tokens?: number; n_ctx?: number };
    };
    if (json.error?.type === "exceed_context_size_error") {
      throw new ContextSizeError(
        json.error.n_prompt_tokens ?? 0,
        json.error.n_ctx ?? 0,
      );
    }
  } catch (e) {
    if (e instanceof ContextSizeError) throw e;
  }
}

// ── Sampling Presets (read from user config) ──────────────────────────────

/** Cached settings — refreshed on each top-level API call. */
let _cached: AppSettings | null = null;

async function cfg(): Promise<AppSettings> {
  // Load once per call tree — subsequent calls within the same tick reuse cache.
  if (!_cached) _cached = await loadSettings();
  return _cached;
}

/** Invalidate cache so next API call picks up fresh settings. */
export function invalidateLlmSettingsCache(): void {
  _cached = null;
}

// Automatically invalidate whenever the user saves settings anywhere in the app.
if (typeof window !== "undefined") {
  window.addEventListener("matrx-settings-changed", () => {
    _cached = null;
  });
}

function chatParams(s: AppSettings) {
  return {
    temperature: s.llmChatTemperature,
    top_p: s.llmChatTopP,
    top_k: s.llmChatTopK,
    chat_template_kwargs: { enable_thinking: false },
  };
}

function reasoningParams(s: AppSettings) {
  return {
    temperature: s.llmReasoningTemperature,
    top_p: s.llmReasoningTopP,
    top_k: s.llmReasoningTopK,
    chat_template_kwargs: { enable_thinking: s.llmEnableThinking },
  };
}

function toolCallParams(s: AppSettings) {
  return {
    temperature: s.llmToolCallTemperature,
    top_p: s.llmToolCallTopP,
    top_k: s.llmToolCallTopK,
    chat_template_kwargs: { enable_thinking: false },
  };
}

// ── Core API Functions ────────────────────────────────────────────────────

/** Simple chat completion (non-streaming). */
export async function chatCompletion(
  port: number,
  messages: ChatMessage[],
  options?: {
    temperature?: number;
    maxTokens?: number;
    thinking?: boolean;
  },
): Promise<string> {
  const s = await cfg();
  const params = options?.thinking ? reasoningParams(s) : chatParams(s);

  const response = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: "local",
      messages,
      temperature: options?.temperature ?? params.temperature,
      max_tokens: options?.maxTokens ?? s.llmChatMaxTokens,
      stream: false,
      top_p: params.top_p,
      top_k: params.top_k,
      chat_template_kwargs: params.chat_template_kwargs,
    }),
  });

  if (!response.ok) {
    const text = await response.text();
    throwIfContextError(response.status, text);
    throw new Error(`LLM request failed (${response.status}): ${text}`);
  }

  const data: ChatCompletionResponse = await response.json();
  const content = data.choices[0]?.message?.content ?? "";

  return stripThinking(content);
}

/** Streaming chat completion. Yields content tokens as they arrive. */
export async function* streamCompletion(
  port: number,
  messages: ChatMessage[],
  options?: {
    temperature?: number;
    maxTokens?: number;
    top_p?: number;
    signal?: AbortSignal;
  },
): AsyncGenerator<string> {
  const s = await cfg();
  const params = chatParams(s);

  const response = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: "local",
      messages,
      temperature: options?.temperature ?? params.temperature,
      max_tokens: options?.maxTokens ?? s.llmStreamMaxTokens,
      stream: true,
      top_p: options?.top_p ?? params.top_p,
      top_k: params.top_k,
      chat_template_kwargs: params.chat_template_kwargs,
    }),
    signal: options?.signal ?? null,
  });

  if (!response.ok) {
    const text = await response.text();
    throwIfContextError(response.status, text);
    throw new Error(`LLM stream failed (${response.status}): ${text}`);
  }

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  // Carry-over buffer: SSE `data:` lines can be split across network reads,
  // and multi-byte UTF-8 sequences can straddle chunk boundaries. Decode in
  // streaming mode and only process complete lines, keeping the remainder.
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n");
    buffer = parts.pop() ?? "";
    const lines = parts.filter((line) => line.startsWith("data: "));

    for (const line of lines) {
      const data = line.slice(6).trim();
      if (data === "[DONE]") return;

      try {
        const parsed = JSON.parse(data);
        const delta = parsed.choices?.[0]?.delta?.content;
        if (delta) yield delta;
      } catch {
        // Ignore malformed SSE chunks
      }
    }
  }
}

/** Chat completion with tool calling support. */
export async function callWithTools(
  port: number,
  messages: ChatMessage[],
  tools: ToolDefinition[],
  signal?: AbortSignal,
): Promise<{
  content: string | null;
  toolCalls: Array<{
    toolName: string;
    toolCallId: string;
    arguments: Record<string, unknown>;
  }>;
  finishReason: string;
}> {
  const s = await cfg();
  const params = toolCallParams(s);

  const response = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: "local",
      messages,
      tools,
      tool_choice: "auto",
      temperature: params.temperature,
      max_tokens: s.llmChatMaxTokens,
      stream: false,
      top_p: params.top_p,
      top_k: params.top_k,
      chat_template_kwargs: params.chat_template_kwargs,
    }),
    signal: signal ?? null,
  });

  if (!response.ok) {
    const text = await response.text();
    throwIfContextError(response.status, text);
    throw new Error(`Tool call failed (${response.status}): ${text}`);
  }

  const data: ChatCompletionResponse = await response.json();
  const choice = data.choices[0];
  const message = choice?.message;

  const toolCalls = (message?.tool_calls ?? []).map((tc: ToolCall) => ({
    toolName: tc.function.name,
    toolCallId: tc.id,
    arguments: (() => {
      try {
        return JSON.parse(tc.function.arguments) as Record<string, unknown>;
      } catch {
        return {} as Record<string, unknown>;
      }
    })(),
  }));

  return {
    content: message?.content ? stripThinking(message.content) : null,
    toolCalls,
    finishReason: choice?.finish_reason ?? "stop",
  };
}

// ── Agentic Loop ──────────────────────────────────────────────────────────

export interface AgenticToolCall {
  toolCallId: string;
  toolName: string;
  arguments: Record<string, unknown>;
  result: string;
  isError: boolean;
  elapsedMs: number;
}

export interface AgenticStep {
  stepIndex: number;
  assistantContent: string | null;
  toolCalls: AgenticToolCall[];
  finishReason: string;
}

export interface AgenticLoopResult {
  steps: AgenticStep[];
  finalContent: string;
  stoppedByUser: boolean;
  stoppedByMaxSteps: boolean;
  totalSteps: number;
  /** Full message history including tool results — use as base for the next turn. */
  fullHistory: ChatMessage[];
}

/**
 * Execute a full agentic tool-calling loop.
 *
 * Calls the model, dispatches tool calls via the engine, feeds results back,
 * and repeats until the model stops requesting tools or limits are hit.
 *
 * @param port          - llama-server port
 * @param messages      - Initial message history (system + user + prior turns)
 * @param tools         - Tool definitions to pass to the model
 * @param invokeTool    - Callback to execute a tool, returning { output, isError }
 * @param onStep        - Called after each round-trip with the step details
 * @param signal        - AbortSignal — triggers immediate stop mid-loop
 * @param maxSteps      - Hard cap on loop iterations (default 10)
 */
export async function runAgenticLoop(
  port: number,
  messages: ChatMessage[],
  tools: ToolDefinition[],
  invokeTool: (
    name: string,
    args: Record<string, unknown>,
  ) => Promise<{ output: string; isError: boolean }>,
  onStep: (step: AgenticStep) => void,
  signal: AbortSignal,
  maxSteps = 10,
): Promise<AgenticLoopResult> {
  const steps: AgenticStep[] = [];
  const history: ChatMessage[] = [...messages];
  let finalContent = "";
  let stoppedByUser = false;
  let stoppedByMaxSteps = false;

  for (let stepIndex = 0; stepIndex < maxSteps; stepIndex++) {
    if (signal.aborted) {
      stoppedByUser = true;
      break;
    }

    let response: Awaited<ReturnType<typeof callWithTools>>;
    try {
      response = await callWithTools(port, history, tools, signal);
    } catch (e) {
      if ((e as Error).name === "AbortError") {
        stoppedByUser = true;
        break;
      }
      throw e;
    }

    const { content, toolCalls, finishReason } = response;

    // Add assistant message to history (with tool_calls if any)
    const assistantMsg: ChatMessage = {
      role: "assistant",
      content: content ?? null,
    };
    if (toolCalls.length > 0) {
      assistantMsg.tool_calls = toolCalls.map((tc) => ({
        id: tc.toolCallId,
        type: "function",
        function: {
          name: tc.toolName,
          arguments: JSON.stringify(tc.arguments),
        },
      }));
    }
    history.push(assistantMsg);

    const executedToolCalls: AgenticToolCall[] = [];

    if (toolCalls.length > 0 && !signal.aborted) {
      // Execute all tool calls in this step
      for (const tc of toolCalls) {
        if (signal.aborted) {
          stoppedByUser = true;
          break;
        }

        const t0 = Date.now();
        let toolOutput = "";
        let isError = false;

        try {
          const result = await invokeTool(tc.toolName, tc.arguments);
          toolOutput = result.output;
          isError = result.isError;
        } catch (e) {
          toolOutput = `Tool execution failed: ${(e as Error).message}`;
          isError = true;
        }

        const elapsedMs = Date.now() - t0;
        executedToolCalls.push({
          toolCallId: tc.toolCallId,
          toolName: tc.toolName,
          arguments: tc.arguments,
          result: toolOutput,
          isError,
          elapsedMs,
        });

        // Add tool result to history
        history.push({
          role: "tool",
          content: isError
            ? `Error executing ${tc.toolName}: ${toolOutput}`
            : toolOutput,
          tool_call_id: tc.toolCallId,
        });
      }
    }

    // Track the last non-empty content from the model as the candidate final answer
    if (content) {
      finalContent = content;
    }

    const step: AgenticStep = {
      stepIndex,
      assistantContent: content,
      toolCalls: executedToolCalls,
      finishReason,
    };
    steps.push(step);
    onStep(step);

    // Stop if user aborted during tool execution
    if (stoppedByUser) break;

    // Stop if no tool calls were requested — model is done
    // finish_reason "tool_calls" means the model wants more tool turns.
    // Any other finish reason (stop, length, etc.) with no pending tool calls = done.
    if (toolCalls.length === 0) {
      break;
    }

    // finish_reason "stop" while tool calls present is unusual but possible in
    // some llama.cpp builds; treat tool calls as authoritative — keep looping.

    if (stepIndex === maxSteps - 1) {
      stoppedByMaxSteps = true;
    }
  }

  return {
    steps,
    finalContent,
    stoppedByUser,
    stoppedByMaxSteps,
    totalSteps: steps.length,
    fullHistory: history,
  };
}

/** Structured output with JSON schema constraint. */
export async function structuredOutput<T>(
  port: number,
  messages: ChatMessage[],
  schema: object,
): Promise<T> {
  const s = await cfg();

  const response = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: "local",
      messages,
      temperature: s.llmStructuredOutputTemperature,
      max_tokens: 2048,
      stream: false,
      response_format: {
        type: "json_schema",
        json_schema: { schema },
      },
      chat_template_kwargs: { enable_thinking: false },
    }),
  });

  if (!response.ok) {
    const text = await response.text();
    throwIfContextError(response.status, text);
    throw new Error(`Structured output failed (${response.status}): ${text}`);
  }

  const data: ChatCompletionResponse = await response.json();
  const choice = data.choices[0];
  if (!choice) throw new Error("Structured output failed: response had no choices");
  return JSON.parse(choice.message.content!) as T;
}

// ── Helpers ───────────────────────────────────────────────────────────────

function stripThinking(content: string): string {
  return content.replace(/<think>[\s\S]*?<\/think>/g, "").trim();
}
