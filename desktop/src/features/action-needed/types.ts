export type ActionNeededKind =
  | "os_permission"
  | "filesystem_access"
  | "api_key"
  | "external_approval"
  | "capability_install";

export type ActionNeededStatus = "active" | "checking" | "resolved";

/** Mirrors app/services/action_needed/models.py. */
export interface ActionNeededAction {
  kind: string;
  label: string;
  permission_key?: string | null;
  provider?: string | null;
  route?: string | null;
  url?: string | null;
  resource_ids?: string[] | null;
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
