/**
 * AI-friendly copy/export/import for the list library.
 *
 * Payloads embed `_matrx.instructions` so pasted context teaches the model how
 * to return valid updates (replace, patch, append, create, merge-all).
 */

import type { MatrixOption } from "@/lib/prompt-matrix/types";
import { makeId } from "@/lib/prompt-matrix/storage";
import { makeListId, parseNamedListImport, type NamedList } from "./types";

export const LIST_LIBRARY_AI_KIND = "matrx-list-library";
export const LIST_LIBRARY_AI_VERSION = 1;

export type AiListOperation =
  | "create-list"
  | "replace-list"
  | "patch-list"
  | "append-options"
  | "merge-all"
  | "replace-all";

export interface AiListTarget {
  id?: string;
  name?: string;
}

/** Compact list shape — options as plain strings for models. */
export interface AiListShape {
  id?: string;
  name: string;
  description?: string;
  options: string[];
}

export interface AiListEnvelope {
  _matrx: {
    kind: typeof LIST_LIBRARY_AI_KIND;
    version: typeof LIST_LIBRARY_AI_VERSION;
    scope?: "single-list" | "all-lists";
    instructions: string;
  };
  operation: AiListOperation;
  target?: AiListTarget;
  list?: AiListShape;
  lists?: AiListShape[];
}

const AI_INSTRUCTIONS = `Matrx List Library — AI interchange format

Return ONLY valid JSON matching this envelope (no markdown fences unless the user asks).

OPERATIONS — set "operation" on your response:

• replace-list — Replace one list entirely.
  Required: target.id (preferred) OR target.name, and list { name, description?, options: string[] }.

• patch-list — Change only specific fields on an existing list.
  Required: target.id. Include ONLY changed fields inside list (name, description, and/or options).
  If options is present, it REPLACES the full option list for that list.

• append-options — Add new options without removing existing ones.
  Required: target.id. list.options = new strings to append at the end.

• create-list — Add a brand-new list.
  Omit target. list must include name and options (string[]).

• merge-all — Update or add several lists at once.
  Required: lists[]. Match existing rows by id when present, otherwise by name (case-insensitive).
  Rows without a matching id/name are created.

• replace-all — Replace the entire library.
  Required: lists[] as the complete new set.

OPTIONS: each entry is one plain string (one batch substitution value). Skip empty strings.

The user imports your JSON via Media Generation → Lists → Import.
Use merge-all or replace-list/patch/append for partial updates; use replace-all only when replacing everything.`;

function optionsToStrings(options: readonly MatrixOption[]): string[] {
  return options
    .filter((o) => o.enabled && o.value.trim().length > 0)
    .map((o) => o.value.trim());
}

function stringsToOptions(values: readonly string[]): MatrixOption[] {
  return values
    .map((raw) => raw.trim())
    .filter((value) => value.length > 0)
    .map((value) => ({
      id: makeId(),
      value,
      enabled: true,
    }));
}

export function listToAiShape(list: NamedList): AiListShape {
  return {
    id: list.id,
    name: list.name,
    description: list.description,
    options: optionsToStrings(list.options),
  };
}

export function buildAiExportForList(list: NamedList): string {
  const envelope: AiListEnvelope = {
    _matrx: {
      kind: LIST_LIBRARY_AI_KIND,
      version: LIST_LIBRARY_AI_VERSION,
      scope: "single-list",
      instructions: AI_INSTRUCTIONS,
    },
    operation: "replace-list",
    target: { id: list.id, name: list.name },
    list: listToAiShape(list),
  };
  return JSON.stringify(envelope, null, 2);
}

export function buildAiExportForAll(lists: readonly NamedList[]): string {
  const envelope: AiListEnvelope = {
    _matrx: {
      kind: LIST_LIBRARY_AI_KIND,
      version: LIST_LIBRARY_AI_VERSION,
      scope: "all-lists",
      instructions: AI_INSTRUCTIONS,
    },
    operation: "merge-all",
    lists: lists.map(listToAiShape),
  };
  return JSON.stringify(envelope, null, 2);
}

function isAiEnvelope(value: unknown): value is AiListEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const row = value as Partial<AiListEnvelope>;
  if (row.operation && typeof row.operation === "string") return true;
  const matrx = row._matrx;
  return (
    typeof matrx === "object" &&
    matrx !== null &&
    (matrx as { kind?: string }).kind === LIST_LIBRARY_AI_KIND
  );
}

function findListByTarget(
  current: readonly NamedList[],
  target?: AiListTarget,
): NamedList | undefined {
  if (!target) return undefined;
  if (target.id) {
    const byId = current.find((row) => row.id === target.id);
    if (byId) return byId;
  }
  if (target.name) {
    const key = target.name.trim().toLowerCase();
    return current.find((row) => row.name.trim().toLowerCase() === key);
  }
  return undefined;
}

function aiShapeToNamedList(
  shape: AiListShape,
  existing?: NamedList,
): NamedList {
  const now = Date.now();
  return {
    id: existing?.id ?? shape.id ?? makeListId(),
    name: shape.name.trim() || existing?.name || "Untitled list",
    description:
      shape.description !== undefined
        ? shape.description.trim()
        : (existing?.description ?? ""),
    options: stringsToOptions(shape.options),
    createdAt: existing?.createdAt ?? now,
    updatedAt: now,
  };
}

