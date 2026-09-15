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

it("a same-subject daemon transition aligns to the running engine process", async () => {
  const f = vi.mocked(fetch);
  f.mockResolvedValueOnce(json({ boot_id: "process-a" }));
  await expect(
    api.prepareSessionTransition({ revision: 1, nextSubject: "actor-a", isCurrent: () => true }),
  ).resolves.toEqual({
    status: "aligned",
    origin: "http://engine-a",
    generation: "process-a",
    credentialRevision: 1,
    subject: "actor-a",
  });
  expect(f).toHaveBeenCalledOnce();
  expect(String(f.mock.calls[0]?.[0])).toBe("http://engine-a/health");
  expect((f.mock.calls[0]?.[1] as RequestInit).method).toBeUndefined();
});

it("an anonymous daemon transition aligns but never deletes an engine credential", async () => {
  const f = vi.mocked(fetch);
  f.mockResolvedValueOnce(json({ boot_id: "process-a" }));
  const result = await api.prepareSessionTransition({ revision: 2, nextSubject: null, isCurrent: () => true });
  expect(result).toEqual({
    status: "aligned",
    origin: "http://engine-a",
    generation: "process-a",
    credentialRevision: 2,
    subject: null,
  });
  expect(f).toHaveBeenCalledOnce();
  expect(f.mock.calls.map((c) => (c[1] as RequestInit | undefined)?.method)).not.toContain("DELETE");
});

it("a lane the desktop is already fixing is a status, never an alarm", () => {
  expect(laneBlockerTone("session_refreshing")).toMatchObject({ role: "status", quiet: true });
  expect(laneBlockerTone("no_active_user_jwt")).toMatchObject({ role: "alert", quiet: false });
  expect(laneBlockerTone(null)).toMatchObject({ role: "alert", quiet: false });
});
