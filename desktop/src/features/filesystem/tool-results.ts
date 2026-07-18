import type { ChatMessage, ToolCall, ToolCallResult } from "@/hooks/use-chat";
import type { ToolImageData, ToolMediaArtifact } from "@/lib/api";
import type {
  FilesystemDirectoryPage,
  FilesystemEntry,
  FilesystemEntryKind,
  FilesystemPlacesResult,
  FilesystemResult,
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
  const modifiedAt = stringValue(source.modified_at, source.mtime, source.modified);
  return {
    name,
    path,
    kind: entryKind(source.kind ?? source.type, source),
    ...(size !== null ? { size } : {}),
    ...(modifiedAt ? { modifiedAt } : {}),
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
  const values = [output, record(output?.data), metadata, record(metadata?.data)];
  return values.filter((item): item is UnknownRecord => item !== null);
}

function normalizeDirectory(source: UnknownRecord): FilesystemDirectoryPage | null {
  const rawEntries = source.entries ?? source.children ?? source.items;
  if (!Array.isArray(rawEntries)) return null;
  const entries = rawEntries
    .map(normalizeEntry)
    .filter((entry): entry is FilesystemEntry => entry !== null);
  const path = stringValue(source.path, source.directory, source.root) ?? "";
  const namespaceValue = stringValue(source.namespace);
  const namespaces = new Set(["host", "workspace", "managed-files", "notes"]);
  const namespace = namespaceValue && namespaces.has(namespaceValue)
    ? (namespaceValue as FilesystemDirectoryPage["namespace"])
    : "unknown";
  return {
    kind: "filesystem.directory-page",
    namespace,
    path,
    entries,
    ...(stringValue(source.summary) ? { summary: stringValue(source.summary)! } : {}),
    ...(stringValue(source.next_cursor, source.nextCursor)
      ? { nextCursor: stringValue(source.next_cursor, source.nextCursor) }
      : {}),
    ...(numberValue(source.total, source.count) !== null
      ? { total: numberValue(source.total, source.count) }
      : {}),
  };
}

function normalizePlaces(source: UnknownRecord): FilesystemPlacesResult | null {
  if (!Array.isArray(source.places)) return null;
  const places = source.places.flatMap((value, index) => {
    const item = record(value);
    if (!item) return [];
    const path = stringValue(item.path);
    if (!path) return [];
    return [{
      id: stringValue(item.id, item.alias) ?? `place-${index}`,
      label: stringValue(item.label, item.name, item.alias) ?? basename(path),
      path,
      ...(stringValue(item.alias) ? { alias: stringValue(item.alias)! } : {}),
    }];
  });
  return {
    kind: "filesystem.places",
    places,
    ...(stringValue(source.summary) ? { summary: stringValue(source.summary)! } : {}),
  };
}

export function normalizeFilesystemResult(result: ToolCallResult): FilesystemResult | null {
  for (const source of candidateRecords(result)) {
    const kind = stringValue(source.kind, source.result_kind, source.type);
    if (kind === "filesystem.places" || Array.isArray(source.places)) {
      const places = normalizePlaces(source);
      if (places) return places;
    }
    if (
      kind === "filesystem.directory-page" ||
      kind === "directory-page" ||
      Array.isArray(source.entries) ||
      Array.isArray(source.children)
    ) {
      const directory = normalizeDirectory(source);
      if (directory) return directory;
    }
  }
  return null;
}

export function isFilesystemTool(name: string): boolean {
  const normalized = name.toLowerCase();
  return normalized === "local_file" || normalized.startsWith("fs_") || normalized.includes("filesystem");
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
      results.push({
        tool_call_id: callId,
        type: item.is_error === true ? "error" : "success",
        output: safeToolOutput(output),
        ...(record(item.metadata) ? { metadata: record(item.metadata)! } : {}),
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
  };
  const hasResult = current.results.some((item) => item.tool_call_id === event.call_id);
  const results = hasResult
    ? current.results.map((item) => item.tool_call_id === event.call_id ? result : item)
    : [...current.results, result];
  return { calls, results };
}
