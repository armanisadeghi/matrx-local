/**
 * The webview's client for the sync daemon (FS-C5b, SPEC-CUSTODY §6, rulings C1/C5/C7/C8/C9).
 *
 * The webview is a **token consumer holding only the `read` scope**. It can obtain short-lived
 * access tokens and read status and events — the same reach the signed-in user already has in the
 * app — and it can do nothing else: `POST /v1/sign-out`, `POST /v1/sign-in` and
 * `POST /v1/shutdown` are control scope, so a malicious page or an XSS in our own UI cannot sign
 * this device out, start a sign-in, or stop syncing. Signing in and out go through Tauri commands
 * where Rust holds the control token.
 *
 * There is **no refresh token in this process**. `supabase.auth` throws by construction (see
 * `lib/supabase.ts`), `localStorage` holds no session, and the only durable credential on the
 * machine is the daemon's keychain item. That is D17, and it is what makes MXL-D-046 —
 * a UI-pushed token nothing headless can renew — impossible rather than merely fixed.
 */

import { invoke } from "@tauri-apps/api/core";

import { devHarnessCustodyConfig } from "@/lib/dev-harness-custody";

/** The five session values of the ONE honest-state enum (ruling C3), plus the daemon's own. */
export type SessionState =
  | "signed_in"
  | "sign_in_needed"
  | "signed_out"
  | "credential_store_unavailable"
  | "offline"
  | "daemon_not_running";

/** The user, as this app actually uses one. Not a Supabase `User`: there is no Supabase session
 *  in this process to produce one, and a hand-built object wearing that type would be a lie.
 *
 *  `user_metadata` and `app_metadata` are read from the access token's own claims — the same
 *  place supabase-js read them from, and a token this process legitimately holds. They are
 *  optional because a surface may render before the first token is in hand; a surface that wants
 *  a name falls back to the email rather than showing a blank where a name was. */
export interface MatrxUser {
  readonly id: string;
  readonly email: string | null;
  readonly user_metadata?: MatrxUserMetadata | undefined;
  readonly app_metadata?: MatrxAppMetadata | undefined;
}

/** The display claims a surface actually reads. Named rather than `Record<string, unknown>` so a
 *  component that shows a name or an avatar gets a `string`, not an `unknown` it must cast. */
export interface MatrxUserMetadata {
  readonly avatar_url?: string;
  readonly full_name?: string;
  readonly name?: string;
  readonly user_name?: string;
  // Everything else a provider put in the claim. `string | undefined` rather than `unknown`
  // because every surface that reads one renders it as text; an `unknown` here would push a cast
  // into each of them, and a cast is where a wrong assumption hides.
  readonly [key: string]: string | undefined;
}

export interface MatrxAppMetadata {
  readonly provider?: string;
  readonly [key: string]: string | string[] | undefined;
}

/** Decode a JWT payload without verifying it. The daemon is not the verifier and neither are we —
 *  the server verifies on every call the token is used for. This reads display claims only. */
function claimsOf(token: string | null): Record<string, unknown> {
  if (!token) return {};
  const payload = token.split(".")[1];
  if (!payload) return {};
  try {
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(json) as Record<string, unknown>;
  } catch {
    return {};
  }
}

/** What replaces a Supabase `Session` here: an identity, never a credential. */
export interface MatrxSession {
  readonly user: MatrxUser;
}

