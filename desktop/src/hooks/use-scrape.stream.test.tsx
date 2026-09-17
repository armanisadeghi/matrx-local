// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const mocks = vi.hoisted(() => ({ stream: vi.fn() }));
vi.mock("@/lib/api", () => ({ engine: { scrapeRemotelyStream: mocks.stream } }));
import { useScrapeMany } from "./use-scrape";
let state: ReturnType<typeof useScrapeMany>;
let root: ReturnType<typeof createRoot>;
let event: (name: string, data: unknown) => void;
let done: () => void;
let error: (e: Error) => void;
function Subject() { state = useScrapeMany(); return null; }
beforeEach(async () => {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn() });
  mocks.stream.mockImplementation(async (_urls, _options, onEvent, onDone, onError) => {
    event = onEvent; done = onDone; error = onError;
    return new AbortController();
  });
  root = createRoot(document.createElement("div"));
  await act(async () => { root.render(<Subject />); });
  await act(async () => { state.addUrls("https://example.com/a\nhttps://example.com/b"); });
});
afterEach(async () => { await act(async () => root.unmount()); vi.clearAllMocks(); vi.unstubAllGlobals(); });
async function start() { await act(async () => { await state.startScrape("remote", true); }); }
it("shows the server reason on every unfinished row after a global error", async () => {
  await start();
  await act(async () => { event("error", { failure_reason: "Search provider unavailable" }); done(); });
  expect(state.entries.map(e => e.status)).toEqual(["error", "error"]);
  expect(state.entries.map(e => e.result?.failure_reason)).toEqual(["Search provider unavailable", "Search provider unavailable"]);
  expect(state.running).toBe(false);
});
it("preserves completed results and fails missing rows at EOF", async () => {
  await start();
  await act(async () => { event("page_result", { url: "https://example.com/a", success: true, text_data: "Page A" }); done(); });
  expect(state.entries.map(e => e.status)).toEqual(["success", "error"]);
  expect(state.entries[1]?.result?.failure_reason).toContain("ended before returning");
});
it("settles unfinished rows on a transport failure", async () => {
  await start();
  await act(async () => { error(new Error("Connection lost")); });
  expect(state.entries.every(e => e.status === "error" && e.result?.failure_reason === "Connection lost")).toBe(true);
  expect(state.running).toBe(false);
});
it("settles rows when stream setup rejects", async () => {
  mocks.stream.mockRejectedValueOnce(new Error("Engine not discovered"));
  await start();
  expect(state.entries.every(e => e.status === "error")).toBe(true);
  expect(state.running).toBe(false);
});
it("does not replace stopped rows with late stream failures", async () => {
  await start();
  await act(async () => { state.stop(); error(new Error("Late failure")); done(); });
  expect(state.entries.every(e => e.result?.failure_reason === "Stopped by user")).toBe(true);
});
