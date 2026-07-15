import { useEffect } from "react";
import { recovery } from "@/lib/recovery";

/**
 * Register a refresh handler for a given route.
 *
 * When `triggerPageRefresh(route)` or `triggerPageRefresh("*")` is called,
 * the provided `onRefresh` callback will be invoked.
 *
 * @param route   The route this handler belongs to, e.g. "/local-models"
 * @param onRefresh  Stable callback (wrap in useCallback in the caller)
 */
export function usePageRefreshHandler(
  route: string,
  onRefresh: () => void | Promise<void>,
): void {
  useEffect(() => {
    return recovery.registerSurface(route, "refresh", onRefresh);
  }, [route, onRefresh]);
}
