/**
 * Google Workspace — the desktop client half.
 *
 * THE CLIENT LAW APPLIES HERE. This file owns no OAuth client, no token
 * store, and no second connection table. It reads the SAME
 * `users.integration_connections` rows matrx-frontend reads (safe metadata
 * only — the refresh token never leaves aidream's vault), and it sends by
 * POSTing the reviewed bytes to aidream's one reviewed-send endpoint with the
 * user's own Supabase JWT. Exactly what the web client does, on the desktop.
 *
 * Scope boundary (never widen it here): `drive.file`, `gmail.send`,
 * `webmasters.readonly`, identity. There is no Drive browsing and no Gmail
 * reading anywhere in the platform.
 */

import supabase from "@/lib/supabase";
import { getAIDreamServerUrl, getWebAppOrigin } from "@/lib/app-config";
import { resolveConversationOrganizationId } from "@/lib/aidream-client";

/**
 * Canonical scope strings. Backend mirror:
 * `aidream/services/google_integrations/scopes.py`; frontend mirror:
 * `matrx-frontend/lib/googleScopes.ts`.
 */
export const GOOGLE_SCOPE = {
  driveFile: "https://www.googleapis.com/auth/drive.file",
  gmailSend: "https://www.googleapis.com/auth/gmail.send",
} as const;

/**
 * The reserved context key attached Google files travel under.
 *
 * They are NOT a content block: the server side does two things at once
 * (aidream `services/google_workspace/attachments.py`, reached through
 * `conversation_context/context_utils.py`) — it names the files for the agent
 * AND injects the `google_workspace` tool for that turn even when the agent's
 * own configuration does not carry it. A content block would deliver the first
 * half and silently drop the second.
 *
 * Keep byte-identical to the mirrors: matrx-frontend
 * `features/google-workspace/attach/googleFileContext.ts` and aidream
 * `services/google_workspace/attachments.py`.
 */
export const GOOGLE_FILES_CONTEXT_KEY = "__google_files";

/** The server truncates past this; the UI refuses past it too. */
export const MAX_ATTACHED_GOOGLE_FILES = 20;

/**
 * Where AI Matrx connects to Google. Every refusal points here — the origin
 * comes from remote app config, never a compiled-in domain.
 */
export async function googleWorkspaceSettingsUrl(): Promise<string> {
  return `${await getWebAppOrigin()}/user-settings/integrations/google-workspace`;
}

export interface GoogleConnectionRef {
  connectionId: string;
  accountEmail: string | null;
  accountName: string | null;
}

interface ConnectionRow {
  id: string;
  account_email: string | null;
  account_name: string | null;
  scopes: string[] | null;
  status: string | null;
  credential_item_id: string | null;
  vault_secret_key: string | null;
}

const CONNECTION_SELECT =
  "id, account_email, account_name, scopes, status, credential_item_id, vault_secret_key";

/**
 * A row whose credential reference is gone CANNOT authorize anything, no
 * matter what `status` claims — parity with aidream's precondition and with
 * matrx-frontend's `health` derivation.
 */
function isUsable(row: ConnectionRow, scope: string): boolean {
  if (row.status === "revoked" || row.status === "needs_attention") return false;
  if (!row.credential_item_id && !row.vault_secret_key) return false;
  return (row.scopes ?? []).includes(scope);
}

async function resolveByScope(
  scope: string,
  signal?: AbortSignal,
): Promise<GoogleConnectionRef | null> {
  const { data, error } = await supabase
    .schema("users")
    .from("integration_connections")
    .select(CONNECTION_SELECT)
    .eq("provider", "google")
    .is("deleted_at", null)
    .order("updated_at", { ascending: false })
    .abortSignal(signal ?? new AbortController().signal);
  if (error) throw new Error(error.message);
  const usable = (data as ConnectionRow[] | null)?.find((row) =>
    isUsable(row, scope),
  );
  if (!usable) return null;
  return {
    connectionId: usable.id,
    accountEmail: usable.account_email,
    accountName: usable.account_name,
  };
}

/**
 * The mailbox a reviewed message would be sent from.
 *
 * Returns null rather than throwing: "no Google account connected" is a
 * normal STATE with a one-click fix, not an error.
 */
export function resolveGmailSendConnection(
  signal?: AbortSignal,
): Promise<GoogleConnectionRef | null> {
  return resolveByScope(GOOGLE_SCOPE.gmailSend, signal);
}

