/**
 * How one conversation's sync truth is PRESENTED. Pure, so it is testable.
 *
 * The words themselves come from the engine — it computes the sentence and the
 * remedy, because three surfaces in two repos must say the same thing (CS-25
 * §2). This module decides only what a screen adds on top: which of the four
 * counts is shown where, what a missing count looks like, the tone of the
 * verdict strip, and whether the Reconcile button can honestly do anything.
 *
 * THE ONE RULE HERE: a count of `null` means the layer could not be read. It
 * renders as "Not readable", never as 0 and never as blank. An empty cell and
 * a zero are both claims, and both would be false.
 */

import type {
  ClaudeSessionSyncTruth,
  SyncTruthCounts,
  SyncVerdictCode,
} from "@/lib/api";

/** What a layer that could not be read shows instead of a number. */
export const UNREADABLE_COUNT = "Not readable";

/**
 * Tone per verdict. `partial_by_design` is deliberately NEUTRAL, not a
 * warning: 655 of Arman's 2033 conversations are hook-lane sessions working
 * exactly as designed, and painting them amber would be 655 false alarms.
 * `diverged` is neutral too — a compacted transcript is not a fault.
 */
export const VERDICT_TONE: Record<SyncVerdictCode, "good" | "warn" | "bad" | "neutral"> = {
  in_sync: "good",
  partial_by_design: "neutral",
  diverged: "neutral",
  behind_local: "warn",
  behind_cloud: "warn",
  mirror_stale: "warn",
  quarantined: "bad",
  not_in_cloud: "warn",
  unknown: "neutral",
};

export const VERDICT_LABEL: Record<SyncVerdictCode, string> = {
  in_sync: "In sync",
  partial_by_design: "Summary only, by design",
  behind_local: "AI Matrx is behind this Mac",
  behind_cloud: "Received, not yet turned into messages",
  mirror_stale: "This Mac's copy is behind AI Matrx",
  diverged: "Claude rewrote the local file",
  quarantined: "Held back",
  not_in_cloud: "Not in AI Matrx",
  unknown: "Cannot tell",
};

export const VERDICT_TONE_CLASS: Record<"good" | "warn" | "bad" | "neutral", string> = {
  good: "border-emerald-500/40 bg-emerald-500/10",
  warn: "border-amber-500/40 bg-amber-500/10",
  bad: "border-destructive/40 bg-destructive/10",
  neutral: "border-border bg-muted/40",
};

export interface SyncCountCell {
  label: string;
  /** Already display-ready: a number as text, or UNREADABLE_COUNT. */
  value: string;
  /** True when the layer could not be read, so the cell can be styled as such. */
  unreadable: boolean;
  /** One line explaining what this number is, for the cell's title attribute. */
  hint: string;
}

const COUNT_HINTS: Record<keyof SyncTruthCounts, string> = {
  transcript: "Entries in Claude Code's own transcript file on this Mac.",
  delivered: "Entries AI Matrx acknowledges receiving for this conversation.",
  cloud_messages: "Messages AI Matrx built from those entries.",
  mirror_messages: "Messages in this Mac's own offline copy of the conversation.",
};

const COUNT_ORDER: (keyof SyncTruthCounts)[] = [
  "transcript",
  "delivered",
  "cloud_messages",
  "mirror_messages",
];

/**
 * The four cells, in the contract's fixed order, with the engine's own labels.
 *
 * The labels come off the wire (`count_labels`) rather than being hardcoded
 * here, so this screen cannot drift from the web's four words.
 */
export function countCells(truth: ClaudeSessionSyncTruth): SyncCountCell[] {
  return COUNT_ORDER.map((key, index) => {
    const raw = truth.counts[key];
    return {
      label: truth.count_labels[index] ?? key,
      value: raw === null || raw === undefined ? UNREADABLE_COUNT : String(raw),
      unreadable: raw === null || raw === undefined,
      hint: COUNT_HINTS[key],
    };
  });
}

/**
 * Whether the Reconcile button should be offered at all.
 *
 * The engine decides this, not the screen: it is the only party that knows a
 * `provider_account_conflict` can never succeed, and offering a button that is
 * guaranteed to fail is the "dead, disabled-looking control" this codebase
 * forbids. When it is false the screen shows the remedy sentence instead, so
 * there is never a control with nothing behind it.
 */
export function canReconcile(truth: ClaudeSessionSyncTruth): boolean {
  return truth.verdict.reconcilable;
}

/** The layer timestamps a person asks for by name: "when was it synced?". */
export interface SyncStamp {
  label: string;
  value: string | null;
}

export function syncStamps(truth: ClaudeSessionSyncTruth): SyncStamp[] {
  return [
    { label: "Last entry written here", value: truth.transcript.last_entry_at },
    { label: "Last delivery accepted", value: truth.delivered.last_receipt_at },
    { label: "Last message in AI Matrx", value: truth.cloud.last_message_at },
    { label: "This Mac's copy last pulled", value: truth.mirror.last_pulled_at },
  ];
}

/**
 * One line per reconcile step, for the result list under the button.
 * `refused` is deliberately not an error tone: refusing to re-send an envelope
 * AI Matrx will always reject is the correct outcome, not a failure.
 */
export const OUTCOME_LABEL: Record<string, string> = {
  queued: "Queued",
  done: "Done",
  nothing_to_do: "Nothing to do",
  skipped: "Skipped",
  refused: "Not attempted",
  failed: "Failed",
};

export const STEP_LABEL: Record<string, string> = {
  deliver: "Deliver the transcript",
  reproject: "Turn entries into messages",
  pull: "Copy onto this Mac",
};
