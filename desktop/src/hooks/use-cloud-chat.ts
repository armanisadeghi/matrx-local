import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AIDREAM_SERVER_URL, fetchAIDreamModels } from "@/lib/aidream-client";
import {
  parseAIDreamStream,
  stringifyStreamDetail,
} from "@/lib/aidream-stream";
import supabase from "@/lib/supabase";
import type {
  ChatMessage,
  ChatMode,
  Conversation,
  ConversationRouteMode,
  ModelOption,
} from "@/hooks/use-chat";
import {
  EventType,
  type TypedDataPayload,
  type UntypedDataPayload,
} from "@/types/python-generated/stream-events";

const STORAGE_KEY = "matrx-cloud-chat-conversations";
const MAX_CONVERSATIONS = 100;

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function generateTitle(content: string): string {
  const cleaned = content.replace(/\n/g, " ").trim();
  return cleaned.length <= 50 ? cleaned : `${cleaned.slice(0, 47)}...`;
}

function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Conversation[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveConversations(conversations: Conversation[]) {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(conversations.slice(0, MAX_CONVERSATIONS)),
    );
  } catch {
    // Storage full: keep the in-memory chat usable.
  }
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

    groups[group] = [...(groups[group] ?? []), conv];
  }

  return groups;
}

function toApiMessages(
  messages: ChatMessage[],
): Array<{ role: string; content: Array<{ type: string; text: string }> }> {
  return messages
    .filter((message) => message.role === "user" || message.role === "assistant")
    .map((message) => ({
      role: message.role,
      content: [{ type: "text", text: message.content }],
    }));
}

function buildRequest(
  conversation: Conversation,
  userContent: string,
  currentModel: string,
  options: { agentId?: string; variables?: Record<string, string> } | undefined,
  allMessages: ChatMessage[],
): { url: string; body: Record<string, unknown> } {
  const base = `${AIDREAM_SERVER_URL}/api/ai`;

  if (conversation.serverConversationId) {
    return {
      url: `${base}/conversations/${conversation.serverConversationId}`,
      body: {
        user_input: userContent,
        stream: true,
      },
    };
  }

  if (options?.agentId) {
    const body: Record<string, unknown> = { stream: true };
    if (userContent) body.user_input = userContent;
    if (options.variables && Object.keys(options.variables).length > 0) {
      body.variables = options.variables;
    }
    return {
      url: `${base}/agents/${options.agentId}`,
      body,
    };
  }

  return {
    url: `${base}/chat`,
    body: {
      ai_model_id: currentModel,
      messages: toApiMessages(allMessages),
      stream: true,
      max_iterations: 20,
    },
  };
}

function readRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function humanizeToken(value: string): string {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function phaseStatus(phase: string): string {
  switch (phase) {
    case "connected":
      return "Connected to AIDream.";
    case "processing":
      return "Processing request...";
    case "generating":
      return "Generating response...";
    case "using_tools":
      return "Using tools...";
    case "persisting":
      return "Saving conversation state...";
    case "searching":
      return "Searching...";
    case "scraping":
      return "Scraping sources...";
    case "analyzing":
      return "Analyzing context...";
    case "synthesizing":
      return "Synthesizing response...";
    case "retrying":
      return "Retrying provider request...";
    case "executing":
      return "Executing...";
    case "complete":
      return "Stream complete.";
    default:
      return `${humanizeToken(phase)}...`;
  }
}

function completionText(result: unknown): string | null {
  const record = readRecord(result);
  if (!record) return null;

  return (
    readString(record.output) ??
    readString(record.text) ??
    readString(record.content) ??
    readString(record.message)
  );
}

function errorMessage(data: {
  message?: string;
  user_message?: string;
  code?: string | null;
  error_type?: string;
  details?: Record<string, unknown> | null;
}): string {
  return (
    data.user_message ||
    data.message ||
    data.code ||
    data.error_type ||
    "AIDream returned an unknown stream error."
  );
}

function describeDataEvent(data: TypedDataPayload | UntypedDataPayload): string {
  const type = readString(data.type) ?? "unknown_data";

  switch (type) {
    case "conversation_id":
      return "Conversation id received.";
    case "conversation_labeled":
      return "Conversation title updated.";
    case "context_changed":
    case "context_delta":
    case "context_persisted":
    case "context_conflict":
      return `Context ${humanizeToken(type.replace("context_", ""))}.`;
    case "context_persist_failed":
      return `Context persist failed: ${readString(readRecord(data)?.error) ?? "Unknown error"}`;
    case "function_result": {
      const record = readRecord(data);
      const name = readString(record?.function_name) ?? "function";
      const success = record?.success === true;
      const error = readString(record?.error);
      return success ? `Function completed: ${name}.` : `Function failed: ${name}${error ? ` - ${error}` : ""}`;
    }
    case "search_results":
    case "fetch_results":
      return "Search results received.";
    case "search_error": {
      const record = readRecord(data);
      return `Search error: ${readString(record?.message) ?? readString(record?.error) ?? stringifyStreamDetail(data)}`;
    }
    case "image_output":
    case "partial_image":
    case "audio_output":
    case "audio_stream_chunk":
    case "audio_stream_end":
    case "video_output":
    case "media_block":
    case "media_notice":
      return `Media event: ${humanizeToken(type)}.`;
    case "display_questionnaire":
      return "Questionnaire received.";
    case "structured_input_warning":
      return "Structured input warning received.";
    case "memory_buffer_spawned":
    case "memory_context_injected":
    case "memory_observer_completed":
    case "memory_reflector_completed":
      return `Memory event: ${humanizeToken(type.replace("memory_", ""))}.`;
    case "memory_error": {
      const record = readRecord(data);
      return `Memory error: ${readString(record?.error) ?? stringifyStreamDetail(data)}`;
    }
    case "workflow_step":
    case "workflow_node_test_result":
      return `Workflow event: ${humanizeToken(type.replace("workflow_", ""))}.`;
    default:
      return `Data event received: ${type}.`;
  }
}

export function useCloudChat() {
  const [conversations, setConversations] = useState<Conversation[]>(() =>
    loadConversations().sort(
      (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
    ),
  );
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [mode, setMode] = useState<ChatMode>("chat");
  const [model, setModel] = useState("");
  const [availableModels, setAvailableModels] = useState<ModelOption[]>([]);
  const [modelError, setModelError] = useState<string | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const conversationsRef = useRef(conversations);

  useEffect(() => {
    conversationsRef.current = conversations;
    saveConversations(conversations);
  }, [conversations]);

  useEffect(() => {
    let cancelled = false;
    fetchAIDreamModels()
      .then((response) => {
        if (cancelled) return;
        const mapped = response.models
          .filter((item) => !item.is_deprecated)
          .map<ModelOption>((item, index) => {
            const modelOption: ModelOption = {
              id: item.name,
              label: item.common_name ?? item.name,
              provider: item.provider ?? "cloud",
              capabilities: item.capabilities ?? [],
              default: index === 0,
            };
            if (typeof item.is_primary === "boolean") {
              modelOption.is_primary = item.is_primary;
            }
            if (typeof item.is_premium === "boolean") {
              modelOption.is_premium = item.is_premium;
            }
            if (typeof item.context_window === "number") {
              modelOption.context_window = item.context_window;
            }
            return modelOption;
        });
        setAvailableModels(mapped);
        setModel((prev) => prev || mapped[0]?.id || "");
        setModelError(null);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setModelError(error instanceof Error ? error.message : "Failed to load cloud models");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const activeConversation =
    conversations.find((conversation) => conversation.id === activeConversationId) ?? null;

  const createConversation = useCallback(
    (initialMode?: ChatMode): Conversation => {
      const conversation: Conversation = {
        id: generateId(),
        title: "New conversation",
        mode: initialMode ?? mode,
        model,
        messages: [],
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      setConversations((prev) => [conversation, ...prev]);
      setActiveConversationId(conversation.id);
      return conversation;
    },
    [mode, model],
  );

  const selectConversation = useCallback((id: string | null) => {
    setActiveConversationId(id);
    if (!id) return;
    const conversation = conversationsRef.current.find((item) => item.id === id);
    if (conversation) {
      setMode(conversation.mode);
      setModel(conversation.model);
    }
  }, []);

  const deleteConversation = useCallback(
    (id: string) => {
      setConversations((prev) => prev.filter((conversation) => conversation.id !== id));
      if (activeConversationId === id) setActiveConversationId(null);
    },
    [activeConversationId],
  );

  const renameConversation = useCallback((id: string, title: string) => {
    setConversations((prev) =>
      prev.map((conversation) =>
        conversation.id === id
          ? { ...conversation, title, updated_at: new Date().toISOString() }
          : conversation,
      ),
    );
  }, []);

  const sendMessage = useCallback(
    async (
      content: string,
      options?: { agentId?: string; variables?: Record<string, string> },
    ) => {
      const trimmed = content.trim();
      const hasAgent = Boolean(options?.agentId);
      if (!hasAgent && !trimmed) return;
      if (isStreaming) return;

      if (!AIDREAM_SERVER_URL) {
        setRequestError("VITE_AIDREAM_SERVER_URL_LIVE is not set.");
        return;
      }
      setRequestError(null);

      abortRef.current?.abort();
      const abort = new AbortController();
      abortRef.current = abort;

      let conversationId = activeConversationId;
      let currentConversation = conversationId
        ? conversationsRef.current.find((item) => item.id === conversationId)
        : undefined;
      let existingMessages = currentConversation?.messages ?? [];

      if (!conversationId) {
        currentConversation = createConversation();
        conversationId = currentConversation.id;
        existingMessages = [];
      }

      if (!currentConversation) {
        currentConversation = {
          id: conversationId,
          title: "New conversation",
          mode,
          model,
          messages: existingMessages,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        };
      }

      const routePatch: Partial<Conversation> =
        hasAgent &&
        options?.agentId &&
        !currentConversation.serverConversationId &&
        existingMessages.length === 0
          ? {
              routeMode: "agent" as ConversationRouteMode,
              agentId: options.agentId,
            }
          : {};

      if (routePatch.agentId) {
        setConversations((prev) =>
          prev.map((conversation) =>
            conversation.id === conversationId ? { ...conversation, ...routePatch } : conversation,
          ),
        );
      }

      const userMessage: ChatMessage | null = trimmed
        ? {
            id: generateId(),
            role: "user",
            content: trimmed,
            timestamp: new Date().toISOString(),
          }
        : null;

      if (userMessage) {
        setConversations((prev) =>
          prev.map((conversation) => {
            if (conversation.id !== conversationId) return conversation;
            const isFirst = conversation.messages.length === 0;
            return {
              ...conversation,
              title: isFirst ? generateTitle(trimmed) : conversation.title,
              messages: [...conversation.messages, userMessage],
              updated_at: new Date().toISOString(),
            };
          }),
        );
      }

      const assistantMessageId = generateId();
      const assistantMessage: ChatMessage = {
        id: assistantMessageId,
        role: "assistant",
        content: "",
        timestamp: new Date().toISOString(),
        model,
        isStreaming: true,
      };

      setConversations((prev) =>
        prev.map((conversation) =>
          conversation.id === conversationId
            ? {
                ...conversation,
                messages: [...conversation.messages, assistantMessage],
                updated_at: new Date().toISOString(),
              }
            : conversation,
        ),
      );

      const updateAssistant = (patch: Partial<ChatMessage>) => {
        setConversations((prev) =>
          prev.map((conversation) => {
            if (conversation.id !== conversationId) return conversation;
            return {
              ...conversation,
              messages: conversation.messages.map((message) =>
                message.id === assistantMessageId ? { ...message, ...patch } : message,
              ),
              updated_at: new Date().toISOString(),
            };
          }),
        );
      };

      const updateConversationMeta = (patch: Partial<Conversation>) => {
        setConversations((prev) =>
          prev.map((conversation) =>
            conversation.id === conversationId ? { ...conversation, ...patch } : conversation,
          ),
        );
      };

      setIsStreaming(true);
      let accumulated = "";
      let reasoning = "";
      let eventCount = 0;
      let sawTerminalEvent = false;
      let lastStatus = "Connecting to AIDream...";
      let diagnostics: string[] = [];

      const setStatus = (status: string) => {
        lastStatus = status;
        updateAssistant({ streamStatus: status });
      };

      const addDiagnostic = (message: string) => {
        diagnostics = [...diagnostics, message].slice(-12);
        updateAssistant({ streamDiagnostics: diagnostics });
      };

      try {
        const {
          data: { session },
        } = await supabase.auth.getSession();
        const token = session?.access_token ?? "";
        const allMessages = userMessage ? [...existingMessages, userMessage] : existingMessages;
        const requestConversation: Conversation = {
          ...currentConversation,
          ...routePatch,
        };
        const { url, body } = buildRequest(
          requestConversation,
          trimmed,
          model,
          options,
          allMessages,
        );

        const response = await fetch(url, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify(body),
          signal: abort.signal,
        });

        if (!response.ok || !response.body) {
          const rawErrorText = await response.text().catch(() => `HTTP ${response.status}`);
          const errorText = rawErrorText || `HTTP ${response.status} ${response.statusText}`;
          const message = `AIDream request failed (${response.status}): ${errorText}`;
          setRequestError(message);
          updateAssistant({
            content: "",
            isStreaming: false,
            streamStatus: "Request failed.",
            error: message,
          });
          return;
        }

        const headerConversationId = response.headers.get("X-Conversation-ID");
        if (headerConversationId && !requestConversation.serverConversationId) {
          updateConversationMeta({
            serverConversationId: headerConversationId,
            routeMode: "conversation",
          });
        }

        setStatus("Connected to AIDream.");

        for await (const event of parseAIDreamStream(response, abort.signal)) {
          eventCount += 1;

          switch (event.event) {
            case EventType.CHUNK: {
              accumulated += event.data.text;
              updateAssistant({
                content: accumulated,
                isStreaming: true,
                streamStatus: "Generating response...",
              });
              break;
            }

            case EventType.REASONING_CHUNK: {
              reasoning += event.data.text;
              updateAssistant({
                reasoning,
                streamStatus: "Reasoning...",
              });
              break;
            }

            case EventType.REASONING: {
              setStatus(
                event.data.state === "started"
                  ? "Reasoning..."
                  : accumulated
                    ? "Generating response..."
                    : "Reasoning complete.",
              );
              break;
            }

            case EventType.PHASE: {
              setStatus(phaseStatus(event.data.phase));
              break;
            }

            case EventType.WARNING: {
              const message =
                event.data.user_message ||
                event.data.system_message ||
                event.data.code ||
                "AIDream returned a warning.";
              addDiagnostic(`Warning: ${message}`);
              setStatus(message);
              break;
            }

            case EventType.INFO: {
              const message =
                event.data.user_message ||
                event.data.system_message ||
                event.data.code ||
                "AIDream sent an info event.";
              setStatus(message);
              break;
            }

            case EventType.DATA: {
              const data = event.data;
              const type = readString(data.type);
              const record = readRecord(data);

              if (type === "conversation_id") {
                const conversationIdValue = readString(record?.conversation_id);
                if (conversationIdValue) {
                  updateConversationMeta({
                    serverConversationId: conversationIdValue,
                    routeMode: "conversation",
                  });
                }
              } else if (type === "conversation_labeled") {
                const title = readString(record?.title);
                if (title) updateConversationMeta({ title });
              } else if (
                type === "image_output" ||
                type === "audio_output" ||
                type === "video_output"
              ) {
                const urlValue =
                  readString(record?.cdn_url) ||
                  readString(record?.url) ||
                  readString(record?.signed_url) ||
                  readString(record?.download_url);
                if (urlValue && type === "image_output") {
                  accumulated += `${accumulated ? "\n\n" : ""}![Generated image](${urlValue})`;
                  updateAssistant({ content: accumulated });
                } else if (urlValue) {
                  accumulated += `${accumulated ? "\n\n" : ""}[Generated ${type.replace("_output", "")}](${urlValue})`;
                  updateAssistant({ content: accumulated });
                }
              } else if (
                type === "search_error" ||
                type === "memory_error" ||
                type === "context_persist_failed"
              ) {
                const message = describeDataEvent(data);
                setRequestError(message);
                addDiagnostic(message);
              }

              setStatus(describeDataEvent(data));
              break;
            }

            case EventType.INIT: {
              setStatus(`${humanizeToken(event.data.operation)} started.`);
              break;
            }

            case EventType.COMPLETION: {
              sawTerminalEvent = true;
              const output = completionText(event.data.result);
              if (output && !accumulated) {
                accumulated = output;
                updateAssistant({ content: accumulated });
              }

              if (event.data.status === "failed" || event.data.status === "cancelled") {
                const message = `${humanizeToken(event.data.operation)} ${event.data.status}.`;
                setRequestError(message);
                updateAssistant({ error: message });
                addDiagnostic(message);
              } else {
                setStatus(`${humanizeToken(event.data.operation)} complete.`);
              }
              break;
            }

            case EventType.ERROR: {
              sawTerminalEvent = true;
              const message = errorMessage(event.data);
              setRequestError(message);
              updateAssistant({
                error: message,
                isStreaming: false,
                streamStatus: "AIDream returned an error.",
              });
              addDiagnostic(`Error: ${message}`);
              break;
            }

            case EventType.TOOL_EVENT: {
              const toolName = event.data.tool_name || "tool";
              const message = event.data.message || humanizeToken(event.data.event);
              setStatus(`${toolName}: ${message}`);

              if (event.data.event === "tool_error") {
                const error = `${toolName}: ${message}`;
                setRequestError(error);
                addDiagnostic(`Tool error: ${error}`);
              }
              break;
            }

            case EventType.BROKER: {
              setStatus(`Broker value received: ${event.data.broker_id}.`);
              break;
            }

            case EventType.HEARTBEAT: {
              if (!accumulated) setStatus(lastStatus || "Waiting for AIDream...");
              break;
            }

            case EventType.END: {
              sawTerminalEvent = true;
              setStatus(event.data.reason ? `Stream ended: ${event.data.reason}.` : "Stream ended.");
              break;
            }

            case EventType.RENDER_BLOCK: {
              const blockType = event.data.type;
              const content = readString(event.data.content);
              if (
                content &&
                (blockType === "text" || blockType === "markdown") &&
                event.data.status === "complete" &&
                !accumulated
              ) {
                accumulated = content;
                updateAssistant({ content: accumulated });
              }

              if (event.data.status === "error") {
                const message = `Render block failed: ${blockType}.`;
                setRequestError(message);
                addDiagnostic(message);
              }
              setStatus(`Render block ${event.data.status}: ${blockType}.`);
              break;
            }

            case EventType.RECORD_RESERVED: {
              setStatus(`Reserved ${event.data.table} record.`);
              break;
            }

            case EventType.RECORD_UPDATE: {
              if (event.data.status === "failed") {
                const message = `${event.data.table} record failed: ${event.data.record_id}.`;
                setRequestError(message);
                addDiagnostic(message);
              }
              setStatus(`${humanizeToken(event.data.table)} record ${event.data.status}.`);
              break;
            }

            case EventType.RESOURCE_CHANGED: {
              setStatus(`Resource ${event.data.action}: ${event.data.kind}.`);
              break;
            }

            case EventType.CONTEXT_ANALYSIS: {
              setStatus(`Context analysis: ${event.data.provider}${event.data.model ? `/${event.data.model}` : ""}.`);
              break;
            }

            case EventType.STRUCTURED_OUTPUT: {
              if (!event.data.success) {
                const message =
                  event.data.reason ||
                  `Structured output failed${event.data.schema_name ? `: ${event.data.schema_name}` : ""}.`;
                setRequestError(message);
                addDiagnostic(message);
              }
              setStatus(
                event.data.success
                  ? `Structured output received${event.data.schema_name ? `: ${event.data.schema_name}` : ""}.`
                  : "Structured output failed.",
              );
              break;
            }

            case EventType.CONTEXT_STATE: {
              setStatus("Context state updated.");
              break;
            }

            case EventType.CONTEXT_TRIMMED: {
              setStatus("Context was trimmed for this request.");
              break;
            }

            case EventType.INJECTION_CONSUMED: {
              setStatus(`Consumed ${event.data.count} context injection${event.data.count === 1 ? "" : "s"}.`);
              break;
            }

            case EventType.PROVIDER_RETRY: {
              const message = event.data.user_message || event.data.message;
              const status = `Provider retry ${event.data.state}: ${message}`;
              setStatus(status);
              if (
                event.data.state === "cancelled" ||
                event.data.state === "suspended"
              ) {
                setRequestError(status);
                addDiagnostic(status);
              } else {
                addDiagnostic(status);
              }
              break;
            }

            default: {
              const unknownEvent = event as { event?: string; data?: unknown };
              const message = `Unmapped stream event: ${unknownEvent.event ?? "unknown"} ${stringifyStreamDetail(unknownEvent.data)}`;
              addDiagnostic(message);
              setStatus(message);
            }
          }
        }

        if (!accumulated && !diagnostics.length && !sawTerminalEvent && eventCount === 0) {
          const message = "AIDream returned an empty stream with no events.";
          setRequestError(message);
          updateAssistant({
            isStreaming: false,
            streamStatus: message,
            error: message,
          });
          return;
        }

        if (!accumulated && !diagnostics.length) {
          updateAssistant({
            streamStatus:
              eventCount > 0
                ? lastStatus || "AIDream completed without text output."
                : "AIDream completed without stream events.",
          });
        }

        updateAssistant({ content: accumulated, isStreaming: false });
      } catch (error: unknown) {
        if (error instanceof Error && error.name === "AbortError") {
          updateAssistant({ isStreaming: false, streamStatus: "Stopped." });
        } else {
          const message =
            error instanceof SyntaxError
              ? `Failed to parse AIDream stream: ${error.message}`
              : error instanceof Error
                ? error.message
                : "Connection error";
          setRequestError(message);
          console.error("[cloud-chat] stream failure", error);
          updateAssistant({
            content: accumulated,
            isStreaming: false,
            streamStatus: accumulated ? "Stream failed after partial response." : "Stream failed.",
            error: message,
          });
        }
      } finally {
        setIsStreaming(false);
      }
    },
    [activeConversationId, createConversation, isStreaming, mode, model],
  );

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
    setIsStreaming(false);
    setConversations((prev) =>
      prev.map((conversation) => ({
        ...conversation,
        messages: conversation.messages.map((message) =>
          message.isStreaming ? { ...message, isStreaming: false } : message,
        ),
      })),
    );
  }, []);

  const clearConversations = useCallback(() => {
    setConversations([]);
    setActiveConversationId(null);
  }, []);

  const groupedConversations = useMemo(() => groupByDate(conversations), [conversations]);

  return {
    conversations,
    activeConversation,
    activeConversationId,
    isStreaming,
    mode,
    model,
    availableModels,
    modelError,
    requestError,
    groupedConversations,
    createConversation,
    selectConversation,
    deleteConversation,
    renameConversation,
    sendMessage,
    stopStreaming,
    clearConversations,
    setMode,
    setModel,
  };
}
