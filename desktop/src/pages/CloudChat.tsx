import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  Cloud,
  Loader2,
  MessageSquarePlus,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { AgentPicker } from "@/components/chat/AgentPicker";
import { ChatInput } from "@/components/chat/ChatInput";
import { ChatMessages } from "@/components/chat/ChatMessages";
import { ChatSidebar } from "@/components/chat/ChatSidebar";
import { GuidedVariableInputs } from "@/components/chat/GuidedVariableInputs";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useCloudAgents } from "@/hooks/use-cloud-agents";
import { useCloudChat } from "@/hooks/use-cloud-chat";
import { cn } from "@/lib/utils";
import type { AgentInfo, PromptVariable } from "@/types/agents";

const CLOUD_SUGGESTIONS = [
  "Help me outline a product spec for a desktop Cloud Chat migration.",
  "Summarize the differences between local and cloud AI workflows.",
  "Draft a test checklist for agent variables and streaming chat.",
  "Create a concise implementation plan for a complex React feature.",
];

function defaultVariableValues(variables: PromptVariable[]): Record<string, string> {
  const defaults: Record<string, string> = {};
  for (const variable of variables) {
    if (variable.defaultValue) defaults[variable.name] = variable.defaultValue;
  }
  return defaults;
}

function CloudWelcome({
  activeAgent,
  onSuggestionClick,
}: {
  activeAgent: AgentInfo | null;
  onSuggestionClick: (prompt: string) => void;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center px-4">
      <div className="mb-8 flex flex-col items-center text-center">
        <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/90 text-primary-foreground">
          <Cloud className="h-8 w-8" />
        </div>
        <h2 className="mb-1.5 text-2xl font-semibold">Cloud Chat</h2>
        <p className="max-w-xl text-sm text-muted-foreground">
          {activeAgent
            ? `Ready to run ${activeAgent.name} with cloud conversation state.`
            : "Choose an agent in the header or start with a model-backed cloud chat."}
        </p>
      </div>

      <div className="grid w-full max-w-2xl grid-cols-1 gap-2.5 sm:grid-cols-2">
        {CLOUD_SUGGESTIONS.map((prompt) => (
          <button
            key={prompt}
            type="button"
            onClick={() => onSuggestionClick(prompt)}
            className="glass-subtle group flex min-h-20 items-start gap-3 rounded-lg px-4 py-3.5 text-left transition-all duration-200 hover:shadow-md active:scale-[0.98]"
          >
            <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground transition-colors group-hover:text-primary" />
            <span className="text-sm leading-snug">{prompt}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function CloudChat() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [agentPickerOpen, setAgentPickerOpen] = useState(false);
  const [activeAgent, setActiveAgent] = useState<AgentInfo | null>(null);
  const [activeVariables, setActiveVariables] = useState<PromptVariable[]>([]);
  const [variableValues, setVariableValues] = useState<Record<string, string>>({});
  const cloudChat = useCloudChat();
  const cloudAgents = useCloudAgents();
  const {
    activeConversationId,
    availableModels,
    createConversation,
    deleteConversation,
    groupedConversations,
    isStreaming,
    mode,
    model,
    renameConversation,
    selectConversation,
    sendMessage,
    setMode,
    setModel,
    stopStreaming,
  } = cloudChat;
  const {
    agents,
    ensureExecutionMinimal,
    error: agentsError,
    executionError,
    executionLoadingAgentId,
    isLoading: agentsLoading,
    refresh,
  } = cloudAgents;

  const activeConversation = cloudChat.activeConversation;
  const messages = activeConversation?.messages ?? [];
  const hasMessages = messages.length > 0;
  const selectedAgentId = activeAgent?.id ?? null;

  useEffect(() => {
    if (!selectedAgentId || hasMessages) {
      setActiveVariables([]);
      setVariableValues({});
      return;
    }

    let cancelled = false;
    void ensureExecutionMinimal(selectedAgentId).then((variables) => {
      if (cancelled) return;
      setActiveVariables(variables);
      setVariableValues(defaultVariableValues(variables));
    });

    return () => {
      cancelled = true;
    };
  }, [ensureExecutionMinimal, hasMessages, selectedAgentId]);

  const activeAgentWithVariables = useMemo(() => {
    if (!activeAgent) return null;
    return {
      ...activeAgent,
      variable_defaults: activeVariables,
    };
  }, [activeAgent, activeVariables]);

  const handleSelectAgent = useCallback(
    (agentId: string | null) => {
      if (!agentId) {
        setActiveAgent(null);
        setActiveVariables([]);
        setVariableValues({});
        return;
      }
      const found = agents.find((agent) => agent.id === agentId) ?? null;
      setActiveAgent(found);
      if (activeConversationId && !hasMessages) {
        selectConversation(null);
      }
    },
    [activeConversationId, agents, hasMessages, selectConversation],
  );

  const handleNewChat = useCallback(() => {
    createConversation();
  }, [createConversation]);

  const handleSend = useCallback(
    async (content: string) => {
      const submittedVariables = { ...variableValues };
      setActiveVariables([]);
      setVariableValues({});
      await sendMessage(content, {
        ...(activeAgentWithVariables?.id ? { agentId: activeAgentWithVariables.id } : {}),
        variables: submittedVariables,
      });
    },
    [activeAgentWithVariables?.id, sendMessage, variableValues],
  );

  const handleVariableChange = useCallback((name: string, value: string) => {
    setVariableValues((prev) => ({ ...prev, [name]: value }));
  }, []);

  const handleSuggestionClick = useCallback(
    (prompt: string) => {
      void handleSend(prompt);
    },
    [handleSend],
  );

  const pickerLabel = activeAgent?.name ?? "Select an agent";
  const agentCount = agents.length;
  const showVariables = activeVariables.length > 0 && !hasMessages;

  return (
    <div className="flex h-full overflow-hidden">
      <ChatSidebar
        conversations={cloudChat.conversations}
        groupedConversations={groupedConversations}
        activeConversationId={activeConversationId}
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((prev) => !prev)}
        onSelect={selectConversation}
        onNew={handleNewChat}
        onDelete={deleteConversation}
        onRename={renameConversation}
      />

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-background">
        <PageHeader
          title={activeConversation?.title ?? "Cloud Chat"}
          description="Cloud-backed agent chat, isolated from the local Chat route"
        >
          <div className="flex min-w-0 items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="max-w-[280px] justify-start"
              onClick={() => setAgentPickerOpen(true)}
            >
              <Cloud className="h-4 w-4" />
              <span className="truncate">{pickerLabel}</span>
              <ChevronDown className="ml-auto h-3.5 w-3.5 opacity-60" />
            </Button>
            <Button variant="ghost" size="sm" onClick={() => void refresh()}>
              <RefreshCw className="h-4 w-4" />
              Refresh
            </Button>
            <Badge variant={agentsError ? "warning" : "secondary"}>
              {agentsLoading ? "Loading agents" : `${agentCount} agents`}
            </Badge>
          </div>
        </PageHeader>

        {(agentsError || executionError || cloudChat.modelError) && (
          <div className="border-b border-amber-500/30 bg-amber-500/5 px-4 py-2 text-xs text-amber-500">
            {agentsError ?? executionError ?? cloudChat.modelError}
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto">
          {messages.length === 0 ? (
            <CloudWelcome
              activeAgent={activeAgentWithVariables}
              onSuggestionClick={handleSuggestionClick}
            />
          ) : (
            <ChatMessages messages={messages} isStreaming={isStreaming} />
          )}
        </div>

        {showVariables && (
          <div className="max-h-[40%] overflow-y-auto border-t border-border/60 px-4 pt-3">
            <div className="mx-auto max-w-3xl">
              <div className="mb-2 flex items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                  <MessageSquarePlus className="h-3.5 w-3.5" />
                  Agent variables
                </div>
                {executionLoadingAgentId === selectedAgentId && (
                  <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Loading
                  </span>
                )}
              </div>
              <GuidedVariableInputs
                variableDefaults={activeVariables}
                values={variableValues}
                onChange={handleVariableChange}
                disabled={isStreaming}
                seamless
              />
            </div>
          </div>
        )}

        <div className={cn("shrink-0 px-4 pb-3", showVariables ? "pt-0" : "pt-1")}>
          <ChatInput
            onSend={handleSend}
            onStop={stopStreaming}
            isStreaming={isStreaming}
            mode={mode}
            model={model}
            availableModels={availableModels}
            onModelChange={setModel}
            onModeChange={setMode}
            engineReady
          />
        </div>
      </div>

      <AgentPicker
        agents={agents}
        selectedAgentId={selectedAgentId}
        onSelect={handleSelectAgent}
        isLoading={agentsLoading}
        open={agentPickerOpen}
        onClose={() => setAgentPickerOpen(false)}
      />
    </div>
  );
}
