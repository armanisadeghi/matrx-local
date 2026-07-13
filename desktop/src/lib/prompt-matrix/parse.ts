/**
 * Template parsing + rendering for {{variable}} tokens.
 *
 * Deliberately tiny and dependency-free: a token is `{{name}}`, where `name`
 * is letters/digits/underscore/hyphen/space (trimmed, internal whitespace
 * collapsed). Names are matched case-insensitively — {{Subject}} and
 * {{subject}} are the same variable — but the first spelling encountered is
 * kept for display.
 */

/** Matches a {{token}} and captures its raw inner name. */
const TOKEN_RE = /\{\{\s*([A-Za-z0-9_\- ]+?)\s*\}\}/g;

/** A malformed `{{` that never closes — surfaced to the user, never ignored. */
const UNCLOSED_RE = /\{\{(?![^{}]*\}\})/;

export interface TokenMatch {
  /** Display spelling, as first written by the user. */
  name: string;
  /** Case-folded identity key. */
  key: string;
  start: number;
  end: number;
}

/** Canonical identity for a variable name. */
export function variableKey(name: string): string {
  return name.trim().replace(/\s+/g, " ").toLowerCase();
}

/** Normalize a name for display (trim + collapse internal whitespace). */
export function normalizeName(name: string): string {
  return name.trim().replace(/\s+/g, " ");
}

/** Every token occurrence in `text`, in source order (duplicates included). */
export function findTokens(text: string): TokenMatch[] {
  const out: TokenMatch[] = [];
  TOKEN_RE.lastIndex = 0;
  let m: RegExpExecArray | null = TOKEN_RE.exec(text);
  while (m !== null) {
    const raw = m[1] ?? "";
    const name = normalizeName(raw);
    if (name.length > 0) {
      out.push({
        name,
        key: variableKey(name),
        start: m.index,
        end: m.index + m[0].length,
      });
    }
    m = TOKEN_RE.exec(text);
  }
  return out;
}

/** Distinct token names across several fields, in first-seen order. */
export function extractVariableNames(texts: readonly string[]): string[] {
  const seen = new Map<string, string>();
  for (const text of texts) {
    for (const tok of findTokens(text)) {
      if (!seen.has(tok.key)) seen.set(tok.key, tok.name);
    }
  }
  return [...seen.values()];
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
  const out = text.replace(TOKEN_RE, (whole: string, raw: string) => {
    const key = variableKey(raw);
    const value = values.get(key);
    if (value === undefined) {
      unresolved.push(normalizeName(raw));
      return whole;
    }
    return value;
  });
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
