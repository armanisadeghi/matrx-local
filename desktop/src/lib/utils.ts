import { type ClassValue, clsx } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

// THE package byte-size formatter (`@ai-matrx/kit/format`, duplication
// census H1 2026-09-07). This repo alone carried THIRTEEN `formatBytes`
// bodies with twelve different roundings and five different words for
// "unknown" — the clearest case in the fleet for one owner.
// THE `formatBytes` ALIAS IS GONE (2026-09-12): a compatibility spelling of a
// collapsed export puts its call sites outside the input guard that judges
// what ENTERS formatFileSize. Nothing imported it from here; every caller in
// this app already imports `formatFileSize` from "@ai-matrx/kit/format".
export { formatFileSize } from "@ai-matrx/kit/format";
/**
 * tailwind-merge does not know this app's `boxShadow.glass` theme entry, so it
 * files `shadow-glass` under shadow-COLOR and stops deduping it against
 * `shadow-sm` / `shadow-xl`. That matters because the `components/ui/*`
 * bindings over `@ai-matrx/design-system` paint the glass surface with it: the
 * package's own `shadow-md` has to lose to it, and a caller's `shadow-xl` has
 * to beat it. Registering the one class in the right group makes BOTH true.
 */
const twMerge = extendTailwindMerge({
  extend: { classGroups: { shadow: [{ shadow: ["glass"] }] } },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}


// `formatDuration(ms)` WAS HERE, and a SECOND `formatDuration(seconds)` lived
// in PromptMatrix/BatchConfirmDialog.tsx — the same spelling over two
// different units, which is the exact ambiguity THE UNIT LAW exists to stop
// (the unit is in the NAME: formatDurationMs / formatDurationSeconds /
// formatDurationMinutes, never a bare `number`). This one had zero importers
// and is deleted; the other is now `formatEtaSeconds`. Call
// `formatDurationMs` from "@ai-matrx/kit/format" directly.

export function truncateUrl(url: string, maxLength = 60): string {
  if (url.length <= maxLength) return url;
  const parsed = new URL(url);
  const path = parsed.pathname + parsed.search;
  const available = maxLength - parsed.origin.length - 3;
  if (available < 10) return url.slice(0, maxLength - 3) + "...";
  return parsed.origin + path.slice(0, available) + "...";
}
