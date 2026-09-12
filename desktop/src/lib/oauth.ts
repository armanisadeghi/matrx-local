/**
 * OAuth 2.1 PKCE helpers for the Matrx Local desktop app.
 *
 * This app is a registered OAuth 2.1 client against the AI Matrx Supabase
 * project (client ID: af37ec97-3e0c-423c-a205-3d6c5adc5645, type: public).
 *
 * Flow (Tauri production — all platforms):
 *   1. App generates PKCE code_verifier + code_challenge, stores verifier in
 *      local transaction storage, constructs the Supabase authorize URL with:
 *        redirect_uri = aimatrx://auth/callback
 *   2. shell.open() opens the URL in the system browser (NOT the webview).
 *   3. Supabase validates params and redirects the browser to the consent UI
 *      at https://www.aimatrx.com/oauth/consent?authorization_id=...
 *   4. User logs in and approves — Supabase sends the browser to:
 *        aimatrx://auth/callback?code=XXX&state=YYY
 *   5. The OS intercepts the aimatrx:// custom URI scheme registered by
 *      tauri-plugin-deep-link, calls the Rust on_open_url handler in lib.rs,
 *      which emits a Tauri event "oauth-callback" with the full URL string.
 *   6. OAuthPending.tsx receives the Tauri event and calls exchangeOAuthCode().
 *   7. We POST to the Supabase token endpoint with code + code_verifier.
 *   8. We call supabase.auth.setSession() so supabase-js is aware of the
 *      session (getSession(), onAuthStateChange(), etc. all work).
 *
 * Flow (web dev — localhost:1420):
 *   Steps 1-3 are the same but redirect_uri is http://localhost:1420/auth/callback.
 *   Steps 4-8 are handled in AuthCallback.tsx which reads ?code= from the URL
 *   query params and calls exchangeOAuthCode() directly.
 */

const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL as string;
const CLIENT_ID = "af37ec97-3e0c-423c-a205-3d6c5adc5645";

// Supabase OAuth 2.1 endpoints
export const OAUTH_AUTHORIZE_URL = `${SUPABASE_URL}/auth/v1/oauth/authorize`;
export const OAUTH_TOKEN_URL = `${SUPABASE_URL}/auth/v1/oauth/token`;

export { CLIENT_ID };

// ---------------------------------------------------------------------------
// PKCE helpers
// ---------------------------------------------------------------------------

function base64URLEncode(buffer: Uint8Array): string {
  return btoa(String.fromCharCode(...buffer))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=/g, "");
}

export function generateCodeVerifier(): string {
  const array = new Uint8Array(32);
  crypto.getRandomValues(array);
  return base64URLEncode(array);
}

export async function generateCodeChallenge(verifier: string): Promise<string> {
  const encoder = new TextEncoder();
  const data = encoder.encode(verifier);
  const hash = await crypto.subtle.digest("SHA-256", data);
  return base64URLEncode(new Uint8Array(hash));
}

// ---------------------------------------------------------------------------
// Persistent storage for PKCE state
//
// We use localStorage (not sessionStorage) because:
//   - In production Tauri, the OS may activate the app via deep link and
//     restart the process. sessionStorage is wiped on restart; localStorage
//     persists to disk across restarts.
//   - In web dev, the tab navigates away to Supabase and back. Most browsers
//     preserve sessionStorage across same-tab navigations to other origins,
//     but some configurations (privacy mode, certain browsers) do not.
//     localStorage is reliable in all cases.
//   - The verifier has no meaningful value after the code exchange completes —
//     we clear it immediately on success or failure. The only risk window is
//     the seconds between clicking "Sign in" and the code returning.
// ---------------------------------------------------------------------------

const TRANSACTION_KEY = "matrx_oauth_transaction";

export function saveOAuthState(verifier: string, state: string, redirectUri: string): void {
  localStorage.setItem(TRANSACTION_KEY, JSON.stringify({ verifier, state, redirectUri }));
}

export function clearOAuthState(): void {
  localStorage.removeItem(TRANSACTION_KEY);
  // Remove values left by an interrupted older build; never accept them.
  localStorage.removeItem("matrx_oauth_code_verifier");
  localStorage.removeItem("matrx_oauth_state");
  localStorage.removeItem(PENDING_KEY);
}

