/**
 * Chat state management hook.
 *
 * Manages conversations, messages, and streaming AI responses via three
 * matrx-ai endpoints, chosen automatically based on context:
 *
 *   1. Agent   — POST /chat/ai/api/ai/agents/{agent_id}
 *      First message when an agent is selected. Sends variables + optional user_input.
 *      Server returns a conversation_id for follow-ups.
 *
 *   2. Conversation — POST /chat/ai/api/ai/conversations/{conversation_id}
 *      Any follow-up after the server has given us a conversation_id.
 *      Only sends user_input — the server manages history and config.
 *
 *   3. Chat   — POST /chat/ai/api/ai/chat
 *      No agent selected. Client manages full message history and config.
 *
 * Conversations are persisted to localStorage.
 */

import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { isTauri } from "@/lib/sidecar";
import supabase from "@/lib/supabase";
import { streamCompletion } from "@/lib/llm/api";
import { loadSettings } from "@/lib/settings";
import type { ToolImageData, ToolMediaArtifact } from "@/lib/api";
import type { ActionNeeded } from "@/features/action-needed";
import type { ChatMessageBlock } from "@/lib/chat-blocks";

// ---- Types ----

export type ChatMode = "chat" | "co-work" | "code";

export interface ToolCall {
  id: string;
  name: string;
  input: Record<string, unknown>;
}

export interface ToolCallResult {
  tool_call_id: string;
  type: "success" | "error";
  output: string;
  image?: ToolImageData;
  artifact?: ToolMediaArtifact;
  metadata?: Record<string, unknown>;
  action_needed?: ActionNeeded | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: string;
  /**
   * Ordered content blocks in true stream-arrival order (text / thinking /
   * tool_call / error interleaved). When present, renderers use this and
   * ignore the flat content/tool_calls fields for layout. Built live by
   * use-cloud-chat via StreamBlockBuilder (lib/chat-blocks.ts).
   */
  blocks?: ChatMessageBlock[];
  tool_calls?: ToolCall[];
  tool_results?: ToolCallResult[];
  model?: string;
  isStreaming?: boolean;
  error?: string;
  streamStatus?: string;
  streamDiagnostics?: string[];
  reasoning?: string;
}

/**
 * Conversation routing mode:
 *   - "chat":         no agent, client manages full message history
 *   - "agent":        first message with a selected agent (hasn't hit the server yet)
 *   - "conversation": server has a conversation_id, all follow-ups go to conversation endpoint
 */
export type ConversationRouteMode = "chat" | "agent" | "conversation";

export interface Conversation {
  id: string;
  title: string;
  description?: string;
  mode: ChatMode;
  model: string;
  messages: ChatMessage[];
  created_at: string;
  updated_at: string;
  serverConversationId?: string;
  cloudConversationId?: string;
  localConversationId?: string;
  routeMode?: ConversationRouteMode;
  agentId?: string;
  executionTarget?: "cloud" | "local";
}

export interface ToolSchema {
  name: string;
  description: string;
  category: string;
  input_schema: {
    type: "object";
    properties: Record<string, unknown>;
    required: string[];
  };
}

export interface ModelOption {
  id: string;           // API model name — sent as ai_model_id in requests
  label: string;        // Display name
  provider: string;     // anthropic | openai | google | groq | together | xai | cerebras | local
  default?: boolean;
  is_primary?: boolean;
  is_premium?: boolean;
  capabilities?: string[];
  context_window?: number;
  /** For local models: the llama-server port to call directly. */
  local_port?: number;
}

/** Local model ID prefix — identifies models served by llama-server. */
export const LOCAL_MODEL_PREFIX = "local::" as const;

// ---- Constants ----

const STORAGE_KEY = "matrx-chat-conversations";
const MAX_CONVERSATIONS = 100;

/** Empty list — the only source of truth for available models is the engine's
 *  SQLite cache (populated from the AI Matrx server). Never hard-code model names
 *  here; they will always fail because the API keys are server-side. */
export const FALLBACK_MODELS: ModelOption[] = [];

