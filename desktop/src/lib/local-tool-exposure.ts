import { engine } from "@/lib/api";

export interface LocalToolItem {
  name: string;
  description: string;
  category: string;
  advertised: boolean;
  enabled: boolean;
  platforms: string[] | null;
}

export interface LocalToolsResponse {
  tools?: Array<Partial<LocalToolItem> & { name: string }>;
}

async function localToolsRequest(
  engineUrl: string,
  path: string,
  init: RequestInit,
): Promise<Response> {
  return fetch(`${engineUrl}${path}`, {
    ...init,
    headers: {
      ...(await engine.getEngineAuthHeaders()),
      ...(init.headers as Record<string, string> | undefined),
    },
  });
}

export async function fetchLocalTools(
  engineUrl: string,
  signal = AbortSignal.timeout(6000),
): Promise<LocalToolsResponse> {
  const response = await localToolsRequest(engineUrl, "/chat/local-tools", {
    signal,
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as LocalToolsResponse;
}

export async function setLocalToolExposure(
  engineUrl: string,
  disabledTools: string[],
  signal = AbortSignal.timeout(6000),
): Promise<void> {
  const response = await localToolsRequest(
    engineUrl,
    "/chat/local-tools/exposure",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ disabled_tools: disabledTools }),
      signal,
    },
  );
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
}
