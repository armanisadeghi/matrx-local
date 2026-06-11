/**
 * useServiceStatus — polls proxy, tunnel, and cloud sync status from the
 * Python engine.
 *
 * Only polls when the engine is connected. Interval: 15 seconds.
 * Provides cached status for the QuickActionBar indicators and a manual
 * cloud sync trigger.
 */

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { engine } from "@/lib/api";
import type { ProxyStatus } from "@/lib/api";
import type { EngineStatus } from "@/hooks/use-engine";

export interface TunnelStatus {
  running: boolean;
  url: string | null;
  ws_url: string | null;
  uptime_seconds: number;
  mode: string;
}

export interface CloudDebugState {
  is_configured: boolean;
  is_orphan: boolean;
  user_id: string | null;
  instance_id: string | null;
  configure_called_at: string | null;
  last_registration_at: string | null;
  last_registration_result: string | null;
  last_error: string | null;
  instance_name: string;
  supabase_url_configured: boolean;
  supabase_key_configured: boolean;
}

export type CloudSyncStatus =
  | "synced"
  | "not-configured"
  | "orphan"
  | "error"
  | "syncing"
  | "unknown";

export interface ServiceStatusState {
  proxy: ProxyStatus | null;
  tunnel: TunnelStatus | null;
  cloudDebug: CloudDebugState | null;
  cloudSyncStatus: CloudSyncStatus;
  cloudSyncing: boolean;
}

const POLL_INTERVAL = 15_000;

function deriveCloudStatus(
  debug: CloudDebugState | null,
  syncing: boolean,
): CloudSyncStatus {
  if (syncing) return "syncing";
  if (!debug) return "unknown";
  if (!debug.is_configured) return "not-configured";
  if (debug.is_orphan) return "orphan";
  if (debug.last_error) return "error";
  if (debug.last_registration_result === "ok") return "synced";
  if (debug.last_registration_result?.startsWith("error")) return "error";
  return "synced";
}

export function useServiceStatus(engineStatus: EngineStatus): [
  ServiceStatusState,
  { triggerCloudSync: () => Promise<void>; refresh: () => void },
] {
  const [state, setState] = useState<ServiceStatusState>({
    proxy: null,
    tunnel: null,
    cloudDebug: null,
    cloudSyncStatus: "unknown",
    cloudSyncing: false,
  });
  const mountedRef = useRef(true);

  const poll = useCallback(async () => {
    if (!engine.engineUrl) return;
    try {
      const [proxyRes, tunnelRes, cloudRes] = await Promise.allSettled([
        engine.proxyStatus(),
        engine.get("/tunnel/status") as Promise<TunnelStatus>,
        engine.get("/cloud/debug") as Promise<CloudDebugState>,
      ]);
      if (!mountedRef.current) return;
      setState((prev) => {
        const newProxy =
          proxyRes.status === "fulfilled" ? proxyRes.value : prev.proxy;
        const newTunnel =
          tunnelRes.status === "fulfilled" ? tunnelRes.value : prev.tunnel;
        const newCloud =
          cloudRes.status === "fulfilled" ? cloudRes.value : prev.cloudDebug;
        return {
          ...prev,
          proxy: newProxy,
          tunnel: newTunnel,
          cloudDebug: newCloud,
          cloudSyncStatus: deriveCloudStatus(newCloud, prev.cloudSyncing),
        };
      });
    } catch {
      /* non-critical */
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    if (engineStatus !== "connected") return;
    poll();
    const id = setInterval(poll, POLL_INTERVAL);
    return () => {
      mountedRef.current = false;
      clearInterval(id);
    };
  }, [engineStatus, poll]);

  const triggerCloudSync = useCallback(async () => {
    if (!engine.engineUrl) return;
    setState((prev) => ({
      ...prev,
      cloudSyncing: true,
      cloudSyncStatus: "syncing",
    }));
    try {
      await engine.triggerCloudSync();
      const debug = (await engine.get("/cloud/debug")) as CloudDebugState;
      setState((prev) => ({
        ...prev,
        cloudSyncing: false,
        cloudDebug: debug,
        cloudSyncStatus: deriveCloudStatus(debug, false),
      }));
    } catch {
      setState((prev) => ({
        ...prev,
        cloudSyncing: false,
        cloudSyncStatus: "error",
      }));
    }
  }, []);

  const refresh = useCallback(() => {
    poll();
  }, [poll]);

  // Stable actions object — see React Patterns in CLAUDE.md.
  const actions = useMemo(
    () => ({ triggerCloudSync, refresh }),
    [triggerCloudSync, refresh],
  );

  return [state, actions];
}
