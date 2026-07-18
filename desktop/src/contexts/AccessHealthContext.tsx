/**
 * AccessHealthContext — singleton provider for filesystem access-health state.
 *
 * Wraps use-access-health so every consumer (global banner, Documents page,
 * RecoveryCenter, Settings) shares ONE store with ONE poll owner. Without
 * this, the banner and the Documents prompt each ran their own poll with
 * separate unguarded state — the stale-response clobber behind the recurring
 * false "Full Disk Access" banner.
 */

import { createContext, useContext, type ReactNode } from "react";
import {
  useAccessHealth,
  type UseAccessHealthReturn,
} from "@/hooks/use-access-health";
import type { EngineStatus } from "@/hooks/use-engine";

const AccessHealthContext = createContext<UseAccessHealthReturn | null>(null);

export function AccessHealthProvider({
  engineStatus,
  children,
}: {
  engineStatus: EngineStatus;
  children: ReactNode;
}) {
  const value = useAccessHealth(engineStatus);
  return (
    <AccessHealthContext.Provider value={value}>
      {children}
    </AccessHealthContext.Provider>
  );
}

export function useAccessHealthContext(): UseAccessHealthReturn {
  const ctx = useContext(AccessHealthContext);
  if (!ctx) {
    throw new Error(
      "useAccessHealthContext must be used inside <AccessHealthProvider>",
    );
  }
  return ctx;
}

export function useOptionalAccessHealthContext(): UseAccessHealthReturn | null {
  return useContext(AccessHealthContext);
}
