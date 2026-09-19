import { resetContentIr, warmContentIr } from "@/features/content-ir/runtime/registry";
import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import {
  currentSession,
  getSession,
  signIn as custodianSignIn,
  signOut as custodianSignOut,
  startSync as custodianStartSync,
  sessionToMatrx,
  type MatrxSession,
  type MatrxUser,
  type SessionSnapshot,
} from "@/lib/custodian";
import { emitClientLog } from "@/hooks/use-client-log";
import {
  invalidateNativeVaultBeforeHostMutation,
  isNativeVaultHostRevisionCurrent,
  nativeVaultAdoptedHostGeneration,
  retryNativeVaultAccountCleanup,
  subscribeNativeVaultHostEvents,
} from "@/lib/native-vault-auth";

export interface AuthState {
  user: MatrxUser | null;
  session: MatrxSession | null;
  isAuthenticated: boolean;
  loading: boolean;
  error: string | null;
  /** Native reconciliation, cleanup, or session verification needs an explicit retry. */
  accountConnectionUnavailable: boolean;
  oauthPending: boolean;
  snapshot: SessionSnapshot;
}

export function useAuth() {
  const [state, setState] = useState<AuthState>(() => ({
    user: null, session: null, isAuthenticated: false, loading: true, error: null,
    accountConnectionUnavailable: false, oauthPending: false, snapshot: currentSession(),
  }));
  const mountedRef = useRef(true);
  const isAuthenticatedRef = useRef(false);
  const authenticatedSubjectRef = useRef<string | null>(null);
  const update = useCallback((partial: Partial<AuthState>) => {
    if (!mountedRef.current) return;
    setState((previous) => {
      const next = { ...previous, ...partial };
      isAuthenticatedRef.current = next.isAuthenticated;
      authenticatedSubjectRef.current = next.isAuthenticated ? next.user?.id ?? null : null;
      return next;
    });
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    const unsubscribe = subscribeNativeVaultHostEvents(({ event, session, revision, completion, snapshot }) => {
      // Do not leave prior-account cached surfaces visible while a daemon account switch settles.
      if (authenticatedSubjectRef.current && (session === null || authenticatedSubjectRef.current !== session.user.id)) {
        update({ isAuthenticated: false, loading: true, error: null, accountConnectionUnavailable: false });
      }
      void completion.then(({ accepted }) => {
        if (!accepted || (session && nativeVaultAdoptedHostGeneration(session.user.id) !== revision)) return;
        if ((event === "TOKEN_REFRESHED" || event === "SIGNED_IN") && session && isAuthenticatedRef.current) {
          update({ session, user: session.user, snapshot, error: null, accountConnectionUnavailable: false, oauthPending: false });
          return;
        }
        resetContentIr();
        if (session) warmContentIr();
        update({ session, user: session?.user ?? null, isAuthenticated: !!session, loading: false, error: null,
          accountConnectionUnavailable: false, oauthPending: false, snapshot });
      }).catch(async (error) => {
        emitClientLog("warn", String(error), "auth");
        if (!isNativeVaultHostRevisionCurrent(revision)) return;
        const currentSnapshot = await getSession();
        if (!isNativeVaultHostRevisionCurrent(revision)) return;
        const current = sessionToMatrx(currentSnapshot);
        // A daemon event for another account will publish its own envelope.
        if (session && !current) {
          resetContentIr();
          update({ session: null, user: null, isAuthenticated: false, loading: false, error: null,
            accountConnectionUnavailable: false, oauthPending: false, snapshot: currentSnapshot });
          return;
        }
        if ((current?.user.id ?? null) !== (session?.user.id ?? null)) return;
        resetContentIr();
        if (!current) {
          update({ session: null, user: null, isAuthenticated: false, loading: false,
            error: "Matrx Local could not finish account cleanup. Retry account connection.",
            accountConnectionUnavailable: true, oauthPending: false, snapshot: currentSnapshot });
          return;
        }
        update({ session: current, user: current.user, isAuthenticated: false, loading: false,
          error: "Your account is signed in, but its connection to Matrx Local is unavailable. Retry account connection.",
          accountConnectionUnavailable: true, oauthPending: false, snapshot: currentSnapshot });
      });
    });
    return () => { mountedRef.current = false; unsubscribe(); };
  }, [update]);

  const signInWithOAuth = useCallback(async () => {
    update({ loading: true, error: null, oauthPending: true, accountConnectionUnavailable: false });
    try { await invalidateNativeVaultBeforeHostMutation(); await custodianSignIn(); }
    catch (error) { update({ loading: false, oauthPending: false, error: String(error) }); }
  }, [update]);
  const cancelOAuth = useCallback(() => update({ loading: false, oauthPending: false, error: null }), [update]);
  const signOut = useCallback(async () => {
    update({ loading: true, error: null });
    let cleanupFailed = false;
    try { await invalidateNativeVaultBeforeHostMutation(); } catch { cleanupFailed = true; }
    try {
      await custodianSignOut();
      const snapshot = await getSession();
      update({ loading: false, oauthPending: false, session: null, user: null, isAuthenticated: false, snapshot,
        accountConnectionUnavailable: cleanupFailed,
        error: cleanupFailed ? "Matrx Local could not finish account cleanup. Retry account connection." : null });
    } catch (error) { update({ loading: false, error: String(error) }); }
  }, [update]);
  /** Start sync — the one control for a daemon that is not running. It answers with the new
   *  state, so a retry that fails replaces the reason on screen instead of doing nothing. */
  const startSync = useCallback(async () => {
    update({ loading: true, error: null });
    const snapshot = await custodianStartSync();
    // Identity stays the custody envelope's job: if sync came up signed in, the daemon's own
    // stream publishes it. This control owns the daemon's state and nothing else.
    update({ loading: false, snapshot });
    return snapshot;
  }, [update]);
  const retryAccountCleanup = useCallback(async () => {
    const snapshot = await getSession();
    const subject = snapshot.user_id;
    update({ loading: true, error: null, accountConnectionUnavailable: false });
    const recovered = await retryNativeVaultAccountCleanup(subject);
    if (!recovered) update({ loading: false, accountConnectionUnavailable: true,
      error: subject ? "Your account is signed in, but its connection to Matrx Local is unavailable. Retry account connection." : "Matrx Local could not finish account cleanup. Retry account connection." });
    return recovered;
  }, [update]);
  return useMemo(() => ({ ...state, signInWithOAuth, cancelOAuth, signOut, startSync, retryAccountCleanup }),
    [state, signInWithOAuth, cancelOAuth, signOut, startSync, retryAccountCleanup]);
}
