/**
 * ONE diagnosis dialog, whichever provider wrote the session.
 *
 * The engine answers a Claude Code row with the deep `ClaudeSessionDiagnosis`
 * (a sidebar record, label ledgers, an import reconciler) and every other
 * provider with the leaner `CodingSessionProviderDiagnosis`. The screen that
 * shipped in 1.4.155 knew only the first shape, so it sent a Cursor chat down
 * the Claude-only read and then printed Claude's prose over the answer.
 *
 * This module is the ONE flattening of both shapes into what the dialog
 * renders, so the dialog never branches on a provider string and never writes
 * one provider's words over another's facts. Two laws are enforced here rather
 * than in JSX:
 *
 *  - **A section this provider has none of is NAMED, not blank** — every such
 *    section leaves a `notApplicable` entry with a full sentence saying which
 *    provider has none of it and why, instead of an empty box.
 *  - **A missing size stays missing** — `transcript.bytes` is `null` where the
 *    provider has no size, and is never rendered as "0 B".
 */

import type {
  ClaudeDeliveryLedger,
  ClaudeSessionDiagnosis,
  ClaudeSessionDiagnosisEnvelope,
  CodingSessionCloudCheck,
  CodingSessionDiagnosis,
  CodingSessionProvider,
  CodingSessionProviderDiagnosis,
  CodingSessionState,
  SessionContinuation,
} from "@/lib/api";
import { noSizeSentence, providerLabel } from "@/lib/coding-sessions/providers";

/** The server's record of this one session, whichever shape it arrived in. */
type DiagnosisBinding = CodingSessionProviderDiagnosis["cloud"]["binding"];

/** A section this provider has none of, said in words. Never an empty box. */
export interface DiagnosisNotApplicable {
  title: string;
  sentence: string;
}

export interface DiagnosisTranscript {
  onDisk: boolean;
  /** Null = THE PROVIDER HAS NO SIZE for this session. Never rendered as 0. */
  bytes: number | null;
  modifiedAt: string | null;
}

export interface DiagnosisCloudView {
  meta: CodingSessionCloudCheck;
  binding: DiagnosisBinding;
  /** How many of THIS provider's sessions the server holds, or why it cannot say. */
  boundCountSentence: string;
}

export interface DiagnosisDeliveryView {
  /** Absent on engines that did not report ledger availability. */
  ledger: ClaudeDeliveryLedger | null;
  /** Null when this Mac could not read the envelope ledger. */
  envelopes: ClaudeSessionDiagnosisEnvelope[] | null;
  queue: { pending: number; quarantined: number } | null;
  publisherBlocker: ClaudeSessionDiagnosis["delivery"]["publisher_blocker"];
  deliveredByThisMacAt: string | null;
  /** What stands where a delivery stamp would be when there is none. */
  neverDeliveredSentence: string;
}

export interface DiagnosisView {
  provider: CodingSessionProvider;
  label: string;
  sessionId: string;
  state: CodingSessionState;
  verdict: { summary: string; remedy: string | null };
  title: string | null;
  project: string | null;
  lastActivityAt: number | null;
  archived: boolean | null;
  /** Null when this Mac holds no local record of the session at all. */
  transcript: DiagnosisTranscript | null;
  /** The provider's own sentence about what it cannot show here. */
  localNote: string | null;
  /** The provider's own display facts, in payload order. */
  facts: Array<[string, unknown]>;
  /** Claude Code's sidebar record. Null for every other provider. */
  sidebar: ClaudeSessionDiagnosis["index"] | null;
  /** Claude Code's label ledgers. Null for every other provider. */
  labels: ClaudeSessionDiagnosis["labels"];
  labelsUnavailable: boolean;
  capture: ClaudeSessionDiagnosis["capture"];
  captureUnavailable: boolean;
  /** True only for Claude Code: the sync-truth comparison is its route alone. */
  hasSyncTruth: boolean;
  /** How this session is reopened, when the engine reported it. */
  continuation: SessionContinuation | null;
  cloud: DiagnosisCloudView;
  delivery: DiagnosisDeliveryView;
  notApplicable: DiagnosisNotApplicable[];
}

function boundCountSentence(meta: CodingSessionCloudCheck, label: string): string {
  if (!meta.checked) {
    return (
      `AI Matrx could not be asked how many of your ${label} sessions are bound there — ` +
      `${meta.detail ?? meta.reason ?? "it reported no reason"}.`
    );
  }
  return `${meta.sessions.toLocaleString()} of your ${label} sessions are bound there`;
}

