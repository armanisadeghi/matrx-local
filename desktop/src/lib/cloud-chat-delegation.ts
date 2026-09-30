import { conversationPendingCallsPath } from "@/lib/api/routes/ai";
import type { StreamBlockBuilder } from "@/lib/chat-blocks";
import {
  reduceLiveToolEvent,
  type ExtractedToolParts,
} from "@/features/filesystem/tool-results";
import type { ToolEventPayload } from "@/types/python-generated/stream-events";

const DELEGATION_POLL_MS = 1000;
const DELEGATION_CLAIM_TTL_SECONDS = 20;
// Longest mega-tool execution timeout is Shell at 900s; add headroom.
const DELEGATION_WAIT_CAP_MS = 16 * 60 * 1000;

/** The answer this desktop posted for a call (engine `settled_result`). */
export interface DelegationCallResult {
  is_error: boolean;
  error_message?: string | null;
  output?: unknown;
  duration_ms?: number | null;
}

export interface DelegationCall {
  call_id: string;
  tool_name: string;
  state: string;
  /** Present once this desktop answered the call (executed, refused, reviewed). */
  result?: DelegationCallResult;
}

/**
 * Call states that have NOT produced a result yet. While any of them is live,
 * this conversation's turn is still suspended on the server: a `/resume` is
 * refused with HTTP 409 `outstanding_delegated_calls`, and a NEW user turn is
 * a turn sent on top of a suspended one. Mirrors `UNSETTLED_CALL_STATES` in
 * `app/services/delegation/engine.py`.
 */
export const UNSETTLED_CALL_STATES = new Set([
  "queued",
  "executing",
  "awaiting_user_review",
]);

export function outstandingCalls(state: EngineDelegationState | null): DelegationCall[] {
  if (state?.outstanding) return state.outstanding;
  return (state?.calls ?? []).filter((call) => UNSETTLED_CALL_STATES.has(call.state));
}

/**
 * The completion event aidream never sends for a delegated call.
 *
 * The server hard-suspends and ends the stream when it delegates, and a
 * `/resume` segment does not replay the answered call — so the open card only
 * finishes because THIS desktop, which answered it, tells itself. The engine's
 * one delivery funnel records the answer (app/services/delegation/mirror.py);
 * this turns it into the same event shape the stream would have carried.
 */
export function answeredCallEvents(state: EngineDelegationState | null): ToolEventPayload[] {
  return (state?.calls ?? [])
    .filter((call): call is DelegationCall & { result: DelegationCallResult } =>
      Boolean(call.result),
    )
    .map((call) =>
      call.result.is_error
        ? {
            event: "tool_error",
            call_id: call.call_id,
            tool_name: call.tool_name,
            message: call.result.error_message || "The tool reported an error.",
            data: { detail: call.result.error_message ?? null },
          }
        : {
            event: "tool_completed",
            call_id: call.call_id,
            tool_name: call.tool_name,
            data: { result: call.result.output ?? null },
          },
    );
}

/**
 * Settle every call this desktop answered in the live message's two stores —
 * the ordered block (card phase) and the rich result list. Idempotent and
 * never a downgrade: a call already terminal in the block is left untouched,
 * so a real stream completion always wins. Returns the new parts, or null
 * when nothing changed.
 */
export function settleAnsweredDelegatedCalls(
  state: EngineDelegationState | null,
  builder: StreamBlockBuilder,
  parts: ExtractedToolParts,
): ExtractedToolParts | null {
  let next = parts;
  let changed = false;
  for (const event of answeredCallEvents(state)) {
    if (!builder.settleToolCall(event)) continue;
    changed = true;
    if (!next.results.some((result) => result.tool_call_id === event.call_id)) {
      next = reduceLiveToolEvent(next, event);
    }
  }
  return changed ? next : null;
}

/**
 * True when the server would refuse a resume right now. aidream raises HTTP
 * 409 `outstanding_delegated_calls` while any sibling call of the same
 * user_request is still delegated, and the desktop used to render that
 * refusal as a failed turn. It is not a failure — it means "wait".
 */
export function isBenignResumeConflict(status: number, body: string): boolean {
  if (status !== 409) return false;
  return /outstanding_delegated_calls|resume_conflict/.test(body);
}

export interface EngineDelegationState {
  claimed?: boolean;
  calls?: DelegationCall[];
  /** Calls the engine itself reports as not yet settled (newer engines). */
  outstanding?: DelegationCall[];
  continuation?: { user_request_id?: string | null; needed?: boolean } | null;
  /**
   * Delegated calls parked for explicit human review (e.g. a proposed Gmail
   * message). A review has NO deadline — a person may take an hour — so the
   * wait below must not abandon the stream while one is open.
   */
  reviews_pending?: number;
}

export type DelegationAccessToken = string | (() => Promise<string>);

async function resolveAccessToken(source: DelegationAccessToken): Promise<string> {
  return typeof source === "function" ? source() : source;
}

function authenticatedHeaders(accessToken: string): HeadersInit {
  return {
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  };
}

