/**
 * AIDream server API client.
 *
 * All reads of shared data (models, tools, mandate resolutions) go through this
 * client. 🚨 There is NO agent list here. The ONE agent catalog is
 * `@ai-matrx/agents/catalog` (rulings D1/D4); a third list path in this file is
 * exactly the drift the package exists to end.
 * The active server URL comes from the cached remote app-config row; the
 * renderer never reads an AIDream URL from a Vite environment variable.
 *
 * Auth:
 *   - Public endpoints (models, tools): no token needed
 *   - Authenticated endpoints: pass the Supabase JWT as a Bearer token plus
 *     the organization aidream verifies. Callers skip the fetch when logged out.
 */

import { applyOrganizationContextHeader } from "@ai-matrx/agents/matrx";

import { getAIDreamServerUrl } from "@/lib/app-config";
import { mandateResolutionPath } from "@/lib/api/routes/ai";
import type { components } from "@/types/python-generated/api-types";

// ---------------------------------------------------------------------------
// Request helpers
// ---------------------------------------------------------------------------

interface RequestOptions {
  jwt?: string | null;
  /**
   * Required alongside `jwt`. aidream's AuthMiddleware refuses EVERY
   * authenticated request that names no organization (400
   * organization_required) before it routes — there is no server-side
   * fallback. `aidreamGet` fails closed below rather than let the request
   * round-trip into a guaranteed refusal.
   */
  organizationId?: string | null;
  signal?: AbortSignal;
}

async function aidreamGet<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const url = `${await getAIDreamServerUrl()}/api${path}`;
  let headers: Record<string, string> = {};

  if (options.jwt) {
    if (!options.organizationId) {
      throw new Error(
        `[aidream-client] ${path} is an authenticated request but no ` +
          "organizationId was provided. aidream refuses every authenticated " +
          "request with no X-Organization-Id header before it routes — " +
          "resolve THIS device's own choice with getActiveOrganizationId / " +
          "requireActiveOrganizationId (@/lib/org/active-org) before calling " +
          "this endpoint. aidream cannot answer 'which organization does this " +
          "client carry' — it only verifies membership.",
      );
    }
    headers["Authorization"] = `Bearer ${options.jwt}`;
    // The header itself is the PACKAGE's (`applyOrganizationContextHeader`,
    // @ai-matrx/agents 0.6.0 — the org-context kernel moved in under C22). It
    // also validates the id and refuses a malformed one rather than letting a
    // corrupt stored value earn an opaque server 400.
    headers = applyOrganizationContextHeader(headers, options.organizationId);
  }

  const response = await fetch(url, {
    method: "GET",
    headers,
    signal: options.signal ?? null,
  });

  if (!response.ok) {
    throw new Error(
      `[aidream-client] ${path} → HTTP ${response.status} ${response.statusText}`,
    );
  }

  return response.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Typed response shapes
// ---------------------------------------------------------------------------

export interface AIDreamModel {
  id: string;
  name: string;
  common_name?: string;
  provider?: string;
  endpoints?: string[];
  capabilities?: string[];
  context_window?: number | null;
  max_tokens?: number | null;
  is_primary?: boolean;
  is_premium?: boolean;
  is_deprecated?: boolean;
}

export interface AIDreamModelsResponse {
  models: AIDreamModel[];
  count: number;
}

// ── Compute targets ────────────────────────────────────────────────────────

export type ComputeTargetKind = "ec2" | "hosted" | "local-pc";

export interface ComputeTarget {
  id: string;
  kind: ComputeTargetKind;
  name: string;
  status: string;
  is_online: boolean;
  is_this_device: boolean;
  sandbox_id: string | null;
  tier: "ec2" | "hosted" | null;
  template: string | null;
  expires_at: string | null;
  instance_id: string | null;
  tunnel_url: string | null;
  platform: string | null;
  last_seen: string | null;
}

export interface ComputeTargetListResponse {
  targets: ComputeTarget[];
  max_sandboxes: number;
  sandbox_count: number;
}

/** GET /api/compute-targets/ — unified list of bindable targets. */
export async function fetchComputeTargets(
  options: RequestOptions = {},
): Promise<ComputeTargetListResponse> {
  return aidreamGet<ComputeTargetListResponse>("/compute-targets/", options);
}

