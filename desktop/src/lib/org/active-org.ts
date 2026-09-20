/**
 * The organization this desktop install acts in — the ONE place that answers
 * "which organization is this request for?".
 *
 * ## Why this exists
 *
 * aidream's AuthMiddleware admits an authenticated request only when it
 * carries a VERIFIED organization (`X-Organization-Id`), and it refuses to
 * pick one for the caller — a server that guesses is exactly how work lands
 * in the wrong tenant. `GET /auth/whoami` cannot answer "which organization
 * does this client carry" either: it now 400s with no header, 200s with a
 * real membership, and 400s with an organization the caller is not a member
 * of. The CLIENT is the side that knows which organization the user chose;
 * the server only verifies membership. There is no server-side lookup that
 * substitutes for this.
 *
 * ## The ruling this file enforces (Arman, 2026-09-19)
 *
 * A saved "default organization" on the user's ACCOUNT is at most a
 * per-client display preference. NOTHING that builds a request may read it,
 * and nothing may fall back to the personal organization. Both rungs used to
 * be in this file and both are gone.
 *
 *     "one missed org check that should have just failed turns into 50 in a
 *     month and 5,000 in a year, and suddenly we don't have orgs any more,
 *     we have a user and a default org, which means we just have user now."
 *
 * What this device MAY remember is the organization the USER THEMSELVES SET
 * here — the little picker's state, not a preference read out of an account.
 *
 * ## Resolution order
 *
 *   1. THIS DEVICE'S SET organization — IF the user is still a member.
 *   2. Exactly ONE membership -> that organization (there is nothing to
 *      choose, so choosing it invents nothing).
 *   3. Otherwise `null`, ON PURPOSE — and `null` at a request boundary is a
 *      HOLD, not a failure: `requireActiveOrganizationId()` raises the
 *      picker, waits for the user to SET one, and the request then proceeds.
 *      Never "first", "personal", "most recent", or "system": a guessed
 *      organization writes a user's work into the wrong tenant, which is the
 *      defect class this whole contract exists to end
 *      (common-docs/projects/no-db-assigned-org).
 *
 * The Python sidecar cannot read this device's `localStorage`, so every SET
 * is also pushed to the engine (`PUT /organization/active`) — that is how a
 * background job on this Mac knows what the user chose instead of guessing.
 */

import supabase from "@/lib/supabase";
import { getAuthedSession } from "@/lib/custodian";
import { getAppRuntimeConfig } from "@/lib/app-config";

const STORAGE_KEY = "matrx-local.active-organization.v1";
const CHANGE_EVENT = "matrx-local.active-organization.change";

export interface MemberOrganization {
  id: string;
  name: string;
  isPersonal: boolean;
}

interface StoredActiveOrganization {
  id: string;
  name: string;
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

interface MembershipRow {
  container_id?: unknown;
  containerId?: unknown;
}

/**
 * Every organization the signed-in user is an active member of, via the
 * canonical `mbr_for_user` RPC (the platform's own membership read — this
 * app never re-derives membership from a junction table). RPCs are not
 * schema-scoped; they stay on the plain client.
 */
export async function listMemberOrganizations(): Promise<MemberOrganization[]> {
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
  if (ids.length === 0) return [];

  const { data: orgRows, error: orgError } = await supabase
    .schema("iam")
    .from("organizations")
    .select("id,name,is_personal")
    .in("id", ids);
  if (orgError) {
    throw new Error(`Could not read your organizations: ${orgError.message}`);
  }
  return (orgRows ?? []).map((row) => ({
    id: String((row as { id: unknown }).id),
    name: String((row as { name?: unknown }).name ?? "Untitled organization"),
    isPersonal: (row as { is_personal?: unknown }).is_personal === true,
  }));
}

function readStoredSelection(): StoredActiveOrganization | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredActiveOrganization;
    return parsed && typeof parsed.id === "string" && parsed.id ? parsed : null;
  } catch {
    return null;
  }
}

