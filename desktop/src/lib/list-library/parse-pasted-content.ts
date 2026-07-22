/**
 * Smart paste parser for list library content.
 *
 * Accepts plain text, delimited values, or JSON (arrays, list objects, AI envelopes)
 * and returns structured data the UI can apply.
 */

import {
  LIST_LIBRARY_AI_KIND,
  type AiListEnvelope,
  type AiListShape,
} from "./ai-export";
import { isNamedList, type NamedList } from "./types";

export type ParsedPasteFormat =
  | "json-array"
  | "json-list-object"
  | "json-lists-bundle"
  | "ai-envelope"
  | "lines"
  | "comma-separated"
  | "semicolon-separated"
  | "single-value";

export interface ParsedListShape {
  name: string;
  description?: string;
  options: string[];
}

export type ParsedPasteContent =
  | {
      kind: "options";
      format: ParsedPasteFormat;
      options: string[];
    }
  | {
      kind: "single-list";
      format: ParsedPasteFormat;
      list: ParsedListShape;
    }
  | {
      kind: "multi-list";
      format: ParsedPasteFormat;
      lists: ParsedListShape[];
    };

function trimValues(values: readonly string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of values) {
    const value = raw.trim();
    if (value.length === 0) continue;
    const key = value.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(value);
  }
  return out;
}

function stripListMarker(line: string): string {
  const trimmed = line.trim();
  const bullet = trimmed.match(/^[-*•]\s+(.*)$/);
  if (bullet) return bullet[1]!.trim();
  const numbered = trimmed.match(/^\d+[.)]\s+(.*)$/);
  if (numbered) return numbered[1]!.trim();
  return trimmed;
}

function splitLines(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map(stripListMarker)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

/** Split comma-separated values, respecting double-quoted segments. */
export function splitCommaSeparated(text: string): string[] {
  const values: string[] = [];
  let current = "";
  let inQuotes = false;

  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i]!;
    if (ch === '"') {
      if (inQuotes && text[i + 1] === '"') {
        current += '"';
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }
    if (ch === "," && !inQuotes) {
      values.push(current.trim());
      current = "";
      continue;
    }
    current += ch;
  }
  values.push(current.trim());
  return values.filter((value) => value.length > 0);
}

function stringsFromUnknownArray(raw: unknown): string[] | null {
  if (!Array.isArray(raw)) return null;
  const values = raw
    .map((item) => {
      if (typeof item === "string") return item.trim();
      if (typeof item === "number" || typeof item === "boolean") {
        return String(item).trim();
      }
      if (typeof item === "object" && item !== null) {
        const row = item as { value?: unknown; label?: unknown };
        if (typeof row.value === "string") return row.value.trim();
        if (typeof row.label === "string") return row.label.trim();
      }
      return "";
    })
    .filter((value) => value.length > 0);
  return values.length > 0 ? values : null;
}

