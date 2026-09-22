/**
 * THE organization store — the ONE place in this app that holds "which
 * organization is this device acting in".
 *
 * There is exactly one state value (`getActiveOrganizationSnapshot()`, read
 * reactively through `useActiveOrganization()`), exactly one persistent
 * value (`localStorage[STORAGE_KEY]`, the last organization each signed-in
 * user set on this device), and exactly one selector that changes it
 * (`features/org/OrganizationSwitcher`, mounted in the top bar; the modal
 * `OrganizationPickerDialog` is the same list raised when a request is
 * HELD). Every request boundary — the aidream client, Cloud Chat, the agent
 * catalog, Google Workspace, the Vault, the coding-session lanes — reads this
 * store and nothing else. The Python sidecar and the server-side
 * coding-session filing organization are MIRRORS of this value, pushed on
 * every SET; they never resolve one on their own while this device has one.
 *
 * ## Why this exists
 *
 * aidream's AuthMiddleware admits an authenticated request only when it
 * carries a VERIFIED organization (`X-Organization-Id`), and it refuses to
 * pick one for the caller — a server that guesses is exactly how work lands
 * in the wrong tenant. `GET /auth/whoami` cannot answer "which organization
 * does this client carry" either. The CLIENT is the side that knows which
 * organization the user chose; the server only verifies membership.
 *
 * ## The ruling this file enforces (Arman, 2026-09-19)
 *
 * A saved "default organization" on the user's ACCOUNT is at most a
 * per-client display preference. NOTHING that builds a request may read it,
 * and nothing may fall back to the personal organization.
 *
 *     "one missed org check that should have just failed turns into 50 in a
 *     month and 5,000 in a year, and suddenly we don't have orgs any more,
 *     we have a user and a default org, which means we just have user now."
 *
 * What this device MAY remember is the organization the USER THEMSELVES SET
 * here — the switcher's state, not a preference read out of an account.
 *
 * ## Resolution order
 *
 *   1. THIS DEVICE'S SET organization for the signed-in user — IF they are
 *      still a member.
 *   2. Exactly ONE membership -> that organization (there is nothing to
 *      choose, so choosing it invents nothing).
 *   3. Otherwise `null`, ON PURPOSE — and `null` at a request boundary is a
 *      HOLD, not a failure: `requireActiveOrganizationId()` raises the
 *      picker, waits for the user to SET one, and the request then proceeds.
 *      Never "first", "personal", "most recent", or "system".
 *
 * The Python sidecar cannot read this device's `localStorage`, so every SET
 * is also pushed to the engine (`PUT /organization/active`) — that is how a
 * background job on this Mac knows what the user chose instead of guessing.
 * The engine in turn answers the server's coding-session filing question
 * with the same value, so one choice here is the only choice anywhere.
 */

import supabase from "@/lib/supabase";
import { currentSession, getAuthedSession } from "@/lib/custodian";
import { getAppRuntimeConfig } from "@/lib/app-config";

/**
 * THE persistent value. One key; the value is the last organization each
 * user set on this device, so an account switch never inherits another
 * account's pick and a returning account finds its own again.
 */
export const STORAGE_KEY = "matrx-local.active-organization.v2";
/** The pre-2026-09-21 key: one unscoped `{id, name}`. Read once, migrated. */
const LEGACY_STORAGE_KEY = "matrx-local.active-organization.v1";
const CHANGE_EVENT = "matrx-local.active-organization.change";

export interface MemberOrganization {
  id: string;
  name: string;
  isPersonal: boolean;
}

interface StoredOrganizations {
  users: Record<string, MemberOrganization>;
}

/** THE state value. Referentially stable between changes. */
export interface ActiveOrganizationSnapshot {
  /** The organization this device is set to for the signed-in user, or null. */
  organization: MemberOrganization | null;
  /** Every organization the signed-in user can act in; null until loaded. */
  organizations: MemberOrganization[] | null;
  /** True while a membership read is in flight. */
  loading: boolean;
  /** The last membership-read failure, verbatim, or null. */
  error: string | null;
  /** The user the snapshot belongs to, or null when signed out. */
  userId: string | null;
}

