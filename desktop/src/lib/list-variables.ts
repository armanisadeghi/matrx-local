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
    return (
      listVariableName !== null && variableKey(listVariableName) === key
    );
  });
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
