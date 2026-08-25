import {
  readMatrxNdjsonStream,
  type MatrxNdjsonIssue,
  type MatrxStreamEnvelope,
} from "@ai-matrx/agents/stream/ndjson";

import type { TypedStreamEvent } from "@/types/python-generated/stream-events";

export type AIDreamStreamProtocolIssue =
  | { kind: "malformed-line"; detail: MatrxNdjsonIssue }
  | { kind: "unknown-envelope"; detail: unknown };

export interface ParseAIDreamStreamOptions {
  signal?: AbortSignal;
  onProtocolIssue?: (issue: AIDreamStreamProtocolIssue) => void;
}

function reportProtocolIssue(issue: AIDreamStreamProtocolIssue): void {
  console.warn("[aidream-stream] Invalid stream frame", issue);
}

/**
 * The one desktop boundary from AIDream response bytes to normalized events.
 *
 * Framing, split UTF-8, compact envelopes, background drainage, cancellation,
 * trailing fragments, and partial transport failures are owned by the public
 * `@ai-matrx/agents` kernel. This adapter only attaches desktop diagnostics and
 * narrows the framework-free package envelope to the generated host contract.
 */
export async function* parseAIDreamStream(
  response: Response,
  options: ParseAIDreamStreamOptions = {},
): AsyncGenerator<TypedStreamEvent, void, undefined> {
  if (!response.body) {
    throw new Error("Response has no body.");
  }

  const emitIssue = options.onProtocolIssue ?? reportProtocolIssue;
  for await (const envelope of readMatrxNdjsonStream(response.body, {
    ...(options.signal ? { signal: options.signal } : {}),
    onMalformedLine: (detail) =>
      emitIssue({ kind: "malformed-line", detail }),
    onUnknownEnvelope: (detail) =>
      emitIssue({ kind: "unknown-envelope", detail }),
  })) {
    yield envelope as TypedStreamEvent;
  }
}

/** Package-level envelope helper for host tests and non-generated consumers. */
export type AIDreamStreamEnvelope = MatrxStreamEnvelope;

export function stringifyStreamDetail(value: unknown): string {
  if (typeof value === "string") return value;
  if (value == null) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