/**
 * Thrown when an organization-scoped operation runs with no organization
 * selected. Carries a remedy the UI shows verbatim — a screen never says
 * "something went wrong" when the fix is one click.
 */
export class OrganizationNotSelectedError extends Error {
  readonly code = "organization_not_selected";
  /** Plain-language remedy for the user. */
  readonly remedy = "Choose your organization, then try again.";

  constructor(message = "No organization is selected for this device.") {
    super(message);
    this.name = "OrganizationNotSelectedError";
  }
}

/** True when `err` is the no-organization-selected failure. */
export function isOrganizationNotSelectedError(
  err: unknown,
): err is OrganizationNotSelectedError {
  return err instanceof OrganizationNotSelectedError;
}

/**
 * Thrown when a held request settles because the signed-in user belongs to NO
 * organization at all — there is nothing the picker can ever produce, so
 * holding for the full timeout would just be a wait for someone the picker
 * cannot help. Distinct from {@link OrganizationNotSelectedError} ("you have
 * organizations but never chose one"): this one names the real remedy —
 * create or join an organization — with the link the app already has for it.
 */
export class OrganizationNoMembershipsError extends Error {
  readonly code = "organization_no_memberships";
  readonly remedy = `You do not belong to any organization yet. Create or join one at ${getAppRuntimeConfig().webAppOrigin}/organizations, then try again.`;

  constructor(message = "You do not belong to any organization yet.") {
    super(message);
    this.name = "OrganizationNoMembershipsError";
  }
}

/** True when `err` is the no-memberships-at-all failure. */
export function isOrganizationNoMembershipsError(
  err: unknown,
): err is OrganizationNoMembershipsError {
  return err instanceof OrganizationNoMembershipsError;
}

// ---------------------------------------------------------------------------
// The store
// ---------------------------------------------------------------------------

const listeners = new Set<() => void>();

let snapshot: ActiveOrganizationSnapshot = {
  organization: null,
  organizations: null,
  loading: false,
  error: null,
  userId: null,
};

function knownUserId(): string | null {
  try {
    const session = currentSession();
    return session.signed_in && session.user_id ? session.user_id : null;
  } catch {
    return null;
  }
}

function emit(): void {
  for (const listener of [...listeners]) listener();
  try {
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
  } catch {
    // No window (test context without one) — the store listeners already ran.
  }
}

function patch(next: Partial<ActiveOrganizationSnapshot>): void {
  snapshot = { ...snapshot, ...next };
  emit();
}

/** Subscribe to the ONE state value (for `useSyncExternalStore`). */
export function subscribeActiveOrganization(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * The ONE state value, synchronously. Re-reads the persisted value for the
 * currently signed-in user so a sign-in / account switch that happened since
 * the last emit is reflected without anyone having to notify this module.
 */
export function getActiveOrganizationSnapshot(): ActiveOrganizationSnapshot {
  const userId = knownUserId();
  if (userId !== snapshot.userId) {
    snapshot = {
      ...snapshot,
      userId,
      organization: readStoredSelection(userId),
      // Memberships belong to a user; a different user starts with none.
      organizations: null,
      error: null,
    };
  }
  return snapshot;
}

// ---------------------------------------------------------------------------
// The persistent value
// ---------------------------------------------------------------------------

function readStoredAll(): StoredOrganizations {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as StoredOrganizations;
      if (parsed && typeof parsed === "object" && parsed.users && typeof parsed.users === "object") {
        return parsed;
      }
    }
  } catch {
    // Unreadable — treated as nothing set; the next SET rewrites it.
  }
  return { users: {} };
}

