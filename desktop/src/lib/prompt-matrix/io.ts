/**
 * JSON export / import for prompt matrices.
 *
 * The on-disk shape is a thin wrapper around MatrixSpec so a matrix can be
 * copied to chat, edited by an agent, and pasted back without touching
 * localStorage.
 */

import type { MatrixSpec } from "./types";
import { coerceSpec, isMatrixSpec } from "./storage";

export const MATRIX_EXPORT_VERSION = 1 as const;

export interface MatrixExportFile {
  v: typeof MATRIX_EXPORT_VERSION;
  targetId: string;
  name?: string;
  spec: MatrixSpec;
}

export type MatrixImportResult =
  | { ok: true; spec: MatrixSpec; name: string | null; targetId: string }
  | { ok: false; error: string };

function slugify(name: string): string {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug.length > 0 ? slug : "matrix-template";
}

/** Pretty-printed JSON ready for clipboard or download. */
export function serializeMatrixExport(
  targetId: string,
  spec: MatrixSpec,
  name?: string,
): string {
  const trimmed = name?.trim();
  const payload: MatrixExportFile = {
    v: MATRIX_EXPORT_VERSION,
    targetId,
    spec,
    ...(trimmed !== undefined && trimmed.length > 0 ? { name: trimmed } : {}),
  };
  return JSON.stringify(payload, null, 2);
}

/** Suggested filename for a downloaded export. */
export function matrixExportFilename(name?: string): string {
  return `${slugify(name ?? "matrix-template")}.json`;
}

/**
 * Parse pasted or uploaded JSON. Accepts either the wrapped export file or a
 * bare MatrixSpec (for hand-edited / agent output that omits the envelope).
 */
export function parseMatrixImport(text: string): MatrixImportResult {
  const trimmed = text.trim();
  if (trimmed.length === 0) {
    return { ok: false, error: "Paste JSON or choose a file first." };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return {
      ok: false,
      error: "Invalid JSON — check for a missing comma or brace.",
    };
  }

  if (typeof parsed !== "object" || parsed === null) {
    return { ok: false, error: "Expected a JSON object." };
  }

  const obj = parsed as Partial<MatrixExportFile>;

  if (isMatrixSpec(parsed)) {
    return {
      ok: true,
      spec: coerceSpec(parsed),
      name: null,
      targetId: "image",
    };
  }

  if (obj.v !== MATRIX_EXPORT_VERSION) {
    return {
      ok: false,
      error: `Unsupported export version "${String(obj.v)}" — expected v${MATRIX_EXPORT_VERSION}.`,
    };
  }

  if (typeof obj.targetId !== "string" || obj.targetId.trim().length === 0) {
    return { ok: false, error: "Export is missing targetId." };
  }

  if (!isMatrixSpec(obj.spec)) {
    return {
      ok: false,
      error: "Export spec is missing fields, variables, strategy, or seed.",
    };
  }

  const importedName =
    typeof obj.name === "string" && obj.name.trim().length > 0
      ? obj.name.trim()
      : null;

  return {
    ok: true,
    spec: coerceSpec(obj.spec),
    name: importedName,
    targetId: obj.targetId,
  };
}

/** Trigger a browser download of the export JSON. */
export function downloadMatrixExport(
  targetId: string,
  spec: MatrixSpec,
  name?: string,
): void {
  const text = serializeMatrixExport(targetId, spec, name);
  const blob = new Blob([text], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = matrixExportFilename(name);
  anchor.click();
  URL.revokeObjectURL(url);
}