/** The three sections only Claude Code has, each with its own reason. */
function claudeOnlySections(label: string): DiagnosisNotApplicable[] {
  return [
    {
      title: "Claude's sidebar record",
      sentence:
        `${label} keeps no sidebar index of its own, so there is no record here to ` +
        `compare against — this session's own facts above are all this Mac holds.`,
    },
    {
      title: "Label sync ledgers",
      sentence:
        `AI Matrx does not push titles or categories back into ${label}, so there ` +
        `are no label sync records for this session.`,
    },
    {
      title: "AI Matrx match check",
      sentence:
        `The content comparison behind "does this match AI Matrx?" reads Claude's ` +
        `own transcript store, which ${label} does not have. The delivery facts ` +
        `below are the evidence for this session instead.`,
    },
  ];
}

function claudeView(data: ClaudeSessionDiagnosis): DiagnosisView {
  const label = providerLabel("claude_code");
  return {
    provider: "claude_code",
    label,
    sessionId: data.session_id,
    state: data.state,
    verdict: data.verdict,
    title: data.index.title,
    project: data.index.project,
    lastActivityAt: data.index.last_activity_at,
    archived: data.index.archived,
    transcript: {
      onDisk: data.transcript.on_disk,
      bytes: data.transcript.bytes,
      modifiedAt: data.transcript.modified_at,
    },
    localNote: null,
    facts: [],
    sidebar: data.index,
    labels: data.labels,
    labelsUnavailable: data.labels_ledger?.checked === false,
    capture: data.capture,
    captureUnavailable: data.capture_ledger?.checked === false,
    hasSyncTruth: true,
    continuation: null,
    cloud: {
      meta: data.cloud,
      binding: data.cloud.binding,
      boundCountSentence: boundCountSentence(data.cloud, label),
    },
    delivery: {
      ledger: data.delivery.ledger ?? null,
      envelopes: data.delivery.envelopes,
      queue: null,
      publisherBlocker: data.delivery.publisher_blocker,
      deliveredByThisMacAt: data.delivery.delivered_by_this_mac_at,
      neverDeliveredSentence:
        "Never (a hook-mirrored session is delivered by Claude Code itself, not by this Mac)",
    },
    notApplicable: [],
  };
}

function providerDiagnosisView(data: CodingSessionProviderDiagnosis): DiagnosisView {
  const label = providerLabel(data.provider);
  const local = data.local;
  const notApplicable: DiagnosisNotApplicable[] = [];
  if (local === null) {
    notApplicable.push({
      title: "Record on this Mac",
      sentence:
        data.local_note ??
        `This Mac holds no local ${label} record of this session; everything below ` +
          `comes from AI Matrx.`,
    });
  }
  notApplicable.push(...claudeOnlySections(label));

  return {
    provider: data.provider,
    label,
    sessionId: data.session_id,
    state: data.state,
    verdict: data.verdict,
    title: local?.title ?? null,
    project: local?.project ?? null,
    lastActivityAt: local?.last_activity_at ?? null,
    archived: local?.archived ?? null,
    transcript:
      local === null
        ? null
        : { onDisk: local.on_disk, bytes: local.bytes, modifiedAt: null },
    localNote: data.local_note ?? (local?.bytes === null ? noSizeSentence(label) : null),
    facts: Object.entries(local?.facts ?? {}),
    sidebar: null,
    labels: null,
    labelsUnavailable: false,
    capture: data.capture.attempts,
    captureUnavailable: data.capture.meta.checked === false,
    hasSyncTruth: false,
    continuation: data.continuation ?? null,
    cloud: {
      meta: data.cloud.meta,
      binding: data.cloud.binding,
      boundCountSentence: boundCountSentence(data.cloud.meta, label),
    },
    delivery: {
      ledger: data.delivery.meta,
      envelopes: data.delivery.envelopes,
      queue: data.delivery.queue,
      publisherBlocker: data.delivery.publisher_blocker,
      // The provider payload carries no delivery stamp at all, so this says
      // what the engine reports rather than inventing a "never".
      deliveredByThisMacAt: null,
      neverDeliveredSentence:
        `This Mac's engine records no delivery stamp for a ${label} session; the ` +
        `queue and deliveries below are the evidence.`,
    },
    notApplicable,
  };
}

/**
 * ONE view of either diagnosis shape.
 *
 * Discriminated on the payload's own structure (only Claude Code carries an
 * `index` sidebar record), never on the provider string a caller passed in —
 * a client that trusts its own guess about the shape is how the Claude-only
 * prose got written over a Cursor chat in the first place.
 */
export function diagnosisView(data: CodingSessionDiagnosis): DiagnosisView {
  return "index" in data ? claudeView(data) : providerDiagnosisView(data);
}
