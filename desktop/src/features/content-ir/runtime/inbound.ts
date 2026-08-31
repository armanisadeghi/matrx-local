/**
 * THE WIRE BOUNDARY for inbound `render_block` events.
 *
 * The guide's rule (§2, "Ingest the envelope without destroying provenance"):
 * a valid `metadata.__ir` is PRESERVED, never reparsed or rewritten just to
 * display it; an invalid one is stripped through the documented helper and
 * reported through diagnostics, with rendering falling back to raw content.
 * A kind identity is never invented.
 */

import { sanitizeInboundEnvelopeMetadata, readEnvelope } from "@ai-matrx/content-ir/core";
import type { RenderBlockPayload } from "@/types/python-generated/stream-events";
import { reportContentIrError } from "./diagnostics";

export interface SanitizedRenderBlock {
  metadata: Record<string, unknown> | undefined;
  /** The kind this block's envelope resolved to, or null when it carries none. */
  kind: string | null;
}

/**
 * Validate one payload's metadata and report the kind it resolved to.
 *
 * `kind === null` means "this is not structured content as far as we can
 * trust" — an ordinary text/code block, or a block whose envelope failed the
 * gate. Either way the caller renders the content, never nothing.
 */
export function sanitizeRenderBlock(payload: RenderBlockPayload): SanitizedRenderBlock {
  if (!payload.metadata) return { metadata: undefined, kind: null };

  const metadata = sanitizeInboundEnvelopeMetadata(
    payload.metadata,
    { blockId: payload.blockId },
    {
      reportMalformed: (report) => {
        reportContentIrError({
          source: "content-ir",
          message:
            `inbound render_block "${report.blockId}" carried a malformed __ir envelope ` +
            `(engine ${String(report.engine)}) — the envelope was stripped and the block ` +
            `renders as plain content.`,
          relation: "inbound-envelope",
          raw: report.raw,
        });
      },
    },
  );

  // A kind that is still resolving its schema (`pending_schema`) has no
  // compliant value yet; the shared route declines to route it, so treating it
  // as structured here would show an empty component instead of the text.
  const envelope = readEnvelope(metadata);
  const kind = envelope?.root.kind ?? null;
  return { metadata, kind: kind && envelope?.root.kindState !== "pending_schema" ? kind : null };
}
