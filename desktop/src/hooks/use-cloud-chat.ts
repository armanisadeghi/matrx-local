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
const CLOUD_SOURCE_APP = "matrx-desktop";
const CLOUD_SOURCE_FEATURE = "chat-route";
const CHAT_HISTORY_FEATURES = ["chat-route", "chat-interface", "quick-chat"];
const HISTORY_COLUMNS =
  "id, title, description, status, message_count, initial_agent_id, last_model_id, source_app, source_feature, created_at, updated_at";

interface CloudConversationRow {
  id: string;
  title?: string | null;
  description?: string | null;
  initial_agent_id?: string | null;
  last_model_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

interface CloudMessageRow {
  id: string;
  role?: string | null;
  content?: unknown;
  user_content?: unknown;
  model_context?: unknown;
  error?: unknown;
  created_at?: string | null;
  updated_at?: string | null;
  position?: number | null;
}

interface ExtractedContent {
  answer: string;
  reasoning: string;
  diagnostics: string[];
}

interface BlockAccumulatorEntry {
  index: number;
  text: string;
}

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
        source_app: CLOUD_SOURCE_APP,
        source_feature: CLOUD_SOURCE_FEATURE,
      },
    };
  }

  if (options?.agentId) {
    const body: Record<string, unknown> = {
      stream: true,
      source_app: CLOUD_SOURCE_APP,
      source_feature: CLOUD_SOURCE_FEATURE,
    };
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
      source_app: CLOUD_SOURCE_APP,
      source_feature: CLOUD_SOURCE_FEATURE,
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

function normalizeRole(role: string | null | undefined): ChatMessage["role"] {
  return role === "assistant" || role === "system" ? role : "user";
}

function mergeText(parts: string[]): string {
  return parts
    .map((part) => part.trim())
    .filter(Boolean)
    .join("\n\n");
}

function splitInlineReasoning(raw: string): { answer: string; reasoning: string } {
  let answer = "";
  let reasoning = "";
  let cursor = 0;
  const openingTag = /<(thinking|reasoning|think)\b[^>]*>/i;

  while (cursor < raw.length) {
    const rest = raw.slice(cursor);
    const match = openingTag.exec(rest);
    if (!match || match.index < 0) {
      answer += rest;
      break;
    }

    const tag = match[1]?.toLowerCase();
    if (!tag) {
      answer += rest;
      break;
    }
    const start = cursor + match.index;
    const contentStart = start + match[0].length;
    answer += raw.slice(cursor, start);

    const closingTag = new RegExp(`</${tag}>`, "i");
    const closeMatch = closingTag.exec(raw.slice(contentStart));
    if (!closeMatch || closeMatch.index < 0) {
      reasoning += raw.slice(contentStart);
      break;
    }

    reasoning += raw.slice(contentStart, contentStart + closeMatch.index);
    cursor = contentStart + closeMatch.index + closeMatch[0].length;
  }

  return { answer, reasoning };
}

function extractTextFromRecord(record: Record<string, unknown> | null): string | null {
  if (!record) return null;
  return (
    readString(record.text) ??
    readString(record.content) ??
    readString(record.markdown) ??
    readString(record.value) ??
    readString(record.output) ??
    readString(record.transcript)
  );
}

function renderBlockText(blockType: string, content: string | null, data: Record<string, unknown> | null): string | null {
  const direct = content ?? extractTextFromRecord(data);
  if (direct) {
    if (blockType === "code") {
      const language = readString(data?.language) ?? "";
      return `\`\`\`${language}\n${direct}\n\`\`\``;
    }
    if (blockType === "mermaid") {
      return `\`\`\`mermaid\n${direct}\n\`\`\``;
    }
    return direct;
  }

  const url =
    readString(data?.url) ??
    readString(data?.cdn_url) ??
    readString(data?.signed_url) ??
    readString(data?.download_url);
  if (!url) return null;
  if (blockType === "image") return `![Generated image](${url})`;
  return `[${humanizeToken(blockType)}](${url})`;
}

function isReasoningBlockType(blockType: string): boolean {
  return (
    blockType === "thinking" ||
    blockType === "reasoning" ||
    blockType === "consolidated_reasoning" ||
    blockType === "private"
  );
}

function contentPartToExtracted(part: unknown): ExtractedContent {
  if (typeof part === "string") {
    const split = splitInlineReasoning(part);
    return { answer: split.answer, reasoning: split.reasoning, diagnostics: [] };
  }

  const record = readRecord(part);
  if (!record) return { answer: "", reasoning: "", diagnostics: [] };

  const type = readString(record.type) ?? "text";
  if (type === "thinking" || type === "reasoning" || type === "consolidated_reasoning") {
    return {
      answer: "",
      reasoning: readString(record.text) ?? readString(record.content) ?? "",
      diagnostics: [],
    };
  }

  if (type === "text") {
    const split = splitInlineReasoning(readString(record.text) ?? readString(record.content) ?? "");
    return { answer: split.answer, reasoning: split.reasoning, diagnostics: [] };
  }

  if (type === "media") {
    const kind = readString(record.kind) ?? "media";
    const url = readString(record.url);
    if (!url) return { answer: "", reasoning: "", diagnostics: [] };
    return {
      answer: kind === "image" ? `![Image](${url})` : `[${humanizeToken(kind)}](${url})`,
      reasoning: "",
      diagnostics: [],
    };
  }

  if (type === "code_exec") {
    const language = readString(record.language) ?? "";
    const code = readString(record.code);
    return {
      answer: code ? `\`\`\`${language}\n${code}\n\`\`\`` : "",
      reasoning: "",
      diagnostics: [],
    };
  }

  if (type === "code_result") {
    return {
      answer: readString(record.output) ?? "",
      reasoning: "",
      diagnostics: readString(record.outcome) ? [`Code result: ${record.outcome}`] : [],
    };
  }

  if (type === "tool_call" || type === "tool_result" || type === "web_search") {
    const name = readString(record.name) ?? readString(record.call_id) ?? type;
    return {
      answer: "",
      reasoning: "",
      diagnostics: [`${humanizeToken(type)}: ${name}`],
    };
  }

  const fallback = extractTextFromRecord(record);
  if (!fallback) return { answer: "", reasoning: "", diagnostics: [] };
  const split = splitInlineReasoning(fallback);
  return { answer: split.answer, reasoning: split.reasoning, diagnostics: [] };
}

function extractMessageContent(content: unknown): ExtractedContent {
  if (typeof content === "string") {
    const split = splitInlineReasoning(content);
    return { answer: split.answer.trim(), reasoning: split.reasoning.trim(), diagnostics: [] };
  }

  if (Array.isArray(content)) {
    const extracted = content.map(contentPartToExtracted);
    return {
      answer: mergeText(extracted.map((item) => item.answer)),
      reasoning: mergeText(extracted.map((item) => item.reasoning)),
      diagnostics: extracted.flatMap((item) => item.diagnostics),
    };
  }

  const record = readRecord(content);
  if (!record) return { answer: "", reasoning: "", diagnostics: [] };

  const text = extractTextFromRecord(record);
  if (!text) return { answer: "", reasoning: "", diagnostics: [] };
  const split = splitInlineReasoning(text);
  return { answer: split.answer.trim(), reasoning: split.reasoning.trim(), diagnostics: [] };
}

function conversationRowToConversation(row: CloudConversationRow): Conversation {
  const createdAt = row.created_at ?? new Date().toISOString();
  const updatedAt = row.updated_at ?? createdAt;
  return {
    id: row.id,
    title: row.title?.trim() || "New Conversation",
    mode: "chat",
    model: row.last_model_id ?? "",
    messages: [],
    created_at: createdAt,
    updated_at: updatedAt,
    serverConversationId: row.id,
    routeMode: "conversation",
    ...(row.description?.trim() ? { description: row.description.trim() } : {}),
    ...(row.initial_agent_id ? { agentId: row.initial_agent_id } : {}),
  };
}

function messageRowToChatMessage(row: CloudMessageRow): ChatMessage {
  const extracted = extractMessageContent(row.content ?? row.user_content);
  const errRecord = readRecord(row.error);
  const error =
    readString(errRecord?.user_message) ??
    readString(errRecord?.message) ??
    readString(errRecord?.code) ??
    null;
  return {
    id: row.id,
    role: normalizeRole(row.role),
    content: extracted.answer,
    timestamp: row.created_at ?? row.updated_at ?? new Date().toISOString(),
    ...(extracted.reasoning ? { reasoning: extracted.reasoning } : {}),
    ...(extracted.diagnostics.length > 0 ? { streamDiagnostics: extracted.diagnostics } : {}),
    ...(error ? { error } : {}),
  };
}

function sortConversations(items: Conversation[]): Conversation[] {
  return [...items].sort(
    (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
  );
}

function mergeRemoteConversations(
  current: Conversation[],
  remoteRows: CloudConversationRow[],
): Conversation[] {
  const remote = remoteRows.map(conversationRowToConversation);
  const consumedLocalIds = new Set<string>();
  const merged = remote.map((remoteConversation) => {
    const existing = current.find(
      (item) =>
        item.id === remoteConversation.id ||
        item.serverConversationId === remoteConversation.serverConversationId,
    );
    if (!existing) return remoteConversation;
    consumedLocalIds.add(existing.id);
    return {
      ...remoteConversation,
      id: existing.id,
      messages: existing.messages.length > 0 ? existing.messages : remoteConversation.messages,
      created_at: existing.created_at || remoteConversation.created_at,
      updated_at:
        new Date(existing.updated_at).getTime() > new Date(remoteConversation.updated_at).getTime()
          ? existing.updated_at
          : remoteConversation.updated_at,
    };
  });

  const localOnly = current.filter(
    (item) =>
      !consumedLocalIds.has(item.id) &&
      (!item.serverConversationId || item.messages.length > 0),
  );

  return sortConversations([...merged, ...localOnly]).slice(0, MAX_CONVERSATIONS);
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
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
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

  const refreshConversations = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      let query = supabase
        .schema("chat")
        .from("conversation")
        .select(HISTORY_COLUMNS)
        .is("deleted_at", null)
        .eq("is_ephemeral", false)
        .order("updated_at", { ascending: false })
        .range(0, MAX_CONVERSATIONS - 1);

      query = query.or(`source_feature.in.(${CHAT_HISTORY_FEATURES.join(",")})`);

      const { data, error } = await query;
      if (error) throw error;
      setConversations((prev) =>
        mergeRemoteConversations(prev, (data ?? []) as CloudConversationRow[]),
      );
    } catch (error: unknown) {
      const message =
        error instanceof Error ? error.message : "Failed to load cloud conversations";
      setHistoryError(message);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshConversations();
  }, [refreshConversations]);

  const hydrateConversationMessages = useCallback(async (id: string) => {
    const target = conversationsRef.current.find((item) => item.id === id);
    if (!target || target.messages.length > 0) return;

    const serverConversationId = target.serverConversationId ?? target.id;
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const { data, error } = await supabase
        .schema("chat")
        .from("message")
        .select("*")
        .eq("conversation_id", serverConversationId)
        .is("deleted_at", null)
        .eq("is_visible_to_user", true)
        .order("position", { ascending: true })
        .order("created_at", { ascending: true })
        .limit(200);

      if (error) throw error;
      const messages = ((data ?? []) as CloudMessageRow[]).map(messageRowToChatMessage);
      setConversations((prev) =>
        prev.map((conversation) =>
          conversation.id === id
            ? {
                ...conversation,
                messages,
              }
            : conversation,
        ),
      );
    } catch (error: unknown) {
      const message =
        error instanceof Error ? error.message : "Failed to load conversation messages";
      setHistoryError(message);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

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
      void hydrateConversationMessages(id);
    }
  }, [hydrateConversationMessages]);

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
            conversation.id === conversationId ||
            (patch.serverConversationId &&
              conversation.serverConversationId === patch.serverConversationId)
              ? { ...conversation, ...patch }
              : conversation,
          ),
        );
      };

      setIsStreaming(true);
      let accumulated = "";
      let rawAnswer = "";
      let reasoning = "";
      let inlineReasoning = "";
      let eventCount = 0;
      let sawTerminalEvent = false;
      let lastStatus = "Connecting to AIDream...";
      let diagnostics: string[] = [];
      const answerRenderBlocks = new Map<string, BlockAccumulatorEntry>();
      const reasoningRenderBlocks = new Map<string, BlockAccumulatorEntry>();

      const setStatus = (status: string) => {
        lastStatus = status;
        updateAssistant({ streamStatus: status });
      };

      const addDiagnostic = (message: string) => {
        diagnostics = [...diagnostics, message].slice(-12);
        updateAssistant({ streamDiagnostics: diagnostics });
      };

      const combinedReasoning = () =>
        mergeText([
          reasoning,
          inlineReasoning,
          ...[...reasoningRenderBlocks.values()]
            .sort((a, b) => a.index - b.index)
            .map((entry) => entry.text),
        ]);

      const updateReasoning = (status?: string) => {
        updateAssistant({
          reasoning: combinedReasoning(),
          ...(status ? { streamStatus: status } : {}),
        });
      };

      const applyAnswerText = (nextRawAnswer: string) => {
        rawAnswer = nextRawAnswer;
        const split = splitInlineReasoning(rawAnswer);
        accumulated = split.answer;
        inlineReasoning = split.reasoning;
        updateAssistant({
          content: accumulated,
          reasoning: combinedReasoning(),
          isStreaming: true,
          streamStatus: "Generating response...",
        });
      };

      const applyRenderAnswerBlocks = () => {
        if (rawAnswer) return;
        accumulated = mergeText(
          [...answerRenderBlocks.values()]
            .sort((a, b) => a.index - b.index)
            .map((entry) => entry.text),
        );
        updateAssistant({ content: accumulated });
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
              applyAnswerText(rawAnswer + event.data.text);
              break;
            }

            case EventType.REASONING_CHUNK: {
              reasoning += event.data.text;
              updateReasoning("Reasoning...");
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
                const description = readString(record?.description);
                const labeledConversationId = readString(record?.conversation_id);
                if (title || description || labeledConversationId) {
                  updateConversationMeta({
                    ...(title ? { title } : {}),
                    ...(description ? { description } : {}),
                    ...(labeledConversationId
                      ? {
                          serverConversationId: labeledConversationId,
                          routeMode: "conversation" as ConversationRouteMode,
                        }
                      : {}),
                  });
                }
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
                applyAnswerText(output);
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
              const blockText = renderBlockText(blockType, content, event.data.data ?? null);
              const blockId = event.data.blockId || `${event.data.blockIndex}-${blockType}`;

              if (blockText) {
                if (isReasoningBlockType(blockType)) {
                  reasoningRenderBlocks.set(blockId, {
                    index: event.data.blockIndex,
                    text: blockText,
                  });
                  updateReasoning(
                    blockType === "thinking" || blockType === "reasoning"
                      ? "Reasoning..."
                      : undefined,
                  );
                } else if (event.data.status === "complete" || !answerRenderBlocks.has(blockId)) {
                  answerRenderBlocks.set(blockId, {
                    index: event.data.blockIndex,
                    text: blockText,
                  });
                  applyRenderAnswerBlocks();
                }
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
        void refreshConversations();
      }
    },
    [activeConversationId, createConversation, isStreaming, mode, model, refreshConversations],
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
    historyError,
    historyLoading,
    groupedConversations,
    createConversation,
    selectConversation,
    deleteConversation,
    renameConversation,
    sendMessage,
    stopStreaming,
    clearConversations,
    refreshConversations,
    setMode,
    setModel,
  };
}