// ---- Helpers ----

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    return JSON.parse(raw);
  } catch {
    return [];
  }
}

function saveConversations(conversations: Conversation[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations.slice(0, MAX_CONVERSATIONS)));
  } catch {
    // Storage full — silently degrade
  }
}

function generateTitle(content: string): string {
  const cleaned = content.replace(/\n/g, " ").trim();
  return cleaned.length <= 50 ? cleaned : cleaned.slice(0, 47) + "...";
}

function groupByDate(conversations: Conversation[]): Record<string, Conversation[]> {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);
  const weekAgo = new Date(today.getTime() - 7 * 86400000);
  const monthAgo = new Date(today.getTime() - 30 * 86400000);
  const groups: Record<string, Conversation[]> = {};

  for (const conv of conversations) {
    const d = new Date(conv.updated_at);
    let group: string;
    if (d >= today) group = "Today";
    else if (d >= yesterday) group = "Yesterday";
    else if (d >= weekAgo) group = "Previous 7 days";
    else if (d >= monthAgo) group = "Previous 30 days";
    else group = "Older";

    let list = groups[group];
    if (!list) {
      list = [];
      groups[group] = list;
    }
    list.push(conv);
  }

  return groups;
}

/** Convert our ChatMessage history to the wire format matrx-ai expects (chat endpoint only). */
function toApiMessages(messages: ChatMessage[]): Array<{role: string; content: Array<{type: string; text: string}>}> {
  return messages
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m) => ({
      role: m.role,
      content: [{ type: "text", text: m.content }],
    }));
}

/**
 * Determine the API endpoint and request body based on routing mode.
 *
 * Three modes:
 *   1. Agent — first message with a selected agent. Hits /agents/{agent_id}.
 *      Body: { user_input, variables, stream }
 *   2. Conversation — server gave us a conversation_id. Hits /conversations/{id}.
 *      Body: { user_input, stream }
 *   3. Chat — no agent, client manages full history. Hits /chat.
 *      Body: { ai_model_id, messages, stream, ... }
 */
function buildRequest(
  baseUrl: string,
  conv: Conversation,
  userContent: string,
  currentModel: string,
  options?: { agentId?: string; variables?: Record<string, string> },
  allMessages?: ChatMessage[],
): { url: string; body: Record<string, unknown> } {
  const serverConvId = conv.serverConversationId;
  const hasAgent = !!options?.agentId;

  if (serverConvId) {
    return {
      url: `${baseUrl}/chat/ai/api/ai/conversations/${serverConvId}`,
      body: {
        user_input: userContent,
        stream: true,
      },
    };
  }

  if (hasAgent) {
    const body: Record<string, unknown> = {
      stream: true,
    };
    if (userContent) {
      body.user_input = userContent;
    }
    if (options?.variables && Object.keys(options.variables).length > 0) {
      body.variables = options.variables;
    }
    return {
      url: `${baseUrl}/chat/ai/api/ai/agents/${options!.agentId}`,
      body,
    };
  }

  return {
    url: `${baseUrl}/chat/ai/api/ai/chat`,
    body: {
      ai_model_id: currentModel,
      messages: toApiMessages(allMessages ?? []),
      stream: true,
      max_iterations: 20,
    },
  };
}

// ---- Hook ----

export interface UseChatOptions {
  engineUrl: string | null;
}

