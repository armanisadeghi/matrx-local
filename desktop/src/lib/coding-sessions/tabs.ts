/**
 * ONE feature, one sidebar entry, three tabs.
 *
 * Coding sessions is one feature and everything lives inside it (Arman,
 * 2026-09-14) — usage was a second top-level nav item, which is how a person
 * ends up believing the two screens are two features. The tab lives in the
 * URL so a deep link, a redirect from the retired `/codex-usage` route, and
 * the back button all land on the same place.
 */

export const CODING_SESSIONS_TABS = ["sessions", "usage", "settings"] as const;

export type CodingSessionsTab = (typeof CODING_SESSIONS_TABS)[number];

export const CODING_SESSIONS_TAB_LABEL: Record<CodingSessionsTab, string> = {
  sessions: "Sessions",
  usage: "Usage",
  settings: "Settings & diagnostics",
};

export const DEFAULT_CODING_SESSIONS_TAB: CodingSessionsTab = "sessions";

/** The tab a URL asks for. Anything unknown is the default, never a blank screen. */
export function parseCodingSessionsTab(search: string | URLSearchParams): CodingSessionsTab {
  const params =
    typeof search === "string" ? new URLSearchParams(search) : search;
  const raw = (params.get("tab") ?? "").trim().toLowerCase();
  return (CODING_SESSIONS_TABS as readonly string[]).includes(raw)
    ? (raw as CodingSessionsTab)
    : DEFAULT_CODING_SESSIONS_TAB;
}

/** The in-app link for one tab. */
export function codingSessionsTabHref(tab: CodingSessionsTab): string {
  return `/coding-sessions?tab=${tab}`;
}
