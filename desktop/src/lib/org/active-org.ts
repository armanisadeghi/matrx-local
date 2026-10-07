/**
 * THE organization store — the ONE place in this app that holds "which
 * organization is this window showing and acting in".
 *
 * There is exactly one state value (`getActiveOrganizationSnapshot()`, read
 * reactively through `useActiveOrganization()`), exactly one persistent
 * value (`localStorage[STORAGE_KEY]`, the last organization each signed-in
 * user chose on this device), and exactly one selector that changes it
 * (`features/org/OrganizationSwitcher`, mounted in the top bar). Every
 * request boundary in the window — the aidream client, Cloud Chat, the agent
 * catalog, Google Workspace, the Vault, the coding-session lanes — reads this
 * store and nothing else.
 *
 * ## THE LOAD LADDER (Arman, 2026-10-07; STATE rules 12-14)
 *
 * The window sets its organization ONCE, when it loads, and it is never none
 * for a signed-in person with at least one membership. In order, each kept
 * only if a current membership:
 *
 *   1. This device's own last choice (the persistent value above).
 *   2. The account's last active organization (follows the person across
 *      devices).
 *   3. The account's start-up organization setting.
 *   4. The person's first organization — the oldest active membership.
 *
 * `resolveActiveOrganization` is the ONLY reader of the two account columns.
 * A ladder answer is held in memory for the window; it is NOT written as the
 * device choice and NOT pushed to the engine. Only a deliberate switch
 * (`setActiveOrganization`) writes the device choice, tells the engine, and
 * calls `users.set_last_active_organization`. Zero memberships is the one
 * honest "none" ({@link OrganizationNoMembershipsError}).
 *
 * There is no hold and no picker on a window request. The Python sidecar is
 * HEADLESS: it shows no organization, so it keeps STATE rule 13 — it carries
 * only the organization the person chose for this connection (pushed on a
 * deliberate switch), or asks once through its `organization_required` item
 * (`OrganizationPickerDialog`), and never the ladder.
 *
 * aidream's AuthMiddleware admits an authenticated request only when it
 * carries a VERIFIED organization (`X-Organization-Id`) and never picks one;
 * the client states it, the server verifies membership.
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
  readonly remedy = "Choose your organization in the top bar, then try again.";

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
 * Thrown when the signed-in user belongs to NO organization at all — the one
 * honest "none". Distinct from {@link OrganizationNotSelectedError} (the
 * ladder could not settle although memberships exist): this one names the real
 * remedy — create or join an organization — with the link the app already has.
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
    return { id: own.id, name: String(own.name ?? "") };
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
    else all.users[userId] = { id: value.id, name: value.name };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  } catch {
    // localStorage unavailable / quota — the selection lives in memory for
    // this session only; the next load re-runs the ladder.
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
  status?: unknown;
  created_at?: unknown;
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
    // Oldest membership first: "first organization" is the head of this list.
    const joinedAt = new Map<string, string>();
    for (const r of rows) {
      if (r.status !== undefined && r.status !== "active") continue;
      const id = typeof r.container_id === "string" ? r.container_id : r.containerId;
      if (typeof id !== "string" || id.length === 0) continue;
      const joined = typeof r.created_at === "string" ? r.created_at : "";
      const prior = joinedAt.get(id);
      if (prior === undefined || (joined && (!prior || joined < prior))) joinedAt.set(id, joined);
    }
    const ids = [...joinedAt.keys()];
    let organizations: MemberOrganization[] = [];
    if (ids.length > 0) {
      const { data: orgRows, error: orgError } = await supabase
        .schema("iam")
        .from("organizations")
        .select("id,name,archived_at")
        .in("id", ids);
      if (orgError) {
        throw new Error(`Could not read your organizations: ${orgError.message}`);
      }
      organizations = (orgRows ?? [])
        .filter((row) => !(row as { archived_at?: unknown }).archived_at)
        .map((row) => ({
          id: String((row as { id: unknown }).id),
          name: String((row as { name?: unknown }).name ?? "Untitled organization"),
        }))
        .sort((a, b) => (joinedAt.get(a.id) ?? "").localeCompare(joinedAt.get(b.id) ?? ""));
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
 * Tell the Python sidecar what the user just CHOSE (a deliberate switch only —
 * never the load ladder's answer).
 *
 * The engine is headless (STATE rule 13): it cannot read this window's
 * `localStorage`, must not derive an organization, and so the person's
 * deliberate choice has to cross the process boundary explicitly. Every
 * background job on this Mac (file sync, the scraper, the vault, delegation,
 * coding-session artifacts) then acts under exactly what the user chose, and
 * the engine answers the server's coding-session filing question with the
 * same value.
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

/**
 * Save a switch to the person's account so the next load, on any device,
 * opens in it (`users.set_last_active_organization` refuses a non-member).
 * The device choice is already stored, so a failed write is logged loudly and
 * never undoes the switch here.
 */
async function saveLastActiveOrganization(organizationId: string): Promise<void> {
  try {
    const { error } = await supabase
      .schema("users")
      .rpc("set_last_active_organization", { p_organization_id: organizationId });
    if (error) throw new Error(error.message);
  } catch (err) {
    console.error(
      "[active-org] could not save the last active organization to the account",
      err,
    );
  }
}

async function persistSelection(userId: string, org: MemberOrganization): Promise<void> {
  writeStoredSelection(userId, org);
  await Promise.all([pushSelectionToEngine(org.id), saveLastActiveOrganization(org.id)]);
}

