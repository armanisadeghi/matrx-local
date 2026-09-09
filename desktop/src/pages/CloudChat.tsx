import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Cloud,
  Cpu,
  Loader2,
  MessageSquarePlus,
} from "lucide-react";
import {
  AgentCatalogProvider,
  AgentListDropdown,
} from "@ai-matrx/agents/catalog/react";
import { ChatInput } from "@/components/chat/ChatInput";
import { ChatMessages } from "@/components/chat/ChatMessages";
import { ChatSidebar } from "@/components/chat/ChatSidebar";
import { GuidedVariableInputs } from "@/components/chat/GuidedVariableInputs";
import { GmailReviewCard } from "@/components/chat/GmailReviewCard";
import { CloudChatPlusMenu } from "@/components/chat/PlusMenu";
import { Button } from "@ai-matrx/design-system";
import { getCloudAgentCatalog } from "@/lib/agent-catalog";
import { useAgentExecution } from "@/hooks/use-agent-execution";
import { useAgentName } from "@/hooks/use-agent-name";
import { useEmailReviews } from "@/hooks/use-email-reviews";
import {
  type ChatAttachment,
  type CloudChatExecutionTarget,
  useCloudChat,
} from "@/hooks/use-cloud-chat";
import type { EngineStatus } from "@/hooks/use-engine";
import { DEFAULT_CHAT_MANDATE_KEY, DEFAULT_CHAT_MANDATE_REF } from "@/lib/mandates";
import { cn } from "@/lib/utils";
import type { PromptVariable } from "@/types/agents";

function defaultVariableValues(variables: PromptVariable[]): Record<string, string> {
  const defaults: Record<string, string> = {};
  for (const variable of variables) {
    if (variable.defaultValue) defaults[variable.name] = variable.defaultValue;
  }
  return defaults;
}

function CloudEmptyState({ agentName }: { agentName: string | null }) {
  return (
    <div className="flex h-full items-center justify-center px-4">
      <div className="text-center text-xs text-muted-foreground">
        {agentName ?? "Select an agent"}
      </div>
    </div>
  );
}

interface CloudChatProps {
  engineStatus: EngineStatus;
  engineUrl: string | null;
}

/**
 * Cloud Chat overrides the app-wide (offline) catalog with the LIVE one. Same
 * package, same picker, same rows in the same order — only the transport
 * differs (ruling D4). `lib/agent-catalog.ts` holds both clients.
 */
export function CloudChat(props: CloudChatProps) {
  return (
    <AgentCatalogProvider catalog={getCloudAgentCatalog()}>
      <CloudChatSurface {...props} />
    </AgentCatalogProvider>
  );
}

