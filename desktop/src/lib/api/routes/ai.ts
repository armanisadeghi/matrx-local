/**
 * AI run-start route definitions (mirror of matrx-extend's
 * `src/lib/api/routes/ai.ts`, adapted to the desktop's bases).
 *
 * Every path here is RELATIVE to the `/ai` base the desktop already builds:
 * `${cloudServerUrl}/api/ai` for the cloud target and `${engineUrl}/ai` for
 * the local engine mirror. Verified against
 * `src/types/python-generated/openapi.json` (2026-08-22): `/ai/agents/{agent_id}`
 * and `/ai/mandates/{mandate_key}` both take the same `AgentStartRequest`
 * body and stream the same events — a Mandate start is a PATH change only.
 */

import { mandateKeyFromAgentRef } from "@/lib/mandates";

/** POST {aiBase}/agents/{agent_id} — start an agent stream. */
export const agentExecutePath = (agentId: string): string =>
  `/agents/${encodeURIComponent(agentId)}`;

/** POST {aiBase}/mandates/{mandate_key} — resolve the Holder server-side and start it. */
export const mandateExecutePath = (mandateKey: string): string =>
  `/mandates/${encodeURIComponent(mandateKey)}`;

/** Route a concrete Agent id or a `mandate:*` UI reference correctly. */
export const agentTargetExecutePath = (target: string): string => {
  const mandateKey = mandateKeyFromAgentRef(target);
  return mandateKey ? mandateExecutePath(mandateKey) : agentExecutePath(target);
};

/**
 * GET /api/mandates/{mandate_key}/resolution — the agent this Mandate resolves
 * to for the caller (system default -> org binding -> user binding). Relative
 * to the server's `/api` root, not the `/ai` base. Used only where a run must
 * go to an agent-id route (the local engine mirror has no mandate route).
 */
export const mandateResolutionPath = (mandateKey: string): string =>
  `/mandates/${encodeURIComponent(mandateKey)}/resolution`;
