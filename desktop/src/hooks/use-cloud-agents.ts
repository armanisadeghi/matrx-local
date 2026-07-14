import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchCloudAgentExecutionFull,
  fetchCloudAgents,
} from "@/lib/cloud-agents";
import type { AgentInfo, AgentSettings, PromptVariable } from "@/types/agents";

interface AgentExecutionPayload {
  variables: PromptVariable[];
  contextSlots: unknown[];
  modelId: string | null;
  settings: AgentSettings;
  tools: string[];
  customTools: unknown;
  uiGates: unknown;
}

interface AgentExecutionState {
  byAgentId: Record<string, AgentExecutionPayload>;
  loadingAgentId: string | null;
  error: string | null;
}

export function useCloudAgents() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [execution, setExecution] = useState<AgentExecutionState>({
    byAgentId: {},
    loadingAgentId: null,
    error: null,
  });
  const abortRef = useRef(false);

  const loadAgents = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const next = await fetchCloudAgents();
      if (!abortRef.current) setAgents(next);
    } catch (err) {
      if (!abortRef.current) {
        setError(err instanceof Error ? err.message : "Failed to load cloud agents");
      }
    } finally {
      if (!abortRef.current) setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    abortRef.current = false;
    void loadAgents();
    return () => {
      abortRef.current = true;
    };
  }, [loadAgents]);

  const ensureExecutionFull = useCallback(
    async (agentId: string): Promise<AgentExecutionPayload> => {
      const cached = execution.byAgentId[agentId];
      if (cached) return cached;

      setExecution((prev) => ({
        ...prev,
        loadingAgentId: agentId,
        error: null,
      }));

      try {
        const payload = await fetchCloudAgentExecutionFull(agentId);
        setExecution((prev) => ({
          byAgentId: {
            ...prev.byAgentId,
            [agentId]: payload,
          },
          loadingAgentId: null,
          error: null,
        }));
        setAgents((prev) =>
          prev.map((agent) =>
            agent.id === agentId
              ? {
                  ...agent,
                  variable_defaults: payload.variables,
                  settings: {
                    ...agent.settings,
                    ...payload.settings,
                    ...(payload.modelId ? { model_id: payload.modelId } : {}),
                  },
                }
              : agent,
          ),
        );
        return payload;
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Failed to load agent execution details";
        setExecution((prev) => ({
          ...prev,
          loadingAgentId: null,
          error: message,
        }));
        return {
          variables: [],
          contextSlots: [],
          modelId: null,
          settings: {},
          tools: [],
          customTools: null,
          uiGates: null,
        };
      }
    },
    [execution.byAgentId],
  );

  const ensureExecutionMinimal = useCallback(
    async (agentId: string): Promise<PromptVariable[]> => {
      const payload = await ensureExecutionFull(agentId);
      return payload.variables;
    },
    [ensureExecutionFull],
  );

  const grouped = useMemo(
    () => ({
      builtins: agents.filter((agent) => agent.source === "builtin"),
      userAgents: agents.filter((agent) => agent.source === "user"),
      sharedAgents: agents.filter((agent) => agent.source === "shared"),
    }),
    [agents],
  );

  return {
    agents,
    ...grouped,
    isLoading,
    error,
    refresh: loadAgents,
    ensureExecutionFull,
    ensureExecutionMinimal,
    executionByAgentId: execution.byAgentId,
    executionLoadingAgentId: execution.loadingAgentId,
    executionError: execution.error,
  };
}