function shapeFromObject(raw: unknown): ParsedListShape | null {
  if (typeof raw !== "object" || raw === null) return null;
  const row = raw as {
    name?: unknown;
    description?: unknown;
    options?: unknown;
    values?: unknown;
  };
  const options = stringsFromUnknownArray(row.options ?? row.values);
  if (!options) return null;
  const name =
    typeof row.name === "string" && row.name.trim().length > 0
      ? row.name.trim()
      : "Pasted list";
  const shape: ParsedListShape = { name, options };
  if (
    typeof row.description === "string" &&
    row.description.trim().length > 0
  ) {
    shape.description = row.description.trim();
  }
  return shape;
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

function aiShapeToParsed(shape: AiListShape): ParsedListShape {
  const parsed: ParsedListShape = {
    name: shape.name.trim() || "Pasted list",
    options: trimValues(shape.options),
  };
  if (shape.description?.trim()) parsed.description = shape.description.trim();
  return parsed;
}

function parseJsonText(text: string): ParsedPasteContent | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }

  if (isAiEnvelope(parsed)) {
    const lists =
      parsed.lists?.map(aiShapeToParsed) ??
      (parsed.list ? [aiShapeToParsed(parsed.list)] : []);
    if (lists.length === 1) {
      return {
        kind: "single-list",
        format: "ai-envelope",
        list: lists[0]!,
      };
    }
    if (lists.length > 1) {
      return {
        kind: "multi-list",
        format: "ai-envelope",
        lists,
      };
    }
    return null;
  }

  if (isNamedList(parsed)) {
    const list = namedListToShape(parsed);
    return { kind: "single-list", format: "json-list-object", list };
  }

  if (Array.isArray(parsed)) {
    const asStrings = stringsFromUnknownArray(parsed);
    if (asStrings) {
      return {
        kind: "options",
        format: "json-array",
        options: trimValues(asStrings),
      };
    }
    const lists = parsed
      .map((row) => {
        if (isNamedList(row)) return namedListToShape(row);
        return shapeFromObject(row);
      })
      .filter((row): row is ParsedListShape => row !== null);
    if (lists.length === 1) {
      return {
        kind: "single-list",
        format: "json-lists-bundle",
        list: lists[0]!,
      };
    }
    if (lists.length > 1) {
      return {
        kind: "multi-list",
        format: "json-lists-bundle",
        lists,
      };
    }
    return null;
  }

  if (typeof parsed === "object" && parsed !== null) {
    const bundle = parsed as { lists?: unknown; name?: unknown };
    if (Array.isArray(bundle.lists)) {
      const lists = bundle.lists
        .map((row) => {
          if (isNamedList(row)) return namedListToShape(row);
          return shapeFromObject(row);
        })
        .filter((row): row is ParsedListShape => row !== null);
      if (lists.length === 1) {
        return {
          kind: "single-list",
          format: "json-lists-bundle",
          list: lists[0]!,
        };
      }
      if (lists.length > 1) {
        return {
          kind: "multi-list",
          format: "json-lists-bundle",
          lists,
        };
      }
    }
    const single = shapeFromObject(parsed);
    if (single) {
      return {
        kind: "single-list",
        format: "json-list-object",
        list: single,
      };
    }
  }

  if (typeof parsed === "string" && parsed.trim().length > 0) {
    return parsePlainText(parsed);
  }

  return null;
}

function namedListToShape(list: NamedList): ParsedListShape {
  const options = list.options
    .filter((o) => o.enabled && o.value.trim().length > 0)
    .map((o) => o.value.trim());
  const shape: ParsedListShape = {
    name: list.name.trim() || "Pasted list",
    options,
  };
  if (list.description.trim()) shape.description = list.description.trim();
  return shape;
}

function parsePlainText(text: string): ParsedPasteContent {
  const trimmed = text.trim();
  if (trimmed.length === 0) {
    return { kind: "options", format: "single-value", options: [] };
  }

  const lines = splitLines(trimmed);
  if (lines.length > 1) {
    return {
      kind: "options",
      format: "lines",
      options: trimValues(lines),
    };
  }

  const singleLine = lines[0] ?? trimmed;
  if (singleLine.includes(";")) {
    const parts = singleLine.split(";").map((part) => part.trim());
    if (parts.filter((part) => part.length > 0).length > 1) {
      return {
        kind: "options",
        format: "semicolon-separated",
        options: trimValues(parts),
      };
    }
  }

  if (singleLine.includes(",")) {
    const parts = splitCommaSeparated(singleLine);
    if (parts.length > 1) {
      return {
        kind: "options",
        format: "comma-separated",
        options: trimValues(parts),
      };
    }
  }

  return {
    kind: "options",
    format: "single-value",
    options: trimValues([singleLine]),
  };
}

/** Parse arbitrary pasted list content into options or list shapes. */
export function parsePastedListContent(text: string): ParsedPasteContent {
  const trimmed = text.trim();
  if (trimmed.length === 0) {
    return { kind: "options", format: "single-value", options: [] };
  }

  const json = parseJsonText(trimmed);
  if (json) return json;

  return parsePlainText(trimmed);
}

export function formatLabelForPaste(format: ParsedPasteFormat): string {
  switch (format) {
    case "json-array":
      return "JSON array";
    case "json-list-object":
      return "JSON list";
    case "json-lists-bundle":
      return "JSON bundle";
    case "ai-envelope":
      return "AI interchange";
    case "lines":
      return "line-separated";
    case "comma-separated":
      return "comma-separated";
    case "semicolon-separated":
      return "semicolon-separated";
    case "single-value":
      return "single value";
    default:
      return format;
  }
}

export function optionCountForPaste(content: ParsedPasteContent): number {
  switch (content.kind) {
    case "options":
      return content.options.length;
    case "single-list":
      return content.list.options.length;
    case "multi-list":
      return content.lists.reduce((sum, list) => sum + list.options.length, 0);
    default:
      return 0;
  }
}

export function listCountForPaste(content: ParsedPasteContent): number {
  switch (content.kind) {
    case "single-list":
      return 1;
    case "multi-list":
      return content.lists.length;
    default:
      return 0;
  }
}
