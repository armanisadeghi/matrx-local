/**
 * Persistence for prompt matrices.
 *
 * Two things are stored, both in localStorage (the repo's convention for
 * renderer-side user state — see lib/settings.ts):
 *
 *  • the WORKING spec, per target — a half-built matrix with ten hand-typed
 *    options must survive a tab switch, a reload, and an app restart. Losing
 *    it would be the single most enraging bug this feature could have.
 *  • SAVED TEMPLATES — named, reusable matrices ("Portrait sweep"), because a
 *    template you can't keep is just a long prompt.
 *
 * Everything is validated on read: a corrupt or half-written entry resets to a
 * clean default rather than crashing the page or, worse, silently generating
 * from a malformed spec.
 */

import type {
  MatrixPool,
  MatrixSpec,
  MatrixVariable,
  TemplateField,
} from "./types";
import { randomSeed } from "./rng";

const WORKING_KEY = "matrx-prompt-matrix-working";
const TEMPLATES_KEY = "matrx-prompt-matrix-templates";

/** A named, reusable matrix. */
export interface SavedTemplate {
  id: string;
  name: string;
  targetId: string;
  spec: MatrixSpec;
  createdAt: number;
  updatedAt: number;
}

export function makeId(): string {
  return crypto.randomUUID();
}

export function emptySpec(fields: TemplateField[]): MatrixSpec {
  return {
    fields: fields.map((f) => ({ ...f, text: "" })),
    variables: [],
    pools: [],
    strategy: { kind: "cartesian" },
    seed: {
      mode: "fixed",
      baseSeed: randomSeed(),
      repeats: 1,
      rngSeed: randomSeed(),
    },
  };
}

/** Older saved specs predate pools — fill the field so the rest of the engine can assume it. */
export function coerceSpec(spec: MatrixSpec): MatrixSpec {
  return { ...spec, pools: Array.isArray(spec.pools) ? spec.pools : [] };
}

/**
 * Structural validation. Anything that fails is discarded — we would rather
 * hand back an empty matrix than let a malformed one reach the planner.
 * `pools` may be missing (pre-pool saves); those are coerced on load.
 */
export function isMatrixSpec(value: unknown): value is MatrixSpec {
  if (typeof value !== "object" || value === null) return false;
  const s = value as Partial<MatrixSpec>;
  if (!Array.isArray(s.fields) || !Array.isArray(s.variables)) return false;
  if (s.pools !== undefined && !Array.isArray(s.pools)) return false;
  if (typeof s.strategy !== "object" || s.strategy === null) return false;
  if (typeof s.seed !== "object" || s.seed === null) return false;
  const okFields = s.fields.every(
    (f) => typeof f?.id === "string" && typeof f?.text === "string",
  );
  const okVars = s.variables.every(
    (v: Partial<MatrixVariable>) =>
      typeof v?.id === "string" &&
      typeof v?.name === "string" &&
      Array.isArray(v?.options) &&
      typeof v?.binding === "object",
  );
  const okPools =
    s.pools === undefined ||
    s.pools.every(
      (p: Partial<MatrixPool>) =>
        typeof p?.id === "string" &&
        typeof p?.name === "string" &&
        Array.isArray(p?.options) &&
        (p.assign === "rotate" || p.assign === "same"),
    );
  return okFields && okVars && okPools;
}

function read<T>(key: string, guard: (v: unknown) => v is T): T | null {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return null;
    const parsed: unknown = JSON.parse(raw);
    return guard(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (err) {
    // Quota exceeded or storage disabled. LOUD, never silent: the user is
    // about to lose work and needs to know the save did not happen.
    console.error(`[prompt-matrix] Could not persist "${key}":`, err);
  }
}

// ── working spec (per target) ────────────────────────────────────────────────

export function loadWorkingSpec(
  targetId: string,
  fields: TemplateField[],
): MatrixSpec {
  const all = read(
    WORKING_KEY,
    (v): v is Record<string, unknown> => typeof v === "object" && v !== null,
  );
  const mine = all?.[targetId];
  if (mine !== undefined && isMatrixSpec(mine)) {
    // Reconcile the stored fields against the target's CURRENT fields, so a
    // target that gained or lost a field doesn't resurrect a stale one.
    const byId = new Map(mine.fields.map((f) => [f.id, f.text]));
    return coerceSpec({
      ...mine,
      fields: fields.map((f) => ({ ...f, text: byId.get(f.id) ?? "" })),
    });
  }
  return emptySpec(fields);
}

export function saveWorkingSpec(targetId: string, spec: MatrixSpec): void {
  const all =
    read(
      WORKING_KEY,
      (v): v is Record<string, unknown> => typeof v === "object" && v !== null,
    ) ?? {};
  write(WORKING_KEY, { ...all, [targetId]: spec });
}

// ── saved templates ──────────────────────────────────────────────────────────

function isTemplateArray(value: unknown): value is SavedTemplate[] {
  return (
    Array.isArray(value) &&
    value.every(
      (t: Partial<SavedTemplate>) =>
        typeof t?.id === "string" &&
        typeof t?.name === "string" &&
        isMatrixSpec(t?.spec),
    )
  );
}

export function loadTemplates(targetId: string): SavedTemplate[] {
  const all = read(TEMPLATES_KEY, isTemplateArray) ?? [];
  return all
    .filter((t) => t.targetId === targetId)
    .sort((a, b) => b.updatedAt - a.updatedAt);
}

/** Create or update by name — saving "Portrait sweep" twice overwrites it. */
export function saveTemplate(
  targetId: string,
  name: string,
  spec: MatrixSpec,
): SavedTemplate {
  const all = read(TEMPLATES_KEY, isTemplateArray) ?? [];
  const now = Date.now();
  const trimmed = name.trim();
  const existing = all.find(
    (t) =>
      t.targetId === targetId && t.name.toLowerCase() === trimmed.toLowerCase(),
  );
  const saved: SavedTemplate = existing
    ? { ...existing, spec, updatedAt: now }
    : {
        id: makeId(),
        name: trimmed,
        targetId,
        spec,
        createdAt: now,
        updatedAt: now,
      };
  write(TEMPLATES_KEY, [...all.filter((t) => t.id !== saved.id), saved]);
  return saved;
}

export function deleteTemplate(id: string): void {
  const all = read(TEMPLATES_KEY, isTemplateArray) ?? [];
  write(
    TEMPLATES_KEY,
    all.filter((t) => t.id !== id),
  );
}

/** Validate templates coming back from the on-disk engine store. */
export function sanitizeSavedTemplates(raw: unknown[]): SavedTemplate[] {
  return raw
    .filter(
      (t: Partial<SavedTemplate>): t is SavedTemplate =>
        typeof t?.id === "string" &&
        typeof t?.name === "string" &&
        typeof t?.targetId === "string" &&
        isMatrixSpec(t?.spec) &&
        typeof t?.createdAt === "number" &&
        typeof t?.updatedAt === "number",
    )
    .map((t) => ({ ...t, spec: coerceSpec(t.spec) }))
    .sort((a, b) => b.updatedAt - a.updatedAt);
}

/** Replace the localStorage template cache (used after a disk load/save). */
export function replaceTemplatesCache(templates: SavedTemplate[]): void {
  write(TEMPLATES_KEY, templates);
}
