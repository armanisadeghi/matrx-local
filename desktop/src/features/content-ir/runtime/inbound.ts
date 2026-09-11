/**
 * THE WIRE BOUNDARY for inbound `render_block` events.
 *
 * The guide's rule (§2, "Ingest the envelope without destroying provenance"):
 * a valid `metadata.__ir` is PRESERVED, never reparsed or rewritten just to
 * display it; an invalid one is stripped through the documented helper and
 * reported through diagnostics, with rendering falling back to raw content.
 * A kind identity is never invented.
 *
 * TWO CHANNELS, ONE BOUNDARY (`@ai-matrx/content-ir` 0.11.0 /
 * `@ai-matrx/content-ir-react` 0.11.1). A producer that SHADOWS its text
 * channel — a workflow node — never hands this client a verified `__ir`. The
 * only identity such a block carries is a `superseded` terminal on
 * `metadata.__ir_partial`, and its own closed JSON. That channel is validated
 * here, at the same boundary and for the same reason as `__ir`: a malformed
 * event is stripped loudly and degrades that block to "no live rendering",
 * never to a wrong render and never to a throw inside the stream handler.
 */

import { sanitizeInboundEnvelopeMetadata, readEnvelope } from "@ai-matrx/content-ir/core";
import {
  readPartialKindEvent,
  sanitizeInboundPartialKindMetadata,
} from "@ai-matrx/content-ir/wire";
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

  const withEnvelope = sanitizeInboundEnvelopeMetadata(
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

  const metadata = sanitizeInboundPartialKindMetadata(
    withEnvelope,
    { blockId: payload.blockId },
    {
      reportMalformed: (report) => {
        reportContentIrError({
          source: "content-ir",
          message:
            `inbound render_block "${report.blockId}" carried a malformed __ir_partial event — ` +
            `the partial channel was stripped and the block renders from its own content only.`,
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
  const verifiedKind = envelope?.root.kind ?? null;
  if (verifiedKind && envelope?.root.kindState !== "pending_schema") {
    return { metadata, kind: verifiedKind };
  }

  // THE SHADOWED LANE. No verified envelope — but a `superseded` terminal is
  // the producer's own proof that the announced kind completed, and the
  // block's content is now closed JSON. That is exactly what
  // `resolveSupersededKindRender` reconstructs from downstream, so the block
  // must reach the kind path rather than being flattened to markdown. Only
  // `superseded` qualifies: a `retracted` terminal says the announcement was
  // WRONG, and a still-`partial` event is not proof of anything yet — both
  // stay on the text path this client already draws honestly.
  const partial = readPartialKindEvent(metadata);
  if (partial !== null && partial.state === "superseded") {
    return { metadata, kind: partial.kind };
  }

  return { metadata, kind: null };
}
