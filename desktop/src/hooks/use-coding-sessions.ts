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

  return useMemo(() => ({ snapshot, refresh }), [snapshot, refresh]);
}
