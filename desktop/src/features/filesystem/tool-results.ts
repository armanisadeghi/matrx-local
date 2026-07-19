import type { ChatMessage, ToolCall, ToolCallResult } from "@/hooks/use-chat";
import type { ToolImageData, ToolMediaArtifact } from "@/lib/api";
import type {
  FilesystemContentMatch,
  FilesystemContentSearch,
  FilesystemDirectoryPage,
  FilesystemEntry,
  FilesystemEntryKind,
  FilesystemModifiedAt,
  FilesystemNamespace,
  FilesystemPlace,
  FilesystemPlacesResult,
  FilesystemResult,
  FilesystemSearchPage,
  FilesystemSemanticMatch,
  FilesystemSemanticSearch,
} from "./types";

type UnknownRecord = Record<string, unknown>;

function record(value: unknown): UnknownRecord | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as UnknownRecord)
    : null;
}

function stringValue(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}

function numberValue(...values: unknown[]): number | null {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

function modifiedAtValue(...values: unknown[]): FilesystemModifiedAt | null | undefined {
  for (const value of values) {
    if (value === null) return null;
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim()) return value;
  }
  return undefined;
}

const FILESYSTEM_NAMESPACES = new Set<FilesystemNamespace>([
  "host",
  "workspace",
  "managed-files",
  "notes",
  "unknown",
]);

function normalizeNamespace(value: unknown): FilesystemNamespace {
  return typeof value === "string" && FILESYSTEM_NAMESPACES.has(value as FilesystemNamespace)
    ? value as FilesystemNamespace
    : "unknown";
}

export function parseJsonValue(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed || (!trimmed.startsWith("{") && !trimmed.startsWith("["))) {
    return value;
  }
  try {
    return JSON.parse(trimmed) as unknown;
  } catch {
    return value;
  }
}

