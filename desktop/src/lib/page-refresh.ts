/** Compatibility facade for the canonical Application Recovery service. */
import { recovery, type RecoveryResult } from "@/lib/recovery";

export const PAGE_REFRESH_EVENT = "matrx-page-refresh";
export interface PageRefreshEvent extends CustomEvent { detail: { route: string } }

/** Refreshes registered data for a route and reports the real outcome. */
export function triggerPageRefresh(route = "*"): Promise<RecoveryResult> {
  return recovery.refreshSurface(route);
}
