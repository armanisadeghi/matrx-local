import type { NamedList } from "@/lib/list-library/types";
import { normalizeName, variableKey } from "@/lib/prompt-matrix";

/**
 * Turn a human-facing list label into a token name accepted by prompt-matrix.
 * Keep readable words and existing `_` / `-` separators; punctuation becomes
 * spacing so a label such as "People / celebrities" remains recognizable.
 */
export function variableNameForList(listName: string): string | null {
  const name = normalizeName(listName.replace(/[^A-Za-z0-9_\- ]+/g, " "));
  return name.length > 0 ? name : null;
}

export function variableTokenForList(listName: string): string | null {
  const name = variableNameForList(listName);
  return name === null ? null : `{{${name}}}`;
}

/** Saved lists whose generated token identity matches a prompt variable. */
export function listsMatchingVariableName(
  lists: readonly NamedList[],
  variableName: string,
): NamedList[] {
  const key = variableKey(variableName);
  return lists.filter((list) => {
    const listVariableName = variableNameForList(list.name);
    return listVariableName !== null && variableKey(listVariableName) === key;
  });
}

/** Loose identity for conservative near matching (spaces, `_`, and `-` equivalent). */
function looseVariableKey(name: string): string {
  return variableKey(name.replace(/[_-]+/g, " "));
}

/**
 * Pick at most one saved list for a prompt variable.
 * Exact normalized matches win; otherwise a small near-match pass (prefix / whole-word).
 * Returns null when ambiguous or nothing close enough.
 */
export function resolveListForVariableName(
  lists: readonly NamedList[],
  variableName: string,
): NamedList | null {
  const exact = listsMatchingVariableName(lists, variableName);
  if (exact.length === 1) return exact[0] ?? null;
  if (exact.length > 1) return null;

  const varKey = looseVariableKey(variableName);
  if (varKey.length === 0) return null;

  const looseExact = lists.filter((list) => {
    const listVariableName = variableNameForList(list.name);
    return (
      listVariableName !== null && looseVariableKey(listVariableName) === varKey
    );
  });
  if (looseExact.length === 1) return looseExact[0] ?? null;
  if (looseExact.length > 1) return null;

  type Scored = { list: NamedList; score: number };
  const scored: Scored[] = [];

  for (const list of lists) {
    const listVariableName = variableNameForList(list.name);
    if (listVariableName === null) continue;
    const listKey = looseVariableKey(listVariableName);
    if (listKey.length === 0) continue;

    let score = 0;
    if (listKey.startsWith(`${varKey} `)) {
      // {{camera}} → "Camera angles"
      score = 90;
    } else if (listKey.split(" ")[0] === varKey) {
      // {{people}} → "People celebrities"
      score = 85;
    } else if (listKey.split(" ").includes(varKey)) {
      // {{angles}} → "Camera angles"
      score = 80;
    }

    if (score > 0) scored.push({ list, score });
  }

  if (scored.length === 0) return null;

  const topScore = Math.max(...scored.map((row) => row.score));
  const best = scored.filter((row) => row.score === topScore);
  return best.length === 1 ? (best[0]?.list ?? null) : null;
}

export interface VariableTokenInsertion {
  text: string;
  cursor: number;
  token: string;
  variableName: string;
}

/** Pure, clamped textarea edit used by the variable picker. */
export function insertListVariableToken(
  text: string,
  listName: string,
  selectionStart: number,
  selectionEnd: number,
): VariableTokenInsertion | null {
  const variableName = variableNameForList(listName);
  if (variableName === null) return null;

  const token = `{{${variableName}}}`;
  const start = Math.max(0, Math.min(text.length, selectionStart));
  const end = Math.max(start, Math.min(text.length, selectionEnd));
  return {
    text: `${text.slice(0, start)}${token}${text.slice(end)}`,
    cursor: start + token.length,
    token,
    variableName,
  };
}

export interface SampledListValue {
  listId: string;
  listName: string;
  optionIndex: number;
  value: string;
}

export type SampledListValues = ReadonlyMap<string, SampledListValue>;

/**
 * Pick one enabled, non-empty option from every list. A reroll avoids the
 * previous option whenever a list has more than one choice.
 */
export function sampleListValues(
  lists: readonly NamedList[],
  previous: SampledListValues | null = null,
  random: () => number = Math.random,
): Map<string, SampledListValue> {
  const sampled = new Map<string, SampledListValue>();
  for (const list of lists) {
    const options = list.options
      .filter((option) => option.enabled && option.value.trim().length > 0)
      .map((option) => option.value.trim());
    if (options.length === 0) continue;

    const priorIndex = previous?.get(list.id)?.optionIndex;
    let optionIndex: number;
    if (
      options.length > 1 &&
      priorIndex !== undefined &&
      priorIndex >= 0 &&
      priorIndex < options.length
    ) {
      const alternate = Math.floor(
        Math.max(0, Math.min(0.999999999, random())) * (options.length - 1),
      );
      optionIndex = alternate >= priorIndex ? alternate + 1 : alternate;
    } else {
      optionIndex = Math.floor(
        Math.max(0, Math.min(0.999999999, random())) * options.length,
      );
    }

    sampled.set(list.id, {
      listId: list.id,
      listName: list.name,
      optionIndex,
      value: options[optionIndex] ?? options[0] ?? "",
    });
  }
  return sampled;
}
