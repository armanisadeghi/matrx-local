/**
 * The engine's index and cloud states, in the screen's words.
 *
 * Every case here fails against the screen as it shipped in 1.4.124, which had
 * no notion of a cold index at all: the overview always answered with a
 * complete list, so an empty list meant an empty Mac. Since 1.4.125 it answers
 * from the persisted index in milliseconds and refreshes behind the response,
 * and an empty list can mean "not read yet" — a difference nothing on screen
 * may paper over.
 */

import { describe, expect, it } from "vitest";

import type { ClaudeCloudCheck, ClaudeIndexReport, ClaudeOverview } from "@/lib/api";
import {
  cloudAgeLabel,
  cloudCheckPending,
  cloudHeaderText,
  cloudPhase,
  indexCountsAreReal,
  indexNotice,
  indexPollDelayMs,
  indexState,
} from "@/lib/coding-sessions/index-state";

function overview(index?: Partial<ClaudeIndexReport>): ClaudeOverview {
  return {
    schema_version: 2,
    conversations: [],
    totals: { conversations: 0 },
    ...(index
      ? {
          index: {
            state: "fresh",
            refreshing: false,
            files_read: 0,
            conversations: 0,
            updated_at: null,
            changed_files: null,
            duration_seconds: null,
            limit_reached: false,
            unreadable: 0,
            error: null,
            ...index,
          } satisfies ClaudeIndexReport,
        }
      : {}),
  } as unknown as ClaudeOverview;
}

function cloud(partial: Partial<ClaudeCloudCheck>): ClaudeCloudCheck {
  return {
    checked: true,
    reason: null,
    detail: null,
    sessions: 0,
    checked_at: "2026-09-15T12:00:00Z",
    ...partial,
  };
}

describe("index state", () => {
  it("reads the engine's own state, and says so when the engine has none", () => {
    expect(indexState(overview({ state: "cold" }))).toBe("cold");
    expect(indexState(overview({ state: "refreshing" }))).toBe("refreshing");
    expect(indexState(overview({ state: "fresh" }))).toBe("fresh");
    expect(indexState(overview())).toBe("unreported");
    expect(indexState(null)).toBe("unreported");
  });

  it("keeps asking while the index is cold and stops once it is fresh", () => {
    expect(indexPollDelayMs(overview({ state: "cold" }))).toBe(2_500);
    expect(indexPollDelayMs(overview({ state: "refreshing" }))).toBe(4_000);
    expect(indexPollDelayMs(overview({ state: "fresh" }))).toBeNull();
    // An engine that reads the whole tree in the request has nothing pending.
    expect(indexPollDelayMs(overview())).toBeNull();
  });

  it("refuses to present counts from an index that has not been read", () => {
    expect(indexCountsAreReal(overview({ state: "cold" }))).toBe(false);
    expect(indexCountsAreReal(overview({ state: "refreshing" }))).toBe(true);
    expect(indexCountsAreReal(overview({ state: "fresh" }))).toBe(true);
    expect(indexCountsAreReal(null)).toBe(false);
  });

  it("shows the first read as a counter, never as zero conversations", () => {
    const notice = indexNotice(overview({ state: "cold", files_read: 12_345 }));
    expect(notice?.state).toBe("cold");
    expect(notice?.headline).toBe("Reading your conversations for the first time…");
    expect(notice?.detail).toContain("12,345 index files read so far");
    // Nothing read yet is still honest, not a "0 files" claim.
    expect(indexNotice(overview({ state: "cold", files_read: 0 }))?.detail).toContain(
      "Nothing has been read yet",
    );
  });

  it("carries the changed-file count and the last duration when the engine knows them", () => {
    const notice = indexNotice(
      overview({ state: "refreshing", changed_files: 7, duration_seconds: 1.25 }),
    );
    expect(notice?.state).toBe("refreshing");
    expect(notice?.detail).toBe("7 changed records · last read took 1.3s");
    expect(
      indexNotice(overview({ state: "refreshing", changed_files: 1, duration_seconds: 42 }))?.detail,
    ).toBe("1 changed record · last read took 42s");
    // Unknown is left unsaid rather than invented.
    expect(indexNotice(overview({ state: "refreshing" }))?.detail).toBeNull();
  });

  it("has nothing to announce for a fresh index or an engine without one", () => {
    expect(indexNotice(overview({ state: "fresh" }))).toBeNull();
    expect(indexNotice(overview())).toBeNull();
    expect(indexNotice(null)).toBeNull();
  });
});

describe("the cloud check", () => {
  it("separates answered, not asked yet, and could not be asked", () => {
    expect(cloudPhase(cloud({ checked: true }))).toBe("checked");
    expect(cloudPhase(cloud({ checked: false, reason: "cloud_check_in_flight" }))).toBe("in_flight");
    expect(cloudPhase(cloud({ checked: false, reason: "signed_out" }))).toBe("unavailable");
    expect(cloudPhase(undefined)).toBe("unavailable");
    expect(cloudCheckPending(cloud({ checked: false, reason: "cloud_check_in_flight" }))).toBe(true);
    expect(cloudCheckPending(cloud({ checked: false, reason: "signed_out" }))).toBe(false);
  });

  it("says how old the answer is with the shared relative-time voice", () => {
    expect(cloudAgeLabel(cloud({ age_seconds: 2 }))).toBe("checked 2s ago");
    expect(cloudAgeLabel(cloud({ age_seconds: 44.6 }))).toBe("checked 44s ago");
    expect(cloudAgeLabel(cloud({ age_seconds: 240 }))).toBe("checked 4m ago");
    // An engine that does not report an age gets no invented one.
    expect(cloudAgeLabel(cloud({}))).toBeNull();
    expect(cloudAgeLabel(cloud({ age_seconds: null }))).toBeNull();
  });

  it("gives each phase its own header wording", () => {
    const stamp = () => "a moment ago";
    expect(cloudHeaderText(cloud({ sessions: 1_671, age_seconds: 120 }), stamp)).toBe(
      "AI Matrx holds 1,671 of them (checked 2m ago)",
    );
    expect(cloudHeaderText(cloud({ sessions: 3 }), stamp)).toBe(
      "AI Matrx holds 3 of them (checked a moment ago)",
    );
    expect(
      cloudHeaderText(cloud({ checked: false, reason: "cloud_check_in_flight" }), stamp),
    ).toBe("AI Matrx is being asked which of them it holds");
    expect(cloudHeaderText(cloud({ checked: false, reason: "signed_out" }), stamp)).toBe(
      "AI Matrx could not be asked",
    );
  });
});