export interface ComputeTargetRef {
  kind: ComputeTargetKind;
  id: string;
}

export interface SandboxBindingPayload {
  sandbox_id: string;
  base_url: string;
  access_token: string;
  root_path: string;
}

/** POST /api/compute-targets/resolve — turn a ref into the full sandbox binding
 * payload aidream's agent loop already consumes. Best-effort: returns null on
 * any failure so the caller can fall through to an unbound run. */
export async function resolveComputeTarget(
  ref: ComputeTargetRef,
  options: RequestOptions = {},
): Promise<SandboxBindingPayload | null> {
  const baseUrl = await getAIDreamServerUrl();
  let headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  try {
    // aidream refuses an authenticated request with no organization before it
    // routes. This function is already best-effort (returns null on any
    // failure and the caller falls through to an unbound run), so a missing
    // organizationId here degrades the same way a network failure does —
    // never a fabricated organization.
    //
    // The header is written by the package kernel (C22). The kernel also
    // VALIDATES the id and throws on a malformed one; that throw is inside
    // this try on purpose, so it degrades to `null` like every other failure
    // here instead of escaping a function whose contract is "never throws".
    if (options.jwt && options.organizationId) {
      headers["Authorization"] = `Bearer ${options.jwt}`;
      headers = applyOrganizationContextHeader(headers, options.organizationId);
    }
    const resp = await fetch(`${baseUrl}/api/compute-targets/resolve`, {
      method: "POST",
      headers,
      body: JSON.stringify(ref),
      signal: options.signal ?? null,
    });
    if (!resp.ok) return null;
    return (await resp.json()) as SandboxBindingPayload;
  } catch {
    return null;
  }
}

export interface AIDreamTool {
  id: string;
  name: string;
  description?: string;
  category?: string;
  tags?: string[];
  parameters?: Record<string, unknown>;
  source_app?: string;
}

