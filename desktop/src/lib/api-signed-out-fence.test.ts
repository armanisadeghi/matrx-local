/**
 * The signed-out request fence: never re-ask a question the engine has
 * already refused for a reason only signing in can change.
 *
 * WHY (SR-05, measured 2026-09-11 -> 2026-09-14): with no Supabase session,
 * two pollers gated only on engine connectivity produced roughly 37,000
 * rejected engine requests in 72 hours — /prompt-matrix/paths 12,377,
 * /prompt-matrix/templates 12,225, /prompt-matrix/library 12,225, plus
 * /access/health, /cloud/debug, /downloads/stream, /filesystem/status and
 * /scrapes/sync-status. Every one was answered 401, logged, and discarded.
 * Nothing was broken; nothing stopped asking.
 */

import { beforeEach, expect, it, vi } from "vitest";

import {
  EngineSignedOutError,
  __fencedWhileSignedOut,
  __resetSignedOutFence,
  engine,
} from "./api";

const api = engine as any;
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

const unauthorized = () =>
  json({ message: "Authorization required", code: "authorization_required" }, 401);

beforeEach(() => {
  __resetSignedOutFence();
  api.baseUrl = "http://engine.test";
  api._getAccessToken = async () => null;
  vi.stubGlobal("fetch", vi.fn());
});

it("asks a refused path exactly once while signed out", async () => {
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockResolvedValue(unauthorized());

  await expect(api.request("/access/health")).rejects.toThrow();
  for (let i = 0; i < 200; i += 1) {
    await expect(api.request("/access/health")).rejects.toBeInstanceOf(
      EngineSignedOutError,
    );
  }

  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(__fencedWhileSignedOut()).toEqual(["GET /access/health"]);
});

it("names the remedy instead of failing silently", async () => {
  vi.mocked(fetch).mockResolvedValue(unauthorized());
  await expect(api.request("/access/health")).rejects.toThrow();

  const error = await api.request("/access/health").catch((e: unknown) => e);
  expect(error).toBeInstanceOf(EngineSignedOutError);
  expect((error as Error).message).toContain("Sign in");
});

it("fences the question, not the query string", async () => {
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockResolvedValue(unauthorized());

  await expect(api.request("/downloads/stream?cursor=1")).rejects.toThrow();
  await expect(api.request("/downloads/stream?cursor=2")).rejects.toBeInstanceOf(
    EngineSignedOutError,
  );

  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("clears the whole fence the moment a token exists", async () => {
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockResolvedValue(unauthorized());
  await expect(api.request("/access/health")).rejects.toThrow();
  expect(__fencedWhileSignedOut()).toHaveLength(1);

  api._getAccessToken = async () => "a-real-jwt";
  fetchMock.mockResolvedValue(json({ ok: true }));

  await expect(api.request("/access/health")).resolves.toEqual({ ok: true });
  expect(__fencedWhileSignedOut()).toEqual([]);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it("never fences a path that answered for a different reason", async () => {
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockResolvedValue(json({ message: "nope" }, 500));

  await expect(api.request("/filesystem/status")).rejects.toThrow();
  await expect(api.request("/filesystem/status")).rejects.toThrow();

  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(__fencedWhileSignedOut()).toEqual([]);
});

it("never fences a 401 that arrived WITH a token (that is a real problem)", async () => {
  const fetchMock = vi.mocked(fetch);
  api._getAccessToken = async () => "a-real-jwt";
  fetchMock.mockResolvedValue(unauthorized());

  await expect(api.request("/access/health")).rejects.toThrow();
  await expect(api.request("/access/health")).rejects.toThrow();

  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(__fencedWhileSignedOut()).toEqual([]);
});