function writeStoredSelection(value: StoredActiveOrganization | null): void {
  try {
    if (value === null) {
      localStorage.removeItem(STORAGE_KEY);
    } else {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
    }
  } catch {
    // localStorage unavailable / quota — the selection does not survive this
    // session; resolution re-runs the sole-membership rule on every call, and
    // otherwise HOLDS and asks again. It never falls through to a guess.
  }
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
}

/**
 * Tell the Python sidecar what the user just SET.
 *
 * The engine cannot read this window's `localStorage`, and it is forbidden to
 * go looking for a saved preference on the account instead — so the set value
 * has to cross the process boundary explicitly. Every background job on this
 * Mac (file sync, the scraper, the vault, delegation, coding-session
 * artifacts) then acts under exactly what the user chose.
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
    // user's choice — the desktop is still correct, and `App.tsx` re-pushes
    // the stored selection when the engine comes back. It IS a reason to say
    // so out loud rather than leave a silent divergence (law 4).
    console.warn(
      "[active-org] the engine did not take this Mac's organization; background work will ask again until it does",
      err,
    );
    return false;
  }
}

async function persistSelection(org: MemberOrganization): Promise<void> {
  writeStoredSelection({ id: org.id, name: org.name });
  await pushSelectionToEngine(org.id);
}

/**
 * Re-state this Mac's set organization to the engine.
 *
 * ## The double-ask this closes
 *
 * The engine keeps its own copy of this Mac's pick, in its own local store; a
 * fresh engine, a reinstall or a `--fresh` dev home starts with none. This used
 * to run exactly ONCE, when `OrganizationPickerDialog` mounted — which is
 * normally BEFORE the sidecar is reachable, so the push failed, warned to the
 * console and was never tried again. Every background job on the Mac then held
 * and published `organization_required`, asking a question the person had
 * already answered here. (The comment in `pushSelectionToEngine` claiming
 * `App.tsx` re-pushes on reconnect described something that did not exist.)
 *
 * So it is now driven by the engine's own connection state and retried until
 * the engine takes it.
 *
 * @returns true when the engine accepted this Mac's answer (or there is
 * nothing set yet, so there is nothing to re-state and no reason to retry).
 */
export async function republishActiveOrganizationToEngine(): Promise<boolean> {
  const stored = readStoredSelection();
  if (!stored) return true;
  return pushSelectionToEngine(stored.id);
}

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
  if (!session?.user?.id) return null;

  const organizations = await listMemberOrganizations();
  if (organizations.length === 0) return null;
  const byId = new Map(organizations.map((o) => [o.id, o]));

  const stored = readStoredSelection();
  if (stored) {
    const match = byId.get(stored.id);
    if (match) return match;
    // Selection survived losing the membership — drop it rather than send an
    // organization the server will refuse.
    writeStoredSelection(null);
  }

  if (organizations.length === 1) {
    const only = organizations[0] as MemberOrganization;
    await persistSelection(only);
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
  const stored = readStoredSelection();
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
      window.removeEventListener(CHANGE_EVENT, onChanged);
      clearTimeout(timer);
      resolve(value);
    };
    function onChanged() {
      const chosen = readStoredSelection();
      if (chosen) finish(chosen.id);
    }
    const timer = setTimeout(() => finish(null), timeoutMs);
    window.addEventListener(CHANGE_EVENT, onChanged);
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
 * Record an explicit user choice. Verified against live membership first —
 * this device never stores an organization the user cannot actually act in.
 */
export async function setActiveOrganization(
  organizationId: string,
): Promise<MemberOrganization> {
  const organizations = await listMemberOrganizations();
  const match = organizations.find((o) => o.id === organizationId);
  if (!match) {
    throw new Error("You are not a member of that organization.");
  }
  await persistSelection(match);
  return match;
}

/** Forget this device's selection (sign-out). The engine forgets too. */
export function clearActiveOrganization(): void {
  writeStoredSelection(null);
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
