/**
 * The custody cutover's one-shot handover (CS-19).
 *
 * **Why this file exists.** Before FS-C5b the webview's Supabase client was built with its
 * defaults, so it persisted the whole session — refresh token included — into this window's
 * `localStorage`. The cutover made the sync daemon the device's only session holder, and the
 * daemon's journal is created by the release that introduced it: on the first start after the
 * upgrade it has never held a session and can only report `signed_out`. The engine's local
 * `auth_tokens` row, the other pre-cutover holder, is DROPPED by local-DB migration 34 without
 * being read. So on a Mac that was signed in yesterday, the only surviving credential is the
 * entry in this window's own storage — and nothing looked at it. That is how a person who was
 * signed in yesterday woke up signed out with no explanation (observed live on 1.4.124,
 * 2026-09-15).
 *
 * **What it does.** Once, on the first session read of the process, and only when the daemon
 * reports a device that has never held a session: find the legacy entry, hand the refresh token to
 * the daemon through the control-scope Tauri command, and re-read the session. The daemon presents
 * it on the ordinary refresh grant and takes custody; from that moment there is one holder again.
 *
 * **What it refuses to do.** It never keeps, logs, or forwards the token anywhere else, and it
 * deletes the legacy entry the moment the daemon says the offer is spent — a refresh token in
 * storage nobody reads is still a refresh token in storage. A transport failure is NOT spent: an
 * app opened on a plane must still be able to offer it tomorrow, so the entry stays and the next
 * start tries again.
 *
 * This is the only place in `desktop/src` that may read a credential, and it exists to get rid of
 * one. When every installed copy has been through it — every 1.4.124-or-earlier install upgraded —
 * this file and its command are deleted, not kept "just in case" (no-legacy).
 */

import { invoke } from "@tauri-apps/api/core";
import type { SessionSnapshot } from "@/lib/custodian";

/** What the daemon reports back. `spent` is false only for a retryable transport failure. */
export interface HandoverResult {
  readonly outcome: "adopted" | "sign_in_needed" | "retry" | "not_needed";
  readonly spent: boolean;
}

/** The pre-cutover session as supabase-js persisted it, narrowed to what we need. */
interface LegacyEntry {
  readonly key: string;
  readonly refreshToken: string | null;
  readonly email: string | null;
}

/** supabase-js's own storage-key shape. Matched rather than recomputed: the project ref it derives
 *  from the URL is its business, and a guess that missed would silently do nothing. */
const LEGACY_KEY = /^sb-.+-auth-token$/;

/** The seams, so the guard can drive this without a Tauri host or a browser. */
export interface HandoverDeps {
  readonly storage: Pick<Storage, "getItem" | "removeItem" | "key" | "length">;
  readonly adopt: (
    refreshToken: string | null,
    email: string | null,
  ) => Promise<HandoverResult>;
  readonly log: (message: string) => void;
}

function defaultDeps(): HandoverDeps | null {
  if (typeof window === "undefined") return null;
  let storage: Storage;
  try {
    storage = window.localStorage;
  } catch {
    // A window with storage denied never had a pre-cutover session to carry.
    return null;
  }
  return {
    storage,
    adopt: (refreshToken, email) =>
      invoke<HandoverResult>("syncd_adopt_legacy_session", { refreshToken, email }),
    log: (message) => console.info(`[custody-handover] ${message}`),
  };
}

/** Read the legacy entry, whichever of supabase-js's two encodings it used. */
export function findLegacySession(
  storage: Pick<Storage, "getItem" | "key" | "length">,
): LegacyEntry | null {
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (!key || !LEGACY_KEY.test(key)) continue;
    const raw = storage.getItem(key);
    if (!raw) continue;
    let text = raw;
    if (text.startsWith("base64-")) {
      try {
        text = atob(text.slice("base64-".length));
      } catch {
        // An entry we cannot decode is still evidence this Mac had a session: report the key with
        // no credential so the person is told, rather than left on a blank sign-in screen.
        return { key, refreshToken: null, email: null };
      }
    }
    try {
      const parsed = JSON.parse(text) as {
        refresh_token?: unknown;
        user?: { email?: unknown };
      };
      const refreshToken =
        typeof parsed.refresh_token === "string" && parsed.refresh_token.length > 0
          ? parsed.refresh_token
          : null;
      const email = typeof parsed.user?.email === "string" ? parsed.user.email : null;
      return { key, refreshToken, email };
    } catch {
      return { key, refreshToken: null, email: null };
    }
  }
  return null;
}

let done = false;

/**
 * Hand a pre-cutover session over if `snapshot` says one is owed, and return the state the daemon
 * reached — or `null` when nothing was done.
 *
 * It takes the snapshot the caller **already read** rather than reading one itself, deliberately:
 * the session-lifecycle seam publishes its first state on the very first microtask after
 * `GET /v1/session` resolves, and several shipped guards assert that timing. So the app's first
 * render is never delayed by this; a successful handover simply publishes a second state, through
 * the same path a sign-in uses.
 *
 * `read` is the daemon's `GET /v1/session`, called again after a handover so the caller renders
 * what the daemon actually reached, never a guess.
 */
export async function handOverLegacySession(
  snapshot: SessionSnapshot,
  read: () => Promise<SessionSnapshot>,
  deps: HandoverDeps | null = defaultDeps(),
): Promise<SessionSnapshot | null> {
  if (done || !deps) return null;
  // Only the never-signed-in device is a migration candidate. `sign_in_needed` means the daemon
  // has already decided (this handover, or a real session loss), and a signed-in or
  // deliberately signed-out device must never be migrated on top of.
  if (snapshot.signed_in || snapshot.state !== "signed_out" || snapshot.user_id !== null) {
    done = true;
    return null;
  }
  const legacy = findLegacySession(deps.storage);
  if (!legacy) {
    done = true;
    return null;
  }

  let result: HandoverResult;
  try {
    result = await deps.adopt(legacy.refreshToken, legacy.email);
  } catch (error) {
    // The daemon is not reachable yet. Nothing was consumed; say so and try again next start.
    deps.log(`could not offer this Mac's previous session to AI Matrx Sync yet: ${String(error)}`);
    return null;
  }

  if (result.spent) {
    done = true;
    try {
      deps.storage.removeItem(legacy.key);
    } catch {
      // Storage refused the delete. The daemon has custody either way; nothing here can use it.
    }
  }
  deps.log(
    result.outcome === "adopted"
      ? "carried this Mac's existing sign-in over to AI Matrx Sync; no sign-in was needed"
      : `this Mac's previous sign-in could not be carried over (${result.outcome})`,
  );
  // An unspent offer changed nothing, so there is nothing to republish.
  return result.spent ? read() : null;
}

/** Tests only: forget that the one-shot already ran. */
export function resetLegacyHandoverForTests(): void {
  done = false;
}
