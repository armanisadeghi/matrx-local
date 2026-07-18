import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  Cloud,
  Cpu,
  Loader2,
  MessageSquarePlus,
} from "lucide-react";
import { AgentPicker } from "@/components/chat/AgentPicker";
import { ChatInput } from "@/components/chat/ChatInput";
import { ChatMessages } from "@/components/chat/ChatMessages";
import { ChatSidebar } from "@/components/chat/ChatSidebar";
import { GuidedVariableInputs } from "@/components/chat/GuidedVariableInputs";
import { Button } from "@/components/ui/button";
import { useCloudAgents } from "@/hooks/use-cloud-agents";
import { type CloudChatExecutionTarget, useCloudChat } from "@/hooks/use-cloud-chat";
import type { EngineStatus } from "@/hooks/use-engine";
import { cn } from "@/lib/utils";
import type { AgentInfo, PromptVariable } from "@/types/agents";

const DEFAULT_GENERAL_CHAT_AGENT_ID = "6b6b4e45-4699-4860-8dea-d8a60e07d69a";

function defaultVariableValues(variables: PromptVariable[]): Record<string, string> {
  const defaults: Record<string, string> = {};
  for (const variable of variables) {
    if (variable.defaultValue) defaults[variable.name] = variable.defaultValue;
  }
  return defaults;
}

function findDefaultAgent(agents: AgentInfo[]): AgentInfo | null {
  return (
    agents.find((agent) => agent.id === DEFAULT_GENERAL_CHAT_AGENT_ID) ??
    agents.find(
      (agent) =>
        agent.source === "builtin" &&
        agent.name.trim().toLowerCase() === "general chat",
    ) ??
    agents.find((agent) => agent.name.trim().toLowerCase() === "general chat") ??
    agents.find((agent) => agent.source === "builtin") ??
    agents[0] ??
    null
  );
}

function CloudEmptyState({ activeAgent }: { activeAgent: AgentInfo | null }) {
  return (
    <div className="flex h-full items-center justify-center px-4">
      <div className="text-center text-xs text-muted-foreground">
        {activeAgent ? activeAgent.name : "Select an agent"}
      </div>
    </div>
  );
}

interface CloudChatProps {
  engineStatus: EngineStatus;
  engineUrl: string | null;
}

