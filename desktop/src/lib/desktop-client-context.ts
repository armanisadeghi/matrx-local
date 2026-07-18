/**
 * Desktop surface envelope for cloud agent requests.
 *
 * Every Cloud Chat request to aidream must declare this machine as an active
 * surface + executor, or the server's tool merge drops every `matrx-local`
 * tool pre-flight and agents report "I don't have desktop tools". The server
 * contract lives in aidream `packages/matrx-ai/matrx_ai/capabilities/
 * desktop_native.py` (payload model `DesktopNativePayload`); the Python
 * delegation engine builds the identical envelope for headless resumes in
 * `app/services/delegation/engine.py::_build_client_context` — keep the two
 * in sync.
 */

export const DESKTOP_SURFACE = "matrx-local/desktop";
export const DESKTOP_CAPABILITY = "desktop-native";

export interface DesktopClientContext {
  surface: string;
  capabilities: string[];
  state: Record<string, Record<string, unknown>>;
  amendments?: { add?: unknown[]; remove?: string[] };
}

interface EngineIdentity {
  instanceId: string;
  engineVersion: string;
}

let cachedIdentity: EngineIdentity | null = null;
let identityEngineUrl: string | null = null;

function browserPlatform(): string {
  const source = `${navigator.platform ?? ""} ${navigator.userAgent ?? ""}`;
  if (/Mac/i.test(source)) return "darwin";
  if (/Win/i.test(source)) return "win32";
  if (/Linux/i.test(source)) return "linux";
  return "";
}

async function fetchEngineIdentity(engineUrl: string): Promise<EngineIdentity> {
  const response = await fetch(`${engineUrl}/health`, {
    signal: AbortSignal.timeout(4000),
  });
  if (!response.ok) {
    throw new Error(`engine /health returned ${response.status}`);
  }
  const payload = (await response.json()) as {
    instance_id?: string | null;
    version?: string | null;
  };
  return {
    instanceId: payload.instance_id ?? "",
    engineVersion: payload.version ?? "",
  };
}

/**
 * Build the `client` envelope for a cloud request. The engine identity is
 * cached per engine URL; a dead engine degrades to an envelope without
 * instance identity rather than blocking the chat (the surface + capability
 * strings alone are what unlock tool injection server-side).
 */
export async function buildDesktopClientContext(
  engineUrl: string | null | undefined,
  options?: { removeTools?: string[]; loadedCategories?: string[] },
): Promise<DesktopClientContext> {
  if (engineUrl && (!cachedIdentity || identityEngineUrl !== engineUrl)) {
    try {
      cachedIdentity = await fetchEngineIdentity(engineUrl);
      identityEngineUrl = engineUrl;
    } catch (error) {
      console.warn(
        "[desktop-client-context] engine identity unavailable; sending envelope without instance_id",
        error,
      );
    }
  }

  const removeTools = options?.removeTools?.filter(Boolean) ?? [];
  return {
    surface: DESKTOP_SURFACE,
    capabilities: [DESKTOP_CAPABILITY],
    state: {
      [DESKTOP_CAPABILITY]: {
        platform: browserPlatform(),
        engine_version: cachedIdentity?.engineVersion ?? "",
        instance_id: cachedIdentity?.instanceId ?? "",
        ...(options?.loadedCategories?.length
          ? { loaded_categories: options.loadedCategories }
          : {}),
      },
    },
    ...(removeTools.length ? { amendments: { remove: removeTools } } : {}),
  };
}
