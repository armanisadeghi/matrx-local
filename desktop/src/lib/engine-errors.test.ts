/**
 * What a person reads when the engine call fails. These sentences are pinned:
 * the move onto `@ai-matrx/api` changed the plumbing, not the words.
 */

import { beforeEach, expect, it, vi } from "vitest";

import {
  EngineHttpError,
  EngineNotFoundError,
  EngineServerError,
  EngineUnreachableError,
} from "./engine-errors";
import { EngineSignedOutError, __resetSignedOutFence, engine } from "./api";

const api = engine as any;
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

beforeEach(() => {
  __resetSignedOutFence();
  api.baseUrl = "http://engine.test";
  api._getAccessToken = async () => "token";
  vi.stubGlobal("fetch", vi.fn());
});

it("engine unreachable: not discovered says so, typed", async () => {
  api.baseUrl = null;
  const error = await api.request("/settings").catch((e: unknown) => e);
  expect(error).toBeInstanceOf(EngineUnreachableError);
  expect((error as Error).message).toBe("Engine not discovered");
});

it("engine unreachable: a refused connection keeps the browser's own words", async () => {
  vi.mocked(fetch).mockRejectedValue(new TypeError("Failed to fetch"));
  const error = await api.request("/settings", { method: "PUT", body: "{}" }).catch((e: unknown) => e);
  expect(error).toBeInstanceOf(EngineUnreachableError);
  expect((error as Error).message).toBe("Failed to fetch");
});

it("401 while signed in: the engine's own reason, typed with its status", async () => {
  vi.mocked(fetch).mockResolvedValue(json({ message: "Token expired" }, 401));
  const error = await api.request("/access/health").catch((e: unknown) => e);
  expect(error).toBeInstanceOf(EngineHttpError);
  expect((error as EngineHttpError).status).toBe(401);
  expect((error as Error).message).toBe("GET /access/health failed: Token expired");
});

it("401 while signed out: the sign-in remedy, typed", async () => {
  api._getAccessToken = async () => null;
  vi.mocked(fetch).mockResolvedValue(json({ message: "Authorization required" }, 401));
  await expect(api.request("/access/health")).rejects.toBeInstanceOf(EngineHttpError);
  const error = await api.request("/access/health").catch((e: unknown) => e);
  expect(error).toBeInstanceOf(EngineSignedOutError);
  expect((error as Error).message).toBe(
    "You are signed out, so the engine refused this request. Sign in to AI Matrx and it will work.",
  );
});

it("404: not found, typed, with the route named", async () => {
  vi.mocked(fetch).mockResolvedValue(json({ detail: "No such note" }, 404));
  const error = await api.request("/notes/notes/1").catch((e: unknown) => e);
  expect(error).toBeInstanceOf(EngineNotFoundError);
  expect((error as Error).message).toBe("GET /notes/notes/1 failed: No such note");
});

it("5xx: server error, typed, with hint and diagnostic reference", async () => {
  vi.mocked(fetch).mockResolvedValue(
    json({ message: "Boom", hint: "Restart the engine.", failure_id: "f-1" }, 500),
  );
  const error = await api.request("/access/health", { method: "POST", body: "{}" }).catch((e: unknown) => e);
  expect(error).toBeInstanceOf(EngineServerError);
  expect((error as Error).message).toBe(
    "POST /access/health failed: Boom Restart the engine. Diagnostic reference: f-1.",
  );
});

it("a method that always had its own sentence still shows it", async () => {
  vi.mocked(fetch).mockResolvedValue(json({ detail: "x" }, 500));
  const settings = await api.getSettings().catch((e: unknown) => e);
  expect((settings as Error).message).toBe("Failed to get settings: 500");
  const tools = await api.listTools().catch((e: unknown) => e);
  expect((tools as Error).message).toBe("Failed to list tools: 500");
  const wake = await api.owwStop().catch((e: unknown) => e);
  expect((wake as Error).message).toBe("OWW stop failed: 500");
});

import {
  MediaGenHttpError,
  VaultLockedError,
  fetchMediaLibraryFile,
  fetchMediaVaultFile,
  getImageGenStatus,
  MediaFileError,
} from "./api";

it("media helpers: a refusal keeps their own error class and words", async () => {
  vi.mocked(fetch).mockResolvedValue(json({ detail: "No GPU" }, 503));
  const image = await getImageGenStatus("http://engine.test").catch((e: unknown) => e);
  expect(image).toBeInstanceOf(MediaGenHttpError);
  expect((image as Error).message).toBe("No GPU");
  expect((image as MediaGenHttpError).status).toBe(503);

  vi.mocked(fetch).mockResolvedValue(json({ detail: "gone" }, 404));
  const file = await fetchMediaLibraryFile("http://engine.test", "i1").catch((e: unknown) => e);
  expect(file).toBeInstanceOf(MediaFileError);
  expect((file as MediaFileError).status).toBe(404);

  vi.mocked(fetch).mockResolvedValue(json({ detail: "locked" }, 423));
  await expect(fetchMediaVaultFile("http://engine.test", "i1")).rejects.toBeInstanceOf(VaultLockedError);
});

it("media helpers: a dead engine is still the browser's TypeError", async () => {
  vi.mocked(fetch).mockRejectedValue(new TypeError("Failed to fetch"));
  const error = await getImageGenStatus("http://engine.test").catch((e: unknown) => e);
  expect(error).toBeInstanceOf(TypeError);
  expect((error as Error).message).toBe("Failed to fetch");
});
