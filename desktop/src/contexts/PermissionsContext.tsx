/**
 * PermissionsContext — singleton provider for macOS permissions state.
 *
 * This context wraps the usePermissions hook so that all consumers (Dashboard,
 * Voice, Devices, PermissionsModal, SetupWizard) share a single instance.
 * Without this, each consumer runs its own checkAll() on mount, causing:
 *   - Redundant permission checks (5x per mount cycle)
 *   - Inconsistent state between different parts of the UI
 *   - Risk of re-triggering macOS permission dialogs on repeated mounts
 */

import { createContext, useContext, type ReactNode } from "react";
import { usePermissions, type UsePermissionsReturn } from "@/hooks/use-permissions";

const PermissionsContext = createContext<UsePermissionsReturn | null>(null);

export function PermissionsProvider({ children }: { children: ReactNode }) {
  const value = usePermissions();
  return (
    <PermissionsContext.Provider value={value}>
      {children}
    </PermissionsContext.Provider>
  );
}

/**
 * usePermissionsContext — consume the shared permissions state.
 * Must be used inside <PermissionsProvider>.
 */
export function usePermissionsContext(): UsePermissionsReturn {
  const ctx = useContext(PermissionsContext);
  if (!ctx) {
    throw new Error("usePermissionsContext must be used inside <PermissionsProvider>");
  }
  return ctx;
}

export function useOptionalPermissionsContext(): UsePermissionsReturn | null {
  return useContext(PermissionsContext);
}
