/**
 * AIDream server API client.
 *
 * All reads of shared data (models, prompts, tools) go through this client.
 * The active server URL comes from the cached remote app-config row; the
 * renderer never reads an AIDream URL from a Vite environment variable.
 *
 * Auth:
 *   - Public endpoints (models, tools): no token needed
 *   - Authenticated endpoints (agents): pass Supabase JWT as Bearer token.
 *     There is no public/anonymous agent listing anymore — /api/agents
 *     requires a JWT. Callers must skip the fetch when logged out.
 */

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
  const headers: Record<string, string> = {};

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
    headers["X-Organization-Id"] = options.organizationId;
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

/**
 * A platform or user agent, as returned by GET /api/agents.
 * Prompt builtins now live in the `agent.definition` table and are served
 * from this single unified endpoint alongside the user's own agents.
 */
export interface AIDreamAgent {
  id: string;
  name: string;
  description?: string;
  category?: string;
  tags?: string[];
  type?: string;
  variables?: unknown[];
}

export interface AIDreamAgentsResponse {
  agents: AIDreamAgent[];
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
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  // aidream refuses an authenticated request with no organization before it
  // routes. This function is already best-effort (returns null on any
  // failure and the caller falls through to an unbound run), so a missing
  // organizationId here degrades the same way a network failure does —
  // never a fabricated organization.
  if (options.jwt && options.organizationId) {
    headers["Authorization"] = `Bearer ${options.jwt}`;
    headers["X-Organization-Id"] = options.organizationId;
  }
  try {
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
 * Fetch the unified agent catalog (platform agents + the user's own agents).
 * Requires a JWT — there is no public/anonymous variant. The old
 * /api/prompts/builtins, /api/prompts and /api/prompts/all endpoints are
 * gone; builtins moved into the `agent.definition` table and are served here.
 * Corresponds to GET /api/agents
 */
export async function fetchAIDreamAgents(
  jwt: string,
  opts: RequestOptions = {},
): Promise<AIDreamAgentsResponse> {
  return aidreamGet<AIDreamAgentsResponse>("/agents", { ...opts, jwt });
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
