/**
 * What the engine's index and cloud check are DOING, in the screen's words.
 *
 * Since engine 1.4.125 the overview answers in milliseconds: it loads the
 * persisted Claude index and kicks the disk refresh behind the response
 * (`app/services/coding_sessions/claude_overview.py`). That speed costs the
 * screen one truth it used to get for free — a fast answer can be an EMPTY or
 * an INCOMPLETE answer, and the old page would have said "0 conversations on
 * this Mac" for a first-run engine that simply had not read them yet.
 *
 * So the payload's own `index.state` decides the voice:
 *   cold        nothing indexed yet — the list is genuinely empty, never "0"
 *   refreshing  rows are real but a re-read is running behind this answer
 *   fresh       the rows are everything on this Mac
 *   unreported  an engine older than the index store (no `index` block)
 *
 * The same rule binds the cloud check: "not asked yet" (`reason:
 * "cloud_check_in_flight"`) is a quiet, self-clearing state, not the
 * could-not-be-asked failure, and it never gets the failure's warning voice.
 *
 * Every function here is pure so the three states are tested without a DOM.
 */

import type { ClaudeCloudCheck, ClaudeIndexReport, ClaudeOverview } from "@/lib/api";

export type IndexState = "cold" | "refreshing" | "fresh" | "unreported";

/** How long before asking again, per state. Null = nothing to wait for. */
const POLL_MS: Record<IndexState, number | null> = {
  // A first read of a big tree takes seconds, not minutes, and the person is
  // watching the counter move: ask often enough that it does.
  cold: 2_500,
  // One follow-up read a few seconds later: by then the refresh behind the
  // last response has landed and the rows are complete.
  refreshing: 4_000,
  fresh: null,
  unreported: null,
};

export function indexReport(overview: ClaudeOverview | null): ClaudeIndexReport | null {
  return overview?.index ?? null;
}

export function indexState(overview: ClaudeOverview | null): IndexState {
  const state = overview?.index?.state;
  if (state === "cold" || state === "refreshing" || state === "fresh") return state;
  return "unreported";
}

/** Milliseconds until this screen should ask again, or null when it should not. */
export function indexPollDelayMs(overview: ClaudeOverview | null): number | null {
  return POLL_MS[indexState(overview)];
}

/**
 * False while the index is cold: the counts in `totals` describe an index that
 * has not been read yet, so nothing on screen may present them as facts about
 * this Mac.
 */
export function indexCountsAreReal(overview: ClaudeOverview | null): boolean {
  return overview !== null && indexState(overview) !== "cold";
}

export interface IndexNotice {
  state: "cold" | "refreshing";
  headline: string;
  detail: string | null;
}

function seconds(value: number): string {
  return value >= 10 ? `${Math.round(value)}s` : `${value.toFixed(1)}s`;
}

/** The plain sentence for a cold or refreshing index, or null when there is none. */
export function indexNotice(overview: ClaudeOverview | null): IndexNotice | null {
  const report = indexReport(overview);
  const state = indexState(overview);
  if (report === null || (state !== "cold" && state !== "refreshing")) return null;
  if (state === "cold") {
    return {
      state: "cold",
      headline: "Reading your conversations for the first time…",
      detail:
        report.files_read > 0
          ? `${report.files_read.toLocaleString()} index files read so far. They appear here as soon as this finishes.`
          : "Nothing has been read yet. Your conversations appear here as soon as this finishes.",
    };
  }
  const parts: string[] = [];
  if (report.changed_files !== null && report.changed_files > 0) {
    parts.push(
      `${report.changed_files.toLocaleString()} changed record${report.changed_files === 1 ? "" : "s"}`,
    );
  }
  if (report.duration_seconds !== null) {
    parts.push(`last read took ${seconds(report.duration_seconds)}`);
  }
  return {
    state: "refreshing",
    headline: "Re-reading this Mac's conversations — the rows below are the last complete answer.",
    detail: parts.length > 0 ? parts.join(" · ") : null,
  };
}

export type CloudPhase = "checked" | "in_flight" | "unavailable";

/**
 * Three different things, never conflated: answered, not asked YET, and could
 * not be asked at all.
 */
export function cloudPhase(cloud: ClaudeCloudCheck | null | undefined): CloudPhase {
  if (!cloud) return "unavailable";
  if (cloud.checked) return "checked";
  return cloud.reason === "cloud_check_in_flight" ? "in_flight" : "unavailable";
}

/** True while the cloud answer is merely pending — rows read "unknown" quietly. */
export function cloudCheckPending(cloud: ClaudeCloudCheck | null | undefined): boolean {
  return cloudPhase(cloud) === "in_flight";
}

/** "checked 3 min ago" from `cloud.age_seconds`, or null when it is absent. */
export function cloudAgeLabel(cloud: ClaudeCloudCheck | null | undefined): string | null {
  const age = cloud?.age_seconds;
  if (age === null || age === undefined || !Number.isFinite(age)) return null;
  if (age < 10) return "checked just now";
  if (age < 90) return `checked ${Math.round(age)} sec ago`;
  const minutes = Math.round(age / 60);
  return `checked ${minutes} min ago`;
}

/** The header's cloud clause, in the voice the phase earns. */
export function cloudHeaderText(
  cloud: ClaudeCloudCheck | null | undefined,
  stampFallback: (value: string | null | undefined) => string,
): string {
  switch (cloudPhase(cloud)) {
    case "checked": {
      const when = cloudAgeLabel(cloud) ?? `checked ${stampFallback(cloud?.checked_at)}`;
      return `AI Matrx holds ${(cloud?.sessions ?? 0).toLocaleString()} of them (${when})`;
    }
    case "in_flight":
      return "AI Matrx is being asked which of them it holds";
    case "unavailable":
      return "AI Matrx could not be asked";
  }
}
