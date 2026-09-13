import { beforeEach, expect, it, vi } from "vitest";

import { engine } from "./api";

const api = engine as any;
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

const context = {
  revision: 1,
  nextSubject: "actor-a",
  isCurrent: () => true,
  engineOrigin: "http://engine.test",
  engineGeneration: "generation-a",
  engineCredentialRevision: 0,
};

beforeEach(() => {
  api.baseUrl = "http://engine.test";
  api.acceptedSessionFence = null;
  vi.stubGlobal("fetch", vi.fn());
});

it("preserves an invalid-session code so startup can perform its defined recovery", async () => {
  const fetchMock = vi.mocked(fetch);
  fetchMock
    .mockResolvedValueOnce(json({
      generation: "generation-a",
      credential_revision: 0,
      subject: null,
      cleanup: null,
    }))
    .mockResolvedValueOnce(json({
      detail: { code: "invalid_supabase_session" },
    }, 401));

  await expect(
    api.syncTokenToPython("access-token", "actor-a", context),
  ).rejects.toThrow("Token hand-off failed: 401 (invalid_supabase_session)");
});
