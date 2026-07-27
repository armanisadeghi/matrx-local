/**
 * Prompt-matrix library — reusable pools and variables stored on disk via the
 * engine (`~/.matrx/prompt-matrix/library.json`).
 *
 * This is the shareable option-list shelf: save "Colors" once, insert into any
 * matrix. Distinct from named templates (full MatrixSpec) which live in
 * templates.json beside it.
 */

import type {
  MatrixOption,
  MatrixPool,
  MatrixVariable,
} from "./types";
import { makeId } from "./storage";

export type LibraryEntryKind = "pool" | "variable";

export interface LibraryEntry {
  id: string;
  name: string;
  kind: LibraryEntryKind;
  options: MatrixOption[];
  updatedAt: number;
}

export function isLibraryEntry(value: unknown): value is LibraryEntry {
  if (typeof value !== "object" || value === null) return false;
  const e = value as Partial<LibraryEntry>;
  return (
    typeof e.id === "string" &&
    typeof e.name === "string" &&
    (e.kind === "pool" || e.kind === "variable") &&
    Array.isArray(e.options) &&
    typeof e.updatedAt === "number"
  );
}

export function sanitizeLibraryEntries(raw: unknown[]): LibraryEntry[] {
  return raw
    .filter(isLibraryEntry)
    .map(({ id, name, kind, options, updatedAt }) => ({
      id,
      name,
      kind,
      options,
      updatedAt,
    }))
    .sort((a, b) => b.updatedAt - a.updatedAt);
}

export function libraryEntryFromPool(
  pool: MatrixPool,
  name?: string,
): LibraryEntry {
  return {
    id: makeId(),
    name: (name ?? pool.name).trim() || pool.name,
    kind: "pool",
    options: pool.options.map((o) => ({ ...o, id: makeId() })),
    updatedAt: Date.now(),
  };
}

export function libraryEntryFromVariable(
  variable: MatrixVariable,
  name?: string,
): LibraryEntry {
  return {
    id: makeId(),
    name: (name ?? variable.name).trim() || variable.name,
    kind: "variable",
    options: variable.options.map((o) => ({ ...o, id: makeId() })),
    updatedAt: Date.now(),
  };
}

/** Turn a library pool entry into a MatrixPool ready to drop into the spec. */
export function poolFromLibraryEntry(entry: LibraryEntry): MatrixPool {
  return {
    id: makeId(),
    name: entry.name,
    options: entry.options.map((o) => ({ ...o, id: makeId() })),
    baselineOptionId: null,
    enabled: true,
  };
}

/** Turn a library variable entry into a text MatrixVariable. */
export function variableFromLibraryEntry(entry: LibraryEntry): MatrixVariable {
  return {
    id: makeId(),
    name: entry.name,
    binding: { kind: "text" },
    options: entry.options.map((o) => ({ ...o, id: makeId() })),
    baselineOptionId: null,
    linkGroup: null,
    enabled: true,
  };
}