/** `GET /v1/session` — what a surface renders, including when there is no session. */
export interface SessionSnapshot {
  readonly signed_in: boolean;
  readonly user_id: string | null;
  readonly email: string | null;
  readonly state: SessionState;
  readonly state_reason: string | null;
  readonly since: string | null;
  readonly next_attempt_at: string | null;
  readonly cloud_state_write_pending: boolean;
  /** What the person can DO about `state_reason`, when the reason alone is not actionable.
   *
   *  The daemon's own `/v1/session` does not send one — its states are session states, and their
   *  reasons already carry the action. It is the daemon-process states (below) that need it: "the
   *  sync helper cannot start on this computer" is not a remedy, "Update AI Matrx" is. */
  readonly remedy?: string | null;
  /** Whether **Start sync** can actually change this state.
   *
   *  Only the daemon-process states answer this. A helper that is missing from the build, one
   *  that cannot execute, and one already asked to step aside and refusing are not fixed by
   *  running the same ladder again — their remedy names the reinstall, the update or the
   *  restart, and the button is not drawn (law 4: a control is absent or honest, never dead). */
  readonly can_start_sync?: boolean;
}

/** `syncd_daemon_state` / `syncd_start` — the daemon PROCESS, classified by Rust. */
export interface DaemonState {
  readonly running: boolean;
  /** `running`, or one of `helper_missing`, `helper_cannot_execute`, `never_became_ready`,
   *  `unreachable`, `incompatible`, `upgrade_blocked`. */
  readonly code: string;
  readonly state_reason: string;
  readonly remedy: string | null;
  /** Whether Start sync is a control that can change this state. */
  readonly can_start_sync: boolean;
  readonly detail: string;
}

interface ClientConfig {
  base_url: string | null;
  read_token: string | null;
  world: string;
}

/** The daemon is not running, and we have not yet asked Rust WHY. A STATE with a remedy, never an
 *  empty render (law 4) — and the generic wording is replaced by the real reason the moment
 *  `syncd_daemon_state` answers. */
export const DAEMON_DOWN: SessionSnapshot = Object.freeze({
  signed_in: false,
  user_id: null,
  email: null,
  state: "daemon_not_running" as const,
  state_reason: "AI Matrx Sync is not running on this computer, so there is no signed-in session.",
  remedy: "Choose Start sync to start it.",
  can_start_sync: true,
  since: null,
  next_attempt_at: null,
  cloud_state_write_pending: false,
});

/** The daemon-down snapshot as currently known: generic until Rust says which of the five ways it
 *  is down, then the honest one. */
let daemonDown: SessionSnapshot = DAEMON_DOWN;
let daemonDownInFlight: Promise<SessionSnapshot> | null = null;

function daemonDownFrom(state: DaemonState): SessionSnapshot {
  return {
    ...DAEMON_DOWN,
    state_reason: state.state_reason,
    remedy: state.remedy,
    can_start_sync: state.can_start_sync,
  };
}

/** Ask Rust why sync is down. One call in flight at a time; never throws. */
export function refreshDaemonDown(): Promise<SessionSnapshot> {
  daemonDownInFlight ??= (async () => {
    try {
      const state = await invoke<DaemonState>("syncd_daemon_state");
      if (state && !state.running) daemonDown = daemonDownFrom(state);
    } catch {
      // The host could not be asked. The generic reason we already have stands; a screen that
      // knows less must never render less.
    }
    return daemonDown;
  })().finally(() => {
    daemonDownInFlight = null;
  });
  return daemonDownInFlight;
}

/** The honest daemon-down snapshot to render RIGHT NOW, with a refresh queued behind it. */
function daemonDownNow(): SessionSnapshot {
  void refreshDaemonDown();
  return daemonDown;
}

/**
 * **Start sync.** Runs the host's startup reconciliation again and answers with what happened.
 *
 * A control that cannot fail visibly is the defect this fixes: if the helper still cannot start,
 * the returned snapshot carries the new reason, and the screen says it.
 */
