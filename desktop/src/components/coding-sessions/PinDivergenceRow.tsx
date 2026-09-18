/**
 * ONE row on the Sessions tab stating the pin divergence out loud.
 *
 * It sits directly under the status counters, beside the "Pinned in the coding
 * agent" card whose number it explains — not in a card of its own, because a
 * card implies a section and this is one line of fact.
 *
 * Every word comes from `lib/coding-sessions/pin-divergence`, which carries
 * the tests and the rule: an unmeasured divergence shows NO numbers, and an
 * unfinished pass says so before its numbers. This file is only the row.
 */

import type { ClaudePinDivergence } from "@/lib/api";
import {
  pinDivergenceLine,
  type PinDivergenceTone,
} from "@/lib/coding-sessions/pin-divergence";

const TONE_CLASS: Record<PinDivergenceTone, string> = {
  good: "text-muted-foreground",
  warn: "text-amber-600 dark:text-amber-400",
  neutral: "text-muted-foreground",
};

export function PinDivergenceRow({
  divergence,
  now,
}: {
  divergence: ClaudePinDivergence | null | undefined;
  /** Injected clock for tests; the screen passes nothing. */
  now?: number;
}) {
  const line = pinDivergenceLine(divergence, now);
  if (!line) return null;
  return (
    <p
      className={`text-xs ${TONE_CLASS[line.tone]}`}
      title={line.hint ?? undefined}
      data-testid="pin-divergence"
    >
      {line.sentence}
    </p>
  );
}
