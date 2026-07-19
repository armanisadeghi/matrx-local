import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";

import { isTauri } from "@/lib/sidecar";
import { isFullWindow } from "@/lib/window-role";
import { reportActionNeeded } from "./store";
import type { ActionNeeded } from "./types";
import {
  ACTION_NEEDED_PENDING_ACTION_KEY,
  ACTION_NEEDED_PENDING_ROUTE_KEY,
  dispatchActionNeeded,
} from "./actions";

/** Receives route handoffs sent by lightweight panel windows. */
export function ActionNeededNavigationBridge() {
  const navigate = useNavigate();
  const recentDispatches = useRef(new Map<string, number>());
  const consumedHandoffs = useRef(new Set<string>());
  useEffect(() => {
    // Panel windows must never consume handoffs persisted for a full peer.
    if (!isTauri() || !isFullWindow) return;
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
    const clearMatchingPendingAction = (handoffId?: string) => {
      const pending = localStorage.getItem(ACTION_NEEDED_PENDING_ACTION_KEY);
      if (!pending) return;
      try {
        const parsed = JSON.parse(pending) as { handoffId?: string };
        if (!handoffId || !parsed.handoffId || parsed.handoffId === handoffId) {
          localStorage.removeItem(ACTION_NEEDED_PENDING_ACTION_KEY);
        }
      } catch {
        localStorage.removeItem(ACTION_NEEDED_PENDING_ACTION_KEY);
      }
    };
    const consumeAction = (item: ActionNeeded, handoffId?: string) => {
      clearMatchingPendingAction(handoffId);
      reportActionNeeded(item);
      if (handoffId) {
        if (consumedHandoffs.current.has(handoffId)) return;
        consumedHandoffs.current.add(handoffId);
      } else {
        // Compatibility for a handoff written by an older panel build, which
        // has no request ID to correlate persistence with its event.
        const now = Date.now();
        const previous = recentDispatches.current.get(item.fingerprint) ?? 0;
        if (now - previous < 2_000) return;
        recentDispatches.current.set(item.fingerprint, now);
      }
      void dispatchActionNeeded(item);
    };
    const pendingAction = localStorage.getItem(ACTION_NEEDED_PENDING_ACTION_KEY);
    if (pendingAction) {
      try {
        const parsed = JSON.parse(pendingAction) as {
          item?: ActionNeeded;
          handoffId?: string;
          requestedAt?: number;
        };
        if (parsed.item && (parsed.requestedAt ?? 0) > Date.now() - 60_000) {
          consumeAction(parsed.item, parsed.handoffId);
        } else {
          localStorage.removeItem(ACTION_NEEDED_PENDING_ACTION_KEY);
        }
      } catch {
        localStorage.removeItem(ACTION_NEEDED_PENDING_ACTION_KEY);
      }
    }
    const unlisteners: Array<() => void> = [];
    let cancelled = false;
    const retainListener = (off: () => void) => {
      if (cancelled) off();
      else unlisteners.push(off);
    };
    void import("@tauri-apps/api/event").then(({ listen }) =>
      listen<string>("action-needed://navigate", (event) => {
        consume(event.payload);
      }).then((off) => {
        retainListener(off);
      }),
    );
    void import("@tauri-apps/api/event").then(({ listen }) =>
      listen<string>("action-needed://dispatch", (event) => {
        try {
          const parsed = JSON.parse(event.payload) as {
            item?: ActionNeeded;
            handoffId?: string;
          };
          if (parsed.item) consumeAction(parsed.item, parsed.handoffId);
        } catch {
          console.error("[action-needed] invalid dispatch handoff payload");
        }
      }).then((off) => {
        retainListener(off);
      }),
    );
    return () => {
      cancelled = true;
      unlisteners.forEach((off) => off());
    };
  }, [navigate]);
  return null;
}
