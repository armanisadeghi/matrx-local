/**
 * How a cloud lane's blocker is DRESSED — the one place that decides.
 *
 * Not every stopped lane is a fault. When this Mac's session is momentarily
 * absent because the desktop is in the middle of handing the engine a fresh
 * one (a reload, a token rotation, an account switch), the engine reports
 * `session_refreshing`: a state with nothing for the person to do and no
 * remedy text. Painting that in destructive red with an alert role is the
 * screen lying about a gap it is itself closing — measured 2026-09-14, an
 * account switch left every lane showing "this Mac has no valid signed-in
 * session" for 27 s and 34 s. Real blockers keep the alarm.
 */
export const SESSION_REFRESHING_CODE = "session_refreshing";

export function isTransientSessionBlocker(code: string | null | undefined): boolean {
  return code === SESSION_REFRESHING_CODE;
}

export interface LaneBlockerTone {
  /** Border/background/text classes for the notice container. */
  readonly container: string;
  /** Assertive for faults, polite for a status the person need not act on. */
  readonly role: "alert" | "status";
  readonly quiet: boolean;
}

export function laneBlockerTone(code: string | null | undefined): LaneBlockerTone {
  return isTransientSessionBlocker(code)
    ? {
        container: "border-border bg-muted/50 text-foreground",
        role: "status",
        quiet: true,
      }
    : { container: "border-destructive/40 bg-destructive/10", role: "alert", quiet: false };
}
