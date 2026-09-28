/**
 * The mapped-only offered values of the two transcript-polish provisions
 * (aidream `client_mandates.py`): `local.transcript_polish` (the polish
 * mandate and the six built-in styles) and
 * `local.transcript_polish_custom_style` (`local.polish_style_custom`).
 *
 * The local door resolves the Holder and fills ITS messages client-side by
 * `{{name}}` (`substituteVariables`); a name the Holder's messages do not
 * reference changes nothing the model receives. The live Holders reference
 * none of these names (checked 2026-09-28), so adding them is
 * payload-neutral today and lets a future Holder use them.
 *
 * The local plumbing is string-only (`Record<string, string>`): numbers are
 * sent as decimal strings, booleans never occur, and a string list is joined
 * with ", ". Absent facts are omitted, never sent empty.
 */
import type { TranscriptionSession } from "./types";

export interface PolishOfferFacts {
  /** The session being polished, when the site holds it. */
  session?: TranscriptionSession | null | undefined;
  /** The text actually sent as `transcript`. */
  transcript: string;
  /** The chosen style preset's display name, when a preset was chosen. */
  styleName?: string | null | undefined;
}

const has = (value: string | null | undefined): value is string =>
  typeof value === "string" && value.trim().length > 0;

/** `local.transcript_polish` — polish mandate and built-in styles. */
export function transcriptPolishOfferedValues(facts: PolishOfferFacts): Record<string, string> {
  const s = facts.session ?? null;
  const out: Record<string, string> = {};
  if (s) {
    if (has(s.title)) out.session_title = s.title;
    if (s.durationSecs > 0) out.duration_secs = String(s.durationSecs);
    if (has(s.createdAt)) out.recorded_at = s.createdAt;
    if (has(s.modelUsed)) out.whisper_model = s.modelUsed;
    if (has(s.deviceUsed)) out.audio_device = s.deviceUsed;
    if (s.segments.length > 0) out.segment_count = String(s.segments.length);
    if (s.aiTags && s.aiTags.length > 0) out.previous_ai_tags = s.aiTags.join(", ");
  }
  out.char_count = String(facts.transcript.length);
  if (has(facts.styleName)) out.style_name = facts.styleName;
  return out;
}

/** `local.transcript_polish_custom_style` — `local.polish_style_custom`. */
export function customStylePolishOfferedValues(facts: PolishOfferFacts): Record<string, string> {
  const s = facts.session ?? null;
  const out: Record<string, string> = {};
  if (has(facts.styleName)) out.style_name = facts.styleName;
  if (s) {
    if (has(s.title)) out.session_title = s.title;
    if (has(s.createdAt)) out.recorded_at = s.createdAt;
    if (s.durationSecs > 0) out.duration_seconds = String(Math.round(s.durationSecs));
    if (has(s.rawText)) out.raw_transcript = s.rawText;
    if (has(s.aiTitle)) out.previous_ai_title = s.aiTitle;
    if (s.aiTags && s.aiTags.length > 0) out.previous_ai_tags = s.aiTags.join(", ");
    if (has(s.modelUsed)) out.whisper_model = s.modelUsed;
  }
  return out;
}