export function stringifyToolOutput(value: unknown): string {
  if (typeof value === "string") return value;
  if (value == null) return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

const MAX_RENDERED_TOOL_OUTPUT = 100_000;

export function redactInlineBinary(value: unknown, key = ""): unknown {
  if (
    typeof value === "string" &&
    value.length > 4096 &&
    (key.toLowerCase().includes("base64") || /^[A-Za-z0-9+/=]+$/.test(value))
  ) {
    return `[inline binary omitted: ${value.length.toLocaleString()} characters]`;
  }
  if (Array.isArray(value)) return value.map((item) => redactInlineBinary(item));
  const item = record(value);
  if (item) {
    return Object.fromEntries(
      Object.entries(item).map(([childKey, child]) => [childKey, redactInlineBinary(child, childKey)]),
    );
  }
  return value;
}

export function safeToolOutput(value: unknown): string {
  const parsed = parseJsonValue(value);
  const output = stringifyToolOutput(redactInlineBinary(parsed));
  return output.length <= MAX_RENDERED_TOOL_OUTPUT
    ? output
    : `${output.slice(0, MAX_RENDERED_TOOL_OUTPUT)}\n… [output truncated for desktop display]`;
}

function entryKind(value: unknown, source: UnknownRecord): FilesystemEntryKind {
  const token = typeof value === "string" ? value.toLowerCase() : "";
  if (token === "directory" || token === "dir" || source.is_dir === true) return "directory";
  if (token === "file" || source.is_file === true) return "file";
  if (token === "symlink" || token === "link" || source.is_symlink === true) return "symlink";
  return "other";
}

function basename(path: string): string {
  const normalized = path.replace(/[\\/]+$/, "");
  const segments = normalized.split(/[\\/]/);
  return segments[segments.length - 1] || path;
}

function normalizeEntry(value: unknown): FilesystemEntry | null {
  if (typeof value === "string") {
    return { name: basename(value), path: value, kind: "other" };
  }
  const source = record(value);
  if (!source) return null;
  const path = stringValue(source.path, source.full_path, source.absolute_path);
  const name = stringValue(source.name, source.basename, path ? basename(path) : null);
  if (!path || !name) return null;
  const rawChildren = Array.isArray(source.children) ? source.children : null;
  const children = rawChildren
    ?.map(normalizeEntry)
    .filter((entry): entry is FilesystemEntry => entry !== null);
  const size = numberValue(source.size, source.size_bytes);
  const modifiedAt = modifiedAtValue(source.modified_at, source.mtime, source.modified);
  return {
    name,
    path,
    kind: entryKind(source.kind ?? source.type, source),
    ...(size !== null ? { size } : {}),
    ...(modifiedAt !== undefined ? { modifiedAt } : {}),
    ...(typeof source.hidden === "boolean" ? { hidden: source.hidden } : {}),
    ...(typeof source.has_children === "boolean"
      ? { hasChildren: source.has_children }
      : children
        ? { hasChildren: children.length > 0 }
        : {}),
    ...(children ? { children } : {}),
  };
}

function candidateRecords(result: ToolCallResult): UnknownRecord[] {
  const output = record(parseJsonValue(result.output));
  const metadata = record(result.metadata);
  // Metadata is the canonical UI contract; output is concise model-facing text
  // and is only a compatibility source when structured metadata is absent.
  const outputMetadata = record(output?.metadata);
  const values = [
    metadata,
    record(metadata?.data),
    outputMetadata,
    record(outputMetadata?.data),
    output,
    record(output?.data),
  ];
  return values.filter((item): item is UnknownRecord => item !== null);
}

function normalizeDirectory(source: UnknownRecord): FilesystemDirectoryPage | null {
  const rawEntries = source.entries ?? source.children ?? source.items;
  if (!Array.isArray(rawEntries)) return null;
  const entries = rawEntries
    .map(normalizeEntry)
    .filter((entry): entry is FilesystemEntry => entry !== null);
  const path = stringValue(source.path, source.directory, source.root);
  if (!path) return null;
  const rawSource = stringValue(source.source);
  const pageSource = rawSource === "index" || rawSource === "disk" || rawSource === "hybrid" ? rawSource : null;
  return {
    kind: "filesystem.directory-page",
    namespace: normalizeNamespace(source.namespace),
    path,
    entries,
    ...(stringValue(source.summary) ? { summary: stringValue(source.summary)! } : {}),
    ...(stringValue(source.next_cursor, source.nextCursor)
      ? { nextCursor: stringValue(source.next_cursor, source.nextCursor) }
      : {}),
    ...(numberValue(source.total, source.count) !== null
      ? { total: numberValue(source.total, source.count) }
      : {}),
    ...(pageSource ? { source: pageSource } : {}),
  };
}

function normalizeSearch(source: UnknownRecord): FilesystemSearchPage | null {
  if (!Array.isArray(source.entries)) return null;
  const query = stringValue(source.query);
  if (!query) return null;
  const entries = source.entries
    .map(normalizeEntry)
    .filter((entry): entry is FilesystemEntry => entry !== null);
  const rawSource = stringValue(source.source);
  const pageSource = rawSource === "index" || rawSource === "disk" || rawSource === "hybrid" ? rawSource : null;
  return {
    kind: "filesystem.search-page",
    namespace: normalizeNamespace(source.namespace),
    query,
    ...(stringValue(source.root) ? { root: stringValue(source.root) } : {}),
    entries,
    ...(stringValue(source.summary) ? { summary: stringValue(source.summary)! } : {}),
    ...(stringValue(source.next_cursor, source.nextCursor)
      ? { nextCursor: stringValue(source.next_cursor, source.nextCursor) }
      : {}),
    ...(pageSource ? { source: pageSource } : {}),
    ...(typeof source.index_complete === "boolean"
      ? { indexComplete: source.index_complete }
      : typeof source.indexComplete === "boolean"
        ? { indexComplete: source.indexComplete }
        : {}),
    ...(typeof source.truncated === "boolean" ? { truncated: source.truncated } : {}),
  };
}

function normalizeContentMatch(value: unknown): FilesystemContentMatch | null {
  const source = record(value);
  if (!source) return null;
  const path = stringValue(source.path);
  if (!path || typeof source.snippet !== "string") return null;
  return { path, snippet: source.snippet };
}

function normalizeContentSearch(source: UnknownRecord): FilesystemContentSearch | null {
  if (!Array.isArray(source.results)) return null;
  const query = stringValue(source.query);
  if (!query) return null;
  return {
    kind: "filesystem.content-search",
    namespace: normalizeNamespace(source.namespace),
    query,
    results: source.results
      .map(normalizeContentMatch)
      .filter((match): match is FilesystemContentMatch => match !== null),
    ...(stringValue(source.summary) ? { summary: stringValue(source.summary)! } : {}),
  };
}

function normalizeSemanticMatch(value: unknown): FilesystemSemanticMatch | null {
  const source = record(value);
  if (!source) return null;
  const score = numberValue(source.score);
  const entry = normalizeEntry(source.entry);
  return score === null || entry === null ? null : { score, entry };
}

function normalizeSemanticSearch(source: UnknownRecord): FilesystemSemanticSearch | null {
  if (!Array.isArray(source.results)) return null;
  const query = stringValue(source.query);
  const model = stringValue(source.model);
  if (!query || !model) return null;
  return {
    kind: "filesystem.semantic-search",
    namespace: normalizeNamespace(source.namespace),
    query,
    model,
    results: source.results
      .map(normalizeSemanticMatch)
      .filter((match): match is FilesystemSemanticMatch => match !== null),
    ...(stringValue(source.summary) ? { summary: stringValue(source.summary)! } : {}),
  };
}

function normalizePlaces(source: UnknownRecord): FilesystemPlacesResult | null {
  if (!Array.isArray(source.places)) return null;
  const places = source.places.flatMap((value, index): FilesystemPlace[] => {
    const item = record(value);
    if (!item) return [];
    const path = stringValue(item.path);
    if (!path) return [];
    const category = item.category === "home" || item.category === "standard" ||
      item.category === "configured" || item.category === "volume"
      ? item.category
      : null;
    const priority = numberValue(item.priority);
    return [{
      id: stringValue(item.id, item.alias) ?? `place-${index}`,
      label: stringValue(item.label, item.name, item.alias) ?? basename(path),
      path,
      ...(stringValue(item.alias) ? { alias: stringValue(item.alias)! } : {}),
      ...(category ? { category } : {}),
      ...(priority !== null ? { priority } : {}),
      ...(typeof item.available === "boolean" ? { available: item.available } : {}),
      ...(typeof item.configured === "boolean" ? { configured: item.configured } : {}),
    }];
  });
  return {
    kind: "filesystem.places",
    namespace: normalizeNamespace(source.namespace),
    places,
    ...(stringValue(source.summary) ? { summary: stringValue(source.summary)! } : {}),
  };
}

function normalizeFilesystemSource(
  source: UnknownRecord,
  allowLegacyShape: boolean,
): FilesystemResult | null {
  const kind = stringValue(source.kind, source.result_kind, source.type);
  if (kind === "filesystem.search-page" || kind === "search-page") {
    return normalizeSearch(source);
  }
  if (kind === "filesystem.content-search" || kind === "content-search") {
    return normalizeContentSearch(source);
  }
  if (kind === "filesystem.semantic-search" || kind === "semantic-search") {
    return normalizeSemanticSearch(source);
  }
  if (kind === "filesystem.places" || (allowLegacyShape && Array.isArray(source.places))) {
    return normalizePlaces(source);
  }
  if (
    kind === "filesystem.directory-page" ||
    kind === "directory-page" ||
    (allowLegacyShape && Array.isArray(source.entries)) ||
    (allowLegacyShape && Array.isArray(source.children))
  ) {
    return normalizeDirectory(source);
  }
  return null;
}

/** Normalize a direct structured engine response through the same UI contract as tool results. */
export function normalizeFilesystemPayload(payload: unknown): FilesystemResult | null {
  const source = record(parseJsonValue(payload));
  return source ? normalizeFilesystemSource(source, false) : null;
}

export function normalizeFilesystemResult(
  result: ToolCallResult,
  toolName?: string,
): FilesystemResult | null {
  const allowLegacyShape = toolName ? isFilesystemTool(toolName) : false;
  for (const source of candidateRecords(result)) {
    const normalized = normalizeFilesystemSource(source, allowLegacyShape);
    if (normalized) return normalized;
  }
  return null;
}

export function isFilesystemTool(name: string): boolean {
  const normalized = name.toLowerCase();
  return new Set([
    "local_file",
    "listdirectory",
    "findpaths",
    "semanticfindpaths",
    "filesystemplaces",
    "local_filesystem_places",
    "local_semantic_find_paths",
  ]).has(normalized) || normalized.startsWith("fs_") || normalized.includes("filesystem");
}

export interface ExtractedToolParts {
  calls: ToolCall[];
  results: ToolCallResult[];
}

/** Recover durable tool blocks from cx_message.content. */
export function extractToolParts(content: unknown): ExtractedToolParts {
  const parts = Array.isArray(content) ? content : [content];
  const calls: ToolCall[] = [];
  const results: ToolCallResult[] = [];
  for (const value of parts) {
    const item = record(value);
    if (!item) continue;
    const type = stringValue(item.type);
    const callId = stringValue(item.call_id, item.tool_use_id);
    if (!callId) continue;
    if (type === "tool_call") {
      calls.push({
        id: callId,
        name: stringValue(item.name) ?? "tool",
        input: record(item.arguments) ?? {},
      });
    } else if (type === "tool_result") {
      const output = item.content ?? item.output ?? item.result ?? item.output_preview ?? "";
      const resultRecord = record(item.result) ?? record(output);
      results.push({
        tool_call_id: callId,
        type: item.is_error === true ? "error" : "success",
        output: safeToolOutput(output),
        ...(record(item.metadata) ? { metadata: record(item.metadata)! } : {}),
        ...(record(item.action_needed) || record(resultRecord?.action_needed)
          ? {
              action_needed: (record(item.action_needed) ??
                record(resultRecord?.action_needed)) as unknown as NonNullable<
                ToolCallResult["action_needed"]
              >,
            }
          : {}),
      });
    }
  }
  return { calls, results };
}

/** Attach durable role=tool rows to the assistant message that owns the call. */
export function stitchHydratedToolMessages(messages: ChatMessage[]): ChatMessage[] {
  const ownerByCallId = new Map<string, number>();
  const stitched = messages.map((message, index) => {
    for (const call of message.tool_calls ?? []) ownerByCallId.set(call.id, index);
    return { ...message };
  });

  for (let index = 0; index < stitched.length; index += 1) {
    const message = stitched[index];
    if (!message?.tool_results?.length) continue;
    const retained: ToolCallResult[] = [];
    for (const result of message.tool_results) {
      const ownerIndex = ownerByCallId.get(result.tool_call_id);
      if (ownerIndex == null || ownerIndex === index) {
        retained.push(result);
        continue;
      }
      const owner = stitched[ownerIndex];
      if (!owner) continue;
      const withoutDuplicate = (owner.tool_results ?? []).filter(
        (item) => item.tool_call_id !== result.tool_call_id,
      );
      stitched[ownerIndex] = { ...owner, tool_results: [...withoutDuplicate, result] };
    }
    if (retained.length > 0) {
      stitched[index] = { ...message, tool_results: retained };
    } else {
      const { tool_results: _removed, ...withoutResults } = message;
      stitched[index] = withoutResults;
    }
  }

  return stitched.filter((message) =>
    message.content.trim() ||
    message.reasoning?.trim() ||
    message.error ||
    message.tool_calls?.length ||
    message.tool_results?.length,
  );
}

export interface LiveToolEvent {
  event: string;
  call_id: string;
  tool_name: string;
  message?: string | null;
  data?: Record<string, unknown>;
}

/** Reduce one stream event into stable call/result arrays keyed by call id. */
export function reduceLiveToolEvent(
  current: ExtractedToolParts,
  event: LiveToolEvent,
): ExtractedToolParts {
  const existingCall = current.calls.find((call) => call.id === event.call_id);
  const argumentsValue = record(event.data?.arguments);
  const call: ToolCall = {
    id: event.call_id,
    name: event.tool_name || existingCall?.name || "tool",
    input: argumentsValue ?? existingCall?.input ?? {},
  };
  const calls = existingCall
    ? current.calls.map((item) => item.id === event.call_id ? call : item)
    : [...current.calls, call];

  if (event.event !== "tool_completed" && event.event !== "tool_error") {
    return { calls, results: current.results };
  }

  const dataResult = event.data?.result;
  const output = event.event === "tool_error"
    ? stringValue(event.data?.detail, event.message) ?? "Tool execution failed."
    : safeToolOutput(dataResult ?? event.message ?? "Tool completed.");
  const result: ToolCallResult = {
    tool_call_id: event.call_id,
    type: event.event === "tool_error" ? "error" : "success",
    output,
    ...(record(dataResult)?.metadata && record(record(dataResult)?.metadata)
      ? { metadata: record(record(dataResult)?.metadata)! }
      : {}),
    ...(record(dataResult)?.artifact
      ? { artifact: record(dataResult)?.artifact as unknown as ToolMediaArtifact }
      : record(dataResult)?.kind === "image_ref"
        ? { artifact: dataResult as ToolMediaArtifact }
        : {}),
    ...(record(dataResult)?.image
      ? { image: record(dataResult)?.image as unknown as ToolImageData }
      : {}),
    ...(record(dataResult)?.action_needed
      ? {
          action_needed: record(dataResult)?.action_needed as NonNullable<
            ToolCallResult["action_needed"]
          >,
        }
      : {}),
  };
  const hasResult = current.results.some((item) => item.tool_call_id === event.call_id);
  const results = hasResult
    ? current.results.map((item) => item.tool_call_id === event.call_id ? result : item)
    : [...current.results, result];
  return { calls, results };
}
