/**
 * useAppConfigStatus — polls the remote app-config provenance from the
 * engine's GET /health (`app_config`: {tier, fetched_at, update_required,
 * notice}).
 *
 * Powers the version gate (update-required banner) and the operator notice
 * banner. Only polls while the engine is connected; interval 60s so an
 * operator-pushed notice / raised version floor lands within a minute of the
 * engine applying it (the engine itself refreshes remote config on its own
 * schedule).
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { engine, type AppConfigStatus } from "@/lib/api";
import type { EngineStatus } from "@/hooks/use-engine";

const POLL_INTERVAL = 60_000;

export function useAppConfigStatus(engineStatus: EngineStatus): AppConfigStatus | null {
  const [status, setStatus] = useState<AppConfigStatus | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const health = await engine.getHealth();
      if (mountedRef.current) {
        setStatus(health.app_config ?? null);
      }
    } catch {
      // Engine unreachable or older engine without app_config — keep the
      // last-known status; the EngineDownBanner owns the outage story.
    }
  }, []);

  useEffect(() => {
    if (engineStatus !== "connected") return;
    void refresh();
    const id = window.setInterval(() => void refresh(), POLL_INTERVAL);
    return () => window.clearInterval(id);
  }, [engineStatus, refresh]);

  return status;
}
