import {
  listLocalTools,
  setLocalToolExposure as setLocalToolExposureOp,
} from "@ai-matrx/api/chat";
import { engine } from "@/lib/api";
import { engineApiAt } from "@/lib/engine-client";

const bearer = async () =>
  ((await engine.getEngineAuthHeaders()).Authorization ?? "").replace(/^Bearer /, "");

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

export async function fetchLocalTools(
  engineUrl: string,
  signal = AbortSignal.timeout(6000),
): Promise<LocalToolsResponse> {
  return (await listLocalTools(engineApiAt(engineUrl, await bearer()), undefined, {
    signal,
  })) as LocalToolsResponse;
}

export async function setLocalToolExposure(
  engineUrl: string,
  disabledTools: string[],
  signal = AbortSignal.timeout(6000),
): Promise<void> {
  await setLocalToolExposureOp(
    engineApiAt(engineUrl, await bearer()),
    { body: { disabled_tools: disabledTools } },
    { signal },
  );
}