export async function startSync(): Promise<SessionSnapshot> {
  let state: DaemonState;
  try {
    state = await invoke<DaemonState>("syncd_start");
  } catch (error) {
    // The host itself could not be reached. A raw `TypeError` on a person's screen is its own
    // law-4 breach, so the detail travels inside a sentence that carries a remedy.
    daemonDown = {
      ...DAEMON_DOWN,
      state_reason: `AI Matrx could not ask this computer to start sync (${String(error)}).`,
      remedy: "Quit AI Matrx and open it again. If this keeps happening, report it from Settings → Support.",
      can_start_sync: false,
    };
    lastSnapshot = daemonDown;
    return lastSnapshot;
  }
  // Both move when a daemon restarts.
  resetCustodianConfig();
  forgetToken();
  if (!state.running) {
    daemonDown = daemonDownFrom(state);
    lastSnapshot = daemonDown;
    return lastSnapshot;
  }
  return getSession();
}

/** S11 — cache until 30 s before expiry, in memory, never persisted. */
const CACHE_MARGIN_MS = 30_000;

let configPromise: Promise<ClientConfig> | null = null;

function loadConfig(): Promise<ClientConfig> {
  // In the packaged app and in `pnpm tauri:dev` this is the ONE answer: Rust reads the discovery
  // file and hands over the read token.
  //
  // In a plain browser page there is no Tauri, so the call rejects — and before MXL-D-091 that
  // was the end of it: no endpoint, no token, no session, and therefore no screen of this app an
  // agent could verify. `devHarnessCustodyConfig` is the dev-server-only fallback that restores
  // it. It is behind `import.meta.env.DEV`, so it is dead code in every shipped bundle, and it
  // receives only the same read token the packaged webview holds.
  configPromise ??= invoke<ClientConfig>("syncd_client_config")
    .then((config) => {
      if (config.base_url && config.read_token) return config;
      configPromise = null;
      throw new Error("AI Matrx Sync is not ready yet.");
    })
    .catch(async (error) => {
      configPromise = null;
      const fallback = await devHarnessCustodyConfig();
      if (fallback.base_url && fallback.read_token) return fallback;
      throw error;
    });
  return configPromise;
}

/** Re-read the endpoint and token — after the daemon restarts, both change. */
export function resetCustodianConfig(): void {
  configPromise = null;
}

async function request(path: string, signal?: AbortSignal): Promise<Response | null> {
  let config: ClientConfig;
  try {
    config = await loadConfig();
  } catch {
    return null;
  }
  if (!config.base_url || !config.read_token) return null;
  try {
    return await fetch(`${config.base_url}${path}`, {
      headers: {
        Authorization: `Bearer ${config.read_token}`,
        // SPEC-ENGINE §3 demands it on every route; it is not a CORS-simple header, which is why
        // the daemon's Access-Control-Allow-Headers names it.
        "X-Matrx-Client": "app",
      },
      signal: signal ?? null,
    });
  } catch {
    // The daemon went away, or its port moved. Ask for the endpoint again next time rather than
    // holding a stale one forever.
    resetCustodianConfig();
    return null;
  }
}

// --------------------------------------------------------------------- token

let cachedToken: string | null = null;
let cachedUntil = 0;
let cachedSubject: string | null = null;
let tokenGeneration = 0;
let inFlight: Promise<string | null> | null = null;

/**
 * A short-lived access token, or `null` when there is no usable session.
 *
 * This is the function handed to supabase-js as its `accessToken` option, which its own docs warn
 * "may be called concurrently and many times. Use memoization and locking techniques" — hence the
 * cache and the single in-flight promise below.
 */
export async function getToken(): Promise<string | null> {
  if (cachedToken && Date.now() < cachedUntil) return cachedToken;
  if (!inFlight) {
    const operation = fetchToken(tokenGeneration).finally(() => {
      if (inFlight === operation) inFlight = null;
    });
    inFlight = operation;
  }
  return inFlight;
}

