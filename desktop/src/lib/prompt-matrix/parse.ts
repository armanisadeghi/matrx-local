/**
 * Template parsing + rendering for {{variable}} tokens.
 *
 * Deliberately tiny and dependency-free: a token is `{{name}}` or a pool slot
 * `{{name#slot}}`, where `name` is letters/digits/underscore/hyphen/space
 * (trimmed, internal whitespace collapsed) and `slot` is alphanumeric.
 * Names are matched case-insensitively — {{Subject}} and {{subject}} are the
 * same variable — but the first spelling encountered is kept for display.
 *
 * Pool slots (`{{color#1}}`) share one option list; bare `{{color}}` remains a
 * normal independent variable. The two must not collide (validated in expand).
 */

/** Matches {{name}} or {{name#slot}} and captures name + optional slot. */
const TOKEN_RE = /\{\{\s*([A-Za-z0-9_\- ]+?)(?:#([A-Za-z0-9]+))?\s*\}\}/g;

/** A malformed `{{` that never closes — surfaced to the user, never ignored. */
const UNCLOSED_RE = /\{\{(?![^{}]*\}\})/;

export interface TokenMatch {
  /** Display spelling of the full token identity (`subject` or `color#1`). */
  name: string;
  /** Case-folded identity key. */
  key: string;
  /** Pool base name when this is a slot token; null for a normal variable. */
  poolName: string | null;
  /** Slot id when this is a pool slot; null for a normal variable. */
  slot: string | null;
  start: number;
  end: number;
}

/** One pool discovered in the template, with every slot that references it. */
export interface PoolRef {
  /** Display spelling of the pool name (first seen). */
  name: string;
  /** Case-folded pool identity. */
  key: string;
  /** Distinct slot ids, in first-seen order (caller may re-sort). */
  slots: string[];
}

/** Canonical identity for a variable / pool / slot name. */
export function variableKey(name: string): string {
  return name.trim().replace(/\s+/g, " ").toLowerCase();
}

/** Normalize a name for display (trim + collapse internal whitespace). */
export function normalizeName(name: string): string {
  return name.trim().replace(/\s+/g, " ");
}

/** Full display name for a pool slot (`color#1`). */
export function poolSlotName(poolName: string, slot: string): string {
  return `${normalizeName(poolName)}#${slot}`;
}

/** The once-declared list name used by a token (`color#1` → `color`). */
export function tokenDeclarationName(token: TokenMatch): string {
  return token.poolName ?? token.name;
}

/** Every token occurrence in `text`, in source order (duplicates included). */
export function findTokens(text: string): TokenMatch[] {
  const out: TokenMatch[] = [];
  TOKEN_RE.lastIndex = 0;
  let m: RegExpExecArray | null = TOKEN_RE.exec(text);
  while (m !== null) {
    const rawName = m[1] ?? "";
    const rawSlot = m[2];
    const base = normalizeName(rawName);
    if (base.length > 0) {
      const slot = rawSlot !== undefined && rawSlot.length > 0 ? rawSlot : null;
      const name = slot !== null ? poolSlotName(base, slot) : base;
      out.push({
        name,
        key: variableKey(name),
        poolName: slot !== null ? base : null,
        slot,
        start: m.index,
        end: m.index + m[0].length,
      });
    }
    m = TOKEN_RE.exec(text);
  }
  return out;
}

/**
 * Distinct NORMAL variable names across several fields, in first-seen order.
 * Pool slots (`{{color#1}}`) are excluded — they belong to pools, not variables.
 */
export function extractVariableNames(texts: readonly string[]): string[] {
  const seen = new Map<string, string>();
  for (const text of texts) {
    for (const tok of findTokens(text)) {
      if (tok.slot !== null) continue;
      if (!seen.has(tok.key)) seen.set(tok.key, tok.name);
    }
  }
  return [...seen.values()];
}

/**
 * Distinct list declarations across several fields, in first-seen order.
 * Numbered slots collapse to their shared base declaration, so
 * `{{color#1}}` + `{{color#2}}` produces one `color` mapping row.
 */
export function extractTokenDeclarationNames(
  texts: readonly string[],
): string[] {
  const seen = new Map<string, string>();
  for (const text of texts) {
    for (const token of findTokens(text)) {
      const name = tokenDeclarationName(token);
      const key = variableKey(name);
      if (!seen.has(key)) seen.set(key, name);
    }
  }
  return [...seen.values()];
}

/**
 * Distinct pools and their slots across several fields, in first-seen order.
 * Slot lists preserve first-seen order; expand sorts them for stable axis order.
 */
export function extractPoolRefs(texts: readonly string[]): PoolRef[] {
  const byKey = new Map<string, PoolRef>();
  for (const text of texts) {
    for (const tok of findTokens(text)) {
      if (tok.poolName === null || tok.slot === null) continue;
      const poolKey = variableKey(tok.poolName);
      const existing = byKey.get(poolKey);
      if (existing === undefined) {
        byKey.set(poolKey, {
          name: tok.poolName,
          key: poolKey,
          slots: [tok.slot],
        });
        continue;
      }
      if (!existing.slots.includes(tok.slot)) {
        existing.slots.push(tok.slot);
      }
    }
  }
  return [...byKey.values()];
}

/** True when `text` contains at least one well-formed token. */
export function hasTokens(text: string): boolean {
  TOKEN_RE.lastIndex = 0;
  return TOKEN_RE.test(text);
}

/** An unterminated `{{` — reported as an error so it never silently generates. */
export function hasUnclosedToken(text: string): boolean {
  return UNCLOSED_RE.test(text);
}

export interface RenderResult {
  text: string;
  /** Tokens with no matching variable — left verbatim and reported. */
  unresolved: string[];
}

/**
 * Substitute `values` (keyed by variableKey) into `text`.
 *
 * An unknown token is left untouched and reported rather than silently
 * emptied — a prompt that generates with a literal "{{subject}}" in it is a
 * wasted 40-second generation, so the caller blocks on `unresolved`.
 */
export function renderTemplate(
  text: string,
  values: ReadonlyMap<string, string>,
): RenderResult {
  const unresolved: string[] = [];
  TOKEN_RE.lastIndex = 0;
  const out = text.replace(
    TOKEN_RE,
    (whole: string, rawName: string, rawSlot?: string) => {
      const base = normalizeName(rawName);
      const slot = rawSlot !== undefined && rawSlot.length > 0 ? rawSlot : null;
      const name = slot !== null ? poolSlotName(base, slot) : base;
      const key = variableKey(name);
      const value = values.get(key);
      if (value === undefined) {
        unresolved.push(name);
        return whole;
      }
      return value;
    },
  );
  return { text: out, unresolved };
}

/**
 * Clean up the punctuation wreckage an empty option leaves behind.
 *
 * An empty option value is a first-class, useful thing — it's how you ask
 * "what does this look like with NO style modifier?" — but naive substitution
 * turns "a cat, {{style}}, at night" into "a cat, , at night". Prompt tools
 * that get this right tidy the result; ones that don't make you pad your
 * templates with hacks.
 */
export function tidyPrompt(text: string): string {
  return text
    .replace(/[ \t]+/g, " ")
    .replace(/\s+([,.;:!?])/g, "$1")
    .replace(/([,;:])\s*(?=[,;:])/g, "")
    .replace(/(^|\n)[ \t]*[,;:]+[ \t]*/g, "$1")
    .replace(/[ \t]*[,;:]+[ \t]*($|\n)/g, "$1")
    .replace(/ *\n */g, "\n")
    .trim();
}

/**
 * Sort slot ids for rotation: numeric when every slot is digits (`1,2,10`),
 * otherwise lexicographic. Deterministic — rotation must not depend on
 * accidental prompt word order.
 */
export function sortSlots(slots: readonly string[]): string[] {
  const unique = [...new Set(slots)];
  const allNumeric = unique.every((s) => /^\d+$/.test(s));
  if (allNumeric) {
    return unique.sort((a, b) => Number(a) - Number(b));
  }
  return unique.sort((a, b) => a.localeCompare(b));
}
