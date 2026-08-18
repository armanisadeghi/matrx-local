/**
 * Pending user-review delegated calls (today: `google_email_send`).
 *
 * The engine parks these instead of executing them — see
 * `app/services/delegation/user_review.py`. This hook is the desktop's window
 * onto that queue: it polls the loopback engine, hands the proposals to
 * <GmailReviewCard>, and posts the user's decision back.
 *
 * Deliberately independent of the Cloud Chat stream loop: a review has no
 * deadline, and a card must survive a reload, a segment boundary, or a chat
 * the user navigated away from and came back to.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import supabase from "@/lib/supabase";

const REVIEW_POLL_MS = 1500;

export interface PendingEmailReview {
  callId: string;
  conversationId: string;
  toolName: string;
  to: string;
  cc: string[];
  subject: string;
  body: string;
}

export type ReviewOutcome = "sent" | "declined" | "cancelled" | "error";

export interface ReviewDecision {
  outcome: ReviewOutcome;
  messageId?: string;
  to?: string;
  cc?: string[];
  subject?: string;
  fromEmail?: string | null;
  edited?: boolean;
  error?: string;
}

interface RawReview {
  call_id?: unknown;
  conversation_id?: unknown;
  tool_name?: unknown;
  kind?: unknown;
  arguments?: unknown;
}

/**
 * The engine's delegation routes are behind the same auth gate as
 * `/chat/delegation/ui-claim` — loopback is not a credential here.
 */
async function authHeaders(): Promise<Record<string, string> | null> {
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session?.access_token) return null;
  return {
    Authorization: `Bearer ${session.access_token}`,
    "Content-Type": "application/json",
  };
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function parseReview(raw: RawReview): PendingEmailReview | null {
  const callId = str(raw.call_id);
  const kind = str(raw.kind);
  if (!callId || kind !== "email_review") return null;
  const args = (raw.arguments ?? {}) as Record<string, unknown>;
  const cc = Array.isArray(args.cc)
    ? args.cc.filter((entry): entry is string => typeof entry === "string")
    : [];
  return {
    callId,
    conversationId: str(raw.conversation_id),
    toolName: str(raw.tool_name),
    to: str(args.to),
    cc,
    subject: str(args.subject),
    body: str(args.body),
  };
}

export interface EmailReviewsActions {
  /** Deliver the user's decision and drop the card. */
  resolve: (callId: string, decision: ReviewDecision) => Promise<void>;
  refresh: () => Promise<void>;
}

export function useEmailReviews(
  engineUrl: string | null,
): [PendingEmailReview[], EmailReviewsActions] {
  const [reviews, setReviews] = useState<PendingEmailReview[]>([]);
  // call_ids already answered locally — keeps a resolved card from flashing
  // back for one poll cycle before the engine's list catches up.
  const resolvedRef = useRef<Set<string>>(new Set());

  const refresh = useCallback(async () => {
    if (!engineUrl) {
      setReviews([]);
      return;
    }
    try {
      const headers = await authHeaders();
      if (!headers) return;
      const response = await fetch(`${engineUrl}/chat/delegation/reviews`, {
        headers,
        signal: AbortSignal.timeout(4000),
      });
      if (!response.ok) return;
      const payload = (await response.json()) as { reviews?: RawReview[] };
      const parsed = (payload.reviews ?? [])
        .map(parseReview)
        .filter((review): review is PendingEmailReview => review !== null)
        .filter((review) => !resolvedRef.current.has(review.callId));
      const live = new Set(parsed.map((review) => review.callId));
      for (const callId of resolvedRef.current) {
        if (!live.has(callId)) resolvedRef.current.delete(callId);
      }
      setReviews(parsed);
    } catch {
      // The engine restarting is a state, not an error — the next poll wins.
    }
  }, [engineUrl]);

  const resolve = useCallback(
    async (callId: string, decision: ReviewDecision) => {
      if (!engineUrl) return;
      const headers = await authHeaders();
      if (!headers) {
        throw new Error("Sign in again — your session expired before this was recorded.");
      }
      resolvedRef.current.add(callId);
      setReviews((prev) => prev.filter((review) => review.callId !== callId));
      const response = await fetch(
        `${engineUrl}/chat/delegation/review-decision`,
        {
          method: "POST",
          headers,
          body: JSON.stringify({
            call_id: callId,
            outcome: decision.outcome,
            message_id: decision.messageId ?? null,
            to: decision.to ?? null,
            cc: decision.cc ?? [],
            subject: decision.subject ?? null,
            from_email: decision.fromEmail ?? null,
            edited: decision.edited ?? false,
            error: decision.error ?? null,
          }),
        },
      );
      if (!response.ok && response.status !== 404) {
        // Delivery of the DECISION failed (not the send). Let the card come
        // back on the next poll so the user is never told it was handled.
        resolvedRef.current.delete(callId);
        await refresh();
        throw new Error(
          `The app could not record your decision (HTTP ${response.status}).`,
        );
      }
    },
    [engineUrl, refresh],
  );

  useEffect(() => {
    if (!engineUrl) {
      setReviews([]);
      return;
    }
    void refresh();
    const id = setInterval(() => void refresh(), REVIEW_POLL_MS);
    return () => clearInterval(id);
  }, [engineUrl, refresh]);

  const actions = useMemo(() => ({ resolve, refresh }), [resolve, refresh]);
  return [reviews, actions];
}
