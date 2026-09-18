/**
 * The ONE sentence the Coding Sessions screen says about pins.
 *
 * WHY IT EXISTS. On 2026-09-18 a verifier measured the live database: Claude
 * Code held 218 pinned sessions, AI Matrx held 160 favourites — 116 pinned
 * here and not favourite there, 58 favourite there and not pinned here — and
 * NOTHING on any screen said so while the last `is_favorite` write was three
 * days old. The divergence was invisible, which is why it sat.
 *
 * THE ONE RULE HERE, and it is law 4 in this repo (a screen is absent or
 * honest, never dead or lying): `checked: false` means no reconcile pass has
 * ever finished, so there are NO numbers to show — not zeroes. "0 to
 * reconcile" for an unread comparison is the exact lie this line exists to
 * end. Likewise a pass whose status is `failed` or `partial` has numbers, but
 * they are not settled, and the sentence says that before it says them.
 *
 * Pure, so the four states are asserted without a DOM. The relative stamp
 * comes from THE package formatter with an injected clock — the same
 * `formatRelativeTime` the rest of this screen speaks through (`formatWhen`,
 * `cloudAgeLabel`), never a second date library and never a second voice.
 */

import type { ClaudePinDivergence } from "@/lib/api";
import { formatCount, formatRelativeTime } from "@ai-matrx/kit/format";

export type PinDivergenceTone = "good" | "warn" | "neutral";

export interface PinDivergenceLine {
  /** The whole user-visible sentence, already assembled. */
  sentence: string;
  tone: PinDivergenceTone;
  /** One line of detail for the row's `title`, or null when there is none. */
  hint: string | null;
}

/** A pass status that means its numbers are a snapshot of an unfinished job. */
function finishedCleanly(status: string | null): boolean {
  return status !== "failed" && status !== "partial";
}

function stamp(at: string | null, now: number | undefined): string | null {
  if (!at) return null;
  return formatRelativeTime(at, { now, fallback: "", fallbackToInput: true }) || null;
}

/**
 * The sentence, or null when the engine said nothing about pins at all (an
 * engine older than this contract). Absent is honest; invented is not.
 */
export function pinDivergenceLine(
  divergence: ClaudePinDivergence | null | undefined,
  now?: number,
): PinDivergenceLine | null {
  if (!divergence) return null;

  if (!divergence.checked) {
    const reason =
      divergence.reason ??
      "this Mac has not compared its pins with AI Matrx yet, so the difference is not known";
    return {
      sentence: `Pins: not compared yet — ${reason}.`,
      tone: "neutral",
      hint: "No numbers are shown because none have been measured. A reconcile pass fills this line in.",
    };
  }

  const parts: string[] = [
    `${formatCount(divergence.local)} on this Mac`,
    `${formatCount(divergence.ai_matrx)} in AI Matrx`,
  ];
  const clean = finishedCleanly(divergence.last_pass_status);
  const outstanding = divergence.to_reconcile;

  if (outstanding === null || outstanding === undefined) {
    parts.push("the number still to reconcile was not recorded");
  } else if (outstanding === 0 && clean) {
    parts.push("they agree");
  } else {
    parts.push(`${formatCount(outstanding)} still to reconcile${clean ? "" : " so far"}`);
  }

  const when = stamp(divergence.last_pass_at, now);
  if (when) parts.push(clean ? `last checked ${when}` : `last tried ${when}`);
  else parts.push("the time of that check was not recorded");

  const lead = clean
    ? "Pins"
    : "Pins: the last check did not finish cleanly, so these numbers are incomplete";
  const sentence = clean
    ? `Pins: ${parts.join(" · ")}`
    : `${lead} · ${parts.join(" · ")}`;

  const hintParts: string[] = [];
  if (divergence.to_pin !== null && divergence.to_unpin !== null) {
    hintParts.push(
      `${formatCount(divergence.to_pin)} to pin in AI Matrx, ${formatCount(divergence.to_unpin)} to unpin there.`,
    );
  }
  if (divergence.compared_sessions !== undefined) {
    hintParts.push(`${formatCount(divergence.compared_sessions)} sessions were compared.`);
  }
  if (!clean) {
    hintParts.push(`That pass ended "${divergence.last_pass_status}".`);
  }

  return {
    sentence,
    tone: clean ? (outstanding === 0 ? "good" : "warn") : "warn",
    hint: hintParts.length > 0 ? hintParts.join(" ") : null,
  };
}