async function fetchToken(generation: number): Promise<string | null> {
  const response = await request("/v1/token");
  if (generation !== tokenGeneration) return null;
  if (!response) {
    lastSnapshot = daemonDownNow();
    cachedToken = null;
    return null;
  }
  if (response.status === 200) {
    const body = (await response.json()) as { access_token: string; expires_at: string; user_id: string };
    if (generation !== tokenGeneration) return null;
    // Pair identity and bearer from the same grant, never from separate stale caches.
    if (!body.user_id || claimsOf(body.access_token).sub !== body.user_id) return null;
    if (lastSnapshot.signed_in && lastSnapshot.user_id !== body.user_id) return null;
    cachedSubject = body.user_id;
    cachedToken = body.access_token;
    const expiresAt = Date.parse(body.expires_at);
    // An expiry we cannot read means "do not cache" — never "cache forever". MXL-D-046 was
    // exactly the opposite mistake.
    cachedUntil = Number.isFinite(expiresAt) ? expiresAt - CACHE_MARGIN_MS : 0;
    return cachedToken;
  }
  // 409 is the documented refusal and carries the state verbatim. A refusal is a STATE: nothing
  // local may be gated on a token (S13), so this returns null and never throws.
  cachedToken = null;
  if (response.status === 409) {
    const body = (await response.json()) as Partial<SessionSnapshot>;
    if (generation !== tokenGeneration) return null;
    lastSnapshot = {
      ...DAEMON_DOWN,
      remedy: null,
      state: (body.state as SessionState) ?? "signed_out",
      state_reason: body.state_reason ?? null,
      email: body.email ?? null,
      since: body.since ?? null,
    };
  }
  return null;
}

/** Drop the cached token — used when a consumer's 401 says ours is no longer good. */
export function forgetToken(): void {
  tokenGeneration += 1;
  cachedToken = null;
  cachedSubject = null;
  cachedUntil = 0;
  inFlight = null;
}

function acceptSnapshot(snapshot: SessionSnapshot, rotated = false): void {
  if (rotated || !snapshot.signed_in ||
      (cachedSubject !== null && cachedSubject !== snapshot.user_id) ||
      (lastSnapshot.user_id !== null && lastSnapshot.user_id !== snapshot.user_id)) {
    forgetToken();
  }
  lastSnapshot = snapshot;
}

// ------------------------------------------------------------------- session

let lastSnapshot: SessionSnapshot = DAEMON_DOWN;

/** The last snapshot seen, for a surface that must render without awaiting. */
export function currentSession(): SessionSnapshot {
  return lastSnapshot;
}

/** `GET /v1/session`. */
export async function getSession(): Promise<SessionSnapshot> {
  const generation = tokenGeneration;
  const response = await request("/v1/session");
  if (generation !== tokenGeneration) return lastSnapshot;
  if (!response || response.status !== 200) {
    lastSnapshot = daemonDownNow();
    return lastSnapshot;
  }
  const snapshot = (await response.json()) as SessionSnapshot;
  if (generation === tokenGeneration) acceptSnapshot(snapshot);
  return lastSnapshot;
}

/** The identity, or `null`. What used to be `session.user`. */
export function sessionToMatrx(snapshot: SessionSnapshot): MatrxSession | null {
  if (!snapshot.signed_in || !snapshot.user_id) return null;
  const claims = claimsOf(cachedToken);
  return {
    user: {
      id: snapshot.user_id,
      email: snapshot.email,
      user_metadata: claims.user_metadata as MatrxUserMetadata | undefined,
      app_metadata: claims.app_metadata as MatrxAppMetadata | undefined,
    },
  };
}

// -------------------------------------------------------------------- events

type Listener = (snapshot: SessionSnapshot, rotated: boolean) => void;

const listeners = new Set<Listener>();
let streamStarted = false;
let streamAbort: AbortController | null = null;

/**
 * Subscribe to `session.changed` (ruling C9) on the daemon's ONE event stream.
 *
 * Read with `fetch` + `ReadableStream`, never `EventSource` (ruling C8): `EventSource` can set no
 * request headers, so the token would have to travel in the URL — which is the thing the whole
 * design refuses.
 */
export function subscribeSession(listener: Listener): () => void {
  listeners.add(listener);
  startStream();
  return () => {
    listeners.delete(listener);
  };
}

