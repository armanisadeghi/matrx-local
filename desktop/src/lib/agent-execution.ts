/**
 * ONE agent's execution detail, in either lane — never a list.
 *
 * `variable_definitions` and `settings` are not catalog columns. No Matrx
 * client's LIST rows carry them, so nothing here may read them off a list: a
 * variables form asks for the ONE agent a person selected, by id.
 *
 *   CLOUD  → Supabase `agx_get_execution_full(p_agent_id)`.
 *   LOCAL  → `GET {engine}/agents/catalog/{agent_id}/execution`, which makes
 *            that SAME RPC call (through the sidecar's one Supabase REST lane)
 *            and serves the row VERBATIM from a fetch-through SQLite cache
 *            (`app/services/agent_catalog/FEATURE.md`).
 *
 * Both lanes therefore hand back the identical RPC row and normalize it with
 * the identical mapper — ruling D4: offline is a data location, never a
 * different structure.
 */

import { engine } from "@/lib/api";
import {
  EMPTY_EXECUTION_PAYLOAD,
  executionPayloadFromRow,
  fetchCloudAgentExecutionFull,
} from "@/lib/cloud-agents";
import { isMandateAgentRef } from "@/lib/mandates";
import type { AgentSettings, PromptVariable } from "@/types/agents";

export interface AgentExecutionPayload {
  variables: PromptVariable[];
  contextSlots: unknown[];
  modelId: string | null;
  settings: AgentSettings;
  tools: string[];
  customTools: unknown;
  uiGates: unknown;
}

export type AgentExecutionLane = "cloud" | "local";

/** A Mandate-backed choice has no client-readable definition — the server resolves it. */
export const EMPTY_AGENT_EXECUTION: AgentExecutionPayload = {
  ...EMPTY_EXECUTION_PAYLOAD,
};

async function fetchLocalAgentExecution(
  agentId: string,
): Promise<AgentExecutionPayload> {
  const row = await engine.get(
    `/agents/catalog/${encodeURIComponent(agentId)}/execution`,
  );
  if (!row || typeof row !== "object" || Array.isArray(row)) {
    throw new Error(
      `/agents/catalog/${agentId}/execution answered with no object — the ` +
        "engine's contract pins the agx_get_execution_full row, verbatim.",
    );
  }
  return executionPayloadFromRow(row);
}

export async function fetchAgentExecution(
  agentId: string,
  lane: AgentExecutionLane,
): Promise<AgentExecutionPayload> {
  if (isMandateAgentRef(agentId)) return { ...EMPTY_AGENT_EXECUTION };
  return lane === "cloud"
    ? fetchCloudAgentExecutionFull(agentId)
    : fetchLocalAgentExecution(agentId);
}
