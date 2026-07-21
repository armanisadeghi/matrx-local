/**
 * List library — named option lists stored on disk via the engine.
 *
 * A list is just a name + options. Whether it becomes a batch variable or a
 * pool is decided at map-time in the matrix UI, not at storage time.
 */

import type { MatrixOption } from "@/lib/prompt-matrix/types";

export interface NamedList {
  /** Stable identity — survives renames. */
  id: string;
  /** Display label only — independent from matrix token names. */
  name: string;
  description: string;
  options: MatrixOption[];
  createdAt: number;
  updatedAt: number;
}

export function isNamedList(value: unknown): value is NamedList {
  if (typeof value !== "object" || value === null) return false;
  const row = value as Partial<NamedList>;
  return (
    typeof row.id === "string" &&
    typeof row.name === "string" &&
    typeof row.description === "string" &&
    Array.isArray(row.options) &&
    typeof row.createdAt === "number" &&
    typeof row.updatedAt === "number"
  );
}

export function sanitizeNamedLists(raw: unknown[]): NamedList[] {
  return raw
    .filter(isNamedList)
    .map((row) => ({
      ...row,
      name: row.name.trim() || "Untitled list",
      description: row.description.trim(),
    }))
    .sort((a, b) => b.updatedAt - a.updatedAt);
}

export function makeListId(): string {
  return crypto.randomUUID();
}

export function emptyNamedList(name = "New list"): NamedList {
  const now = Date.now();
  return {
    id: makeListId(),
    name,
    description: "",
    options: [],
    createdAt: now,
    updatedAt: now,
  };
}

/** Import payload — one list or a bundle of lists. */
export interface NamedListImportBundle {
  v?: number;
  lists: NamedList[];
}

export function parseNamedListImport(text: string): NamedList[] {
  const parsed: unknown = JSON.parse(text);
  const now = Date.now();

  if (Array.isArray(parsed)) {
    return sanitizeNamedLists(
      parsed.map((row) => normalizeImportRow(row, now)),
    );
  }

  if (typeof parsed === "object" && parsed !== null) {
    const bundle = parsed as Partial<NamedListImportBundle> & {
      name?: string;
      options?: unknown;
    };
    if (Array.isArray(bundle.lists)) {
      return sanitizeNamedLists(
        bundle.lists.map((row) => normalizeImportRow(row, now)),
      );
    }
    const single = normalizeImportRow(bundle, now);
    if (single) return sanitizeNamedLists([single]);
  }

  throw new Error(
    'Expected a list object, {"lists":[…]}, or a bare array of lists.',
  );
}

function normalizeImportRow(row: unknown, now: number): NamedList {
  if (isNamedList(row)) {
    return {
      ...row,
      id: makeListId(),
      createdAt: row.createdAt || now,
      updatedAt: now,
      options: row.options.map((o) => ({
        ...o,
        id: crypto.randomUUID(),
      })),
    };
  }

  if (typeof row === "object" && row !== null) {
    const obj = row as {
      name?: unknown;
      description?: unknown;
      options?: unknown;
      values?: unknown;
    };
    const name =
      typeof obj.name === "string" && obj.name.trim().length > 0
        ? obj.name.trim()
        : "Imported list";
    const options = optionsFromImport(obj.options ?? obj.values);
    return {
      id: makeListId(),
      name,
      description:
        typeof obj.description === "string" ? obj.description.trim() : "",
      options,
      createdAt: now,
      updatedAt: now,
    };
  }

  throw new Error(
    "Each imported list must be an object with name and options.",
  );
}

function optionsFromImport(raw: unknown): MatrixOption[] {
  if (!Array.isArray(raw)) {
    throw new Error("options must be an array");
  }
  return raw.map((item) => {
    if (typeof item === "string") {
      const value = item.trim();
      return {
        id: crypto.randomUUID(),
        value,
        enabled: value.length > 0,
      };
    }
    if (typeof item === "object" && item !== null) {
      const o = item as {
        value?: unknown;
        label?: unknown;
        enabled?: unknown;
      };
      const value = typeof o.value === "string" ? o.value : "";
      const option: MatrixOption = {
        id: crypto.randomUUID(),
        value,
        enabled: o.enabled !== false,
      };
      if (typeof o.label === "string" && o.label.length > 0) {
        option.label = o.label;
      }
      return option;
    }
    return {
      id: crypto.randomUUID(),
      value: String(item),
      enabled: true,
    };
  });
}

export function exportNamedLists(lists: readonly NamedList[]): string {
  return JSON.stringify({ v: 2, lists }, null, 2);
}

export function exportNamedList(list: NamedList): string {
  return JSON.stringify(list, null, 2);
}
