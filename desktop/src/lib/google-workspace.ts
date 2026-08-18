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
  const response = await fetch(
    `${await getAIDreamServerUrl()}/api/google-workspace/gmail/send-reviewed`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${session.access_token}`,
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