/**
 * One Google file the user has ALREADY registered through the Picker on the
 * web app. `fileId` is the Drive file id (`resource_ref`) — the exact value
 * `__google_files` carries.
 */
export interface RegisteredGoogleFile {
  fileId: string;
  name: string;
  isSheet: boolean;
  accountEmail: string | null;
}

interface ResourceRow {
  connection_id: string;
  resource_type: string | null;
  resource_ref: string | null;
  display_name: string | null;
}

/**
 * Every Doc/Sheet the user registered on a usable `drive.file` connection.
 *
 * There is NO Drive browsing here and never will be: registering a new file is
 * a Google Picker flow that lives on the web app. This reads what is already
 * registered so a desktop chat can hand one to an agent.
 *
 * An empty array is a normal STATE (nothing connected, or nothing registered
 * yet), not an error — the caller shows the pitch.
 */
export async function listRegisteredGoogleFiles(
  signal?: AbortSignal,
): Promise<RegisteredGoogleFile[]> {
  const { data: connectionData, error: connectionError } = await supabase
    .schema("users")
    .from("integration_connections")
    .select(CONNECTION_SELECT)
    .eq("provider", "google")
    .is("deleted_at", null)
    .order("updated_at", { ascending: false })
    .abortSignal(signal ?? new AbortController().signal);
  if (connectionError) throw new Error(connectionError.message);

  const usable = ((connectionData as ConnectionRow[] | null) ?? []).filter(
    (row) => isUsable(row, GOOGLE_SCOPE.driveFile),
  );
  if (usable.length === 0) return [];

  const emailByConnection = new Map(
    usable.map((row) => [row.id, row.account_email]),
  );
  const { data: resourceData, error: resourceError } = await supabase
    .schema("users")
    .from("integration_connection_resources")
    .select("connection_id, resource_type, resource_ref, display_name")
    .in("connection_id", [...emailByConnection.keys()])
    .in("resource_type", ["google_document", "google_spreadsheet"])
    .is("deleted_at", null)
    .order("updated_at", { ascending: false })
    .abortSignal(signal ?? new AbortController().signal);
  if (resourceError) throw new Error(resourceError.message);

  const files: RegisteredGoogleFile[] = [];
  const seen = new Set<string>();
  for (const row of (resourceData as ResourceRow[] | null) ?? []) {
    const fileId = row.resource_ref?.trim();
    if (!fileId || seen.has(fileId)) continue;
    seen.add(fileId);
    files.push({
      fileId,
      name: row.display_name?.trim() || fileId,
      isSheet: row.resource_type === "google_spreadsheet",
      accountEmail: emailByConnection.get(row.connection_id) ?? null,
    });
  }
  return files;
}

export interface ReviewedGmailDraft {
  connectionId: string;
  to: string;
  cc: string[];
  subject: string;
  body: string;
}

/**
 * Send exactly one explicitly reviewed message.
 *
 * The caller passes the bytes that were on screen when the user pressed Send
 * — never the agent's original arguments. `user_confirmed` is asserted HERE,
 * by the code path a human click reached, and there is no agent-reachable
 * route to this function.
 */
export async function sendReviewedGmail(
  draft: ReviewedGmailDraft,
): Promise<string> {
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session?.access_token) {
    throw new Error("Sign in to send from your connected Google account.");
  }
  // aidream's AuthMiddleware refuses every authenticated request that names
  // no organization (400 organization_required) before it even reaches the
  // reviewed-send route. Resolve the caller's organization the same way
  // conversation start does — the server's own whoami answer, never a
  // client-side guess — and fail closed here rather than let the send
  // round-trip into a guaranteed refusal.
  const organizationId = await resolveConversationOrganizationId(
    session.access_token,
  );
  const response = await fetch(
    `${await getAIDreamServerUrl()}/api/google-workspace/gmail/send-reviewed`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${session.access_token}`,
        "X-Organization-Id": organizationId,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        connection_id: draft.connectionId,
        to: draft.to,
        cc: draft.cc,
        subject: draft.subject,
        body: draft.body,
        user_confirmed: true,
      }),
    },
  );
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as {
      detail?: unknown;
    };
    throw new Error(
      typeof payload.detail === "string"
        ? payload.detail
        : `Unable to send the reviewed Gmail message (HTTP ${response.status}).`,
    );
  }
  const body = (await response.json()) as { message_id?: unknown };
  if (typeof body.message_id !== "string") {
    throw new Error("Gmail accepted the message but returned no message id.");
  }
  return body.message_id;
}
