/**
 * The reply door for a coding-session conversation — THE SAME door as the web.
 *
 * "There's no way to send messages" (Arman, 2026-09-14). A mirrored Claude
 * Code / Codex conversation opened from a session row was read-only here, and
 * read-only in the web app, even though the server has always accepted a new
 * turn on it (`POST /api/ai/conversations/{id}`). The web door (lane XT-03a)
 * asks one endpoint who answers and renders the server's own sentence; this
 * module is that same decision, so the desktop cannot drift into its own
 * wording, its own enabled/disabled rule, or its own idea of who replies.
 * Matrx Local is never an exception (law: identical UI, shapes and doors).
 *
 * Everything here is derived from the server's report. Nothing is invented:
 * the label, the refusal sentence and the stand-in notice are the server's
 * strings, and `canSend` is gated on the server's `can_reply` — which is
 * computed from the very predicate the reply POST itself gates on.
 */

import type { CodingReplyResponderReport } from "@/lib/aidream-client";

/**
 * The `source_feature` a reply from this door carries. aidream reads it to
 * stamp `metadata.origin = "ai_matrx_reply"` and the responder's `agent_id`
 * onto both the reply and the answer, exactly as the web door produces them.
 */
export const CODING_SESSION_REPLY_SOURCE_FEATURE = "coding_session_reply";

export type ReplyDoorStatus = "idle" | "loading" | "ready" | "error";

export interface ReplyDoorState {
  status: ReplyDoorStatus;
  report: CodingReplyResponderReport | null;
  error: string | null;
}

export interface ReplyDoorView {
  /** True when this conversation is a mirrored coding session at all. */
  isMirror: boolean;
  /** The server's sentence naming who answers, or null when it said nothing. */
  label: string | null;
  /** Shown when the server refuses the reply: its label, else its reason. */
  refusalSentence: string | null;
  /** The platform-default stand-in notice, only when a stand-in was used. */
  standInNotice: string | null;
  /** True while we do not yet know, or the server refused the reply. */
  inputBlocked: boolean;
  /** The composer placeholder for this state. */
  placeholder: string;
  /** What a send from this door files itself as, or null for a normal chat. */
  sourceFeature: string | null;
}

const DEFAULT_PLACEHOLDER = "Ask AI Matrx about this session, or say what to do next.";
const REFUSED_PLACEHOLDER = "A reply cannot be sent here.";

export function replyDoorView(state: ReplyDoorState): ReplyDoorView {
  if (state.status === "idle") {
    return {
      isMirror: false,
      label: null,
      refusalSentence: null,
      standInNotice: null,
      inputBlocked: false,
      placeholder: DEFAULT_PLACEHOLDER,
      sourceFeature: null,
    };
  }
  if (state.status === "loading") {
    return {
      isMirror: false,
      label: null,
      refusalSentence: null,
      standInNotice: null,
      // Never offer a send before the server has said whether it is allowed:
      // a composer that accepts text and then refuses it is a lying screen.
      inputBlocked: true,
      placeholder: DEFAULT_PLACEHOLDER,
      sourceFeature: null,
    };
  }
  if (state.status === "error" || state.report === null) {
    return {
      isMirror: false,
      label: null,
      refusalSentence: null,
      standInNotice: null,
      // A failed read is not a refusal: the ordinary chat door still works,
      // and the screen says plainly that it is not naming anyone.
      inputBlocked: false,
      placeholder: DEFAULT_PLACEHOLDER,
      sourceFeature: null,
    };
  }

  const report = state.report;
  const isMirror = report.is_coding_session_mirror === true;
  const label = isMirror && report.composer_label ? report.composer_label : null;
  const refused = report.can_reply !== true;
  return {
    isMirror,
    label,
    refusalSentence: refused ? (label ?? report.reason ?? null) : null,
    standInNotice: report.responder?.used_platform_default
      ? (report.stand_in_notice ?? null)
      : null,
    inputBlocked: refused,
    placeholder: refused ? REFUSED_PLACEHOLDER : DEFAULT_PLACEHOLDER,
    sourceFeature: isMirror ? CODING_SESSION_REPLY_SOURCE_FEATURE : null,
  };
}

/** The sentence a failed read shows — the real error, never a shrug. */
export function replyDoorErrorSentence(error: string): string {
  return `Who answers here could not be loaded, so this screen is not naming anyone: ${error}`;
}
