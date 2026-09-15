/**
 * What Refresh actually does, as a testable thing.
 *
 * "The refresh button, when you click it, does nothing. It just stares at you"
 * (Arman, 2026-09-14). The old page set its loading flag only from
 * `useState(true)`, so every later click ran a ~4.3s fetch with nothing on
 * screen changing. Two rules are enforced here instead of in a component:
 *
 *  1. A refresh announces itself BEFORE it awaits anything — `emit` is called
 *     synchronously with `refreshing: true`.
 *  2. A refresh never empties the screen. The rows already shown stay, the
 *     fast endpoints land first, and the slow session list replaces itself in
 *     place when it arrives.
 */

import type {
  ClaudeOverview,
  CodingSessionArtifactsSessionSummary,
  CodingSessionArtifactsStatus,
  CodingSessionBridgeStatus,
  CodingSessionProviderReadinessStatus,
} from "@/lib/api";
import {
  readCachedOverview,
  writeCachedOverview,
  type CachedOverview,
} from "@/lib/coding-sessions/overview-cache";

export interface CodingSessionsSources {
  overview(): Promise<ClaudeOverview>;
  bridgeStatus(): Promise<CodingSessionBridgeStatus>;
  readiness(): Promise<CodingSessionProviderReadinessStatus>;
  artifactsStatus(): Promise<CodingSessionArtifactsStatus>;
  artifactsSessions(): Promise<{ sessions: CodingSessionArtifactsSessionSummary[] }>;
}

export interface CodingSessionsSnapshot {
  overview: ClaudeOverview | null;
  /** When the rows on screen were read, so their age is never implied. */
  overviewAt: number | null;
  /** True while the rows on screen are the previous answer from this Mac. */
  overviewFromCache: boolean;
  /** True when the cache holds fewer rows than the list it came from. */
  overviewCacheTruncated: boolean;
  bridge: CodingSessionBridgeStatus | null;
  readiness: CodingSessionProviderReadinessStatus | null;
  artifacts: CodingSessionArtifactsStatus | null;
  artifactSessions: Map<string, CodingSessionArtifactsSessionSummary> | null;
  error: string | null;
  artifactsError: string | null;
  artifactSessionsError: string | null;
  cacheError: string | null;
  /** Anything in flight. The Refresh button spins on this. */
  refreshing: boolean;
  /** The slow session list specifically. Rows dim on this. */
  overviewPending: boolean;
  /** False until a real answer (not the cache) has landed at least once. */
  loadedFromEngine: boolean;
}

function reason(value: unknown): string {
  return value instanceof Error ? value.message : String(value);
}

export function emptySnapshot(cached: CachedOverview | null = null): CodingSessionsSnapshot {
  return {
    overview: cached?.overview ?? null,
    overviewAt: cached?.at ?? null,
    overviewFromCache: cached !== null,
    overviewCacheTruncated: cached?.truncated ?? false,
    bridge: null,
    readiness: null,
    artifacts: null,
    artifactSessions: null,
    error: null,
    artifactsError: null,
    artifactSessionsError: null,
    cacheError: null,
    refreshing: false,
    overviewPending: false,
    loadedFromEngine: false,
  };
}

export function initialSnapshot(): CodingSessionsSnapshot {
  return emptySnapshot(readCachedOverview());
}

export interface RefreshOptions {
  now?: () => number;
  persist?: (overview: ClaudeOverview, at: number) => string | null;
}

/**
 * Run one refresh, emitting every intermediate state. Resolves with the final
 * snapshot. Never throws: every source's failure becomes a visible message.
 */
export async function refreshCodingSessions(
  sources: CodingSessionsSources,
  current: CodingSessionsSnapshot,
  emit: (snapshot: CodingSessionsSnapshot) => void,
  options: RefreshOptions = {},
): Promise<CodingSessionsSnapshot> {
  const now = options.now ?? (() => Date.now());
  const persist = options.persist ?? writeCachedOverview;

  // RULE 1 — announce before awaiting. Nothing below this line may be the
  // first thing a person sees change.
  let snapshot: CodingSessionsSnapshot = {
    ...current,
    refreshing: true,
    overviewPending: true,
  };
  emit(snapshot);

  // The slow one starts first and is awaited last: the fast facts paint while
  // the disk index walk is still running.
  const overviewPromise = (async () => sources.overview())();

  const [status, ready, artifactStatus, artifactList] = await Promise.allSettled([
    sources.bridgeStatus(),
    sources.readiness(),
    sources.artifactsStatus(),
    sources.artifactsSessions(),
  ]);

  snapshot = { ...snapshot };
  if (status.status === "fulfilled") snapshot.bridge = status.value;
  if (ready.status === "fulfilled") snapshot.readiness = ready.value;
  if (artifactStatus.status === "fulfilled") {
    snapshot.artifacts = artifactStatus.value;
    snapshot.artifactsError = null;
  } else {
    snapshot.artifactsError = reason(artifactStatus.reason);
  }
  if (artifactList.status === "fulfilled") {
    snapshot.artifactSessions = new Map(
      artifactList.value.sessions.map((row) => [row.cli_session_id, row]),
    );
    snapshot.artifactSessionsError = null;
  } else {
    snapshot.artifactSessionsError = reason(artifactList.reason);
  }
  emit(snapshot);

  const overview = await overviewPromise.then(
    (value) => ({ ok: true as const, value }),
    (error: unknown) => ({ ok: false as const, error }),
  );

  snapshot = { ...snapshot, refreshing: false, overviewPending: false };
  if (overview.ok) {
    const at = now();
    snapshot.overview = overview.value;
    snapshot.overviewAt = at;
    snapshot.overviewFromCache = false;
    snapshot.overviewCacheTruncated = false;
    snapshot.loadedFromEngine = true;
    snapshot.error = null;
    snapshot.cacheError = persist(overview.value, at);
  } else {
    // The rows already on screen stay, and now carry a visible reason they
    // might be stale. Emptying the list would hide what we know.
    snapshot.error = reason(overview.error);
  }
  emit(snapshot);
  return snapshot;
}