function CloudChatSurface({ engineStatus, engineUrl }: CloudChatProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [defaultAgentApplied, setDefaultAgentApplied] = useState(false);
  // ONE piece of agent state: which id is selected. The list, its tabs, sorts,
  // filters, favourites and the live-named default row all belong to
  // `@ai-matrx/agents/catalog` (ruling D1).
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [activeVariables, setActiveVariables] = useState<PromptVariable[]>([]);
  const [variableValues, setVariableValues] = useState<Record<string, string>>({});
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [draftInsertion, setDraftInsertion] = useState<{ id: number; text: string } | null>(null);
  const cloudChat = useCloudChat({ engineUrl });
  // Delegated calls parked for explicit human review (a proposed Gmail
  // message). Nothing has been sent; the card below IS the authorization.
  const [emailReviews, emailReviewActions] = useEmailReviews(engineUrl);
  const resolveEmailReview = emailReviewActions.resolve;
  const agentExecution = useAgentExecution("cloud");
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
    ensureExecution,
    error: executionError,
    loadingAgentId: executionLoadingAgentId,
  } = agentExecution;
  const selectedAgentName = useAgentName(selectedAgentId);

  const activeConversation = cloudChat.activeConversation;
  const messages = activeConversation?.messages ?? [];
  const hasMessages = messages.length > 0;

  const handleReferencePaths = useCallback((paths: string[]) => {
    const text = paths.length === 1
      ? `Use this local path: ${paths[0]}`
      : `Use these local paths:\n${paths.map((path) => `- ${path}`).join("\n")}`;
    setDraftInsertion({ id: Date.now(), text });
  }, []);

  // The default choice is the `local.cloud_chat` Mandate — a platform answer,
  // not an agent id — so it needs no agent list to be selectable.
  useEffect(() => {
    if (defaultAgentApplied || selectedAgentId || activeConversationId) return;
    setSelectedAgentId(DEFAULT_CHAT_MANDATE_REF);
    setDefaultAgentApplied(true);
  }, [activeConversationId, defaultAgentApplied, selectedAgentId]);

  useEffect(() => {
    if (!activeConversationId) return;
    setSelectedAgentId(activeConversation?.agentId ?? null);
    if (activeConversation?.agentId) setDefaultAgentApplied(true);
  }, [activeConversation?.agentId, activeConversationId]);

  useEffect(() => {
    if (!selectedAgentId || hasMessages) {
      setActiveVariables([]);
      setVariableValues({});
      return;
    }

    let cancelled = false;
    void ensureExecution(selectedAgentId).then((payload) => {
      if (cancelled) return;
      setActiveVariables(payload.variables);
      setVariableValues(defaultVariableValues(payload.variables));
      if (payload.modelId) setModel(payload.modelId);
    });

    return () => {
      cancelled = true;
    };
  }, [ensureExecution, hasMessages, selectedAgentId, setModel]);

  const handleSelectAgent = useCallback(
    (agentId: string) => {
      setSelectedAgentId(agentId);
      setDefaultAgentApplied(true);
      setActiveVariables([]);
      setVariableValues({});
      if (activeConversationId) {
        selectConversation(null);
      }
    },
    [activeConversationId, selectConversation],
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
    if (!selectedAgentId) setSelectedAgentId(DEFAULT_CHAT_MANDATE_REF);
    createConversation();
  }, [createConversation, selectedAgentId]);

  const handleSelectConversation = useCallback(
    (conversationId: string) => {
      const conversation = cloudChat.conversations.find((item) => item.id === conversationId);
      const target =
        conversation?.executionTarget ??
        (conversation?.localConversationId ? "local" : "cloud");
      setExecutionTarget(target);
      selectConversation(conversationId);
      if (conversation?.agentId) {
        setSelectedAgentId(conversation.agentId);
        setDefaultAgentApplied(true);
      }
      setActiveVariables([]);
      setVariableValues({});
    },
    [cloudChat.conversations, selectConversation, setExecutionTarget],
  );

  const handleSend = useCallback(
    async (content: string) => {
      const submittedVariables = { ...variableValues };
      const submittedAttachments = attachments;
      setActiveVariables([]);
      setVariableValues({});
      setAttachments([]);
      await sendMessage(content, {
        ...(selectedAgentId ? { agentId: selectedAgentId } : {}),
        variables: submittedVariables,
        ...(submittedAttachments.length > 0
          ? { attachments: submittedAttachments }
          : {}),
      });
    },
    [attachments, selectedAgentId, sendMessage, variableValues],
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
    executionError ??
    cloudChat.modelError ??
    cloudChat.requestError ??
    historyError ??
    localTargetError;
  const sidebarAgentControls = (
    <div className="space-y-1.5">
      {/* THE ONE AGENT PICKER. The trigger's label is the package's — the live
          name of the selected agent, or of the mandate's real Holder. */}
      <AgentListDropdown
        onSelect={handleSelectAgent}
        activeAgentId={selectedAgentId}
        defaultMandateKey={DEFAULT_CHAT_MANDATE_KEY}
        consumerId="matrx-local.cloud-chat"
        contentSide="right"
        className="w-full"
      />
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
        headerContent={sidebarAgentControls}
      />

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-background">
        {cloudError && (
          <div className="border-b border-amber-500/30 bg-amber-500/5 px-4 py-2 text-xs text-amber-500">
            {cloudError}
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto">
          {messages.length === 0 ? (
            <CloudEmptyState agentName={selectedAgentName} />
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
            sendBlockedReason={executionError}
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

    </div>
  );
}
