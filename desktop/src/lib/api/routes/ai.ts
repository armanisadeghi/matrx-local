/**
 * AI route definitions (mirror of matrx-extend's `src/lib/api/routes/ai.ts`,
 * adapted to the desktop's two bases).
 *
 * Every path here is written in its **v1, in-app form** — `/ai/...` — and is
 * joined to a root by `aiRequestUrl` below. Verified against
 * `src/types/python-generated/openapi.json` (2026-08-22): `/ai/agents/{agent_id}`
 * and `/ai/mandates/{mandate_key}` both take the same `AgentStartRequest`
 * body and stream the same events — a Mandate start is a PATH change only.
 *
 * ── Who decides the API version (C22, @ai-matrx/agents 0.6.0) ──────────────
 * NOT this file. `applyAiApiVersion` owns the covered-surface allowlist
 * (`V2_COVERED_AI_PATH_TEMPLATES`) and the `/v2` insertion, so this repo never
 * carries a hand-maintained list of which `/ai/*` surfaces have a v2 sibling —
 * the twin that drifts the moment the backend's v2 surface grows. Never
 * hardcode a `/v2` segment at a call site or anywhere in this file.
 */

import { MATRX_AI_API_VERSION_DEFAULT, applyAiApiVersion } from "@ai-matrx/agents/matrx";

import { mandateKeyFromAgentRef } from "@/lib/mandates";

/** Which side of the desktop's split an AI request is going to. */
export type AiExecutionTarget = "cloud" | "local";

/**
 * THE ONE DOOR from an in-app v1 AI path to the URL this desktop calls.
 *
 * `root` is `${cloudServerUrl}/api` for the cloud target and the engine's base
 * URL (`http://127.0.0.1:221xx`) for the local mirror.
 *
 * The local engine is deliberately never promoted to `/v2`: `/v2` is aidream's
 * runtime-spine namespace (`aidream/api/routers/v2.py`), and the desktop
 * engine mirrors only the proven `/ai/*` routes. Asking it for `/v2/ai/...`
 * would be a 404 on every call — a downgrade the fallback would then have to
 * pay for on every single local turn.
 */
export const aiRequestUrl = (
  target: AiExecutionTarget,
  root: string,
  v1Path: string,
): string =>
  target === "cloud"
    ? `${root}${applyAiApiVersion(v1Path, MATRX_AI_API_VERSION_DEFAULT)}`
    : `${root}${v1Path}`;

/** POST /ai/agents/{agent_id} (promoted to /v2 on cloud) — start an agent stream. */
export const agentExecutePath = (agentId: string): string =>
  `/ai/agents/${encodeURIComponent(agentId)}`;

/** POST /ai/mandates/{mandate_key} (promoted to /v2 on cloud) — resolve the Holder server-side and start it. */
export const mandateExecutePath = (mandateKey: string): string =>
  `/ai/mandates/${encodeURIComponent(mandateKey)}`;

/** Route a concrete Agent id or a `mandate:*` UI reference correctly. */
export const agentTargetExecutePath = (target: string): string => {
  const mandateKey = mandateKeyFromAgentRef(target);
  return mandateKey ? mandateExecutePath(mandateKey) : agentExecutePath(target);
};

/** POST /ai/conversations/{conversation_id} (promoted to /v2 on cloud) — continue a turn. */
export const conversationContinuePath = (conversationId: string): string =>
  `/ai/conversations/${encodeURIComponent(conversationId)}`;

/**
 * POST /ai/conversations/{conversation_id}/resume — resume a turn the server
 * suspended for a delegated (locally-executed) tool call.
 *
 * NOT versioned, and correctly so: there is no `/v2/ai/conversations/{id}/resume`
 * on the backend. The package's allowlist is anchored per whole path segment,
 * so routing this through `aiRequestUrl` would leave it on v1 anyway — but
 * writing it as if it had a v2 form would misstate the contract.
 */
export const conversationResumePath = (conversationId: string): string =>
  `/ai/conversations/${encodeURIComponent(conversationId)}/resume`;

/** POST /ai/chat (promoted to /v2 on cloud) — model-addressed chat. */
export const CHAT_PATH = "/ai/chat";

/**
 * GET /api/mandates/{mandate_key}/resolution — the agent this Mandate resolves
 * to for the caller (system default -> org binding -> user binding). Relative
 * to the server's `/api` root, and NOT an `/ai` surface, so it never goes
 * through `aiRequestUrl`. Used only where a run must go to an agent-id route
 * (the local engine mirror has no mandate route).
 */
export const mandateResolutionPath = (mandateKey: string): string =>
  `/mandates/${encodeURIComponent(mandateKey)}/resolution`;
