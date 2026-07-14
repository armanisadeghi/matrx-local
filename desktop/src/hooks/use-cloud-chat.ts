import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AIDREAM_SERVER_URL, fetchAIDreamModels } from "@/lib/aidream-client";
import supabase from "@/lib/supabase";
import type {
  ChatMessage,
  ChatMode,
  Conversation,
  ConversationRouteMode,
  ModelOption,
} from "@/hooks/use-chat";

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

function parseStreamLine(line: string): { event: string; data?: Record<string, unknown> } | null {
  const trimmed = line.trim();
  if (!trimmed || trimmed === "data: [DONE]") return null;
  const jsonText = trimmed.startsWith("data:") ? trimmed.slice(5).trim() : trimmed;
  if (!jsonText) return null;
  const parsed = JSON.parse(jsonText) as unknown;
  if (!parsed || typeof parsed !== "object") return null;
  const record = parsed as Record<string, unknown>;
  if (typeof record.event !== "string") return null;
  const data = readData(record.data);
  return data ? { event: record.event, data } : { event: record.event };
}

function readData(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  return value as Record<string, unknown>;
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
        setModel((prev) => (mapped.some((item) => item.id === prev) ? prev : (mapped[0]?.id ?? "")));
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
      const hasVariables = Boolean(options?.variables && Object.keys(options.variables).length > 0);
      if (!hasAgent && !trimmed) return;
      if (hasAgent && !trimmed && !hasVariables) return;
      if (isStreaming) return;

      if (!AIDREAM_SERVER_URL) {
        throw new Error("VITE_AIDREAM_SERVER_URL_LIVE is not set.");
      }

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
          const errorText = await response.text().catch(() => `HTTP ${response.status}`);
          updateAssistant({
            content: `Error: ${errorText}`,
            isStreaming: false,
            error: errorText,
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

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            let event: { event: string; data?: Record<string, unknown> } | null = null;
            try {
              event = parseStreamLine(line);
            } catch {
              continue;
            }
            if (!event) continue;

            if (event.event === "chunk") {
              const text = typeof event.data?.text === "string" ? event.data.text : "";
              accumulated += text;
              updateAssistant({ content: accumulated, isStreaming: true });
            } else if (event.event === "completion") {
              const output = typeof event.data?.output === "string" ? event.data.output : "";
              if (output && !accumulated) accumulated = output;
            } else if (event.event === "data") {
              const innerEvent = event.data?.event;
              const conversationIdValue = event.data?.conversation_id;
              if (innerEvent === "conversation_id" && typeof conversationIdValue === "string") {
                updateConversationMeta({
                  serverConversationId: conversationIdValue,
                  routeMode: "conversation",
                });
              }
            } else if (event.event === "error") {
              const message =
                typeof event.data?.message === "string" ? event.data.message : "Unknown error";
              updateAssistant({
                content: accumulated || message,
                isStreaming: false,
                error: message,
              });
            }
          }
        }

        updateAssistant({ content: accumulated, isStreaming: false });
      } catch (error: unknown) {
        if (error instanceof Error && error.name === "AbortError") {
          updateAssistant({ isStreaming: false });
        } else {
          const message = error instanceof Error ? error.message : "Connection error";
          updateAssistant({
            content: accumulated || `Failed to reach Cloud Chat: ${message}`,
            isStreaming: false,
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
