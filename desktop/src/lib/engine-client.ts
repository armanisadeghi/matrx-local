// The generated-operation client for an engine origin the caller already holds
// (and, optionally, the bearer token it already holds) — for code that is
// handed an `engineUrl` instead of going through the `engine` singleton
// (windows, hooks keyed on a discovered URL, the first-run screen).
// The singleton's own client, with the signed-out fence and re-ask, is
// `engine.api`.

import { createApi, type Api } from "@ai-matrx/api";

export function engineApiAt(engineUrl: string, accessToken?: string | null): Api {
  return createApi({
    environment: "localhost",
    resolveBaseUrl: () => engineUrl,
    retryAttempts: 1,
    ...(accessToken ? { credentials: { kind: "user" as const, accessToken } } : {}),
  });
}