function pendingOAuthVerifier(state: string, redirectUri: string): string {
  let stored: unknown;
  try {
    stored = JSON.parse(localStorage.getItem(TRANSACTION_KEY) ?? "null");
  } catch {
    throw new Error("Sign-in session unavailable. Please start sign-in again.");
  }
  if (!stored || typeof stored !== "object" ||
      !("state" in stored) || stored.state !== state || !state ||
      !("redirectUri" in stored) || stored.redirectUri !== redirectUri ||
      !("verifier" in stored) || typeof stored.verifier !== "string" || !stored.verifier) {
    throw new Error("Sign-in callback does not match this session. Please start sign-in again.");
  }
  return stored.verifier;
}

export function isCurrentOAuthCallback(state: string, redirectUri: string): boolean {
  try { pendingOAuthVerifier(state, redirectUri); return true; }
  catch { return false; }
}

/** Claim one locally initiated transaction before the first network await. */
function takeOAuthVerifier(state: string, redirectUri: string): string {
  const verifier = pendingOAuthVerifier(state, redirectUri);
  localStorage.removeItem(TRANSACTION_KEY);
  return verifier;
}

// ---------------------------------------------------------------------------
// OAuth pending flag
//
// Set to "1" when shell.open() launches the system browser. Cleared on
// success, failure, or cancel. Persists across app backgrounding so that
// OAuthPending renders correctly when the OS re-activates the app after
// the user approves in the browser.
// ---------------------------------------------------------------------------

const PENDING_KEY = "matrx_oauth_pending";

export function setOAuthPending(): void {
  localStorage.setItem(PENDING_KEY, "1");
}

export function isOAuthPending(): boolean {
  return localStorage.getItem(PENDING_KEY) === "1";
}

export function clearOAuthPending(): void {
  localStorage.removeItem(PENDING_KEY);
}

// ---------------------------------------------------------------------------
// Authorization URL builder
// ---------------------------------------------------------------------------

export interface BuildAuthUrlOptions {
  redirectUri: string;
  scopes?: string[];
}

export interface AuthUrlResult {
  url: string;
  state: string;
  codeVerifier: string;
}

export async function buildOAuthAuthorizeUrl(
  opts: BuildAuthUrlOptions
): Promise<AuthUrlResult> {
  const codeVerifier = generateCodeVerifier();
  const codeChallenge = await generateCodeChallenge(codeVerifier);

  // Independent state binds the callback; the PKCE verifier stays local.
  const state = base64URLEncode(crypto.getRandomValues(new Uint8Array(32)));

  // Do NOT include "openid" — it requires asymmetric JWT signing keys (RS256/ES256)
  // which Supabase must be explicitly migrated to. With the default HS256 key,
  // requesting "openid" causes Supabase to return a 500 "Error generating ID token".
  // We only need email + profile to identify the user; the access_token itself is
  // a valid Supabase JWT that works with all APIs and RLS policies.
  const scope = (opts.scopes ?? ["email", "profile"]).join(" ");

  const params = new URLSearchParams({
    response_type: "code",
    client_id: CLIENT_ID,
    redirect_uri: opts.redirectUri,
    state,
    code_challenge: codeChallenge,
    code_challenge_method: "S256",
    scope,
  });

  return {
    url: `${OAUTH_AUTHORIZE_URL}?${params.toString()}`,
    state,
    codeVerifier,
  };
}

// ---------------------------------------------------------------------------
// Token exchange
// ---------------------------------------------------------------------------

export interface OAuthTokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export async function exchangeOAuthCode(
  code: string,
  returnedState: string,
  redirectUri: string
): Promise<OAuthTokens> {
  if (!code) throw new Error("Sign-in callback is missing its authorization code.");
  const codeVerifier = takeOAuthVerifier(returnedState, redirectUri);
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: CLIENT_ID,
    code,
    code_verifier: codeVerifier,
    redirect_uri: redirectUri,
  });

  const response = await fetch(OAUTH_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });

  if (!response.ok) {
    throw new Error(`Sign-in exchange failed (HTTP ${response.status}). Please try again.`);
  }

  let data: unknown;
  try { data = await response.json(); }
  catch { throw new Error("Sign-in service returned an invalid response. Please try again."); }
  if (!data || typeof data !== "object" ||
      !("access_token" in data) || typeof data.access_token !== "string" || !data.access_token ||
      !("refresh_token" in data) || typeof data.refresh_token !== "string" || !data.refresh_token ||
      !("expires_in" in data) || typeof data.expires_in !== "number" || !Number.isFinite(data.expires_in) || data.expires_in <= 0 ||
      !("token_type" in data) || typeof data.token_type !== "string") {
    throw new Error("Sign-in service returned an invalid response. Please try again.");
  }
  return { access_token: data.access_token, refresh_token: data.refresh_token,
    expires_in: data.expires_in, token_type: data.token_type };

}
