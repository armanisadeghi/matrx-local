/**
 * Authentication hook — OAuth 2.1 PKCE flow via registered client.
 *
 * This app is registered as an OAuth 2.1 *client* (client ID:
 * af37ec97-3e0c-423c-a205-3d6c5adc5645) against the AI Matrx Supabase
 * project, which acts as the authorization server.
 *
 * WHY NOT supabase.auth.signInWithOAuth()?
 * ----------------------------------------
 * supabase.auth.signInWithOAuth() is for direct social provider login (Google,
 * GitHub, etc.) where Supabase is the identity provider for THIS app. That
 * approach routes through provider credentials baked into Supabase — it is not
 * the "Sign in with AI Matrx" flow and cannot be shipped cleanly.
 *
 * The correct approach: this app uses the standard OAuth 2.1 authorization
 * code flow with PKCE. The user is sent to our consent UI at aimatrx.com,
 * where they authenticate however they choose (Google, email, etc.). Supabase
 * then issues tokens that our app exchanges directly at the token endpoint.
 *
 * Redirect URI selection:
 * ─────────────────────────────────────────────────────────────────────────
 *  DEV  (import.meta.env.DEV === true, any platform):
 *    redirect_uri = http://localhost:1420/auth/callback
 *    How it arrives: Supabase redirects the browser tab back to the Vite
 *    server. React Router renders <AuthCallback /> which reads ?code= and
 *    calls completeOAuthExchange().
 *
 *  PRODUCTION Tauri (all platforms — macOS, Linux, Windows):
 *    redirect_uri = aimatrx://auth/callback
 *    How it arrives: The OS intercepts the custom URI scheme registered by
 *    tauri-plugin-deep-link (scheme: "aimatrx" in tauri.conf.json). Rust
 *    calls app.deep_link().on_open_url(), brings the window to front, and
 *    emits a Tauri event "oauth-callback" with the full URL string to the
 *    webview. OAuthPending.tsx listens for that event and calls
 *    completeOAuthExchange().
 *
 * Registered redirect URIs on OAuth client af37ec97-...:
 *   - http://localhost:1420/auth/callback   (Vite dev)
 *   - aimatrx://auth/callback              (Tauri production, all platforms)
 *   (keep http://localhost:22140/auth/callback for fallback/testing)
 *
 * Email / password still uses supabase.auth.signInWithPassword() directly —
 * that flow does not go through OAuth.
 */

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import type { User, Session } from "@supabase/supabase-js";
import supabase from "@/lib/supabase";
import {
  buildOAuthAuthorizeUrl,
  exchangeOAuthCode,
  saveOAuthState,
  clearOAuthState,
  setOAuthPending,
  clearOAuthPending,
  isOAuthPending,
} from "@/lib/oauth";
import { emitClientLog } from "@/hooks/use-client-log";

export interface AuthState {
  user: User | null;
  session: Session | null;
  isAuthenticated: boolean;
  loading: boolean;
  error: string | null;
  oauthPending: boolean;
}

/**
 * Determine the correct redirect URI for the current environment.
 *
 * DEV:        Vite dev server at localhost:1420 — browser tab navigates back.
 * Production: aimatrx:// custom scheme — OS deep link → Tauri event.
 *
 * The aimatrx:// scheme works identically on macOS, Linux, and Windows because
 * tauri-plugin-deep-link handles the OS-level registration for all three.
 *
 * NOTE: We do NOT use a web intermediary (aimatrx.com/oauth/callback) because
 * Windows browsers (Chrome/Edge) silently block custom URI scheme navigations
 * triggered by server redirects with no user gesture. Using aimatrx:// directly
 * as the Supabase redirect_uri bypasses the browser entirely — the OS intercepts
 * the URL before any browser policy can block it.
 */
function getRedirectUri(): string {
  if (import.meta.env.DEV) {
    return "http://localhost:1420/auth/callback";
  }
  return "aimatrx://auth/callback";
}

