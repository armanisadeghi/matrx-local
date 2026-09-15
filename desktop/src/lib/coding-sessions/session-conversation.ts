/**
 * A session row's name is a door to its conversation.
 *
 * "It shows names, but if you click on them, it doesn't actually bring up the
 * conversations" (Arman, 2026-09-14). The conversation is not a new thing to
 * build: the overview row already carries the server's binding
 * (`cloud.conversation_id`), and the app already has ONE chat surface that
 * renders a conversation's messages. So a row click resolves to that
 * conversation id and opens it there — same surface, same shapes.
 *
 * When there is no conversation to open, the row says so in the words of the
 * state it is actually in — never a dead click, never a dialog pretending to
 * be the conversation.
 */

import type { ClaudeConversation, ClaudeSessionState } from "@/lib/api";

export type SessionOpenTarget =
  | { kind: "conversation"; conversationId: string }
  | { kind: "unavailable"; reason: string };

/** Why a session has no conversation to open yet, in that state's own words. */
const NO_CONVERSATION_REASON: Record<ClaudeSessionState, string> = {
  in_cloud:
    "AI Matrx holds this session but did not return a conversation for it, so there is nothing to open yet.",
  changed:
    "AI Matrx holds this session but did not return a conversation for it, so there is nothing to open yet.",
  queued:
    "Not in AI Matrx yet — its events are still waiting in this Mac's delivery queue.",
  failed:
    "Not in AI Matrx yet — a delivery was refused and the envelope is preserved here, waiting on a decision.",
  not_in_cloud:
    "Not in AI Matrx yet — nothing on the server and nothing queued for it.",
  unknown:
    "AI Matrx could not be asked, so this Mac cannot say whether a conversation exists for it.",
};

export function resolveSessionConversation(row: ClaudeConversation): SessionOpenTarget {
  const conversationId = row.cloud?.conversation_id ?? null;
  if (conversationId) return { kind: "conversation", conversationId };
  return { kind: "unavailable", reason: NO_CONVERSATION_REASON[row.state] };
}

/**
 * The in-app link that opens one conversation in this app's chat surface, with
 * the way back to the list it was opened from.
 */
export function conversationChatHref(conversationId: string): string {
  return `/cloud-chat?conversation=${encodeURIComponent(conversationId)}&from=coding-sessions`;
}

/** The same conversation on the web. `origin` comes from the app's runtime config. */
export function conversationWebUrl(origin: string, conversationId: string): string {
  return `${origin.replace(/\/+$/, "")}/chat/${encodeURIComponent(conversationId)}`;
}