function readStoredSelection(userId: string | null): MemberOrganization | null {
  if (!userId) return null;
  const all = readStoredAll();
  const own = all.users[userId];
  if (own && typeof own.id === "string" && own.id) {
    return { id: own.id, name: String(own.name ?? ""), isPersonal: own.isPersonal === true };
  }
  // One-time migration of the unscoped pre-2026-09-21 value: the first user
  // to read it on this device claims it (it was theirs — one account per
  // device was the norm), and it is verified against live membership on
  // the next resolution like every stored value.
  try {
    const legacy = localStorage.getItem(LEGACY_STORAGE_KEY);
    if (legacy) {
      const parsed = JSON.parse(legacy) as { id?: unknown; name?: unknown };
      localStorage.removeItem(LEGACY_STORAGE_KEY);
      if (parsed && typeof parsed.id === "string" && parsed.id) {
        const migrated: MemberOrganization = {
          id: parsed.id,
          name: typeof parsed.name === "string" ? parsed.name : "",
          isPersonal: false,
        };
        writeStoredSelection(userId, migrated, { silent: true });
        return migrated;
      }
    }
  } catch {
    // Nothing to migrate.
  }
  return null;
}

function writeStoredSelection(
  userId: string,
  value: MemberOrganization | null,
  options?: { silent?: boolean },
): void {
  try {
    const all = readStoredAll();
    if (value === null) delete all.users[userId];
    else all.users[userId] = { id: value.id, name: value.name, isPersonal: value.isPersonal };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  } catch {
    // localStorage unavailable / quota — the selection lives in memory for
    // this session only; resolution re-runs the sole-membership rule on
    // every call, and otherwise HOLDS and asks again. It never guesses.
  }
  snapshot = { ...snapshot, userId, organization: value };
  if (!options?.silent) emit();
}

// ---------------------------------------------------------------------------
// Memberships
// ---------------------------------------------------------------------------

interface MembershipRow {
  container_id?: unknown;
  containerId?: unknown;
}

/**
 * Every organization the signed-in user is an active member of, via the
 * canonical `mbr_for_user` RPC (the platform's own membership read — this
 * app never re-derives membership from a junction table). RPCs are not
 * schema-scoped; they stay on the plain client.
 *
 * Every read also refreshes the store's `organizations`, so the switcher and
 * the picker show the same list any request boundary just verified against.
 */
export async function listMemberOrganizations(): Promise<MemberOrganization[]> {
  patch({ loading: true, error: null });
  try {
    const { data, error } = await supabase.rpc("mbr_for_user", {
      p_container_type: "organization",
    });
    if (error) {
      throw new Error(`Could not read your organizations: ${error.message}`);
    }
    const rows: MembershipRow[] = Array.isArray(data) ? (data as MembershipRow[]) : [];
    const ids = [
      ...new Set(
        rows
          .map((r) => (typeof r.container_id === "string" ? r.container_id : r.containerId))
          .filter((id): id is string => typeof id === "string" && id.length > 0),
      ),
    ];
    let organizations: MemberOrganization[] = [];
    if (ids.length > 0) {
      const { data: orgRows, error: orgError } = await supabase
        .schema("iam")
        .from("organizations")
        .select("id,name,is_personal")
        .in("id", ids);
      if (orgError) {
        throw new Error(`Could not read your organizations: ${orgError.message}`);
      }
      organizations = (orgRows ?? []).map((row) => ({
        id: String((row as { id: unknown }).id),
        name: String((row as { name?: unknown }).name ?? "Untitled organization"),
        isPersonal: (row as { is_personal?: unknown }).is_personal === true,
      }));
    }
    // The stored name can go stale (an org renamed on the web); the live
    // list is the truth the switcher shows.
    const { organization: current, userId } = getActiveOrganizationSnapshot();
    const refreshed = current ? organizations.find((o) => o.id === current.id) ?? current : null;
    if (userId && refreshed && current && refreshed.name !== current.name) {
      writeStoredSelection(userId, refreshed, { silent: true });
    }
    patch({ organizations, loading: false, error: null, organization: refreshed });
    return organizations;
  } catch (err) {
    patch({ loading: false, error: err instanceof Error ? err.message : String(err) });
    throw err;
  }
}

// ---------------------------------------------------------------------------
// The engine mirror
// ---------------------------------------------------------------------------

