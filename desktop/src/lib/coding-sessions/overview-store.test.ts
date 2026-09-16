/**
 * Forcing tests for the two rules Refresh broke.
 *
 * Both fail against the screen as it shipped in 1.4.110: `load()` there set no
 * loading flag on a click (so nothing announced the work) and each pass waited
 * for the ~4.3s overview before anything on screen moved.
 */
import { describe, expect, it, vi } from "vitest";

import {
  emptySnapshot,
  cloudInventoryFailureClass,
  refreshCodingSessions,
  type CodingSessionsSnapshot,
  type CodingSessionsSources,
} from "@/lib/coding-sessions/overview-store";
import type { ClaudeOverview } from "@/lib/api";

function overview(count: number, title = "Row"): ClaudeOverview {
  return {
    schema_version: 2,
    account_id: "acct",
    accounts: [],
    cloud: { checked: true, sessions: count, checked_at: null, reason: null, detail: null },
    conversations: Array.from({ length: count }, (_, index) => ({
      session_id: `s${index}`,
      title: `${title} ${index}`,
      title_source: null,
      project: null,
      last_activity_at: 0,
      bytes: 0,
      on_disk: true,
      state: "in_cloud",
      pinned: false,
      pinned_rank: null,
      category: null,
      archived: false,
      in_claude_sidebar: true,
      cloud: { conversation_id: `c${index}`, fidelity: null, last_seen_at: null },
      delivery: { pending: 0, quarantined: 0 },
    })),
    totals: {
      conversations: count,
      pinned: 0,
      index_files_read: 0,
      transcript_only: 0,
      transcripts_on_disk: count,
      index_limit_reached: false,
      unreadable: 0,
      in_cloud: count,
      changed: 0,
      queued: 0,
      failed: 0,
      not_in_cloud: 0,
      unknown: 0,
      waiting: 0,
      quarantined: 0,
    },
  } as unknown as ClaudeOverview;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function sources(partial: Partial<CodingSessionsSources> = {}): CodingSessionsSources {
  return {
    overview: () => Promise.resolve(overview(1)),
    bridgeStatus: () => Promise.resolve({} as never),
    readiness: () => Promise.resolve({} as never),
    artifactsStatus: () => Promise.resolve({} as never),
    artifactsSessions: () => Promise.resolve({ sessions: [] }),
    ...partial,
  };
}

const noPersist = { persist: () => null };

describe("refreshCodingSessions", () => {
  it("announces the refresh before it awaits anything", () => {
    const seen: CodingSessionsSnapshot[] = [];
    const slow = deferred<ClaudeOverview>();
    void refreshCodingSessions(
      sources({ overview: () => slow.promise }),
      emptySnapshot(),
      (next) => seen.push(next),
      noPersist,
    );
    // No await here on purpose: this is what the person sees on the click.
    expect(seen).toHaveLength(1);
    expect(seen[0]!.refreshing).toBe(true);
    expect(seen[0]!.overviewPending).toBe(true);
    slow.resolve(overview(1));
  });

  it("keeps the rows already on screen while the slow list is re-read", async () => {
    const slow = deferred<ClaudeOverview>();
    const current: CodingSessionsSnapshot = {
      ...emptySnapshot(),
      overview: overview(3, "Old"),
      overviewAt: 1,
      loadedFromEngine: true,
    };
    const seen: CodingSessionsSnapshot[] = [];
    const pass = refreshCodingSessions(
      sources({ overview: () => slow.promise }),
      current,
      (next) => seen.push(next),
      noPersist,
    );
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    // The fast endpoints have landed; the list is still the previous answer.
    const beforeList = seen[seen.length - 1]!;
    expect(beforeList.overviewPending).toBe(true);
    expect(beforeList.overview?.conversations).toHaveLength(3);
    expect(beforeList.overview?.conversations[0]!.title).toBe("Old 0");
    slow.resolve(overview(5, "New"));
    const final = await pass;
    expect(final.overviewPending).toBe(false);
    expect(final.refreshing).toBe(false);
    expect(final.overview?.conversations).toHaveLength(5);
    expect(final.overviewFromCache).toBe(false);
  });

  it("shows a failed list read as an error and keeps the last rows", async () => {
    const current: CodingSessionsSnapshot = {
      ...emptySnapshot(),
      overview: overview(2, "Old"),
      overviewAt: 1,
    };
    const final = await refreshCodingSessions(
      sources({ overview: () => Promise.reject(new Error("index unreadable")) }),
      current,
      () => {},
      noPersist,
    );
    expect(final.error).toBe("index unreadable");
    expect(final.overview?.conversations).toHaveLength(2);
    expect(final.refreshing).toBe(false);
  });

  it("reports each lane's failure separately instead of one blank screen", async () => {
    const final = await refreshCodingSessions(
      sources({
        artifactsStatus: () => Promise.reject(new Error("lane down")),
        artifactsSessions: () => Promise.reject(new Error("no manifest")),
      }),
      emptySnapshot(),
      () => {},
      noPersist,
    );
    expect(final.artifactsError).toBe("lane down");
    expect(final.artifactSessionsError).toBe("no manifest");
    expect(final.overview?.conversations).toHaveLength(1);
    expect(final.error).toBeNull();
  });

  it("records when the list was read and says so when a cache write fails", async () => {
    const persist = vi.fn().mockReturnValue("disk full");
    const final = await refreshCodingSessions(
      sources(),
      emptySnapshot(),
      () => {},
      { now: () => 1_700_000_000_000, persist },
    );
    expect(final.overviewAt).toBe(1_700_000_000_000);
    expect(persist).toHaveBeenCalledTimes(1);
    expect(final.cacheError).toBe("disk full");
  });

  it("captures only transitions into actionable cloud inventory failures", async () => {
    const capture = vi.fn(() => true);
    const unreachable = overview(1);
    unreachable.cloud = {
      checked: false,
      sessions: 0,
      checked_at: "2026-09-16T00:00:00Z",
      reason: "aidream_unreachable",
      detail: "private server detail must not be captured",
    };

    const first = await refreshCodingSessions(
      sources({ overview: () => Promise.resolve(unreachable) }),
      emptySnapshot(),
      () => {},
      { ...noPersist, capture },
    );
    await refreshCodingSessions(
      sources({ overview: () => Promise.resolve(unreachable) }),
      first,
      () => {},
      { ...noPersist, capture },
    );

    expect(capture).toHaveBeenCalledTimes(1);
    expect(capture).toHaveBeenCalledWith({
      level: "error",
      source: "coding-session-cloud-inventory",
      message: "AI Matrx conversation inventory entered terminal state: unreachable.",
      causalSignature: "coding-session-cloud-inventory:unreachable",
      requireIdentity: true,
    });
  });

  it("retries an unchanged cloud incident after identity-gated capture is refused", async () => {
    const capture = vi.fn().mockReturnValueOnce(false).mockReturnValue(true);
    const unreachable = overview(1);
    unreachable.cloud = {
      checked: false,
      sessions: 0,
      checked_at: "2026-09-16T00:00:00Z",
      reason: "aidream_unreachable",
      detail: null,
    };

    const first = await refreshCodingSessions(
      sources({ overview: () => Promise.resolve(unreachable) }),
      emptySnapshot(),
      () => {},
      { ...noPersist, capture },
    );
    const second = await refreshCodingSessions(
      sources({ overview: () => Promise.resolve(unreachable) }),
      first,
      () => {},
      { ...noPersist, capture },
    );
    await refreshCodingSessions(
      sources({ overview: () => Promise.resolve(unreachable) }),
      second,
      () => {},
      { ...noPersist, capture },
    );

    expect(capture).toHaveBeenCalledTimes(2);
    expect(second.capturedCloudFailureClass).toBe("unreachable");
  });

  it("keeps authentication, organization, and in-flight readiness states uncaptured", async () => {
    expect(cloudInventoryFailureClass({
      checked: false,
      sessions: 0,
      checked_at: "2026-09-16T00:00:00Z",
      reason: "cloud_check_in_flight",
      detail: null,
    })).toBeNull();
    expect(cloudInventoryFailureClass({
      checked: false,
      sessions: 0,
      checked_at: "2026-09-16T00:00:00Z",
      reason: "aidream_error:Cannot name an organization for this request",
      detail: null,
    })).toBeNull();
    expect(cloudInventoryFailureClass({
      checked: false,
      sessions: 0,
      checked_at: "2026-09-16T00:00:00Z",
      reason: "aidream_error:HTTP 401",
      detail: null,
    })).toBeNull();
  });
});
