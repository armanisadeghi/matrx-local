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
import { GmailReviewCard } from "@/components/chat/GmailReviewCard";
import { CloudChatPlusMenu } from "@/components/chat/PlusMenu";
import { Button } from "@/components/ui/button";
import { useCloudAgents } from "@/hooks/use-cloud-agents";
import { useEmailReviews } from "@/hooks/use-email-reviews";
import {
  type ChatAttachment,
  type CloudChatExecutionTarget,
  useCloudChat,
} from "@/hooks/use-cloud-chat";
import type { EngineStatus } from "@/hooks/use-engine";
import { DEFAULT_CHAT_AGENT } from "@/lib/cloud-agents";
import { cn } from "@/lib/utils";
import type { AgentInfo, PromptVariable } from "@/types/agents";

function defaultVariableValues(variables: PromptVariable[]): Record<string, string> {
  const defaults: Record<string, string> = {};
  for (const variable of variables) {
    if (variable.defaultValue) defaults[variable.name] = variable.defaultValue;
  }
  return defaults;
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
  const [pendingAgentId, setPendingAgentId] = useState<string | null>(null);
  const [agentSelectionError, setAgentSelectionError] = useState<string | null>(null);
  const [activeVariables, setActiveVariables] = useState<PromptVariable[]>([]);
  const [variableValues, setVariableValues] = useState<Record<string, string>>({});
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [draftInsertion, setDraftInsertion] = useState<{ id: number; text: string } | null>(null);
  const cloudChat = useCloudChat({ engineUrl });
  // Delegated calls parked for explicit human review (a proposed Gmail
  // message). Nothing has been sent; the card below IS the authorization.
  const [emailReviews, emailReviewActions] = useEmailReviews(engineUrl);
  const resolveEmailReview = emailReviewActions.resolve;
  const cloudAgents = useCloudAgents();
  const {
    activeConversationId,
    attachedGoogleFiles,
    googleFileActions,
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
    availableModels,
    renameConversation,
    refreshLocalLlmStatus,
    runControls,
    runControlActions,
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
  const selectedAgentId = pendingAgentId ?? activeAgent?.id ?? null;
  const currentActiveAgent = selectedAgentId
    ? (agents.find((agent) => agent.id === selectedAgentId) ?? activeAgent)
    : null;

  const handleReferencePaths = useCallback((paths: string[]) => {
    const text = paths.length === 1
      ? `Use this local path: ${paths[0]}`
      : `Use these local paths:\n${paths.map((path) => `- ${path}`).join("\n")}`;
    setDraftInsertion({ id: Date.now(), text });
  }, []);

  // The default choice is the `local.cloud_chat` Mandate — a platform answer,
  // not an agent id — so it needs no agent list to be selectable.
  useEffect(() => {
    if (defaultAgentApplied || activeAgent || activeConversationId) return;
    setActiveAgent(DEFAULT_CHAT_AGENT);
    setDefaultAgentApplied(true);
  }, [activeAgent, activeConversationId, defaultAgentApplied]);

  useEffect(() => {
    if (!activeConversationId) return;
    if (!activeConversation?.agentId) {
      setActiveAgent(null);
      return;
    }
    const conversationAgent = agents.find((agent) => agent.id === activeConversation.agentId);
    if (conversationAgent) {
      setActiveAgent(conversationAgent);
      setPendingAgentId(null);
      setAgentSelectionError(null);
      setDefaultAgentApplied(true);
    } else {
      setPendingAgentId(activeConversation.agentId);
      setAgentSelectionError(
        "This conversation's agent is still syncing. Sending is paused until it is available.",
      );
    }
  }, [activeConversation?.agentId, activeConversationId, agents]);

  useEffect(() => {
    if (!pendingAgentId) return;
    const resolved = agents.find((agent) => agent.id === pendingAgentId);
    if (!resolved) return;
    setActiveAgent(resolved);
    setPendingAgentId(null);
    setAgentSelectionError(null);
  }, [agents, pendingAgentId]);

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
        setPendingAgentId(null);
        setAgentSelectionError(null);
        setActiveVariables([]);
        setVariableValues({});
        return;
      }
      const found = agents.find((agent) => agent.id === agentId) ?? null;
      if (!found) {
        setPendingAgentId(agentId);
        setAgentSelectionError(
          "The selected agent is still syncing. Sending is paused until it is available.",
        );
        return;
      }
      setActiveAgent(found);
      setPendingAgentId(null);
      setAgentSelectionError(null);
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
    if (!activeAgent) setActiveAgent(DEFAULT_CHAT_AGENT);
    createConversation();
  }, [activeAgent, createConversation]);

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
      const submittedAttachments = attachments;
      setActiveVariables([]);
      setVariableValues({});
      setAttachments([]);
      await sendMessage(content, {
        ...(activeAgentWithVariables?.id ? { agentId: activeAgentWithVariables.id } : {}),
        variables: submittedVariables,
        ...(submittedAttachments.length > 0
          ? { attachments: submittedAttachments }
          : {}),
      });
    },
    [activeAgentWithVariables?.id, attachments, sendMessage, variableValues],
  );

  const handleAddAttachments = useCallback((files: ChatAttachment[]) => {
    setAttachments((prev) => [...prev, ...files]);
  }, []);

  const handleRemoveAttachment = useCallback((id: string) => {
    setAttachments((prev) => prev.filter((file) => file.id !== id));
  }, []);

  const handleVariableChange = useCallback((name: string, value: string) => {
    setVariableValues((prev) => ({ ...prev, [name]: value }));
  }, []);

  // Google file chips are cloud-only, exactly like the menu section that sets
  // them — a chip for something the local target ignores would lie.
  const visibleGoogleFiles = useMemo(
    () => (executionTarget === "cloud" ? attachedGoogleFiles : []),
    [attachedGoogleFiles, executionTarget],
  );

  const pickerLabel = activeAgent?.name ?? "Select an agent";
  const showVariables = activeVariables.length > 0 && !hasMessages;
  const engineReady = executionTarget === "cloud" || engineStatus === "connected";
  const localTargetError =
    executionTarget === "local" && engineStatus !== "connected"
      ? "Local engine is not connected."
      : executionTarget === "local" &&
          (localLlmError || localLlmStatus?.registered === false)
        ? (localLlmError ??
            localLlmStatus?.error ??
            localLlmStatus?.instructions ??
            "Local model is not registered with the engine.")
        : null;
  const cloudError =
    agentSelectionError ??
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
            <ChatMessages messages={messages} isStreaming={isStreaming} onReferencePaths={handleReferencePaths} />
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

        {emailReviews.length > 0 && (
          <div className="max-h-[60%] shrink-0 overflow-y-auto px-4 pt-2">
            <div className="mx-auto flex max-w-3xl flex-col gap-2">
              {emailReviews.map((review) => (
                <GmailReviewCard
                  key={review.callId}
                  review={review}
                  onResolve={resolveEmailReview}
                />
              ))}
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
            sendBlockedReason={agentSelectionError}
            selectedAgentId={selectedAgentId}
            showModelSelector={false}
            showModeSelector={false}
            attachments={attachments}
            onRemoveAttachment={handleRemoveAttachment}
            googleFiles={visibleGoogleFiles}
            onRemoveGoogleFile={googleFileActions.remove}
            draftInsertion={draftInsertion}
            plusMenuSlot={
              <CloudChatPlusMenu
                engineUrl={engineUrl}
                executionTarget={executionTarget}
                models={availableModels}
                runControls={runControls}
                onModelOverride={runControlActions.setModelOverride}
                onTemperature={runControlActions.setTemperature}
                onMaxTokens={runControlActions.setMaxTokens}
                onExcludedTools={runControlActions.setExcludedTools}
                onResetOverrides={runControlActions.resetOverrides}
                attachments={attachments}
                onAddAttachments={handleAddAttachments}
                attachedGoogleFiles={attachedGoogleFiles}
                onToggleGoogleFile={googleFileActions.toggle}
                disabled={isStreaming}
              />
            }
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
