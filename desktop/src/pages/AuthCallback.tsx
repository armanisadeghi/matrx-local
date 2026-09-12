import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { exchangeOAuthCode, clearOAuthState } from "@/lib/oauth";
import supabase from "@/lib/supabase";
import { Loader2, AlertTriangle } from "lucide-react";

/**
 * AuthCallback — handles the OAuth code exchange in the web dev browser.
 *
 * Flow:
 *   1. User clicks "Sign in with AI Matrx" in Matrx Local dev server
 *   2. Browser navigates to Supabase authorize endpoint
 *   3. User authenticates on aimatrx.com/oauth/consent and approves
 *   4. Supabase redirects browser to http://localhost:1420/auth/callback?code=XXX
 *   5. App.tsx module-level bridge detects /auth/callback path and does
 *      window.location.replace("/#/auth/callback?code=XXX")
 *   6. HashRouter renders this component
 *   7. We read the code from the hash, exchange it for tokens, set session
 *
 * In production Tauri this component is never rendered — the aimatrx://
 * deep link fires the Rust handler which emits a Tauri event that
 * OAuthPending.tsx handles instead.
 */
export function AuthCallback() {
  const navigate = useNavigate();
  const handled = useRef(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (handled.current) return;
    handled.current = true;

    async function handleWebCallback() {
      // ── Step 1: Extract the authorization code ──────────────────────────
      //
      // After the App.tsx bridge, the URL is:
      //   http://localhost:1420/#/auth/callback?code=XXX&state=YYY
      //
      // In this URL structure:
      //   window.location.pathname = "/"
      //   window.location.hash     = "#/auth/callback?code=XXX&state=YYY"
      //   window.location.search   = "" (empty — the ? is inside the hash)
      //
      // We must parse the query string from inside the hash fragment.
      const hash = window.location.hash; // "#/auth/callback?code=XXX..."
      const qIdx = hash.indexOf("?");
      const hashSearch = qIdx !== -1 ? hash.slice(qIdx) : "";
      const params = new URLSearchParams(hashSearch);
      const code = params.get("code");
      const state = params.get("state");

      window.history.replaceState({}, "", `${window.location.pathname}#/auth/callback`);
      if (!code || !state) {
        clearOAuthState();
        setErrorMsg("Sign-in callback is incomplete. Please start sign-in again.");
        setTimeout(() => navigate("/login", { replace: true }), 4000);
        return;
      }

      // ── Step 3: Exchange the code for tokens ─────────────────────────────
      try {
        console.log("[AuthCallback] exchanging code for tokens...");
        const tokens = await exchangeOAuthCode(
          code,
          state,
          "http://localhost:1420/auth/callback"
        );
        clearOAuthState();
        console.log("[AuthCallback] token exchange succeeded, storing session...");

        const { error } = await supabase.auth.setSession({
          access_token: tokens.access_token,
          refresh_token: tokens.refresh_token,
        });
        if (error) throw new Error("Could not establish your sign-in session. Please try again.");
        navigate("/", { replace: true });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.error("[AuthCallback] token exchange failed:", msg);
        clearOAuthState();
        setErrorMsg(`Token exchange failed: ${msg}`);
        setTimeout(() => navigate("/login", { replace: true }), 4000);
      }
    }

    handleWebCallback();
  }, [navigate]);

  if (errorMsg) {
    return (
      <div className="flex h-screen items-center justify-center bg-background px-6">
        <div className="flex flex-col items-center gap-4 max-w-sm text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
            <AlertTriangle className="h-6 w-6 text-destructive" />
          </div>
          <p className="text-sm font-medium text-foreground">Sign-in failed</p>
          <p className="text-xs text-muted-foreground">{errorMsg}</p>
          <p className="text-xs text-muted-foreground/60">Returning to login…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen items-center justify-center bg-background">
      <div className="flex flex-col items-center gap-4">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground">Completing sign in…</p>
      </div>
    </div>
  );
}
