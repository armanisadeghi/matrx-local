/**
 * The coding-sessions screen's data, in one place, for every tab.
 *
 * A thin React binding over `lib/coding-sessions/overview-store`, which owns
 * the rules (announce before awaiting; never empty the list) and carries the
 * tests. Per this repo's hook rules the fetch lives inside the hook and the
 * returned actions are memoised, so no consumer can turn them into an effect
 * dependency loop.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { engine } from "@/lib/api";
import { indexPollDelayMs } from "@/lib/coding-sessions/index-state";
import {
  initialSnapshot,
  pendingCodingSessionsSnapshot,
  refreshCodingSessions,
  withOverviewRetry,
  type CodingSessionsSnapshot,
  type CodingSessionsSources,
} from "@/lib/coding-sessions/overview-store";

const engineSources: CodingSessionsSources = {
  overview: () => engine.getCodingSessionsOverview(),
  bridgeStatus: () => engine.getCodingSessionStatus(),
  readiness: () => engine.getCodingSessionProviderReadiness(),
  artifactsStatus: () => engine.getCodingSessionArtifactsStatus(),
  artifactsSessions: () => engine.getCodingSessionArtifactsSessions(),
  labelStatus: () => engine.getClaudeLabelStatus(),
  onEngineConnected: (listener) =>
    typeof engine.on === "function" ? engine.on("connected", listener) : () => undefined,
};

// Match the existing cold-index cadence. A failed transport has no index
// report to supply its own delay, but it deserves the same single follow-up.
const OVERVIEW_RETRY_FALLBACK_MS = 2_500;

// Two tabs (and React's development replay) read the same engine. Keep one
// complete pass per source alive so they do not multiply the expensive local
// overview request; each consumer still announces that it joined that pass.
const sharedRefreshes = new WeakMap<
  CodingSessionsSources,
  Promise<CodingSessionsSnapshot>
>();

export interface CodingSessionsData {
  snapshot: CodingSessionsSnapshot;
  refresh: () => Promise<void>;
}

export function useCodingSessions(
  sources: CodingSessionsSources = engineSources,
): CodingSessionsData {
  const [snapshot, setSnapshot] = useState<CodingSessionsSnapshot>(initialSnapshot);
  const snapshotRef = useRef(snapshot);
  snapshotRef.current = snapshot;
  const mounted = useRef(true);
  const inFlight = useRef<Promise<void> | null>(null);
  const automaticRetryUsed = useRef(false);

  const publish = useCallback((next: CodingSessionsSnapshot) => {
    snapshotRef.current = next;
    if (mounted.current) setSnapshot(next);
  }, []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const runRefresh = useCallback(async (resetRetryBudget = false) => {
    if (resetRetryBudget) automaticRetryUsed.current = false;
    // A second click while the first pass is still running joins it instead of
    // stacking another 4-second read of the same index.
    if (inFlight.current) return inFlight.current;
    const joined = sharedRefreshes.get(sources);
    const refreshPass = joined ?? refreshCodingSessions(
      sources,
      snapshotRef.current,
      publish,
    );
    if (!joined) {
      sharedRefreshes.set(sources, refreshPass);
      void refreshPass.finally(() => {
        if (sharedRefreshes.get(sources) === refreshPass) {
          sharedRefreshes.delete(sources);
        }
      });
    } else {
      publish(pendingCodingSessionsSnapshot(snapshotRef.current));
    }
    const pass = refreshPass
      .then((next) => {
        if (joined) publish(next);
      })
      .then(() => undefined)
      .finally(() => {
        inFlight.current = null;
      });
    inFlight.current = pass;
    return pass;
  }, [publish, sources]);

  const refresh = useCallback(() => runRefresh(true), [runRefresh]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // A confirmed WebSocket reconnection is a new engine lifetime. Let it read
  // once now and restore the one transient-failure recovery budget; this is
  // deliberately a connectivity signal, not a retry of arbitrary errors.
  useEffect(() => {
    if (!sources.onEngineConnected) return;
    return sources.onEngineConnected(() => {
      void runRefresh(true);
    });
  }, [runRefresh, sources]);

  // A cold or refreshing index means the engine is still reading this Mac
  // BEHIND the answer we just got, so the screen asks again on its own rather
  // than leaving a person on "Reading your conversations…" with a Refresh
  // button as the only way forward. Gated on the engine's own state string
  // (never on an object identity) and cleared on unmount, per the repo's hook
  // rules — a cold index polls until it is fresh, a refreshing one re-reads
  // once and stops when the state changes.
  const indexState = snapshot.overview?.index?.state;
  const delay = indexPollDelayMs(snapshot.overview);
  // The read's own identity, so a second still-cold answer schedules a third
  // look. Without it the state string, the delay and the refreshing flag are
  // all unchanged between two cold answers, React re-runs nothing, and the
  // "poll until fresh" loop quietly stops after one pass.
  const readAt = snapshot.overviewAt;
  useEffect(() => {
    // A stale cold/refreshing answer must not turn an error into a second
    // polling policy. Only the transient-recovery effect below may retry it.
    if (delay === null || snapshot.refreshing || snapshot.overviewFailure || snapshot.error) return;
    const timer = window.setTimeout(() => {
      void runRefresh();
    }, delay);
    return () => window.clearTimeout(timer);
    // `indexState` is the gate; `delay` is derived from it.
  }, [indexState, delay, readAt, runRefresh, snapshot.error, snapshot.overviewFailure, snapshot.refreshing]);

  // A timeout or browser transport failure has no HTTP response to diagnose.
  // Recover once at the established index-poll cadence, then leave the cached
  // rows and an honest exhausted state. HTTP/auth/schema/server errors never
  // set `overviewFailure`, so they cannot enter this loop.
  const retryDelay = delay ?? OVERVIEW_RETRY_FALLBACK_MS;
  useEffect(() => {
    if (!snapshot.overviewFailure || snapshot.refreshing) return;
    if (snapshot.overviewRetry === "exhausted") return;

    if (snapshot.overviewRetry !== "scheduled") {
      if (automaticRetryUsed.current) {
        publish(withOverviewRetry(snapshot, "exhausted"));
        return;
      }
      automaticRetryUsed.current = true;
      publish(withOverviewRetry(snapshot, "scheduled"));
      return;
    }

    const timer = window.setTimeout(() => {
      void runRefresh();
    }, retryDelay);
    return () => window.clearTimeout(timer);
  }, [publish, retryDelay, runRefresh, snapshot]);

  return useMemo(() => ({ snapshot, refresh }), [snapshot, refresh]);
}