export interface AIDreamToolsResponse {
  tools: AIDreamTool[];
  count: number;
  source_app?: string;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Fetch all AI models. Public — no auth needed.
 * Corresponds to GET /api/ai-models
 */
export async function fetchAIDreamModels(
  opts: RequestOptions = {},
): Promise<AIDreamModelsResponse> {
  return aidreamGet<AIDreamModelsResponse>("/ai-models", opts);
}

/**
 * Fetch all registered tools. Public — no auth needed.
 * Corresponds to GET /api/ai-tools
 */
export async function fetchAIDreamTools(
  opts: RequestOptions = {},
): Promise<AIDreamToolsResponse> {
  return aidreamGet<AIDreamToolsResponse>("/ai-tools", opts);
}

/**
 * Fetch tools for a specific source app. Public — no auth needed.
 * Corresponds to GET /api/ai-tools/app/{source_app}/all
 */
export async function fetchAIDreamToolsForApp(
  sourceApp: string,
  opts: RequestOptions = {},
): Promise<AIDreamToolsResponse> {
  return aidreamGet<AIDreamToolsResponse>(`/ai-tools/app/${sourceApp}/all`, opts);
}

// ---------------------------------------------------------------------------
// Mandates
// ---------------------------------------------------------------------------

export type MandateResolution = components["schemas"]["MandateResolutionResponse"];

/**
 * Corresponds to GET /api/mandates/{mandate_key}/resolution (auth required).
 * Returns the agent the Mandate resolves to for THIS caller — the same
 * precedence the server applies when it runs the Mandate itself. A Mandate
 * resolves or refuses: there is no client-side fallback agent.
 */
export async function fetchMandateResolution(
  mandateKey: string,
  jwt: string,
  organizationId: string,
  signal?: AbortSignal,
): Promise<MandateResolution> {
  return aidreamGet<MandateResolution>(mandateResolutionPath(mandateKey), {
    jwt,
    organizationId,
    ...(signal ? { signal } : {}),
  });
}

export type AgentExecutionDefinition =
  components["schemas"]["ExecutionAgentDefinition"];

// The Holder's execution definition for a LOCAL-model run is read by the
// engine (GET {engine}/local-mandates/{key} → LocalExecutionAgentSource), which
// keeps it for offline runs — the desktop never fetches it around that cache.

// ---------------------------------------------------------------------------
// The reply door for a coding-session conversation
// ---------------------------------------------------------------------------

/** One AI Matrx agent, as the responder report names it. */
export interface CodingReplyResponder {
  agent_id: string;
  agent_name?: string | null;
  setting_key: string;
  used_platform_default: boolean;
}

/**
 * GET /api/coding-sessions/conversations/{conversation_id}/responder.
 *
 * THE SAME door the web app opens (matrx-frontend
 * `features/ai-work/conversations/components/useCodingReplyResponder.ts`).
 * The server assembles `composer_label` and decides `can_reply` from the very
 * predicate the reply POST gates on, so this client renders the server's
 * sentence and never writes its own: a local copy of "X is answering" would
 * drift the day the responder Mandate is rebound. Matrx Local is never an
 * exception — same endpoint, same shapes, same words as the web.
 */
export interface CodingReplyResponderReport {
  schema_version?: number;
  conversation_id: string;
  is_coding_session_mirror: boolean;
  provider?: string | null;
  origin?: string | null;
  composer_label: string;
  can_reply: boolean;
  reason?: string | null;
  responder?: CodingReplyResponder | null;
  stand_in_notice?: string | null;
}

export async function fetchCodingReplyResponder(
  conversationId: string,
  jwt: string,
  organizationId: string,
  signal?: AbortSignal,
): Promise<CodingReplyResponderReport> {
  return aidreamGet<CodingReplyResponderReport>(
    `/coding-sessions/conversations/${encodeURIComponent(conversationId)}/responder`,
    { jwt, organizationId, ...(signal ? { signal } : {}) },
  );
}

// ---------------------------------------------------------------------------
// The bridge's capability verdict (provider × origin)
// ---------------------------------------------------------------------------

/** One operation's answer. `reason` is mandatory when `supported` is false. */
export interface BridgeCapabilityVerdict {
  operation: string;
  supported: boolean;
  reason?: string | null;
  live_probe?: string | null;
}

export interface BridgeCapabilityReport {
  provider: string;
  origin?: string | null;
  runtime?: string | null;
  available: boolean;
  reason?: string | null;
  operations: BridgeCapabilityVerdict[];
  fidelity: Record<string, unknown>;
  supported_actions: string[];
}

/**
 * POST /api/coding-sessions/bridge with `action=capabilities`.
 *
 * The ONE place any client may learn what it can do with a provider on an
 * origin (lane XT-01). This app asks it instead of deciding locally whether a
 * "Continue in Claude Code" control belongs on a row: a Codex row gets the
 * server's real sentence about why no Codex runtime exists, and the day one
 * lands the same control lights up with no desktop release.
 */
export async function fetchBridgeCapabilities(
  provider: string,
  origin: string | null,
  jwt: string,
  organizationId: string,
  signal?: AbortSignal,
): Promise<BridgeCapabilityReport> {
  const url = `${await getAIDreamServerUrl()}/api/coding-sessions/bridge`;
  let headers: Record<string, string> = { "Content-Type": "application/json" };
  headers["Authorization"] = `Bearer ${jwt}`;
  headers = applyOrganizationContextHeader(headers, organizationId);
  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify({
      schema_version: 1,
      action: "capabilities",
      provider,
      ...(origin ? { origin } : {}),
    }),
    signal: signal ?? null,
  });
  if (!response.ok) {
    // The server's refusal envelope names the reason; surface it verbatim
    // rather than an HTTP number nobody can act on.
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (body?.detail) detail = JSON.stringify(body.detail);
    } catch {
      // Body was not JSON; the status line stays the reason.
    }
    throw new Error(`[aidream-client] coding-sessions/bridge capabilities → ${detail}`);
  }
  const body = (await response.json()) as {
    dispatch?: { capabilities?: BridgeCapabilityReport | null } | null;
  };
  const report = body.dispatch?.capabilities ?? null;
  if (report === null) {
    throw new Error(
      "[aidream-client] the bridge answered the capabilities action with no report",
    );
  }
  return report;
}
