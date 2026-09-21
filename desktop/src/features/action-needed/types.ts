export type ActionNeededKind =
  | "os_permission"
  | "filesystem_access"
  | "api_key"
  | "external_approval"
  | "capability_install"
  | "organization";

export type ActionNeededStatus = "active" | "checking" | "resolved";

/** One option the person can pick to resolve an item — a picker row. */
export interface ActionNeededChoice {
  id: string;
  label: string;
  description?: string | null;
}

/** Mirrors app/services/action_needed/models.py. */
export interface ActionNeededAction {
  kind: string;
  label: string;
  permission_key?: string | null;
  provider?: string | null;
  route?: string | null;
  url?: string | null;
  resource_ids?: string[] | null;
  /**
   * A CHOICE the person makes to resolve the item, carried by the primitive
   * so any source can ask one (which organization, which account, which
   * folder) without a one-off dialog. The card renders one button per choice
   * and PUTs `{ choice: id }` to `choice_route` on the engine; the source that
   * raised the item owns what happens next and withdraws it once the choice
   * took.
   */
  choices?: ActionNeededChoice[] | null;
  choice_route?: string | null;
}

/** A source-owned, user-fixable state. It is not a transient toast. */
export interface ActionNeeded {
  fingerprint: string;
  code: string;
  kind: ActionNeededKind;
  feature: string;
  title: string;
  message: string;
  action: ActionNeededAction;
  source: string;
  status: ActionNeededStatus;
  observed_at?: number | null;
  details?: Record<string, unknown> | null;
}

export interface ActionNeededSnapshot {
  source: string;
  /** Process identity for the source's version sequence. */
  epoch?: string;
  /** Monotonically increasing source version. Older snapshots are ignored. */
  version: number;
  items: ActionNeeded[] | null;
}
