/**
 * The per-agent execution cache — variables, settings, model — for whichever
 * lane a chat surface runs in.
 *
 * This is deliberately NOT an agent list. The list is
 * `@ai-matrx/agents/catalog` and only that (rulings D1/D4); this hook answers
 * "what does THIS one agent need from the person before it runs".
 */

import { useCallback, useRef, useState } from "react";

import {
  EMPTY_AGENT_EXECUTION,
  fetchAgentExecution,
  type AgentExecutionLane,
  type AgentExecutionPayload,
} from "@/lib/agent-execution";

export function useAgentExecution(lane: AgentExecutionLane) {
  const [byAgentId, setByAgentId] = useState<
    Record<string, AgentExecutionPayload>
  >({});
  const [loadingAgentId, setLoadingAgentId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cacheRef = useRef<Record<string, AgentExecutionPayload>>({});

  const ensureExecution = useCallback(
    async (agentId: string): Promise<AgentExecutionPayload> => {
      const cached = cacheRef.current[agentId];
      if (cached) return cached;

      setLoadingAgentId(agentId);
      setError(null);
      try {
        const payload = await fetchAgentExecution(agentId, lane);
        cacheRef.current = { ...cacheRef.current, [agentId]: payload };
        setByAgentId(cacheRef.current);
        setLoadingAgentId(null);
        return payload;
      } catch (err) {
        // Never a silent empty form: an agent whose variables could not be
        // read is announced, and the caller renders no questions rather than
        // pretending there are none.
        setError(
          err instanceof Error
            ? `Could not load this agent's inputs: ${err.message}`
            : "Could not load this agent's inputs.",
        );
        setLoadingAgentId(null);
        return EMPTY_AGENT_EXECUTION;
      }
    },
    [lane],
  );

  return { ensureExecution, byAgentId, loadingAgentId, error };
}