export function CloudChat({ engineStatus, engineUrl }: CloudChatProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [agentPickerOpen, setAgentPickerOpen] = useState(false);
  const [defaultAgentApplied, setDefaultAgentApplied] = useState(false);
  const [activeAgent, setActiveAgent] = useState<AgentInfo | null>(null);
  const [activeVariables, setActiveVariables] = useState<PromptVariable[]>([]);
  const [variableValues, setVariableValues] = useState<Record<string, string>>({});
  const cloudChat = useCloudChat({ engineUrl });
  const cloudAgents = useCloudAgents();
  const {
    activeConversationId,
    createConversation,
    deleteConversation,
    executionTarget,
    groupedConversations,
    historyError,
    isStreaming,
    localLlmError,
    localLlmStatus,
    mode,
    model,
    renameConversation,
    refreshLocalLlmStatus,
    selectConversation,
    sendMessage,
    setExecutionTarget,
    setMode,
    setModel,
    stopStreaming,
  } = cloudChat;
  const {
    agents,
    ensureExecutionFull,
    error: agentsError,
    executionError,
    executionLoadingAgentId,
    isLoading: agentsLoading,
  } = cloudAgents;

  const activeConversation = cloudChat.activeConversation;
  const messages = activeConversation?.messages ?? [];
  const hasMessages = messages.length > 0;
  const selectedAgentId = activeAgent?.id ?? null;
  const currentActiveAgent = selectedAgentId
    ? (agents.find((agent) => agent.id === selectedAgentId) ?? activeAgent)
    : null;

  useEffect(() => {
    if (defaultAgentApplied || activeAgent || activeConversationId || agents.length === 0) return;
    const defaultAgent = findDefaultAgent(agents);
    if (!defaultAgent) return;
    setActiveAgent(defaultAgent);
    setDefaultAgentApplied(true);
  }, [activeAgent, activeConversationId, agents, defaultAgentApplied]);

  useEffect(() => {
    if (!activeConversationId) return;
    if (!activeConversation?.agentId) {
      setActiveAgent(null);
      return;
    }
    const conversationAgent = agents.find((agent) => agent.id === activeConversation.agentId);
    if (conversationAgent) {
      setActiveAgent(conversationAgent);
      setDefaultAgentApplied(true);
    } else {
      setActiveAgent(null);
    }
  }, [activeConversation?.agentId, activeConversationId, agents]);

  useEffect(() => {
    if (!selectedAgentId || hasMessages) {
      setActiveVariables([]);
      setVariableValues({});
      return;
    }

    let cancelled = false;
    void ensureExecutionFull(selectedAgentId).then((payload) => {
      if (cancelled) return;
      setActiveVariables(payload.variables);
      setVariableValues(defaultVariableValues(payload.variables));
      if (payload.modelId) setModel(payload.modelId);
    });

    return () => {
      cancelled = true;
    };
  }, [ensureExecutionFull, hasMessages, selectedAgentId, setModel]);

  const activeAgentWithVariables = useMemo(() => {
    if (!currentActiveAgent) return null;
    return {
      ...currentActiveAgent,
      variable_defaults: activeVariables,
    };
  }, [currentActiveAgent, activeVariables]);

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
      setDefaultAgentApplied(true);
      setActiveVariables([]);
      setVariableValues({});
      if (activeConversationId) {
        selectConversation(null);
      }
    },
    [activeConversationId, agents, selectConversation],
  );

  const handleTargetChange = useCallback(
    (target: CloudChatExecutionTarget) => {
      if (target === executionTarget) return;
      setExecutionTarget(target);
      selectConversation(null);
      setActiveVariables([]);
      setVariableValues({});
      if (target === "local") {
        void refreshLocalLlmStatus();
      }
    },
    [executionTarget, refreshLocalLlmStatus, selectConversation, setExecutionTarget],
  );

  const handleNewChat = useCallback(() => {
    if (!activeAgent) {
      const defaultAgent = findDefaultAgent(agents);
      if (defaultAgent) setActiveAgent(defaultAgent);
    }
    createConversation();
  }, [activeAgent, agents, createConversation]);

  const handleSelectConversation = useCallback(
    (conversationId: string) => {
      const conversation = cloudChat.conversations.find((item) => item.id === conversationId);
      const target =
        conversation?.executionTarget ??
        (conversation?.localConversationId ? "local" : "cloud");
      setExecutionTarget(target);
      selectConversation(conversationId);
      const conversationAgent = conversation?.agentId
        ? agents.find((agent) => agent.id === conversation.agentId)
        : null;
      if (conversationAgent) {
        setActiveAgent(conversationAgent);
        setDefaultAgentApplied(true);
      }
      setActiveVariables([]);
      setVariableValues({});
    },
    [agents, cloudChat.conversations, selectConversation, setExecutionTarget],
  );

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

  const pickerLabel = activeAgent?.name ?? "Select an agent";
  const showVariables = activeVariables.length > 0 && !hasMessages;
  const engineReady = executionTarget === "cloud" || engineStatus === "connected";
  const localTargetError =
    executionTarget === "local" && engineStatus !== "connected"
      ? "Local engine is not connected."
      : executionTarget === "local" &&
          (localLlmError || localLlmStatus?.available === false)
        ? (localLlmError ??
            localLlmStatus?.error ??
            localLlmStatus?.instructions ??
            "Local model is not registered with the engine.")
        : null;
  const cloudError =
    agentsError ??
    executionError ??
    cloudChat.modelError ??
    cloudChat.requestError ??
    historyError ??
    localTargetError;
  const sidebarAgentPicker = (
    <div className="space-y-1.5">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-8 w-full justify-start px-2 text-xs"
        onClick={() => setAgentPickerOpen(true)}
      >
        <Cloud className="h-3.5 w-3.5 shrink-0" />
        <span className="min-w-0 flex-1 truncate text-left">{pickerLabel}</span>
        <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-60" />
      </Button>
      <div className="grid grid-cols-2 gap-1 rounded-md border border-border/60 p-0.5">
        <Button
          type="button"
          variant={executionTarget === "cloud" ? "secondary" : "ghost"}
          size="sm"
          className="h-7 px-2 text-[11px]"
          onClick={() => handleTargetChange("cloud")}
          disabled={isStreaming}
        >
          <Cloud className="mr-1.5 h-3.5 w-3.5" />
          Cloud
        </Button>
        <Button
          type="button"
          variant={executionTarget === "local" ? "secondary" : "ghost"}
          size="sm"
          className="h-7 px-2 text-[11px]"
          onClick={() => handleTargetChange("local")}
          disabled={isStreaming}
        >
          <Cpu className="mr-1.5 h-3.5 w-3.5" />
          Local
        </Button>
      </div>
    </div>
  );

  return (
    <div className="flex h-full overflow-hidden">
      <ChatSidebar
        conversations={cloudChat.conversations}
        groupedConversations={groupedConversations}
        activeConversationId={activeConversationId}
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((prev) => !prev)}
        onSelect={handleSelectConversation}
        onNew={handleNewChat}
        onDelete={deleteConversation}
        onRename={renameConversation}
        headerContent={sidebarAgentPicker}
      />

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-background">
        {cloudError && (
          <div className="border-b border-amber-500/30 bg-amber-500/5 px-4 py-2 text-xs text-amber-500">
            {cloudError}
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto">
          {messages.length === 0 ? (
            <CloudEmptyState activeAgent={activeAgentWithVariables} />
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
            availableModels={[]}
            onModelChange={setModel}
            onModeChange={setMode}
            engineReady={engineReady}
            selectedAgentId={selectedAgentId}
            showModelSelector={false}
            showModeSelector={false}
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
