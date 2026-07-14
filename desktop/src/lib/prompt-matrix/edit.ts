/**
 * Pure edit operations for the prompt-matrix working spec.
 *
 * These keep template text, variables, and pools in one coherent state. UI
 * components should call the hook actions; the hook delegates here so behavior
 * is testable without a browser.
 */

import {
  extractPoolRefs,
  extractVariableNames,
  findTokens,
  normalizeName,
  variableKey,
} from "./parse";
import {
  poolFromLibraryEntry,
  variableFromLibraryEntry,
  type LibraryEntry,
} from "./library";
import { makeId } from "./storage";
import { syncPoolsWithTokens, syncVariablesWithTokens } from "./targets";
import type { MatrixSpec } from "./types";

function syncSpecTokens(spec: MatrixSpec): MatrixSpec {
  const texts = spec.fields.map((field) => field.text);
  return {
    ...spec,
    pools: syncPoolsWithTokens(spec.pools ?? [], extractPoolRefs(texts), makeId),
    variables: syncVariablesWithTokens(
      spec.variables,
      extractVariableNames(texts),
      makeId,
    ),
  };
}

function replacePlainVariableTokens(
  text: string,
  oldKey: string,
  nextName: string,
): string {
  const matches = findTokens(text).filter(
    (tok) => tok.slot === null && tok.key === oldKey,
  );
  if (matches.length === 0) return text;

  let out = "";
  let cursor = 0;
  for (const tok of matches) {
    out += text.slice(cursor, tok.start);
    out += `{{${nextName}}}`;
    cursor = tok.end;
  }
  return out + text.slice(cursor);
}

function fieldsContainPlainToken(fields: MatrixSpec["fields"], name: string) {
  const key = variableKey(name);
  return fields.some((field) =>
    findTokens(field.text).some((tok) => tok.slot === null && tok.key === key),
  );
}

function insertPlainTokenIntoPrimaryField(
  fields: MatrixSpec["fields"],
  name: string,
): MatrixSpec["fields"] {
  if (fieldsContainPlainToken(fields, name)) return [...fields];

  const token = `{{${name}}}`;
  const preferredIdx = fields.findIndex((field) => field.id === "prompt");
  const idx = preferredIdx >= 0 ? preferredIdx : 0;
  return fields.map((field, i) =>
    i === idx
      ? {
          ...field,
          text: field.text.trim().length > 0 ? `${field.text} ${token}` : token,
        }
      : field,
  );
}

export function renameVariableInSpec(
  spec: MatrixSpec,
  variableId: string,
  name: string,
): MatrixSpec {
  const nextName = normalizeName(name);
  if (nextName.length === 0) return spec;

  const variable = spec.variables.find((v) => v.id === variableId);
  if (variable === undefined || variable.name === nextName) return spec;

  const oldKey = variableKey(variable.name);
  const variables = spec.variables.map((v) =>
    v.id === variableId ? { ...v, name: nextName } : v,
  );

  if (variable.binding.kind !== "text") {
    return { ...spec, pools: spec.pools ?? [], variables };
  }

  return syncSpecTokens({
    ...spec,
    pools: spec.pools ?? [],
    fields: spec.fields.map((field) => ({
      ...field,
      text: replacePlainVariableTokens(field.text, oldKey, nextName),
    })),
    variables,
  });
}

export function insertLibraryEntryInSpec(
  spec: MatrixSpec,
  entry: LibraryEntry,
): MatrixSpec {
  if (entry.kind === "pool") {
    const pool = poolFromLibraryEntry(entry);
    const pools = spec.pools ?? [];
    const idx = pools.findIndex(
      (p) => variableKey(p.name) === variableKey(pool.name),
    );
    if (idx >= 0) {
      const next = [...pools];
      const existing = next[idx];
      if (existing === undefined) return spec;
      next[idx] = {
        ...existing,
        options: pool.options,
        assign: pool.assign,
        enabled: true,
      };
      return { ...spec, pools: next };
    }
    return { ...spec, pools: [...pools, pool] };
  }

  const variable = variableFromLibraryEntry(entry);
  const fields = insertPlainTokenIntoPrimaryField(spec.fields, variable.name);
  const idx = spec.variables.findIndex(
    (v) =>
      v.binding.kind === "text" && variableKey(v.name) === variableKey(variable.name),
  );
  if (idx >= 0) {
    const variables = [...spec.variables];
    const existing = variables[idx];
    if (existing === undefined) return spec;
    variables[idx] = { ...existing, options: variable.options, enabled: true };
    return syncSpecTokens({
      ...spec,
      fields,
      pools: spec.pools ?? [],
      variables,
    });
  }

  return syncSpecTokens({
    ...spec,
    fields,
    pools: spec.pools ?? [],
    variables: [...spec.variables, variable],
  });
}
