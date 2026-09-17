/** @vitest-environment jsdom */

import { act, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ClaudeOverviewReadError,
  type ClaudeCloudCheck,
  type ClaudeIndexReport,
  type ClaudeOverview,
} from "@/lib/api";
import type { CodingSessionsSources } from "@/lib/coding-sessions/overview-store";
import { useCodingSessions } from "./use-coding-sessions";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function overview(
  title = "Current",
  options: {
    indexState?: ClaudeIndexReport["state"];
    cloud?: Partial<ClaudeCloudCheck>;
  } = {},
): ClaudeOverview {
  return {
    schema_version: 2,
    account_id: "account",
    accounts: [],
    cloud: {
      checked: true, sessions: 1, checked_at: null, reason: null, detail: null,
      ...options.cloud,
    },
    ...(options.indexState ? {
      index: {
        state: options.indexState,
        refreshing: options.indexState === "refreshing",
        files_read: 0,
        conversations: 1,
        updated_at: null,
        changed_files: null,
        duration_seconds: null,
        limit_reached: false,
        unreadable: 0,
        error: null,
      },
    } : {}),
    conversations: [{
      session_id: "session-1", title, title_source: null, project: null,
      last_activity_at: 0, bytes: 0, on_disk: true, state: "in_cloud",
      pinned: false, pinned_rank: null, category: null, archived: false,
      in_claude_sidebar: true, cloud: null, delivery: { pending: 0, quarantined: 0 },
    }],
    totals: {
      conversations: 1, pinned: 0, index_files_read: 1, transcript_only: 0,
      transcripts_on_disk: 1, index_limit_reached: false, unreadable: 0,
      in_cloud: 1, changed: 0, queued: 0, failed: 0, not_in_cloud: 0,
      unknown: 0, waiting: 0, quarantined: 0,
    },
  } as unknown as ClaudeOverview;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function sources(
  getOverview: () => Promise<ClaudeOverview>,
  onConnected?: (listener: () => void) => () => void,
): CodingSessionsSources {
  return {
    overview: getOverview,
    bridgeStatus: async () => ({} as never),
    readiness: async () => ({} as never),
    artifactsStatus: async () => ({} as never),
    artifactsSessions: async () => ({ sessions: [] }),
    ...(onConnected ? { onEngineConnected: onConnected } : {}),
  };
}

let host: HTMLDivElement | null = null;
let root: Root | null = null;
let state: ReturnType<typeof useCodingSessions> | null = null;

function Subject({ value }: { value: CodingSessionsSources }) {
  state = useCodingSessions(value);
  return null;
}

async function mount(value: CodingSessionsSources, strict = false, copies = 1) {
  host = document.createElement("div");
  root = createRoot(host);
  await act(async () => {
    const children = Array.from({ length: copies }, (_, index) => <Subject key={index} value={value} />);
    root!.render(strict ? <StrictMode>{children}</StrictMode> : <>{children}</>);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  state = null;
  vi.useRealTimers();
});

describe("useCodingSessions transient overview recovery", () => {
  it("coalesces a blocked overview through Strict Mode and concurrent consumers", async () => {
    const slow = deferred<ClaudeOverview>();
    const getOverview = vi.fn(() => slow.promise);
    const value = sources(getOverview);

    await mount(value, true, 2);
    expect(getOverview).toHaveBeenCalledTimes(1);

    await act(async () => { slow.resolve(overview()); });
    await settle();
    expect(state?.snapshot.overview?.conversations[0]?.title).toBe("Current");
  });

  it("keeps cached rows, retries one transient timeout, then clears the failure", async () => {
    const getOverview = vi.fn()
      .mockRejectedValueOnce(new ClaudeOverviewReadError("timeout", new Error("timed out")))
      .mockResolvedValueOnce(overview("Recovered"));
    await mount(sources(getOverview));
    await settle();

    expect(state?.snapshot.overviewRetry).toBe("scheduled");
    expect(state?.snapshot.error).toBe("timed out");
    await act(async () => { await vi.advanceTimersByTimeAsync(2_500); });
    await settle();

    expect(getOverview).toHaveBeenCalledTimes(2);
    expect(state?.snapshot.overviewRetry).toBeNull();
    expect(state?.snapshot.error).toBeNull();
    expect(state?.snapshot.overview?.conversations[0]?.title).toBe("Recovered");
  });

  it("leaves an honest exhausted state and lets manual refresh earn one new retry", async () => {
    const timeout = () => new ClaudeOverviewReadError("timeout", new Error("timed out"));
    const getOverview = vi.fn()
      .mockRejectedValueOnce(timeout())
      .mockRejectedValueOnce(timeout())
      .mockRejectedValueOnce(timeout())
      .mockResolvedValueOnce(overview("Manual recovery"));
    await mount(sources(getOverview));
    await settle();
    await act(async () => { await vi.advanceTimersByTimeAsync(2_500); });
    await settle();

    expect(state?.snapshot.overviewRetry).toBe("exhausted");
    await act(async () => { await state!.refresh(); });
    await settle();
    expect(state?.snapshot.overviewRetry).toBe("scheduled");
    await act(async () => { await vi.advanceTimersByTimeAsync(2_500); });
    await settle();

    expect(getOverview).toHaveBeenCalledTimes(4);
    expect(state?.snapshot.error).toBeNull();
  });

  it("cancels a scheduled retry on unmount", async () => {
    const getOverview = vi.fn(() => Promise.reject(
      new ClaudeOverviewReadError("network", new TypeError("network down")),
    ));
    await mount(sources(getOverview));
    await settle();
    expect(state?.snapshot.overviewRetry).toBe("scheduled");

    await act(async () => root?.unmount());
    await act(async () => { await vi.advanceTimersByTimeAsync(2_500); });
    expect(getOverview).toHaveBeenCalledTimes(1);
  });

  it("does not retry an authorization or server response", async () => {
    const getOverview = vi.fn(() => Promise.reject(
      new Error("GET /coding-session/claude/overview failed: HTTP 403"),
    ));
    await mount(sources(getOverview));
    await settle();

    expect(state?.snapshot.overviewFailure).toBeNull();
    expect(state?.snapshot.overviewRetry).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(getOverview).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["cold", 2_500],
    ["refreshing", 4_000],
  ] as const)("does not let a cached %s index bypass the one transient-retry budget", async (indexState, indexDelay) => {
    const timeout = () => new ClaudeOverviewReadError("timeout", new Error("timed out"));
    const getOverview = vi.fn()
      .mockResolvedValueOnce(overview("Cached index", { indexState }))
      .mockRejectedValueOnce(timeout())
      .mockRejectedValueOnce(timeout());
    await mount(sources(getOverview));
    await settle();

    await act(async () => { await vi.advanceTimersByTimeAsync(indexDelay); });
    await settle();
    expect(state?.snapshot.overviewRetry).toBe("scheduled");
    await act(async () => { await vi.advanceTimersByTimeAsync(indexDelay); });
    await settle();

    expect(state?.snapshot.overviewRetry).toBe("exhausted");
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(getOverview).toHaveBeenCalledTimes(3);
  });

  it("does not let a cached cold index retry an authorization failure", async () => {
    const getOverview = vi.fn()
      .mockResolvedValueOnce(overview("Cached cold", { indexState: "cold" }))
      .mockRejectedValueOnce(new Error("GET /coding-session/claude/overview failed: HTTP 403"));
    await mount(sources(getOverview));
    await settle();
    await act(async () => { await vi.advanceTimersByTimeAsync(2_500); });
    await settle();

    expect(state?.snapshot.overviewFailure).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(getOverview).toHaveBeenCalledTimes(2);
  });

  it("follows an in-flight cloud check once and stops after it answers", async () => {
    const getOverview = vi.fn()
      .mockResolvedValueOnce(overview("Local", {
        indexState: "fresh",
        cloud: { checked: false, reason: "cloud_check_in_flight", refreshing: true },
      }))
      .mockResolvedValueOnce(overview("Cloud checked", {
        indexState: "fresh",
        cloud: { checked: true, reason: null, refreshing: false },
      }));
    await mount(sources(getOverview));
    await settle();
    await act(async () => { await vi.advanceTimersByTimeAsync(2_500); });
    await settle();

    expect(getOverview).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(getOverview).toHaveBeenCalledTimes(2);
  });

  it("does not poll a terminal cloud-auth state", async () => {
    const getOverview = vi.fn(() => Promise.resolve(overview("Local", {
      indexState: "fresh",
      cloud: { checked: false, reason: "no_active_user_jwt", refreshing: false },
    })));
    await mount(sources(getOverview));
    await settle();
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(getOverview).toHaveBeenCalledTimes(1);
  });

  it("resets the exhausted budget and reads immediately when the engine reconnects", async () => {
    const timeout = () => new ClaudeOverviewReadError("timeout", new Error("timed out"));
    const getOverview = vi.fn()
      .mockRejectedValueOnce(timeout())
      .mockRejectedValueOnce(timeout())
      .mockResolvedValueOnce(overview("Reconnected"));
    let connected: (() => void) | undefined;
    await mount(sources(getOverview, (listener) => {
      connected = listener;
      return () => { connected = undefined; };
    }));
    await settle();
    await act(async () => { await vi.advanceTimersByTimeAsync(2_500); });
    await settle();
    expect(state?.snapshot.overviewRetry).toBe("exhausted");

    await act(async () => { connected?.(); });
    await settle();
    expect(getOverview).toHaveBeenCalledTimes(3);
    expect(state?.snapshot.error).toBeNull();
  });
});
