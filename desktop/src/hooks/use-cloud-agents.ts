import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchCloudAgentExecutionMinimal,
  fetchCloudAgents,
} from "@/lib/cloud-agents";
import type { AgentInfo, PromptVariable } from "@/types/agents";

interface AgentExecutionState {
  variablesByAgentId: Record<string, PromptVariable[]>;
  loadingAgentId: string | null;
  error: string | null;
}

export function useCloudAgents() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [execution, setExecution] = useState<AgentExecutionState>({
    variablesByAgentId: {},
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

  const ensureExecutionMinimal = useCallback(
    async (agentId: string): Promise<PromptVariable[]> => {
      const cached = execution.variablesByAgentId[agentId];
      if (cached) return cached;

      setExecution((prev) => ({
        ...prev,
        loadingAgentId: agentId,
        error: null,
      }));

      try {
        const payload = await fetchCloudAgentExecutionMinimal(agentId);
        setExecution((prev) => ({
          variablesByAgentId: {
            ...prev.variablesByAgentId,
            [agentId]: payload.variables,
          },
          loadingAgentId: null,
          error: null,
        }));
        setAgents((prev) =>
          prev.map((agent) =>
            agent.id === agentId
              ? { ...agent, variable_defaults: payload.variables }
              : agent,
          ),
        );
        return payload.variables;
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Failed to load agent variables";
        setExecution((prev) => ({
          ...prev,
          loadingAgentId: null,
          error: message,
        }));
        return [];
      }
    },
    [execution.variablesByAgentId],
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
    ensureExecutionMinimal,
    executionLoadingAgentId: execution.loadingAgentId,
    executionError: execution.error,
  };
}
