/**
 * ONE agent's execution detail, in either lane — never a list.
 *
 * `variable_defaults` and `settings` are not catalog columns. No Matrx client's
 * LIST rows carry them, so nothing here may read them off a list: a variables
 * form asks for the ONE agent a person selected, by id.
 *
 *   CLOUD  → Supabase `agx_get_execution_full(p_agent_id)`.
 *   LOCAL  → `GET {engine}/agents/catalog/{agent_id}/execution`, the offline
 *            twin of that read, served from the mirror's detail cache
 *            (`app/services/agent_catalog/FEATURE.md`).
 *
 * Both answer the SAME payload shape — ruling D4: offline is a data location,
 * never a different structure.
 */

import { engine } from "@/lib/api";
import {
  fetchCloudAgentExecutionFull,
  normalizeVariableList,
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
  variables: [],
  contextSlots: [],
  modelId: null,
  settings: {},
  tools: [],
  customTools: null,
  uiGates: null,
};

function readRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

async function fetchLocalAgentExecution(
  agentId: string,
): Promise<AgentExecutionPayload> {
  const raw = readRecord(
    await engine.get(`/agents/catalog/${encodeURIComponent(agentId)}/execution`),
  );
  if (!raw) {
    throw new Error(
      `/agents/catalog/${agentId}/execution answered with no object — the ` +
        "engine's contract pins a {variable_defaults, settings} payload.",
    );
  }
  const settings = (readRecord(raw.settings) ?? {}) as AgentSettings;
  const modelId =
    typeof settings.model_id === "string" ? settings.model_id : null;
  return {
    variables: normalizeVariableList(raw.variable_defaults),
    contextSlots: [],
    modelId,
    settings,
    tools: Array.isArray(settings.tools) ? settings.tools : [],
    customTools: null,
    uiGates: null,
  };
}

export async function fetchAgentExecution(
  agentId: string,
  lane: AgentExecutionLane,
): Promise<AgentExecutionPayload> {
  if (isMandateAgentRef(agentId)) return EMPTY_AGENT_EXECUTION;
  return lane === "cloud"
    ? fetchCloudAgentExecutionFull(agentId)
    : fetchLocalAgentExecution(agentId);
}
