/**
 * Who answers a reply typed into a mirrored coding-session conversation.
 *
 * ONE endpoint answers this for every client — the web app's composer reads
 * the same report (lane XT-03a) — so the desktop asks rather than deciding.
 * The hook holds nothing but the server's answer and the state of asking for
 * it; every sentence a person reads comes from that answer
 * (`@ai-matrx/chat/agents/coding-session-reply`).
 *
 * It is asked for every open cloud conversation, not only ones reached from a
 * session row: an ordinary chat comes back `is_coding_session_mirror: false`
 * and changes nothing, while a mirrored conversation opened from the sidebar
 * gets the same door as one opened from the Coding Sessions list.
 */

import { useCallback } from "react";

import {
  type CodingReplyResponderRead,
  useCodingReplyResponder as usePackageCodingReplyResponder,
} from "@ai-matrx/chat/agents/coding-session-reply/useCodingReplyResponder";
import type { ReplyDoorState } from "@ai-matrx/chat/agents/coding-session-reply/reply-door";
import { fetchCodingReplyResponder } from "@/lib/aidream-client";
import { getAuthedSession } from "@/lib/custodian";
import { isOrganizationNotSelectedError, requireActiveOrganizationId } from "@/lib/org/active-org";

/** The desktop's read (custodian token + the window's organization), driving the package's reply door. */
export function useCodingReplyResponder(
  conversationId: string | null,
  enabled: boolean,
): ReplyDoorState & { refresh: () => void } {
  const read = useCallback<CodingReplyResponderRead>(async (id, signal) => {
    const session = await getAuthedSession();
    if (!session?.access_token) throw new Error("Sign in to AI Matrx to see who answers here.");
    let organizationId: string;
    try {
      organizationId = await requireActiveOrganizationId();
    } catch (cause) {
      // A missing organization choice is not a broken door — the person can fix it.
      if (isOrganizationNotSelectedError(cause)) throw new Error("Choose an organization to see who answers here.");
      throw cause;
    }
    return fetchCodingReplyResponder(id, session.access_token, organizationId, signal);
  }, []);
  return usePackageCodingReplyResponder({ conversationId, enabled, read });
}
