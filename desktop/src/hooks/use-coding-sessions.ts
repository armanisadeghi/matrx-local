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
  refreshCodingSessions,
  type CodingSessionsSnapshot,
  type CodingSessionsSources,
} from "@/lib/coding-sessions/overview-store";

const engineSources: CodingSessionsSources = {
  overview: () => engine.getClaudeOverview(),
  bridgeStatus: () => engine.getCodingSessionStatus(),
  readiness: () => engine.getCodingSessionProviderReadiness(),
  artifactsStatus: () => engine.getCodingSessionArtifactsStatus(),
  artifactsSessions: () => engine.getCodingSessionArtifactsSessions(),
};

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

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    // A second click while the first pass is still running joins it instead of
    // stacking another 4-second read of the same index.
    if (inFlight.current) return inFlight.current;
    const pass = refreshCodingSessions(sources, snapshotRef.current, (next) => {
      snapshotRef.current = next;
      if (mounted.current) setSnapshot(next);
    })
      .then(() => undefined)
      .finally(() => {
        inFlight.current = null;
      });
    inFlight.current = pass;
    return pass;
  }, [sources]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

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
    if (delay === null || snapshot.refreshing) return;
    const timer = window.setTimeout(() => {
      void refresh();
    }, delay);
    return () => window.clearTimeout(timer);
    // `indexState` is the gate; `delay` is derived from it.
  }, [indexState, delay, readAt, snapshot.refreshing, refresh]);

  return useMemo(() => ({ snapshot, refresh }), [snapshot, refresh]);
}