function startStream(): void {
  if (streamStarted) return;
  streamStarted = true;
  void runStream();
}

async function runStream(): Promise<void> {
  // Reconnect forever with a bounded backoff: the daemon restarting is ordinary (an upgrade, a
  // "restart sync"), and a stream that gave up would leave every surface frozen on a stale state.
  let backoffMs = 1_000;
  for (;;) {
    streamAbort = new AbortController();
    const response = await request("/v1/events", streamAbort.signal);
    if (response?.body) {
      backoffMs = 1_000;
      try {
        await readStream(response.body);
      } catch {
        // A dropped stream is a reconnect, not an error to surface.
      }
    }
    await new Promise((resolve) => setTimeout(resolve, backoffMs));
    backoffMs = Math.min(backoffMs * 2, 30_000);
    // The port and token change when the daemon restarts.
    resetCustodianConfig();
  }
}

async function readStream(body: ReadableStream<Uint8Array>): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let split = buffer.indexOf("\n\n");
    while (split !== -1) {
      handleFrame(buffer.slice(0, split));
      buffer = buffer.slice(split + 2);
      split = buffer.indexOf("\n\n");
    }
  }
}

function handleFrame(frame: string): void {
  let event = "";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (event !== "session.changed" || !data) return;
  let parsed: { session: SessionSnapshot; rotated: boolean };
  try {
    parsed = JSON.parse(data) as { session: SessionSnapshot; rotated: boolean };
  } catch {
    return;
  }
  acceptSnapshot(parsed.session, parsed.rotated);
  for (const listener of listeners) {
    try {
      listener(parsed.session, parsed.rotated);
    } catch {
      /* one subscriber cannot block another */
    }
  }
}

/** Stop the stream. Used only by tests and by a full teardown. */
export function stopSessionStream(): void {
  streamAbort?.abort();
  streamStarted = false;
}

// ------------------------------------------------------- sign in and sign out

/**
 * Start a sign-in and open the system browser.
 *
 * The daemon generates the verifier and returns the URL; Rust makes the call because
 * `POST /v1/sign-in` is control scope. The webview never receives a verifier or a code.
 */
export async function signIn(): Promise<void> {
  const start = await invoke<{ authorize_url: string }>("syncd_sign_in");
  const { open } = await import("@tauri-apps/plugin-shell");
  await open(start.authorize_url);
}

/** Sign this device out. Control scope — Rust makes the call (S17, §12). */
export async function signOut(): Promise<void> {
  await invoke("syncd_sign_out");
  forgetToken();
  lastSnapshot = { ...DAEMON_DOWN, state: "signed_out", state_reason: null, remedy: null };
}

// ------------------------------------------------- the shape call sites read

/**
 * A token plus the identity that goes with it, or `null` when there is no session.
 *
 * This is deliberately the shape the 27 former `supabase.auth.getSession()` call sites already
 * destructured — `session.access_token`, `session.user.id` — so the cutover changed where the
 * value comes from and not what every caller does with it. It is **not** a Supabase `Session`:
 * there is no `refresh_token` field, because no refresh token exists in this process.
 */
export interface AuthedSession {
  readonly access_token: string;
  readonly user: MatrxUser;
}

export async function getAuthedSession(): Promise<AuthedSession | null> {
  const token = await getToken();
  if (!token) return null;
  const snapshot = lastSnapshot.user_id ? lastSnapshot : await getSession();
  if (!snapshot.signed_in || !snapshot.user_id) return null;
  const claims = claimsOf(token);
  if (claims.sub !== snapshot.user_id || cachedSubject !== snapshot.user_id || cachedToken !== token) return null;
  return {
    access_token: token,
    user: {
      id: snapshot.user_id,
      email: snapshot.email,
      user_metadata: claims.user_metadata as MatrxUserMetadata | undefined,
      app_metadata: claims.app_metadata as MatrxAppMetadata | undefined,
    },
  };
}
