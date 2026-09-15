/**
 * THE MOUNT-TIME RE-ASK — a 401 we caused by sending no credential is never
 * the person's answer until we have asked for a credential again.
 *
 * WHAT BREAKS THIS TEST: deleting the re-ask in `EngineAPI.request()` (the
 * `resp.status === 401 && !authHdrs.Authorization` branch that calls
 * `authHeaders()` a second time and re-sends).
 *
 * WHY (measured 2026-09-15 on installed app 1.4.115,
 * common-docs/projects/coding-agent-bridge/verify-2026-09-15-matrx-local.md):
 * the Coding Sessions page mounted before the session daemon had answered, so
 * all five of its reads — /coding-session/claude/overview, /status,
 * /providers/readiness, /artifacts/status, /artifacts/sessions — left the app
 * with no Authorization header, the engine correctly answered 401 to every
 * one, and the screen told a signed-in person "Couldn't read your
 * conversations" with zero rows until they clicked Refresh by hand.
 */

import { beforeEach, expect, it, vi } from "vitest";

import { __fencedWhileSignedOut, __resetSignedOutFence, engine } from "./api";

const api = engine as any;
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
const unauthorized = () =>
  json({ message: "Authorization required", code: "authorization_required" }, 401);

const authOf = (call: number): string | null =>
  new Headers(vi.mocked(fetch).mock.calls[call]?.[1]?.headers).get(
    "Authorization",
  );

beforeEach(() => {
  __resetSignedOutFence();
  api.baseUrl = "http://engine.test";
  vi.stubGlobal("fetch", vi.fn());
});

it("re-asks and delivers the rows when the token lands after a tokenless 401", async () => {
  // The real race: the daemon's grant arrives while the mount-time read is
  // already in flight. The page must end up with its data, not an error.
  let token: string | null = null;
  api._getAccessToken = async () => token;
  vi.mocked(fetch).mockImplementation(async (_url, init) => {
    if (!new Headers(init?.headers).get("Authorization")) {
      token = "jwt-that-just-landed";
      return unauthorized();
    }
    return json({ conversations: 2012, accounts: 8 });
  });

  await expect(
    api.request("/coding-session/claude/overview"),
  ).resolves.toEqual({ conversations: 2012, accounts: 8 });
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
  expect(authOf(0)).toBeNull();
  expect(authOf(1)).toBe("Bearer jwt-that-just-landed");
  // A read that succeeded on the re-ask must not leave the path fenced.
  expect(__fencedWhileSignedOut()).toEqual([]);
});

it("every mount-time Coding Sessions read recovers, not just the first", async () => {
  const paths = [
    "/coding-session/claude/overview",
    "/coding-session/status",
    "/coding-session/providers/readiness",
    "/coding-session/artifacts/status",
    "/coding-session/artifacts/sessions",
  ];
  let token: string | null = null;
  api._getAccessToken = async () => token;
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    if (!new Headers(init?.headers).get("Authorization")) {
      token = "jwt-that-just-landed";
      return unauthorized();
    }
    return json({ path: new URL(String(url)).pathname });
  });

  for (const path of paths) {
    token = null;
    await expect(api.request(path)).resolves.toEqual({ path });
  }
  expect(__fencedWhileSignedOut()).toEqual([]);
});

it("still refuses and fences when the second ask is also empty", async () => {
  // Genuinely signed out: a different answer, so a constant cannot satisfy
  // both this test and the one above.
  api._getAccessToken = async () => null;
  vi.mocked(fetch).mockResolvedValue(unauthorized());

  await expect(api.request("/coding-session/status")).rejects.toThrow(
    "Authorization required",
  );
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  expect(__fencedWhileSignedOut()).toEqual(["GET /coding-session/status"]);
});

it("never re-asks a 401 that already carried a token", async () => {
  api._getAccessToken = async () => "a-real-jwt";
  vi.mocked(fetch).mockResolvedValue(unauthorized());

  await expect(api.request("/coding-session/status")).rejects.toThrow();
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  expect(__fencedWhileSignedOut()).toEqual([]);
});