export function useAuth() {
  const [state, setState] = useState<AuthState>({
    user: null,
    session: null,
    isAuthenticated: false,
    loading: true,
    error: null,
    // Restore from localStorage — if the user had launched OAuth and the app
    // was backgrounded, this is still true when the app is re-activated.
    oauthPending: isOAuthPending(),
  });

  const mountedRef = useRef(true);
  // Mirrors isAuthenticated for use inside event-listener closures without
  // stale capture — updated in sync with setState.
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

    supabase.auth.getSession().then(({ data: { session } }) => {
      if (session) {
        emitClientLog("success", `Auth: session restored for ${session.user.email ?? session.user.id}`, "auth");
      } else {
        emitClientLog("info", "Auth: no active session — login required", "auth");
      }
      update({
        session,
        user: session?.user ?? null,
        isAuthenticated: !!session,
        loading: false,
      });
    });

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((event, session) => {
      // INITIAL_SESSION with no session is the normal signed-out boot state,
      // and SIGNED_OUT is a user action — neither is degraded functionality.
      emitClientLog(
        session ? "success" : event === "INITIAL_SESSION" || event === "SIGNED_OUT" ? "info" : "warn",
        `Auth state: ${event}${session ? ` (${session.user.email ?? session.user.id})` : ""}`,
        "auth",
      );

      // TOKEN_REFRESHED fires on silent background refresh.
      // SIGNED_IN fires on both real logins AND on window refocus when Supabase
      // refreshes an expiring token — we must distinguish the two cases.
      //
      // If we are already authenticated, this is a token refresh: update the
      // session/user objects so downstream callers have a fresh JWT, but do NOT
      // re-set isAuthenticated (that would re-trigger useEngine's initialize()
      // and restart the engine every time the user switches windows).
      //
      // If we are NOT yet authenticated, this is a genuine first sign-in: fall
      // through to the full update so isAuthenticated flips to true and the
      // engine initializes.
      if (
        (event === "TOKEN_REFRESHED" || event === "SIGNED_IN") &&
        session !== null &&
        isAuthenticatedRef.current
      ) {
        update({ session, user: session.user });
        return;
      }

      update({
        session,
        user: session?.user ?? null,
        isAuthenticated: !!session,
        loading: false,
        error: null,
      });
    });

    return () => {
      mountedRef.current = false;
      subscription.unsubscribe();
    };
  }, [update]);

  /**
   * Initiate OAuth 2.1 authorization code flow (PKCE).
   *
   * In DEV: navigates the browser tab to the authorization URL. After approval,
   * Supabase redirects back to localhost:1420/auth/callback where AuthCallback
   * reads ?code= and finishes the exchange.
   *
   * In production Tauri: opens the system browser with shell.open(). The
   * webview stays on OAuthPending which listens for the Tauri "oauth-callback"
   * event emitted by the Rust deep-link handler.
   */
  const signInWithOAuth = useCallback(async () => {
    emitClientLog("cmd", "OAuth sign-in initiated", "auth");
    update({ loading: true, error: null });

    const isInTauri =
      typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

    const redirectUri = getRedirectUri();

    try {
      const { url, state: oauthState, codeVerifier } =
        await buildOAuthAuthorizeUrl({ redirectUri });

      // Persist verifier + state so the callback can complete the exchange.
      // Uses localStorage so it survives app backgrounding and potential restart.
      saveOAuthState(codeVerifier, oauthState);

      if (isInTauri) {
        // Mark OAuth as in-flight BEFORE opening the browser. This persists
        // to localStorage so OAuthPending renders correctly when the OS
        // re-activates the app window after the user approves in the browser.
        setOAuthPending();
        update({ oauthPending: true });

        // Open the authorization URL in the system browser.
        try {
          const { open } = await import("@tauri-apps/plugin-shell");
          await open(url);
        } catch (shellErr) {
          console.error("[auth] shell.open failed:", shellErr);
          window.open(url, "_blank");
        }
        // loading remains true — OAuthPending clears it on success/cancel.
      } else {
        // Web dev: navigate the tab directly to the authorization URL.
        window.location.href = url;
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error("[auth] signInWithOAuth error:", message);
      update({ loading: false, error: message });
    }
  }, [update]);

  /**
   * Complete an OAuth 2.1 code exchange.
   *
   * Called by:
   *   - OAuthPending.tsx (Tauri path): receives the code from the Tauri
   *     "oauth-callback" deep-link event.
   *   - AuthCallback.tsx (web dev path): reads the code from the URL query
   *     params after the browser tab is redirected back.
   *
   * The redirectUri passed here MUST exactly match what was sent in the
   * authorization request (stored in localStorage) and what is registered
   * on the OAuth client.
   *
   * Returns true on success.
   */
  const completeOAuthExchange = useCallback(
    async (code: string, redirectUri: string): Promise<boolean> => {
      const stored = localStorage.getItem("matrx_oauth_code_verifier");
      if (!stored) {
        console.error("[auth] completeOAuthExchange: no code_verifier in localStorage");
        update({
          loading: false,
          error: "OAuth session expired. Please try signing in again.",
        });
        return false;
      }

      try {
        const tokens = await exchangeOAuthCode(code, stored, redirectUri);
        clearOAuthState();
        clearOAuthPending();

        // Inject tokens into supabase-js so getSession(), onAuthStateChange(),
        // and all other supabase-js APIs work correctly going forward.
        const { error } = await supabase.auth.setSession({
          access_token: tokens.access_token,
          refresh_token: tokens.refresh_token,
        });

        if (error) {
          console.error("[auth] setSession error:", error.message);
          update({ loading: false, oauthPending: false, error: error.message });
          return false;
        }

        // onAuthStateChange fires automatically and updates state.
        update({ oauthPending: false });
        return true;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        console.error("[auth] completeOAuthExchange error:", message);
        clearOAuthState();
        clearOAuthPending();
        update({ loading: false, oauthPending: false, error: message });
        return false;
      }
    },
    [update]
  );

  const signInWithEmail = useCallback(
    async (email: string, password: string) => {
      update({ loading: true, error: null });

      const { error } = await supabase.auth.signInWithPassword({
        email,
        password,
      });

      if (error) {
        update({ loading: false, error: error.message });
      }
    },
    [update]
  );

  const cancelOAuth = useCallback(() => {
    clearOAuthState();
    clearOAuthPending();
    update({ loading: false, oauthPending: false, error: null });
  }, [update]);

  const signOut = useCallback(async () => {
    emitClientLog("cmd", "Sign-out initiated", "auth");
    update({ loading: true, error: null });
    try {
      const result = await Promise.race([
        supabase.auth.signOut(),
        new Promise<{ error: { message: string } }>((resolve) =>
          setTimeout(
            () => resolve({ error: { message: "Sign-out timed out" } }),
            5000
          )
        ),
      ]);
      if (result.error) {
        console.warn("[signOut]", result.error.message);
      }
    } catch (err) {
      console.warn("[signOut] unexpected error:", err);
    } finally {
      clearOAuthState();
      clearOAuthPending();
      // Best-effort: drain any stale Rust PendingOAuthUrl so the next login
      // attempt doesn't pick up a code from this session.
      try {
        const { invoke } = await import("@tauri-apps/api/core");
        await invoke("get_pending_oauth_url"); // drain — result intentionally ignored
      } catch {
        // Not in Tauri (web dev) or invoke unavailable — safe to ignore
      }
      update({
        loading: false,
        oauthPending: false,
        session: null,
        user: null,
        isAuthenticated: false,
        error: null,
      });
    }
  }, [update]);

  const getAccessToken = useCallback(async (): Promise<string | null> => {
    const {
      data: { session },
    } = await supabase.auth.getSession();
    return session?.access_token ?? null;
  }, []);

  return useMemo(
    () => ({
      ...state,
      signInWithOAuth,
      completeOAuthExchange,
      cancelOAuth,
      signInWithEmail,
      signOut,
      getAccessToken,
    }),
    [state, signInWithOAuth, completeOAuthExchange, cancelOAuth, signInWithEmail, signOut, getAccessToken],
  );
}