export function useChat({ engineUrl }: UseChatOptions) {
  const [conversations, setConversations] = useState<Conversation[]>(() =>
    loadConversations().sort(
      (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
    )
  );
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [mode, setMode] = useState<ChatMode>("chat");
  const [model, setModel] = useState("");
  const [availableModels, setAvailableModels] = useState<ModelOption[]>([]);
  const [toolSchemas, setToolSchemas] = useState<ToolSchema[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const cloudModelsRef = useRef<ModelOption[]>([]);

  // Load default model and mode from user configuration on mount
  useEffect(() => {
    loadSettings().then((s) => {
      if (s.chatDefaultModel) setModel(s.chatDefaultModel);
      if (s.chatDefaultMode) setMode(s.chatDefaultMode);
    });
  }, []);

  /** Merge cloud models with any running local model. */
  const mergeLocalModel = useCallback(
    (
      cloudModels: ModelOption[],
      serverStatus: { running: boolean; port: number; model_name: string } | null
    ) => {
      if (!serverStatus?.running || !serverStatus.model_name) return cloudModels;
      const localId = `${LOCAL_MODEL_PREFIX}${serverStatus.model_name}`;
      const localEntry: ModelOption = {
        id: localId,
        label: `${serverStatus.model_name} (Local)`,
        provider: "local",
        local_port: serverStatus.port,
      };
      // Remove any stale local entries, then prepend the new one
      return [localEntry, ...cloudModels.filter((m) => m.provider !== "local")];
    },
    []
  );

  // Load live models from DB via engine
  useEffect(() => {
    if (!engineUrl) return;
    let cancelled = false;
    let syncRetryTimer: ReturnType<typeof setTimeout> | null = null;

    const load = async () => {
      try {
        const resp = await fetch(`${engineUrl}/chat/models`);
        if (!resp.ok) return;
        const data = await resp.json() as { models: Array<{
          name: string; common_name: string; provider: string;
          is_primary: boolean; is_premium: boolean;
          capabilities: string[]; context_window: number;
        }>; source: string; syncing?: boolean };
        if (cancelled) return;
        if (!data.models?.length) {
          // Engine is still syncing its model catalog (fresh install) —
          // re-poll until populated instead of leaving the picker empty
          // until app restart (mirrors use-agents' syncing retry).
          if (data.syncing) {
            syncRetryTimer = setTimeout(() => void load(), 3_000);
          }
          return;
        }

        const mapped: ModelOption[] = data.models.map((m, i) => ({
          id: m.name,
          label: m.common_name,
          provider: m.provider,
          is_primary: m.is_primary,
          is_premium: m.is_premium,
          capabilities: m.capabilities,
          context_window: m.context_window,
          default: i === 0,
        }));

        cloudModelsRef.current = mapped;

        // Check if llama-server is already running and inject local model
        let serverStatus: { running: boolean; port: number; model_name: string } | null = null;
        if (isTauri()) {
          try {
            const { invoke } = await import("@tauri-apps/api/core");
            serverStatus = await invoke("get_llm_server_status");
          } catch {
            // Tauri invoke unavailable — ignore
          }
        }
        const merged = mergeLocalModel(mapped, serverStatus);
        if (cancelled) return;
        setAvailableModels(merged);
        // If current model isn't in the new list, switch to first available (or keep empty)
        setModel((prev) => merged.find((m) => m.id === prev) ? prev : (merged[0]?.id ?? ""));
      } catch {
        // Keep fallback models if engine unreachable
      }
    };

    load();
    return () => {
      cancelled = true;
      if (syncRetryTimer) clearTimeout(syncRetryTimer);
    };
  }, [engineUrl, mergeLocalModel]);

  // Listen for llama-server lifecycle events to dynamically add/remove local model
  useEffect(() => {
    if (!isTauri()) return;

    let mounted = true;
    const unlistenPromises: Array<Promise<() => void>> = [];

    const setup = async () => {
      const { listen } = await import("@tauri-apps/api/event");

      unlistenPromises.push(
        listen<{ running: boolean; port: number; model_name: string }>(
          "llm-server-ready",
          (event) => {
            if (!mounted) return;
            setAvailableModels((prev) => {
              const cloud = prev.filter((m) => m.provider !== "local");
              return mergeLocalModel(cloud, event.payload);
            });
          }
        )
      );

      unlistenPromises.push(
        listen("llm-server-stopped", () => {
          if (!mounted) return;
          setAvailableModels((prev) => prev.filter((m) => m.provider !== "local"));
          // If a local model was selected, fall back to first cloud model (or empty)
          setModel((prev) => {
            if (prev.startsWith(LOCAL_MODEL_PREFIX)) {
              return cloudModelsRef.current[0]?.id ?? "";
            }
            return prev;
          });
        })
      );
    };

    setup();

    return () => {
      mounted = false;
      unlistenPromises.forEach((p) => p.then((fn) => fn()).catch((e) => console.warn("[chat] cleanup unlisten failed:", e)));
    };
  }, [mergeLocalModel]);

  // Mirror conversations into a ref for synchronous reads inside async
  // callbacks (sendMessage). Reading via a setConversations side-effect
  // updater is unreliable — React may defer or skip running the updater
  // synchronously, and falling back to localStorage reads stale data.
  const conversationsRef = useRef(conversations);
  useEffect(() => {
    conversationsRef.current = conversations;
  }, [conversations]);

  useEffect(() => {
    saveConversations(conversations);
  }, [conversations]);

  const activeConversation = conversations.find((c) => c.id === activeConversationId) ?? null;

  const createConversation = useCallback(
    (initialMode?: ChatMode): Conversation => {
      const conv: Conversation = {
        id: generateId(),
        title: "New conversation",
        mode: initialMode ?? mode,
        model,
        messages: [],
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      setConversations((prev) => [conv, ...prev]);
      setActiveConversationId(conv.id);
      return conv;
    },
    [mode, model]
  );

  const selectConversation = useCallback((id: string | null) => {
    setActiveConversationId(id);
    if (id) {
      const conv = loadConversations().find((c) => c.id === id);
      if (conv) {
        setMode(conv.mode);
        setModel(conv.model);
      }
    }
  }, []);

  const deleteConversation = useCallback(
    (id: string) => {
      setConversations((prev) => prev.filter((c) => c.id !== id));
      if (activeConversationId === id) setActiveConversationId(null);
    },
    [activeConversationId]
  );

  const renameConversation = useCallback((id: string, title: string) => {
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, title, updated_at: new Date().toISOString() } : c))
    );
  }, []);

  const sendMessage = useCallback(
    async (
      content: string,
      options?: {
        agentId?: string;
        variables?: Record<string, string>;
      }
    ) => {
      const trimmed = content.trim();
      const hasAgent = !!options?.agentId;
      const hasVars = options?.variables && Object.keys(options.variables).length > 0;

      // Agent mode allows empty text when variables are provided
      if (!hasAgent && !trimmed) return;
      if (hasAgent && !trimmed && !hasVars) return;
      if (isStreaming) return;

      // Check if a local model is selected — it bypasses the engine entirely
      const selectedModelEntry = availableModels.find((m) => m.id === model);
      const isLocalModel = model.startsWith(LOCAL_MODEL_PREFIX) && selectedModelEntry?.local_port;

      // NOTE: the engine-not-connected case is NOT dropped here. We continue so
      // the conversation/user-message/assistant-message are created, then set a
      // visible error on the assistant bubble below (see the guard right after
      // `updateAssistant` is defined). Silently returning here made every
      // ungated caller (e.g. ChatWelcome suggestion cards) do nothing.

      abortRef.current?.abort();
      const abort = new AbortController();
      abortRef.current = abort;

      // Ensure we have a conversation
      let convId = activeConversationId;
      let existingMessages: ChatMessage[] = [];
      let currentConv: Conversation | undefined;

      if (convId) {
        currentConv = conversationsRef.current.find((x) => x.id === convId);
        if (currentConv) existingMessages = currentConv.messages;
      }

      if (!convId) {
        const conv = createConversation();
        convId = conv.id;
        currentConv = conv;
        existingMessages = [];
      } else if (!currentConv) {
        currentConv = loadConversations().find((c) => c.id === convId);
        existingMessages = currentConv?.messages ?? [];
      }

      // If the user selected an agent and this is the first message, set routeMode + agentId
      if (hasAgent && !currentConv?.serverConversationId && existingMessages.length === 0) {
        setConversations((prev) =>
          prev.map((c) =>
            c.id === convId
              ? {
                  ...c,
                  routeMode: "agent" as ConversationRouteMode,
                  ...(options!.agentId !== undefined ? { agentId: options!.agentId } : {}),
                }
              : c
          )
        );
      }

      // Re-read current conv state after potential update
      const convSnapshot: Conversation = {
        ...(currentConv ?? {
          id: convId!,
          title: "New conversation",
          mode: mode,
          model,
          messages: existingMessages,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }),
        ...(hasAgent && !currentConv?.serverConversationId && existingMessages.length === 0
          ? { routeMode: "agent" as ConversationRouteMode, agentId: options!.agentId }
          : {}),
      };

      // Only add a user message bubble if there's actual text
      const userMsg: ChatMessage | null = trimmed
        ? {
            id: generateId(),
            role: "user",
            content: trimmed,
            timestamp: new Date().toISOString(),
          }
        : null;

      if (userMsg) {
        setConversations((prev) =>
          prev.map((c) => {
            if (c.id !== convId) return c;
            const isFirst = c.messages.length === 0;
            return {
              ...c,
              title: isFirst ? generateTitle(content) : c.title,
              messages: [...c.messages, userMsg],
              updated_at: new Date().toISOString(),
            };
          })
        );
      }

      const assistantMsgId = generateId();
      const assistantMsg: ChatMessage = {
        id: assistantMsgId,
        role: "assistant",
        content: "",
        timestamp: new Date().toISOString(),
        model,
        isStreaming: true,
      };

      setConversations((prev) =>
        prev.map((c) =>
          c.id === convId
            ? { ...c, messages: [...c.messages, assistantMsg], updated_at: new Date().toISOString() }
            : c
        )
      );

      setIsStreaming(true);

      const updateAssistant = (patch: Partial<ChatMessage>) => {
        setConversations((prev) =>
          prev.map((c) => {
            if (c.id !== convId) return c;
            return {
              ...c,
              messages: c.messages.map((m) =>
                m.id === assistantMsgId ? { ...m, ...patch } : m
              ),
              updated_at: new Date().toISOString(),
            };
          })
        );
      };

      const updateConversationMeta = (patch: Partial<Conversation>) => {
        setConversations((prev) =>
          prev.map((c) => (c.id === convId ? { ...c, ...patch } : c))
        );
      };

      // Engine required but not connected (and this isn't a local model) —
      // surface a visible assistant error instead of silently dropping the
      // message. Reuses the standard message-error rendering infra.
      if (!isLocalModel && !engineUrl) {
        updateAssistant({
          content: "Engine not connected — the message was not sent. Reconnect to the engine and try again.",
          isStreaming: false,
          error: "Engine not connected",
        });
        setIsStreaming(false);
        return;
      }

      let accumulated = "";

      try {
        // ── Local Model Path ──────────────────────────────────────────────
        if (isLocalModel && selectedModelEntry?.local_port) {
          const port = selectedModelEntry.local_port;
          const allMessages = userMsg ? [...existingMessages, userMsg] : existingMessages;
          const llmMessages = allMessages
            .filter((m) => m.role === "user" || m.role === "assistant")
            .map((m) => ({ role: m.role as "user" | "assistant", content: m.content }));

          try {
            const gen = streamCompletion(port, llmMessages);
            for await (const token of gen) {
              if (abort.signal.aborted) break;
              accumulated += token;
              updateAssistant({ content: accumulated, isStreaming: true });
            }
          } catch (e) {
            const msg = e instanceof Error ? e.message : "Local inference error";
            updateAssistant({ content: accumulated || msg, isStreaming: false, error: msg });
            setIsStreaming(false);
            return;
          }
          updateAssistant({ content: accumulated, isStreaming: false });
          setIsStreaming(false);
          return;
        }

        // ── Cloud / Engine Path ───────────────────────────────────────────
        const { data: { session } } = await supabase.auth.getSession();
        const token = session?.access_token ?? "";

        const allMessages = userMsg ? [...existingMessages, userMsg] : existingMessages;

        const { url, body } = buildRequest(
          engineUrl!,
          convSnapshot,
          trimmed,
          model,
          options,
          allMessages,
        );

        // Attach the user's bound compute target (sandbox or local PC) so
        // matrx-ai's fs/shell tools dispatch into that target. Best-effort:
        // unresolvable target → agent runs unbound for this turn.
        const boundTarget = (
          await import("@/state/compute-target-store")
        ).useComputeTargetStore.getState().bound;
        if (boundTarget) {
          const { resolveComputeTarget } = await import("@/lib/aidream-client");
          const binding = await resolveComputeTarget(
            { kind: boundTarget.kind, id: boundTarget.rowId },
            { jwt: token || null, signal: abort.signal },
          );
          if (binding) {
            (body as Record<string, unknown>).sandbox = binding;
          }
        }

        const resp = await fetch(url, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify(body),
          signal: abort.signal,
        });

        if (!resp.ok || !resp.body) {
          const errText = await resp.text().catch(() => `HTTP ${resp.status}`);
          updateAssistant({ content: `Error: ${errText}`, isStreaming: false, error: errText });
          setIsStreaming(false);
          return;
        }

        // Capture server conversation_id from response header as immediate fallback
        const headerConvId = resp.headers.get("X-Conversation-ID");
        if (headerConvId && !convSnapshot.serverConversationId) {
          updateConversationMeta({
            serverConversationId: headerConvId,
            routeMode: "conversation",
          });
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            const raw = line.trim();
            if (!raw) continue;

            try {
              const event = JSON.parse(raw) as {
                event: string;
                data?: Record<string, unknown>;
              };

              switch (event.event) {
                case "data": {
                  const inner = event.data as { event?: string; conversation_id?: string } | undefined;
                  if (inner?.event === "conversation_id" && inner.conversation_id) {
                    updateConversationMeta({
                      serverConversationId: inner.conversation_id,
                      routeMode: "conversation",
                    });
                  }
                  break;
                }
                case "chunk": {
                  const text = (event.data as { text?: string })?.text ?? "";
                  accumulated += text;
                  updateAssistant({ content: accumulated, isStreaming: true });
                  break;
                }
                case "completion": {
                  const output = (event.data as { output?: string })?.output;
                  if (output && !accumulated) accumulated = output;
                  break;
                }
                case "error": {
                  const msg = (event.data as { message?: string })?.message ?? "Unknown error";
                  updateAssistant({ content: accumulated || msg, isStreaming: false, error: msg });
                  break;
                }
                case "end":
                  break;
              }
            } catch {
              // Malformed line — skip
            }
          }
        }

        updateAssistant({ content: accumulated, isStreaming: false });
      } catch (err: unknown) {
        if (err instanceof Error && err.name === "AbortError") {
          updateAssistant({ isStreaming: false });
        } else {
          const msg = err instanceof Error ? err.message : "Connection error";
          updateAssistant({
            content: accumulated || `Failed to reach engine: ${msg}`,
            isStreaming: false,
            error: msg,
          });
        }
      } finally {
        setIsStreaming(false);
      }
    },
    [activeConversationId, availableModels, createConversation, engineUrl, isStreaming, model]
  );

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
    setIsStreaming(false);
    setConversations((prev) =>
      prev.map((c) => ({
        ...c,
        messages: c.messages.map((m) => (m.isStreaming ? { ...m, isStreaming: false } : m)),
      }))
    );
  }, []);

  const clearConversations = useCallback(() => {
    setConversations([]);
    setActiveConversationId(null);
  }, []);

  const groupedConversations = useMemo(
    () => groupByDate(conversations),
    [conversations],
  );

  const actions = useMemo(
    () => ({
      createConversation,
      selectConversation,
      deleteConversation,
      renameConversation,
      sendMessage,
      stopStreaming,
      clearConversations,
      setMode,
      setModel,
      setToolSchemas,
    }),
    [
      createConversation,
      selectConversation,
      deleteConversation,
      renameConversation,
      sendMessage,
      stopStreaming,
      clearConversations,
      setMode,
      setModel,
      setToolSchemas,
    ],
  );

  return {
    conversations,
    activeConversation,
    activeConversationId,
    isStreaming,
    mode,
    model,
    toolSchemas,
    availableModels,
    groupedConversations,
    ...actions,
  };
}