/**
 * Tell the Python sidecar what the user just SET.
 *
 * The engine cannot read this window's `localStorage`, and it is forbidden to
 * go looking for a saved preference on the account instead — so the set value
 * has to cross the process boundary explicitly. Every background job on this
 * Mac (file sync, the scraper, the vault, delegation, coding-session
 * artifacts) then acts under exactly what the user chose, and the engine
 * answers the server's coding-session filing question with the same value.
 *
 * Imported lazily: `@/lib/api` is the app's heaviest module and pulls this
 * one back in transitively.
 */
async function pushSelectionToEngine(organizationId: string | null): Promise<boolean> {
  try {
    const { engine } = await import("@/lib/api");
    if (organizationId === null) await engine.delete("/organization/active");
    else await engine.put("/organization/active", { organization_id: organizationId });
    return true;
  } catch (err) {
    // The engine may simply not be up yet. That is not a reason to fail the
    // user's choice — the desktop is still correct, and the engine-connected
    // republish (below) re-states it until the engine takes it. It IS a
    // reason to say so out loud rather than leave a silent divergence.
    console.warn(
      "[active-org] the engine did not take this Mac's organization; background work will ask again until it does",
      err,
    );
    return false;
  }
}

async function persistSelection(userId: string, org: MemberOrganization): Promise<void> {
  writeStoredSelection(userId, org);
  await pushSelectionToEngine(org.id);
}

/**
 * Re-state this Mac's set organization to the engine.
 *
 * The engine keeps its own copy of this Mac's pick, in its own local store; a
 * fresh engine, a reinstall or a `--fresh` dev home starts with none. It is
 * driven by the engine's own connection state and retried until the engine
 * takes it, and it is ALSO the answer to the engine's own
 * `organization_required` ask: when the engine raises that while this device
 * already has an answer, the answer is re-sent instead of the person being
 * asked a question they already answered.
 *
 * @returns true when the engine accepted this Mac's answer (or there is
 * nothing set yet, so there is nothing to re-state and no reason to retry).
 */
export async function republishActiveOrganizationToEngine(): Promise<boolean> {
  const stored = getActiveOrganizationSnapshot().organization;
  if (!stored) return true;
  return pushSelectionToEngine(stored.id);
}

// ---------------------------------------------------------------------------
// Resolution
// ---------------------------------------------------------------------------

/**
 * Resolve the organization for this device, verifying it against live
 * membership. Returns null when the user must pick — never a guess.
 *
 * Membership verification is not paranoia: a stored selection outlives being
 * removed from an organization, and sending a stale one produces a server
 * rejection the user cannot interpret.
 */
export async function resolveActiveOrganization(): Promise<MemberOrganization | null> {
  const session = await getAuthedSession();
  const userId = session?.user?.id;
  if (!userId) return null;

  const organizations = await listMemberOrganizations();
  if (organizations.length === 0) return null;
  const byId = new Map(organizations.map((o) => [o.id, o]));

  const stored = readStoredSelection(userId);
  if (stored) {
    const match = byId.get(stored.id);
    if (match) {
      if (snapshot.organization?.id !== match.id || snapshot.userId !== userId) {
        writeStoredSelection(userId, match);
      }
      return match;
    }
    // Selection survived losing the membership — drop it rather than send an
    // organization the server will refuse.
    writeStoredSelection(userId, null);
  }

  if (organizations.length === 1) {
    const only = organizations[0] as MemberOrganization;
    await persistSelection(userId, only);
    return only;
  }

  return null;
}

/**
 * The active organization id, or null when the user must choose. Cheap: the
 * stored selection short-circuits, so the membership round-trip happens only
 * when there is nothing chosen yet or the choice needs re-verification.
 */
export async function getActiveOrganizationId(): Promise<string | null> {
  const stored = getActiveOrganizationSnapshot().organization;
  if (stored) return stored.id;
  const resolved = await resolveActiveOrganization();
  return resolved?.id ?? null;
}

/**
 * How long a held request waits for the user to set an organization before it
 * gives up. A knob, not a magic number (`limits-are-knobs-agents-set-them`):
 * pass `timeoutMs` to change it for one call site rather than editing this.
 */
