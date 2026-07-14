import {
  expandCompactEvent,
  isCompactEvent,
  type TypedStreamEvent,
} from "@/types/python-generated/stream-events";

function readRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function normalizeWireEvent(parsed: unknown): TypedStreamEvent | null {
  if (isCompactEvent(parsed)) return expandCompactEvent(parsed);

  const record = readRecord(parsed);
  if (!record) return null;

  if (typeof record.event === "string") {
    return parsed as TypedStreamEvent;
  }

  return null;
}

function parseStreamLine(line: string): TypedStreamEvent | null {
  const trimmed = line.trim();
  if (!trimmed || trimmed === "data: [DONE]" || trimmed === "[DONE]") return null;

  const jsonText = trimmed.startsWith("data:") ? trimmed.slice(5).trim() : trimmed;
  if (!jsonText || jsonText === "[DONE]") return null;

  return normalizeWireEvent(JSON.parse(jsonText));
}

/**
 * Parse AIDream streaming responses.
 *
 * The live API primarily emits NDJSON, but some paths have historically looked
 * like SSE (`data: {...}`). This parser accepts both, expands compact `e/t`
 * frames from the generated stream contract, and flushes trailing UTF-8 data so
 * the final error/end event cannot disappear.
 */
export async function* parseAIDreamStream(
  response: Response,
  signal?: AbortSignal,
): AsyncGenerator<TypedStreamEvent, void, undefined> {
  if (!response.body) {
    throw new Error("Response has no body.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      if (signal?.aborted) break;

      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const event = parseStreamLine(line);
        if (event) yield event;
      }
    }

    buffer += decoder.decode();
    const remaining = buffer.trim();
    if (remaining) {
      const event = parseStreamLine(remaining);
      if (event) yield event;
    }
  } finally {
    reader.releaseLock();
  }
}

export function stringifyStreamDetail(value: unknown): string {
  if (typeof value === "string") return value;
  if (value == null) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
