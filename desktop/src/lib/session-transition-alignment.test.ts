import { beforeEach, expect, it, vi } from "vitest";
import { engine } from "./api";
import { laneBlockerTone } from "./lane-blocker";

const api = engine as any;
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

beforeEach(() => {
  api.baseUrl = "http://engine-a";
  api.acceptedSessionFence = null;
  vi.stubGlobal("fetch", vi.fn());
});

/**
 * A renderer reload re-runs the whole auth lifecycle with the SAME person
 * signed in. That transition is ALIGNED: the engine already holds exactly that
 * subject's credential, so nothing is revoked and no cloud lane ever sees a
 * gap. Only a real host mutation (sign-out, or an account switch, which
 * arrives as a null incoming subject) may clear engine custody.
 */
it("a same-subject transition is aligned and sends no DELETE", async () => {
  const f = vi.mocked(fetch);
  f.mockResolvedValueOnce(
    json({ generation: "g-a", credential_revision: 3, subject: "actor-a", cleanup: null }),
  );
  await expect(
    api.prepareSessionTransition({ revision: 1, nextSubject: "actor-a", isCurrent: () => true }),
  ).resolves.toEqual({
    status: "aligned",
    origin: "http://engine-a",
    generation: "g-a",
    credentialRevision: 3,
    subject: "actor-a",
  });
  expect(f).toHaveBeenCalledTimes(1);
  expect(f.mock.calls.map((c) => String(c[1] && (c[1] as RequestInit).method))).not.toContain("DELETE");
  expect(api.acceptedSessionFence).toBeNull();
});

it("an account switch (null incoming subject) still revokes engine custody", async () => {
  const f = vi.mocked(fetch);
  f.mockResolvedValueOnce(
    json({ generation: "g-a", credential_revision: 3, subject: "actor-a", cleanup: null }),
  )
    .mockResolvedValueOnce(json({ status: "ok", generation: "g-next", credential_revision: 0 }))
    .mockResolvedValueOnce(json({ generation: "g-next", credential_revision: 0, subject: null, cleanup: null }))
    .mockResolvedValueOnce(json({ generation: "g-next", credential_revision: 0, subject: null, cleanup: null }));
  const result = await api.prepareSessionTransition({
    revision: 2,
    nextSubject: null,
    isCurrent: () => true,
  });
  expect(result.status).toBe("aligned");
  const deletes = f.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "DELETE");
  expect(deletes).toHaveLength(1);
  expect(String(deletes[0]?.[0])).toContain("/auth/token?expected_generation=g-a");
});

it("a lane the desktop is already fixing is a status, never an alarm", () => {
  expect(laneBlockerTone("session_refreshing")).toMatchObject({ role: "status", quiet: true });
  expect(laneBlockerTone("no_active_user_jwt")).toMatchObject({ role: "alert", quiet: false });
  expect(laneBlockerTone(null)).toMatchObject({ role: "alert", quiet: false });
});
