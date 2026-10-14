/**
 * AI route definitions (mirror of matrx-extend's `src/lib/api/routes/ai.ts`).
 *
 * Every path is the plain `/ai/...` form; callers join it to a root
 * (`${cloudServerUrl}/api` for cloud, the engine's base URL for the local
 * mirror). Verified against `src/types/python-generated/openapi.json`:
 * `/ai/agents/{agent_id}` and `/ai/mandates/{mandate_key}` take the same
 * `AgentStartRequest` body and stream the same events — a Mandate start is a
 * PATH change only.
 */

import { type AnyMandateKey, mandateKeyFromAgentRef } from "@/lib/mandates";

/** Which side of the desktop's split an AI request is going to. */
export type AiExecutionTarget = "cloud" | "local";

/**
 * Provenance attestation carried on the body of EVERY AI request this desktop
 * sends — cloud and local alike. It is the ONLY input a client has into the
 * server's `origin_class`: `"user"` means a person directly triggered the run
 * (typed and sent, clicked a suggestion), `"auto"` means desktop code did (a
 * mount effect, a timer, a delegated-tool continuation).
 *
 * OMIT the field and aidream classes the traffic `api` — an unattested HTTP
 * caller — which is why every honest human chat from this app used to be filed
 * as a robot. The field is an attestation, not a label: aidream derives the
 * real class from this plus its own witnessed facts (route, auth type, process
 * role) and overrules a background caller that claims `"user"`, so a dishonest
 * stamp buys nothing and corrupts the platform's human/automation split.
 *
 * Declared here, not taken from `src/types/python-generated/api-types.ts`:
 * that file is generated from aidream's OpenAPI and is never hand-edited.
 */
export type RequestInitiation = "user" | "auto";

/** POST /ai/agents/{agent_id} — start an agent stream. */
export const agentExecutePath = (agentId: string): string =>
  `/ai/agents/${encodeURIComponent(agentId)}`;

/** POST /ai/mandates/{mandate_key} — resolve the Holder server-side and start it. */
export const mandateExecutePath = (mandateKey: AnyMandateKey): string =>
  `/ai/mandates/${encodeURIComponent(mandateKey)}`;

/** Route a concrete Agent id or a `mandate:*` UI reference correctly. */
export const agentTargetExecutePath = (target: string): string => {
  const mandateKey: AnyMandateKey | null = mandateKeyFromAgentRef(target);
  return mandateKey ? mandateExecutePath(mandateKey) : agentExecutePath(target);
};

/** POST /ai/conversations/{conversation_id} — continue a turn. */
export const conversationContinuePath = (conversationId: string): string =>
  `/ai/conversations/${encodeURIComponent(conversationId)}`;

/**
 * POST /ai/conversations/{conversation_id}/resume — resume a turn the server
 * suspended for a delegated (locally-executed) tool call.
 */
export const conversationResumePath = (conversationId: string): string =>
  `/ai/conversations/${encodeURIComponent(conversationId)}/resume`;

/**
 * GET /ai/conversations/{conversation_id}/pending_calls — the client-delegated
 * tool calls this conversation is still suspended on.
 *
 * A READ: unlike `/ai/user/pending_calls?instance_id=…`, which atomically
 * LEASES the calls it returns, this one only reports. It is how a surface
 * answers "is this turn still running?" without taking the work.
 */
export const conversationPendingCallsPath = (conversationId: string): string =>
  `/ai/conversations/${encodeURIComponent(conversationId)}/pending_calls`;

/** POST /ai/chat — model-addressed chat. */
export const CHAT_PATH = "/ai/chat";

/**
 * GET /api/mandates/{mandate_key}/resolution — the agent this Mandate resolves
 * to for the caller (system default -> org binding -> user binding). Relative
 * to the server's `/api` root, and NOT an `/ai` surface. Used only where a run must go to an agent-id route
 * (the local engine mirror has no mandate route).
 */
export const mandateResolutionPath = (mandateKey: string): string =>
  `/mandates/${encodeURIComponent(mandateKey)}/resolution`;
