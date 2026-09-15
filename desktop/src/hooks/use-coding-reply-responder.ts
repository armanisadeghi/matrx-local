/**
 * Who answers a reply typed into a mirrored coding-session conversation.
 *
 * ONE endpoint answers this for every client — the web app's composer reads
 * the same report (lane XT-03a) — so the desktop asks rather than deciding.
 * The hook holds nothing but the server's answer and the state of asking for
 * it; every sentence a person reads comes from that answer
 * (`@/lib/coding-sessions/reply-door`).
 *
 * It is asked for every open cloud conversation, not only ones reached from a
 * session row: an ordinary chat comes back `is_coding_session_mirror: false`
 * and changes nothing, while a mirrored conversation opened from the sidebar
 * gets the same door as one opened from the Coding Sessions list.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  type CodingReplyResponderReport,
  fetchCodingReplyResponder,
} from "@/lib/aidream-client";
import { getAuthedSession } from "@/lib/custodian";
import { isOrganizationNotSelectedError, requireActiveOrganizationId } from "@/lib/org/active-org";
import type { ReplyDoorState } from "@/lib/coding-sessions/reply-door";

const IDLE: ReplyDoorState = { status: "idle", report: null, error: null };

export function useCodingReplyResponder(
  conversationId: string | null,
  enabled: boolean,
): ReplyDoorState & { refresh: () => void } {
  const [state, setState] = useState<ReplyDoorState>(IDLE);
  // Bumping this re-runs the read without changing the conversation, for the
  // case where the read failed and the person is still sitting on the screen.
  const [attempt, setAttempt] = useState(0);
  const refresh = useCallback(() => setAttempt((value) => value + 1), []);
  // The conversation the in-flight read belongs to: a fast switch must never
  // paint one conversation's responder onto another's composer.
  const wanted = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled || !conversationId) {
      wanted.current = null;
      setState(IDLE);
      return;
    }
    wanted.current = conversationId;
    const controller = new AbortController();
    let cancelled = false;
    setState({ status: "loading", report: null, error: null });

    void (async () => {
      try {
        const session = await getAuthedSession();
        if (!session?.access_token) {
          throw new Error("Sign in to AI Matrx to see who answers here.");
        }
        const organizationId = await requireActiveOrganizationId();
        const report: CodingReplyResponderReport = await fetchCodingReplyResponder(
          conversationId,
          session.access_token,
          organizationId,
          controller.signal,
        );
        if (cancelled || wanted.current !== conversationId) return;
        setState({ status: "ready", report, error: null });
      } catch (cause: unknown) {
        if (cancelled || controller.signal.aborted) return;
        if (wanted.current !== conversationId) return;
        // A missing organization choice is not a broken door — it is a thing
        // the person can fix — so it is reported in its own words.
        const message = isOrganizationNotSelectedError(cause)
          ? "Choose an organization to see who answers here."
          : cause instanceof Error
            ? cause.message
            : String(cause);
        setState({ status: "error", report: null, error: message });
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [attempt, conversationId, enabled]);

  return { ...state, refresh };
}
