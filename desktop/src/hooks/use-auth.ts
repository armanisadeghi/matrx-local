/**
 * Authentication — a **view onto the sync daemon's session** (FS-C5b, SPEC-CUSTODY D17/§10).
 *
 * This hook used to run the OAuth 2.1 PKCE transaction itself: it generated the verifier into
 * `localStorage`, exchanged the authorization code in the webview, and called
 * `supabase.auth.setSession()`, which persisted a **refresh token in webview localStorage**. That
 * was the device's second rotating credential holder, and nothing headless could renew it — the
 * MXL-D-046 class.
 *
 * Now: `POST /v1/sign-in` asks the daemon to open a transaction (the verifier is generated inside
 * it and never leaves), the system browser does the consent, the OS hands the callback to the
 * Rust host, and **the daemon exchanges the code**. This hook only renders what the daemon
 * reports and asks it to sign in or out. There is no verifier, no code, and no refresh token in
 * this process — `supabase.auth` throws by construction.
 *
 * Email/password sign-in is gone with it. It ran through `supabase.auth.signInWithPassword`, which
 * now throws, and the daemon's contract is PKCE only; leaving the form on screen would be a dead
 * control, which law 4 forbids as firmly as a lie. "Sign in with AI Matrx" reaches the same
 * accounts through the same provider.
 */

import { resetContentIr, warmContentIr } from "@/features/content-ir/runtime/registry";
import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import {
  currentSession,
  getToken,
  signIn as custodianSignIn,
  signOut as custodianSignOut,
  type MatrxSession,
  type MatrxUser,
  type SessionSnapshot,
} from "@/lib/custodian";
import { emitClientLog } from "@/hooks/use-client-log";
import {
  invalidateNativeVaultBeforeHostMutation,
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
  /** True between opening the system browser and the daemon reporting a session. */
  oauthPending: boolean;
  /** The daemon's full honest state — what a surface shows when there is no session. */
  snapshot: SessionSnapshot;
}

export function useAuth() {
  const [state, setState] = useState<AuthState>(() => ({
    user: null,
    session: null,
    isAuthenticated: false,
    loading: true,
    error: null,
    oauthPending: false,
    snapshot: currentSession(),
  }));

  const mountedRef = useRef(true);
  const isAuthenticatedRef = useRef(false);

  const update = useCallback((partial: Partial<AuthState>) => {
    if (mountedRef.current) {
      setState((prev) => {
        const next = { ...prev, ...partial };
        isAuthenticatedRef.current = next.isAuthenticated;
        return next;
      });
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;

    const unsubscribe = subscribeNativeVaultHostEvents(({ event, session, revision, completion, snapshot }) => {
      // Revoke all dependent use synchronously while the fence still holds. Native I/O stays
      // deferred below, exactly as it did under supabase-js.
      void completion.then(({ accepted }) => {
          if (!accepted) return;
          // A signed-out session has no actor to adopt, but its exact lifecycle revision still
          // settles the loading state. Signed-in sessions must additionally hold the actor-bound
          // adoption fence.
          if (session && nativeVaultAdoptedHostGeneration(session.user.id) !== revision) return;
          if (
            (event === "TOKEN_REFRESHED" || event === "SIGNED_IN") &&
            session !== null &&
            isAuthenticatedRef.current
          ) {
            update({ session, user: session.user, snapshot });
            return;
          }
          resetContentIr();
          if (session) warmContentIr();
          update({
            session,
            user: session?.user ?? null,
            isAuthenticated: !!session,
            loading: false,
            error: null,
            snapshot,
            // The browser round trip is over the moment the daemon reports ANY session change:
            // either it exchanged the code, or it told us why it could not.
            oauthPending: false,
          });
      }).catch((error) => {
          emitClientLog("warn", String(error), "auth");
          resetContentIr();
          update({
            session: null,
            user: null,
            isAuthenticated: false,
            loading: false,
            oauthPending: false,
            error: "Native Vault account check failed. Retry sign-in.",
          });
      });

      emitClientLog(
        session ? "success" : event === "INITIAL_SESSION" || event === "SIGNED_OUT" ? "info" : "warn",
        `Auth state: ${event}${session ? ` (${session.user.email ?? session.user.id})` : ""}`,
        "auth",
      );
    });

    return () => {
      mountedRef.current = false;
      unsubscribe();
    };
  }, [update]);

  /**
   * Ask the daemon to start a sign-in and open the system browser.
   *
   * The daemon returns the authorize URL; the verifier stays inside it. The callback comes back
   * through the OS to the Rust host, which forwards the code to the daemon — this webview never
   * sees it.
   */
  const signInWithOAuth = useCallback(async () => {
    emitClientLog("cmd", "OAuth sign-in initiated", "auth");
    update({ loading: true, error: null, oauthPending: true });
    try {
      await invalidateNativeVaultBeforeHostMutation();
      await custodianSignIn();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error("[auth] signInWithOAuth error:", message);
      update({ loading: false, oauthPending: false, error: message });
    }
  }, [update]);

  const cancelOAuth = useCallback(() => {
    // Only this screen is cancelled. The daemon's transaction expires on its own ten-minute TTL,
    // and a second sign-in cancels it — nothing here needs to reach across and do that.
    update({ loading: false, oauthPending: false, error: null });
  }, [update]);

  const signOut = useCallback(async () => {
    emitClientLog("cmd", "Sign-out initiated", "auth");
    update({ loading: true, error: null });
    let cleanupError: string | null = null;
    try {
      await invalidateNativeVaultBeforeHostMutation();
    } catch (err) {
      cleanupError = err instanceof Error ? err.message : String(err);
    }
    try {
      // The daemon wipes the keychain item, writes `signed_out` to this device's rows, and emits
      // `session.changed` — which is what actually updates this hook. It revokes nothing
      // server-side (S20): a grant is per account, not per device.
      await custodianSignOut();
      update({
        loading: false,
        oauthPending: false,
        session: null,
        user: null,
        isAuthenticated: false,
        error: cleanupError ? "Signed out, but engine account cleanup needs retry." : null,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error("[auth] signOut error:", message);
      update({ loading: false, error: message });
    }
  }, [update]);

  const getAccessToken = useCallback(async (): Promise<string | null> => getToken(), []);

  const retryAccountCleanup = useCallback(async () => {
    const subject = currentSession().user_id;
    if (!subject) return false;
    update({ loading: true, error: null });
    const alignment = await retryNativeVaultAccountCleanup(subject);
    const complete = alignment?.status === "aligned";
    update({ loading: false, error: complete ? null : "Account cleanup still needs retry." });
    return complete;
  }, [update]);

  return useMemo(
    () => ({
      ...state,
      signInWithOAuth,
      cancelOAuth,
      signOut,
      retryAccountCleanup,
      getAccessToken,
    }),
    [state, signInWithOAuth, cancelOAuth, signOut, retryAccountCleanup, getAccessToken],
  );
}