export const ORGANIZATION_HOLD_TIMEOUT_MS = 2 * 60 * 1000;

/**
 * THE REQUEST BOUNDARY. Every org-scoped call goes through here.
 *
 * When nothing is set on this device this does NOT fail — it HOLDS: it raises
 * the picker, waits for the user to set one, and then returns that id so the
 * caller's request proceeds. A request that dies with "no organization" and
 * makes the user go hunting for a setting is the shape Arman outlawed on
 * 2026-09-19; the whole point is that the missed check turns into a question,
 * not into a silent default.
 *
 * It only throws when the user does not answer inside the hold window, so a
 * caller can never block forever, and the error still carries the remedy.
 */
export async function requireActiveOrganizationId(options?: {
  timeoutMs?: number;
}): Promise<string> {
  const already = await getActiveOrganizationId();
  if (already) return already;

  // Zero memberships means there is nothing the picker can ever produce —
  // settle NOW with the typed refusal instead of holding for the full
  // timeout on a user the picker cannot help. Still raise the picker (it
  // shows the same "you have no organization" message to whoever is
  // looking), but never let a silent multi-minute clock be what ends the
  // hold.
  const organizations = await listMemberOrganizations();
  if (organizations.length === 0) {
    requestOrganizationPicker();
    throw new OrganizationNoMembershipsError();
  }

  const timeoutMs = options?.timeoutMs ?? ORGANIZATION_HOLD_TIMEOUT_MS;
  const held = await new Promise<string | null>((resolve) => {
    let settled = false;
    const finish = (value: string | null) => {
      if (settled) return;
      settled = true;
      unsubscribe();
      clearTimeout(timer);
      resolve(value);
    };
    function onChanged() {
      const chosen = getActiveOrganizationSnapshot().organization;
      if (chosen) finish(chosen.id);
    }
    const timer = setTimeout(() => finish(null), timeoutMs);
    const unsubscribe = subscribeActiveOrganization(onChanged);
    // Ask AFTER the listener is attached: the picker can resolve on the very
    // next tick if one is already open and the user clicks immediately.
    requestOrganizationPicker();
    // And re-check once, in case a selection landed between the read above
    // and the listener being attached.
    onChanged();
  });

  if (!held) throw new OrganizationNotSelectedError();
  return held;
}

/**
 * THE ONE WRITE. Record an explicit user choice — from the switcher or the
 * picker. Verified against live membership first: this device never stores
 * an organization the user cannot actually act in. Mirrored to the engine.
 */
export async function setActiveOrganization(
  organizationId: string,
): Promise<MemberOrganization> {
  const session = await getAuthedSession();
  const userId = session?.user?.id;
  if (!userId) {
    throw new Error("Sign in before choosing an organization.");
  }
  const organizations = await listMemberOrganizations();
  const match = organizations.find((o) => o.id === organizationId);
  if (!match) {
    throw new Error("You are not a member of that organization.");
  }
  await persistSelection(userId, match);
  return match;
}

/** Forget this device's selection for the signed-in user (sign-out). The engine forgets too. */
export function clearActiveOrganization(): void {
  const userId = snapshot.userId ?? knownUserId();
  if (userId) writeStoredSelection(userId, null);
  else patch({ organization: null });
  void pushSelectionToEngine(null);
}

/**
 * The event `OrganizationPickerDialog` (mounted once near the app root)
 * listens for. Any call site that catches `OrganizationNotSelectedError`
 * calls `requestOrganizationPicker()` so the user can act on the remedy
 * immediately, instead of the failure only living in an error message they
 * have to interpret and go fix somewhere else — nothing fails silently.
 */
export const REQUEST_PICKER_EVENT = "matrx-local.active-organization.request-picker";

export function requestOrganizationPicker(): void {
  try {
    window.dispatchEvent(new CustomEvent(REQUEST_PICKER_EVENT));
  } catch {
    // No window (non-browser test context) — nothing to open.
  }
}

/** Re-exported for consumers that want to react to a selection change. */
export { CHANGE_EVENT as ACTIVE_ORGANIZATION_CHANGE_EVENT };