export async function claimDelegationUi(
  engineUrl: string,
  conversationId: string,
  accessToken: string,
): Promise<EngineDelegationState | null> {
  try {
    const response = await fetch(`${engineUrl}/chat/delegation/ui-claim`, {
      method: "POST",
      headers: authenticatedHeaders(accessToken),
      body: JSON.stringify({
        conversation_id: conversationId,
        ttl_seconds: DELEGATION_CLAIM_TTL_SECONDS,
      }),
      signal: AbortSignal.timeout(4000),
    });
    if (!response.ok) return null;
    return (await response.json()) as EngineDelegationState;
  } catch {
    return null;
  }
}

/**
 * Read-only delegation snapshot. Unlike `claimDelegationUi` this takes no
 * ownership of the continuation, so the composer gate can poll it without
 * changing who resumes the conversation.
 */
export async function readDelegationState(
  engineUrl: string,
  conversationId: string,
  accessToken: string,
): Promise<EngineDelegationState | null> {
  try {
    const response = await fetch(
      `${engineUrl}/chat/delegation/conversation/${encodeURIComponent(conversationId)}`,
      {
        headers: { Authorization: `Bearer ${accessToken}` },
        signal: AbortSignal.timeout(4000),
      },
    );
    if (!response.ok) return null;
    return (await response.json()) as EngineDelegationState;
  } catch {
    return null;
  }
}

export async function releaseDelegationUi(
  engineUrl: string,
  conversationId: string,
  accessToken: string,
): Promise<void> {
  try {
    await fetch(`${engineUrl}/chat/delegation/ui-release`, {
      method: "POST",
      headers: authenticatedHeaders(accessToken),
      body: JSON.stringify({ conversation_id: conversationId }),
      signal: AbortSignal.timeout(4000),
    });
  } catch {
    // Claim TTL expiry makes the engine self-heal; release is best-effort.
  }
}

/**
 * Poll the local engine until the delegated calls resolve and a continuation
 * (`user_request_id`) is available, re-claiming UI ownership on every poll.
 * Returns null when the wait is abandoned — the engine's headless resume
 * then finishes the conversation once the claim expires.
 */
export async function waitForDelegatedContinuation(
  engineUrl: string,
  conversationId: string,
  accessToken: DelegationAccessToken,
  signal: AbortSignal,
  onStatus: (status: string) => void,
  /** Every engine snapshot, so the caller can settle answered calls. */
  onSnapshot?: (state: EngineDelegationState) => void,
): Promise<string | null> {
  let deadline = Date.now() + DELEGATION_WAIT_CAP_MS;
  while (!signal.aborted && Date.now() < deadline) {
    const state = await claimDelegationUi(
      engineUrl,
      conversationId,
      await resolveAccessToken(accessToken),
    );
    if (state) {
      onSnapshot?.(state);
      const outstanding = outstandingCalls(state);
      const continuation = state.continuation;
      if (
        outstanding.length === 0 &&
        continuation?.needed &&
        continuation.user_request_id
      ) {
        return continuation.user_request_id;
      }
      if ((state.reviews_pending ?? 0) > 0) {
        // The user is reading a proposed message. Hold the stream open for as
        // long as that takes; the timeout only bounds machine work.
        deadline = Date.now() + DELEGATION_WAIT_CAP_MS;
        onStatus("Waiting for you to review a message before it is sent...");
        await new Promise((resolve) => setTimeout(resolve, DELEGATION_POLL_MS));
        continue;
      }
      if (outstanding.length > 0) {
        onStatus(
          `Running on this computer: ${outstanding.map((call) => call.tool_name).join(", ")}...`,
        );
      } else {
        onStatus("Waiting for local tool results...");
      }
    } else {
      onStatus("Waiting for the local engine...");
    }
    await new Promise((resolve) => setTimeout(resolve, DELEGATION_POLL_MS));
  }
  return null;
}

/**
 * Is this conversation's turn STILL RUNNING, even though this surface is no
 * longer streaming it?
 *
 * On 2026-09-22 the desktop told the owner his turn had failed and the same
 * turn went on running for nine more minutes, delegating tool call after tool
 * call to his Mac. A screen that reports a dead turn that is alive is worse
 * than one that reports nothing. Two independent sources answer it:
 * the server's own suspended-call ledger for this conversation (a READ — it
 * does not lease), and this desktop's engine, which knows what it still owes.
 */
export async function isTurnStillRunning(
  cloudServerUrl: string,
  cloudConversationId: string,
  accessToken: string,
  engineUrl: string | null | undefined,
): Promise<boolean> {
  try {
    const response = await fetch(
      `${cloudServerUrl}/api${conversationPendingCallsPath(cloudConversationId)}`,
      {
        headers: { Authorization: `Bearer ${accessToken}` },
        signal: AbortSignal.timeout(8000),
      },
    );
    if (response.ok) {
      const body: unknown = await response.json();
      const calls = Array.isArray(body)
        ? body
        : Array.isArray((body as { pending_calls?: unknown[] })?.pending_calls)
          ? (body as { pending_calls: unknown[] }).pending_calls
          : [];
      if (calls.length > 0) return true;
    }
  } catch {
    // An unreachable server is not evidence the turn died.
  }
  if (!engineUrl) return false;
  const local = await readDelegationState(engineUrl, cloudConversationId, accessToken);
  return outstandingCalls(local).length > 0;
}