/**
 * The organization the person CHOSE on this device (the persistent value) —
 * unlike the snapshot's `organization`, which may be the load ladder's answer.
 * The headless engine only ever inherits this one.
 */
export function getDeviceOrganizationChoice(): MemberOrganization | null {
  return readStoredSelection(getActiveOrganizationSnapshot().userId ?? knownUserId());
}

/**
 * Re-state the organization the person CHOSE on this Mac to the engine.
 *
 * The engine keeps its own copy of this Mac's choice, in its own local store; a
 * fresh engine, a reinstall or a `--fresh` dev home starts with none. It is
 * driven by the engine's own connection state and retried until the engine
 * takes it, and it is ALSO the answer to the engine's own
 * `organization_required` ask: when the engine raises that while this device
 * already has a deliberate choice, it is re-sent instead of the person being
 * asked a question they already answered. With only a ladder answer on screen
 * there is no choice to re-state, and the person is asked once.
 *
 * @returns true when the engine accepted this Mac's answer (or there is
 * nothing set yet, so there is nothing to re-state and no reason to retry).
 */
export async function republishActiveOrganizationToEngine(): Promise<boolean> {
  const stored = getDeviceOrganizationChoice();
  if (!stored) return true;
  return pushSelectionToEngine(stored.id);
}

// ---------------------------------------------------------------------------
// Resolution — THE LOAD LADDER
// ---------------------------------------------------------------------------

/**
 * THE ONLY READER of the account's two organization columns (STATE rules 12
 * and 14). A failed read throws — a guessed first organization would hide the
 * person's real last choice.
 */
async function readAccountOrganizationChoice(
  userId: string,
): Promise<{ lastActive: string | null; startup: string | null }> {
  const { data, error } = await supabase
    .schema("users")
    .from("user_preferences")
    .select("last_active_organization_id,startup_organization_id")
    .eq("user_id", userId)
    .maybeSingle();
  if (error) {
    throw new Error(`Could not read your saved organization: ${error.message}`);
  }
  const row = (data ?? {}) as {
    last_active_organization_id?: unknown;
    startup_organization_id?: unknown;
  };
  const pick = (v: unknown) => (typeof v === "string" && v ? v : null);
  return {
    lastActive: pick(row.last_active_organization_id),
    startup: pick(row.startup_organization_id),
  };
}

let ladderInFlight: { userId: string; promise: Promise<MemberOrganization | null> } | null = null;

/**
 * Settle the organization for this window: the load ladder, verified against
 * live membership. Returns null only for a signed-out window or a person with
 * no memberships. The result is shown and carried by requests, but is NOT a
 * device choice and is NOT pushed to the headless engine.
 */
export async function resolveActiveOrganization(): Promise<MemberOrganization | null> {
  const session = await getAuthedSession();
  const userId = session?.user?.id;
  if (!userId) return null;
  if (ladderInFlight?.userId === userId) return ladderInFlight.promise;
  const promise = runLadder(userId)
    .catch((err: unknown) => {
      patch({ error: err instanceof Error ? err.message : String(err) });
      throw err;
    })
    .finally(() => {
    if (ladderInFlight?.promise === promise) ladderInFlight = null;
  });
  ladderInFlight = { userId, promise };
  return promise;
}

async function runLadder(userId: string): Promise<MemberOrganization | null> {
  const organizations = await listMemberOrganizations();
  if (organizations.length === 0) return null;
  const byId = new Map(organizations.map((o) => [o.id, o]));

  // 1. This device's own last choice.
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

  // 2-4. The account's last active, its start-up organization, the first.
  const account = await readAccountOrganizationChoice(userId);
  const chosen =
    (account.lastActive ? byId.get(account.lastActive) : undefined) ??
    (account.startup ? byId.get(account.startup) : undefined) ??
    (organizations[0] as MemberOrganization);
  // In memory only: shown and carried by requests, never stored as a choice.
  patch({ userId, organization: chosen });
  return chosen;
}

/**
 * The active organization id. Cheap: once the window has settled its
 * organization the snapshot answers; otherwise the ladder runs (once, shared
 * by concurrent first requests).
 */
export async function getActiveOrganizationId(): Promise<string | null> {
  const stored = getActiveOrganizationSnapshot().organization;
  if (stored) return stored.id;
  const resolved = await resolveActiveOrganization();
  return resolved?.id ?? null;
}

/**
 * THE REQUEST BOUNDARY. Every org-scoped call from the window goes through
 * here and gets the active organization — never a hold, never a question.
 * The only refusal is a person with no memberships, carrying its remedy.
 */
export async function requireActiveOrganizationId(): Promise<string> {
  const already = await getActiveOrganizationId();
  if (already) return already;
  const organizations = await listMemberOrganizations();
  if (organizations.length === 0) throw new OrganizationNoMembershipsError();
  throw new OrganizationNotSelectedError();
}

/**
 * THE ONE WRITE. Record a deliberate user choice — from the switcher or the
 * picker. Verified against live membership first: this device never stores an
 * organization the user cannot actually act in. Stored as the device choice,
 * mirrored to the headless engine, and saved to the account as the last
 * active organization.
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
 * listens for. It is the headless engine's ask-once surface (STATE rule 13)
 * and the "Choose organization" buttons that offer it; no window request
 * raises it any more.
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
