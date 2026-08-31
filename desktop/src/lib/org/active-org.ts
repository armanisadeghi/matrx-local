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
 * ## Resolution order (mirrors matrx-extend's canonical resolver —
 * `src/lib/org/active-org.ts` — which itself mirrors matrx-frontend's
 * `lib/organizations/resolveActiveOrgContext.ts`; consume the platform's
 * answer, never invent a second one)
 *
 *   1. This install's stored selection — IF the user is still a member.
 *   2. The user's durable default-organization preference
 *      (`users.user_preferences` → `organization.defaultOrganizationId`) —
 *      IF they are still a member.
 *   3. Exactly ONE membership → that organization (there is nothing to
 *      choose, so choosing it invents nothing).
 *   4. Otherwise `null`, ON PURPOSE — the signal the UI uses to make the user
 *      pick, via `OrganizationNotSelectedError`. Never "first", "personal",
 *      "most recent", or "system": a guessed organization writes a user's
 *      work into the wrong tenant, which is the defect class this whole
 *      contract exists to end (common-docs/projects/no-db-assigned-org).
 */

import supabase from "@/lib/supabase";

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

/**
 * The user's durable, cross-device default organization. Read straight from
 * `users.user_preferences` (the same row the web app and the extension
 * write) so this device agrees with every other surface. Never throws — a
 * preference we cannot read simply does not participate in resolution.
 */
async function readDefaultOrganizationId(userId: string): Promise<string | null> {
  try {
    const { data, error } = await supabase
      .schema("users")
      .from("user_preferences")
      .select("preferences")
      .eq("user_id", userId)
      .maybeSingle();
    if (error || !data) return null;
    const prefs = (data as { preferences?: unknown }).preferences as
      | { organization?: { defaultOrganizationId?: string | null } }
      | null
      | undefined;
    return prefs?.organization?.defaultOrganizationId ?? null;
  } catch {
    return null;
  }
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
    // localStorage unavailable / quota — selection lives in memory only for
    // this session; resolution falls through to the default/sole-membership
    // rules on every call instead.
  }
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
}

async function persistSelection(org: MemberOrganization): Promise<void> {
  writeStoredSelection({ id: org.id, name: org.name });
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
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const userId = session?.user?.id;
  if (!userId) return null;

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

  const preferred = await readDefaultOrganizationId(userId);
  if (preferred) {
    const match = byId.get(preferred);
    if (match) {
      await persistSelection(match);
      return match;
    }
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

/** The active organization id, or a loud, remediable failure. */
export async function requireActiveOrganizationId(): Promise<string> {
  const id = await getActiveOrganizationId();
  if (!id) throw new OrganizationNotSelectedError();
  return id;
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

/** Forget this device's selection (sign-out). */
export function clearActiveOrganization(): void {
  writeStoredSelection(null);
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
