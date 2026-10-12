/**
 * The engine's event streams ride the generated SSE operations. These pin what
 * each caller receives so the hand-rolled readers they replaced stay gone.
 */

import { beforeEach, expect, it, vi } from "vitest";

import { __resetSignedOutFence, engine } from "./api";

const api = engine as any;

const sse = (...frames: string[]) =>
  new Response(frames.map((f) => `${f}\n\n`).join(""), {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });

beforeEach(() => {
  __resetSignedOutFence();
  api.baseUrl = "http://engine.test";
  api._getAccessToken = async () => "token";
  vi.stubGlobal("fetch", vi.fn());
});

it("setup install: progress, total progress, raw lines, completion", async () => {
  vi.mocked(fetch).mockResolvedValue(
    sse(
      'event: started\ndata: {"message":"go"}',
      'event: progress\ndata: {"component":"a","status":"installing","percent":10}',
      'event: total_progress\ndata: {"total_percent":50,"message":"half"}',
      "event: junk\ndata: not-json",
      'event: complete\ndata: {"ok":true}',
    ),
  );
  const progress: unknown[] = [];
  const total: unknown[] = [];
  const done: unknown[] = [];
  const raw: string[] = [];
  const errors: string[] = [];
  await engine.runSetupInstall({
    mode: "first_run",
    onProgress: (d) => progress.push(d),
    onTotalProgress: (p, m) => total.push([p, m]),
    onComplete: (d) => done.push(d),
    onError: (e) => errors.push(e),
    onRawLine: (l) => raw.push(l),
  });
  expect(vi.mocked(fetch).mock.calls[0]?.[0]).toContain("/setup/install?mode=first_run");
  expect(progress).toEqual([
    { component: "_system", status: "installing", message: "go", percent: 0 },
    { component: "a", status: "installing", percent: 10 },
  ]);
  expect(total).toEqual([[50, "half"]]);
  expect(done).toEqual([{ ok: true }]);
  expect(errors).toEqual([]);
  expect(raw).toContain("event: complete");
});

it("setup install: a stream that ends early says so", async () => {
  vi.mocked(fetch).mockResolvedValue(sse('event: progress\ndata: {"percent":1}'));
  const errors: string[] = [];
  await engine.runSetupInstall({ onProgress() {}, onComplete() {}, onError: (e) => errors.push(e) });
  expect(errors[0]).toMatch(/^Setup stream ended without a completion event/);
});

it("install refusals keep their sentences", async () => {
  vi.mocked(fetch).mockResolvedValue(new Response("{}", { status: 503, headers: { "content-type": "application/json" } }));
  const errors: string[] = [];
  await engine.runSetupInstall({ onProgress() {}, onComplete() {}, onError: (e) => errors.push(e) });
  await engine.runTranscriptionInstall("m", { onProgress() {}, onComplete() {}, onError: (e) => errors.push(e) });
  await engine.installBrowserRuntime({ onProgress() {}, onComplete() {}, onError: (e) => errors.push(e) });
  expect(errors).toEqual([
    "Setup install failed: 503",
    "Transcription install failed: 503",
    "Browser install failed: HTTP 503",
  ]);
});

it("wake-word model download: progress then complete", async () => {
  vi.mocked(fetch).mockResolvedValue(
    sse(
      'event: progress\ndata: {"bytes_done":1,"total_bytes":2,"percent":50}',
      'event: complete\ndata: {"name":"hey","size_mb":1}',
    ),
  );
  const seen: string[] = [];
  await engine.owwDownloadModelStream("hey", {
    onProgress: (d) => seen.push(`p${d.percent}`),
    onComplete: (d) => seen.push(`c${d.name}`),
    onError: (e) => seen.push(`e${e}`),
  });
  expect(seen).toEqual(["p50", "chey"]);
});

it("streamLogs: log, history_end, connected", async () => {
  vi.mocked(fetch).mockResolvedValue(
    sse(
      'event: connected\ndata: {"log_path":"/x.log"}',
      'event: log\ndata: {"line":"hi","level":"INFO","timestamp":1}',
      'event: history_end\ndata: {"lines_sent":1}',
    ),
  );
  const seen: unknown[] = [];
  await new Promise<void>((resolve) => {
    engine.streamLogs({
      onConnected: (p) => seen.push(["connected", p]),
      onLine: (d) => seen.push(["line", d.line]),
      onHistoryEnd: (n) => {
        seen.push(["end", n]);
        resolve();
      },
    });
  });
  expect(seen).toEqual([["connected", "/x.log"], ["line", "hi"], ["end", 1]]);
});

it("streamSSE: parsed data, raw text when not JSON, then done", async () => {
  vi.mocked(fetch).mockResolvedValue(sse('event: page\ndata: {"u":1}', "data: plain"));
  const events: unknown[] = [];
  await new Promise<void>((resolve, reject) => {
    void engine.scrapeRemotelyStream(["https://a.test"], undefined, (e, d) => events.push([e, d]), resolve, reject);
  });
  expect(events).toEqual([["page", { u: 1 }], ["message", "plain"]]);
});
