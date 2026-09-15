/**
 * "Continue in Claude Code" — a real native continue, or the honest reason.
 *
 * The row's Continue action used to copy `claude --resume <id>` and nothing
 * else (CS-11), while this machine has run a full local Claude Code runtime
 * the whole time — start, native resume, live events, cancel — reachable on
 * loopback. This module decides what that control may offer, from three
 * verdicts and never from a guess:
 *
 *  1. **The platform verdict** — the bridge's `capabilities` action for this
 *     provider × `origin=matrx_local` (lane XT-01). It is the only place a
 *     client may learn whether `resume_native` is a thing at all, so a Codex
 *     row shows the server's real sentence instead of a hidden button.
 *  2. **The machine verdict** — the engine's own runtime capabilities: the
 *     `claude` binary, its login, the SDK, the AI Matrx sign-in.
 *  3. **The session verdict** — `resumable`, answered ONLY from Claude's own
 *     local store: no transcript on this Mac means no native resume, ever.
 *
 * A refusal is never a dead button: the control opens, states which of the
 * three said no in that verdict's own words, and still hands over the resume
 * command a person can paste into their own terminal.
 */

import type { BridgeCapabilityReport } from "@/lib/aidream-client";
import type { LocalRuntimeCapabilities } from "@/lib/api";

export interface SessionResumeVerdict {
  resumable: boolean;
  reason?: string;
  session_id?: string;
  workspace?: string;
  transcript_present?: boolean;
}

export interface ContinueDoorInput {
  /** The copyable command for this session — the fallback, always present. */
  copyCommand: string;
  platform: { report: BridgeCapabilityReport | null; error: string | null };
  machine: { capabilities: LocalRuntimeCapabilities | null; error: string | null };
  session: { verdict: SessionResumeVerdict | null; error: string | null };
}

export interface ContinueDoorView {
  status: "checking" | "ready" | "refused";
  /** Every sentence a person needs, in the order the checks were made. */
  reasons: string[];
  canStart: boolean;
  /** The approved folder the resumed turn runs in, when there is one. */
  workspace: string | null;
  /** The raw Claude session id the resume names, when it is resumable. */
  resumeSessionId: string | null;
  copyCommand: string;
}

/** The engine's reason codes, said the way a person would say them. */
const SESSION_REASONS: Record<string, string> = {
  transcript_not_on_this_machine:
    "Claude's own transcript for this session is not on this Mac, so there is no history to resume from here. It can still be continued on the machine that holds it.",
  workspace_unknown:
    "The folder this session ran in could not be determined from Claude's own records, so a resumed turn has nowhere to run.",
  workspace_missing:
    "The folder this session ran in no longer exists on this Mac, so a resumed turn has nowhere to run.",
  not_a_claude_local_session_identity:
    "This row's session identity is not a local Claude session, so Claude Code cannot resume it.",
};

function sessionReasonSentence(verdict: SessionResumeVerdict): string {
  const code = verdict.reason ?? "";
  return (
    SESSION_REASONS[code] ??
    `Claude Code cannot resume this session on this Mac (${code || "no reason reported"}).`
  );
}

function resumeVerdict(report: BridgeCapabilityReport) {
  return report.operations.find((verdict) => verdict.operation === "resume_native") ?? null;
}

export function continueDoorView(input: ContinueDoorInput): ContinueDoorView {
  const refused = (reasons: string[]): ContinueDoorView => ({
    status: "refused",
    reasons,
    canStart: false,
    workspace: null,
    resumeSessionId: null,
    copyCommand: input.copyCommand,
  });

  // A failed read is its own answer, and it names what failed. It is never
  // silently treated as "unsupported" — that would hide a working runtime.
  if (input.platform.error) {
    return refused([
      `AI Matrx could not be asked whether this session can be continued natively: ${input.platform.error}`,
    ]);
  }
  if (input.machine.error) {
    return refused([
      `This Mac's engine could not be asked about its Claude Code runtime: ${input.machine.error}`,
    ]);
  }
  if (input.session.error) {
    return refused([
      `This Mac could not be asked whether Claude's own transcript for this session is here: ${input.session.error}`,
    ]);
  }

  const report = input.platform.report;
  const machine = input.machine.capabilities;
  const verdict = input.session.verdict;
  if (report === null || machine === null || verdict === null) {
    return {
      status: "checking",
      reasons: [],
      canStart: false,
      workspace: null,
      resumeSessionId: null,
      copyCommand: input.copyCommand,
    };
  }

  const platformResume = resumeVerdict(report);
  if (platformResume === null) {
    return refused([
      "AI Matrx returned no verdict for resuming this provider natively, so this app will not claim it can.",
    ]);
  }
  if (!platformResume.supported) {
    return refused([
      platformResume.reason ??
        report.reason ??
        "AI Matrx reports that this provider cannot be resumed natively, without saying why.",
    ]);
  }

  if (!machine.available) {
    // The engine's own prerequisite list, verbatim: no `claude` binary, no
    // Claude login, no AI Matrx sign-in — each is separately fixable.
    const reasons = machine.reasons.length > 0
      ? machine.reasons
      : ["This Mac's Claude Code runtime is unavailable and reported no reason."];
    return refused(reasons);
  }
  if (!machine.capabilities.resume_native) {
    return refused([
      "This Mac's Claude Code runtime does not offer native resume, so a resumed turn cannot be started here.",
    ]);
  }
  if (!verdict.resumable) {
    return refused([sessionReasonSentence(verdict)]);
  }

  const workspace = verdict.workspace ?? null;
  if (workspace === null) {
    return refused([
      "Claude's records name no folder for this session, so a resumed turn has nowhere to run.",
    ]);
  }
  const approved = machine.approved_folders.includes(workspace);
  if (!approved) {
    return refused([
      `This session ran in ${workspace}, which is not approved for agent execution on this Mac yet. Approve that folder in the Agent Runtime settings and this continue starts here.`,
    ]);
  }

  return {
    status: "ready",
    reasons: [
      `Continuing runs a new Claude Code turn on this Mac in ${workspace}, on your own Claude login, with this session's full history. It becomes a first-class Claude Code session you can open and resume yourself, and AI Matrx mirrors it.`,
    ],
    canStart: true,
    workspace,
    resumeSessionId: verdict.session_id ?? null,
    copyCommand: input.copyCommand,
  };
}

/** Live status wording for a started run — no spinner without a sentence. */
export function runStatusSentence(run: {
  status: string;
  turns_completed: number;
  error: string | null;
}): string {
  switch (run.status) {
    case "starting":
      return "Starting Claude Code on this Mac…";
    case "running":
      return run.turns_completed > 0
        ? `Running — ${run.turns_completed} turn${run.turns_completed === 1 ? "" : "s"} completed.`
        : "Running — no turn has completed yet.";
    case "completed":
      return `Finished after ${run.turns_completed} turn${
        run.turns_completed === 1 ? "" : "s"
      }. The transcript is Claude's own, and AI Matrx mirrors it.`;
    case "cancelled":
      return "Cancelled. Whatever the turn had already written to disk stays.";
    case "failed":
      return run.error
        ? `Failed: ${run.error}`
        : "Failed, and the runtime reported no reason.";
    default:
      return `Runtime reported an unrecognised status (${run.status}).`;
  }
}
