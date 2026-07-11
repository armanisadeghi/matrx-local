import { useState, useEffect, useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, AlertCircle, X } from "lucide-react";
import { useChat } from "@/hooks/use-chat";
import { useAgents } from "@/hooks/use-agents";
import { useChatTts } from "@/hooks/use-chat-tts";
import { useTtsApp } from "@/contexts/TtsContext";
import { ChatMessages } from "@/components/chat/ChatMessages";
import { ChatInput } from "@/components/chat/ChatInput";
import { ChatWelcome } from "@/components/chat/ChatWelcome";
import { GuidedVariableInputs } from "@/components/chat/GuidedVariableInputs";
import { SandboxPicker } from "@/features/compute/SandboxPicker";
import { useServiceStatus } from "@/hooks/use-service-status";
import { cn } from "@/lib/utils";
import { engine as engineAPI } from "@/lib/api";
import { loadSettings } from "@/lib/settings";
import type { EngineStatus } from "@/hooks/use-engine";
import type { ActiveAgent, AgentInfo, PromptVariable } from "@/types/agents";

type UseChatReturn = ReturnType<typeof useChat>;

interface AiStatusWarning {
  message: string;
  detail: string;
}

export interface ChatPanelProps {
  engineStatus: EngineStatus;
  engineUrl: string | null;
  tools: string[];
  compact?: boolean;
  forceLocalModel?: boolean;
  /** Pass an external useChat instance to share state with the sidebar. */
  chatState?: UseChatReturn;
}

