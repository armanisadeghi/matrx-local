/**
 * React access to THE organization store (`active-org.ts`). One hook, one
 * snapshot: every component that shows or changes the organization reads
 * this, so the top-bar switcher, the headless-ask picker and every page
 * banner agree by construction.
 */

import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";

import {
  getActiveOrganizationSnapshot,
  listMemberOrganizations,
  resolveActiveOrganization,
  setActiveOrganization,
  subscribeActiveOrganization,
  type ActiveOrganizationSnapshot,
} from "./active-org";

export interface UseActiveOrganization extends ActiveOrganizationSnapshot {
  /** THE ONE WRITE — verified against membership, mirrored to the engine. */
  choose: (organizationId: string) => Promise<void>;
  /** Re-read memberships (after an invite, a rename, a removal). */
  reload: () => Promise<void>;
}

export function useActiveOrganization(options?: { load?: boolean }): UseActiveOrganization {
  const snapshot = useSyncExternalStore(
    subscribeActiveOrganization,
    getActiveOrganizationSnapshot,
    getActiveOrganizationSnapshot,
  );

  const reload = useCallback(async () => {
    try {
      await listMemberOrganizations();
      if (!getActiveOrganizationSnapshot().organization) await resolveActiveOrganization();
    } catch {
      // The store already carries `error`; the caller renders it.
    }
  }, []);

  const choose = useCallback(async (organizationId: string) => {
    await setActiveOrganization(organizationId);
  }, []);

  // Load memberships once per signed-in user when asked to. Gated on the
  // specific facts (a user, nothing loaded, not already loading) so it never
  // polls and never runs signed out.
  const wantLoad = options?.load !== false;
  const { userId, organizations, loading } = snapshot;
  useEffect(() => {
    if (!wantLoad || !userId || organizations !== null || loading) return;
    void reload();
  }, [wantLoad, userId, organizations, loading, reload]);

  // THE LOAD LADDER runs once per signed-in user when a window that shows the
  // organization mounts: the window opens on one and is never left empty. A
  // failed run keeps its error on the snapshot (an honest retry state) and is
  // not re-run in a loop — `reload` retries.
  const laddered = useRef<string | null>(null);
  useEffect(() => {
    if (!wantLoad || !userId) {
      laddered.current = null;
      return;
    }
    if (snapshot.organization || laddered.current === userId) return;
    laddered.current = userId;
    void resolveActiveOrganization().catch(() => {
      // The store already carries `error`; the switcher renders it.
    });
  }, [wantLoad, userId, snapshot.organization]);

  return { ...snapshot, choose, reload };
}