function normalizeAiListShape(
  raw: unknown,
  opts?: { optionsRequired?: boolean },
): AiListShape | null {
  const optionsRequired = opts?.optionsRequired ?? true;
  if (typeof raw !== "object" || raw === null) return null;
  const row = raw as {
    id?: unknown;
    name?: unknown;
    description?: unknown;
    options?: unknown;
    values?: unknown;
  };
  if (typeof row.name !== "string") return null;
  const optRaw = row.options ?? row.values;
  let options: string[] = [];
  if (Array.isArray(optRaw)) {
    options = optRaw
      .map((item) => {
        if (typeof item === "string") return item.trim();
        if (typeof item === "object" && item !== null && "value" in item) {
          const v = (item as { value?: unknown }).value;
          return typeof v === "string" ? v.trim() : "";
        }
        return String(item).trim();
      })
      .filter((value) => value.length > 0);
  } else if (optionsRequired) {
    return null;
  }
  const shape: AiListShape = {
    name: row.name.trim(),
    options,
  };
  if (typeof row.id === "string") shape.id = row.id;
  if (typeof row.description === "string") shape.description = row.description;
  return shape;
}

function mergeListsByIdentity(
  current: readonly NamedList[],
  incoming: NamedList[],
): NamedList[] {
  const next = [...current];
  for (const row of incoming) {
    const idxById = next.findIndex((existing) => existing.id === row.id);
    if (idxById >= 0) {
      next[idxById] = row;
      continue;
    }
    const key = row.name.trim().toLowerCase();
    const idxByName = next.findIndex(
      (existing) => existing.name.trim().toLowerCase() === key,
    );
    if (idxByName >= 0) {
      next[idxByName] = { ...row, id: next[idxByName]!.id };
      continue;
    }
    next.unshift(row);
  }
  return next;
}

export interface AiListImportResult {
  lists: NamedList[];
  /** Human-readable summary for UI toasts. */
  summary: string;
  /** When true, caller should replace rather than merge with current. */
  replaceAll: boolean;
}

export function applyAiListImport(
  current: readonly NamedList[],
  text: string,
): AiListImportResult {
  const parsed: unknown = JSON.parse(text);

  if (!isAiEnvelope(parsed)) {
    const imported = parseNamedListImport(text);
    return {
      lists: imported,
      summary: `Imported ${imported.length} list${imported.length === 1 ? "" : "s"}`,
      replaceAll: false,
    };
  }

  const operation = parsed.operation ?? "merge-all";
  const now = Date.now();

  switch (operation) {
    case "replace-all": {
      const shapes = parsed.lists ?? (parsed.list ? [parsed.list] : []);
      const lists = shapes
        .map((row) => normalizeAiListShape(row))
        .filter((row): row is AiListShape => row !== null)
        .map((row) => aiShapeToNamedList(row));
      return {
        lists,
        summary: `Replaced library with ${lists.length} list${lists.length === 1 ? "" : "s"}`,
        replaceAll: true,
      };
    }
    case "merge-all": {
      const shapes = parsed.lists ?? [];
      const incoming = shapes
        .map((row) => normalizeAiListShape(row))
        .filter((row): row is AiListShape => row !== null)
        .map((shape) => {
          const target: AiListTarget = { name: shape.name };
          if (shape.id) target.id = shape.id;
          const existing = findListByTarget(current, target) ?? undefined;
          return aiShapeToNamedList(shape, existing);
        });
      const lists = mergeListsByIdentity(current, incoming);
      return {
        lists,
        summary: `Merged ${incoming.length} list${incoming.length === 1 ? "" : "s"}`,
        replaceAll: false,
      };
    }
    case "create-list": {
      const shape = normalizeAiListShape(parsed.list);
      if (!shape) throw new Error("create-list requires a list object.");
      const created = aiShapeToNamedList(shape);
      return {
        lists: [created, ...current],
        summary: `Created list "${created.name}"`,
        replaceAll: false,
      };
    }
    case "replace-list": {
      const shape = normalizeAiListShape(parsed.list);
      if (!shape) throw new Error("replace-list requires a list object.");
      const existing = findListByTarget(current, parsed.target);
      if (!existing) {
        throw new Error(
          "replace-list: target list not found (need target.id or target.name).",
        );
      }
      const replaced = aiShapeToNamedList(shape, existing);
      const lists = current.map((row) =>
        row.id === existing.id ? replaced : row,
      );
      return {
        lists,
        summary: `Replaced list "${replaced.name}"`,
        replaceAll: false,
      };
    }
    case "patch-list": {
      const shape = normalizeAiListShape(parsed.list, {
        optionsRequired: false,
      });
      if (!shape) throw new Error("patch-list requires a list object.");
      const existing = findListByTarget(current, parsed.target);
      if (!existing) {
        throw new Error("patch-list: target list not found (need target.id).");
      }
      const patched: NamedList = {
        ...existing,
        name: shape.name.trim() || existing.name,
        description:
          shape.description !== undefined
            ? shape.description.trim()
            : existing.description,
        options:
          shape.options.length > 0
            ? stringsToOptions(shape.options)
            : existing.options,
        updatedAt: now,
      };
      const lists = current.map((row) =>
        row.id === existing.id ? patched : row,
      );
      return {
        lists,
        summary: `Patched list "${patched.name}"`,
        replaceAll: false,
      };
    }
    case "append-options": {
      const shape = normalizeAiListShape(parsed.list);
      if (!shape) throw new Error("append-options requires list.options.");
      const existing = findListByTarget(current, parsed.target);
      if (!existing) {
        throw new Error(
          "append-options: target list not found (need target.id).",
        );
      }
      const appended = stringsToOptions(shape.options);
      const patched: NamedList = {
        ...existing,
        options: [...existing.options, ...appended],
        updatedAt: now,
      };
      const lists = current.map((row) =>
        row.id === existing.id ? patched : row,
      );
      return {
        lists,
        summary: `Added ${appended.length} option${appended.length === 1 ? "" : "s"} to "${patched.name}"`,
        replaceAll: false,
      };
    }
    default:
      throw new Error(`Unknown list-library operation: ${String(operation)}`);
  }
}