export function ChatPanel({
  engineStatus,
  engineUrl,
  tools,
  compact = false,
  forceLocalModel = false,
  chatState: externalChat,
}: ChatPanelProps) {
  const navigate = useNavigate();
  const [aiWarning, setAiWarning] = useState<AiStatusWarning | null>(null);
  const [aiWarningDismissed, setAiWarningDismissed] = useState(false);
  // Read this engine's own instance_id (the matrx-local app_instances row this
  // desktop registered as) so the SandboxPicker can label the matching target
  // as "This computer".
  const [serviceState] = useServiceStatus(engineStatus);
  const thisDeviceInstanceId = serviceState.cloudDebug?.instance_id ?? null;


  useEffect(() => {
    if (compact || forceLocalModel) return;
    if (engineStatus !== "connected" || !engineUrl) return;
    engineAPI
      .getAiStatus()
      .then((status) => {
        if (!status.providers.any_available) {
          const missing = status.providers.missing;
          setAiWarning({
            message: "No AI provider API keys are configured.",
            detail: `Add at least one key (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, etc.) to the engine .env file and restart. Missing: ${missing.join(", ")}.`,
          });
        }
      })
      .catch((e) => console.warn("[chat] getAiStatus failed:", e));
  }, [engineStatus, engineUrl, compact, forceLocalModel]);

  const {
    builtins,
    userAgents,
    sharedAgents,
    isLoading: agentsLoading,
  } = useAgents({ engineUrl });
  const [activeAgent, setActiveAgent] = useState<ActiveAgent | null>(null);

  const [variableValues, setVariableValues] = useState<Record<string, string>>(
    {},
  );
  const [activeVariables, setActiveVariables] = useState<PromptVariable[]>([]);

  const internalChat = useChat({ engineUrl: externalChat ? null : engineUrl });
  const chat = externalChat ?? internalChat;

  const {
    activeConversation,
    isStreaming,
    mode,
    model,
    availableModels,
    sendMessage,
    stopStreaming,
    setMode,
    setModel,
    setToolSchemas,
  } = chat;

  // "Confidential" (forceLocalModel) chats must never reach a cloud provider.
  // The prop previously only suppressed the API-key warning — model selection
  // could still default to a cloud model and stream the user's text off-box.
  const localOnlyModels = useMemo(
    () => availableModels.filter((m) => m.provider === "local"),
    [availableModels],
  );
  useEffect(() => {
    if (!forceLocalModel) return;
    const current = availableModels.find((m) => m.id === model);
    if (current?.provider === "local") return;
    if (localOnlyModels.length > 0) {
      setModel(localOnlyModels[0].id);
    }
  }, [forceLocalModel, model, availableModels, localOnlyModels, setModel]);


  useEffect(() => {
    if (engineStatus !== "connected" || !engineUrl) return;
    const loadSchemas = async () => {
      try {
        const resp = await fetch(`${engineUrl}/chat/tools`);
        if (resp.ok) {
          const data = await resp.json();
          setToolSchemas(data.tools ?? []);
        }
      } catch {
        /* optional */
      }
    };
    loadSchemas();
  }, [engineStatus, engineUrl, setToolSchemas]);

  const messages = activeConversation?.messages ?? [];
  const hasMessages = messages.length > 0;

  // ── TTS read-aloud integration ──────────────────────────────────────
  const [ttsReadAloudEnabled, setTtsReadAloudEnabled] = useState(true);
  const [readingMessageId, setReadingMessageId] = useState<string | null>(null);

  useEffect(() => {
    loadSettings().then((s) => {
      setTtsReadAloudEnabled(s.ttsReadAloudEnabled);
    });
  }, []);

  let ttsActions = null;
  let ttsState: ReturnType<typeof useTtsApp>[0] | null = null;
  try {
    const [state, actions] = useTtsApp();
    ttsActions = actions;
    ttsState = state;
  } catch {
    // TtsProvider not mounted — read-aloud unavailable
  }

  const assistantMsgs = messages.filter((m) => m.role === "assistant");
  const lastAssistantMsg = assistantMsgs[assistantMsgs.length - 1] ?? null;
  const chatTts = useChatTts(ttsActions, lastAssistantMsg, isStreaming);

  const handleReadAloud = useCallback(
    (messageId: string, content: string) => {
      setReadingMessageId(messageId);
      chatTts.readCompleteMessage(content);
    },
    [chatTts],
  );

  const handleStopReadAloud = useCallback(() => {
    setReadingMessageId(null);
    chatTts.stopReadAloud();
  }, [chatTts]);

  useEffect(() => {
    if (!chatTts.isReadingAloud && readingMessageId) {
      setReadingMessageId(null);
    }
  }, [chatTts.isReadingAloud, readingMessageId]);

  useEffect(() => {
    if (hasMessages) {
      setActiveVariables([]);
      setVariableValues({});
      return;
    }
    if (!activeAgent || activeAgent.id === "") {
      setActiveVariables([]);
      setVariableValues({});
      return;
    }
    const vars = activeAgent.variable_defaults ?? [];
    setActiveVariables(vars);
    const defaults: Record<string, string> = {};
    vars.forEach((v) => {
      if (v.defaultValue) defaults[v.name] = v.defaultValue;
    });
    setVariableValues(defaults);
  }, [activeAgent?.id, hasMessages]);

  // NOTE: compact mode no longer creates a conversation on mount — every
  // Quick Chat open appended a permanent empty "New conversation" to the
  // shared history (evicting real ones at the 100 cap). use-chat's
  // sendMessage creates one lazily on first send.

  const handleSuggestionClick = useCallback(
    (prompt: string) => {
      sendMessage(prompt);
    },
    [sendMessage],
  );

  const handleSend = useCallback(
    async (content: string) => {
      if (forceLocalModel) {
        const current = availableModels.find((m) => m.id === model);
        if (current?.provider !== "local") {
          // No local model connected yet — refuse rather than silently
          // routing a confidential message to a cloud provider.
          return;
        }
      }
      const submittedVars = { ...variableValues };
      setActiveVariables([]);
      setVariableValues({});
      await sendMessage(content, {
        ...(activeAgent?.id ? { agentId: activeAgent.id } : {}),
        variables: submittedVars,
      });
    },
    [sendMessage, activeAgent, variableValues, forceLocalModel, availableModels, model],
  );

  const handleVariableChange = (name: string, value: string) => {
    setVariableValues((prev) => ({ ...prev, [name]: value }));
  };

  const showWelcome = !activeConversation || messages.length === 0;
  const hasVariables = activeVariables.length > 0;

  return (
    <div className="flex h-full flex-col overflow-hidden bg-background">
      {!compact && (
        <header className="no-select flex h-12 items-center justify-between border-b px-4">
          <div className="flex items-center gap-2">
            <h1 className="text-sm font-medium">
              {activeConversation?.title ?? "New chat"}
            </h1>
          </div>
          <div className="flex items-center gap-2">
            <SandboxPicker thisDeviceInstanceId={thisDeviceInstanceId} />
            <div className="flex items-center gap-1.5">
              <div
                className={`h-2 w-2 rounded-full ${
                  engineStatus === "connected"
                    ? "bg-emerald-500"
                    : engineStatus === "discovering" ||
                        engineStatus === "starting"
                      ? "bg-amber-500 animate-pulse"
                      : "bg-zinc-500"
                }`}
              />
              <span className="text-[11px] text-muted-foreground">
                {tools.length} tools
              </span>
            </div>
          </div>
        </header>
      )}

      {!compact && aiWarning && !aiWarningDismissed && (
        <div className="flex items-start gap-3 border-b border-amber-500/30 bg-amber-500/5 px-4 py-2.5">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium text-amber-500">
              {aiWarning.message}
            </p>
            {aiWarning.detail && (
              <p className="mt-0.5 text-xs text-muted-foreground">
                {aiWarning.detail}
              </p>
            )}
          </div>
          <button
            onClick={() => navigate("/settings?tab=api-keys")}
            className="shrink-0 whitespace-nowrap text-xs text-amber-500 underline transition-colors hover:text-amber-400"
          >
            Configure API keys →
          </button>
          <button
            onClick={() => setAiWarningDismissed(true)}
            className="shrink-0 text-muted-foreground transition-colors hover:text-foreground"
            aria-label="Dismiss"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {showWelcome ? (
          <ChatWelcome
            onSuggestionClick={handleSuggestionClick}
            toolCount={tools.length}
            disabled={engineStatus !== "connected"}
            disabledReason="Engine not connected — reconnect to the engine to send a message."
          />
        ) : (
          <ChatMessages
            messages={messages}
            isStreaming={isStreaming}
            ttsReadAloudEnabled={ttsReadAloudEnabled}
            readingMessageId={readingMessageId}
            onReadAloud={handleReadAloud}
            onStopReadAloud={handleStopReadAloud}
          />
        )}
      </div>

      {hasVariables && (
        <div className="max-h-[40%] overflow-y-auto px-4 pt-2">
          <GuidedVariableInputs
            variableDefaults={activeVariables}
            values={variableValues}
            onChange={handleVariableChange}
            disabled={isStreaming}
            seamless
          />
        </div>
      )}

      {chatTts.readAloudError && (
        <div className="shrink-0 px-4 pt-1">
          <div
            role="alert"
            className="flex items-start gap-2 rounded bg-destructive/10 px-3 py-2 text-xs text-destructive"
          >
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span className="flex-1">
              {chatTts.readAloudError.code === "model_not_downloaded" &&
              ttsState?.status?.is_downloading
                ? `TTS voice model is downloading (${Math.round(
                    ttsState.status.download_progress,
                  )}%)…`
                : chatTts.readAloudError.message}
            </span>
            <button
              className="shrink-0 text-destructive/70 underline hover:text-destructive"
              onClick={() => chatTts.clearReadAloudError()}
              title="Dismiss"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      <div className={cn("shrink-0 px-4 pb-3", hasVariables ? "pt-0" : "pt-1")}>
        <ChatInput
          onSend={handleSend}
          onStop={stopStreaming}
          isStreaming={isStreaming}
          mode={mode}
          model={forceLocalModel ? (localOnlyModels[0]?.id ?? model) : model}
          availableModels={forceLocalModel ? localOnlyModels : availableModels}
          onModelChange={setModel}
          onModeChange={setMode}
          engineReady={engineStatus === "connected"}
          autoFocus={compact}
          agents={[...builtins, ...userAgents, ...sharedAgents]}
          selectedAgentId={activeAgent?.id ?? null}
          onAgentChange={(agentId) => {
            if (!agentId) {
              setActiveAgent(null);
              return;
            }
            const all: AgentInfo[] = [
              ...builtins,
              ...userAgents,
              ...sharedAgents,
            ];
            const found = all.find((a) => a.id === agentId);
            setActiveAgent(found ?? null);
          }}
          agentsLoading={agentsLoading}
        />
      </div>
    </div>
  );
}
