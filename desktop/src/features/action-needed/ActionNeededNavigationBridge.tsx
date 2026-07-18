import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

import { isTauri } from "@/lib/sidecar";
import { ACTION_NEEDED_PENDING_ROUTE_KEY } from "./actions";

/** Receives route handoffs sent by lightweight panel windows. */
export function ActionNeededNavigationBridge() {
  const navigate = useNavigate();
  useEffect(() => {
    if (!isTauri()) return;
    const consume = (route: string) => {
      if (!route.startsWith("/")) return;
      localStorage.removeItem(ACTION_NEEDED_PENDING_ROUTE_KEY);
      navigate(route);
    };
    const pending = localStorage.getItem(ACTION_NEEDED_PENDING_ROUTE_KEY);
    if (pending) {
      try {
        const parsed = JSON.parse(pending) as { route?: string; requestedAt?: number };
        // Do not resurrect an abandoned route from an old app session.
        if (
          parsed.route &&
          (parsed.requestedAt ?? 0) > Date.now() - 60_000
        ) {
          consume(parsed.route);
        } else {
          localStorage.removeItem(ACTION_NEEDED_PENDING_ROUTE_KEY);
        }
      } catch {
        localStorage.removeItem(ACTION_NEEDED_PENDING_ROUTE_KEY);
      }
    }
    let unlisten: (() => void) | undefined;
    void import("@tauri-apps/api/event").then(({ listen }) =>
      listen<string>("action-needed://navigate", (event) => {
        consume(event.payload);
      }).then((off) => {
        unlisten = off;
      }),
    );
    return () => unlisten?.();
  }, [navigate]);
  return null;
}
