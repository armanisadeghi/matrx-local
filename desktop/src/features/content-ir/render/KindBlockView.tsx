/**
 * THE RENDER SEAM for one server-built render block carrying a kind.
 *
 * The pipeline, end to end, with nothing invented on this side:
 *
 *   server detects + validates → `render_block` + `metadata.__ir`
 *     → `sanitizeRenderBlock` (kernel gate: valid envelope preserved, or none)
 *     → `applyIrKindRoute` (SHARED — the same decisions matrx-frontend makes)
 *     → this dispatch → a bundled component, or the generic floor
 *
 * Before this existed, `renderBlockText` flattened every block to a markdown
 * string and `metadata.__ir` was never read at all: a flashcard deck arrived
 * as a wall of text and the envelope was discarded.
 */

import { useMemo } from "react";
import {
  applyIrKindRoute,
  GenericStructuredView,
  useContentIrKindVersion,
  type IrRenderBlock,
} from "@ai-matrx/content-ir-react";
import { readEnvelope, reconstructRegionValue } from "@ai-matrx/content-ir/core";
import { contentIrRouteEnv, contentIrVersionSources } from "../runtime/route-env";
import { componentRegistry } from "../runtime/registry";
import { CONTENT_IR_PLATFORM } from "../platform";
import { lookupKindComponent } from "./dispatch";
import { ContentIrHostBoundary } from "./host";

export interface KindBlockViewProps {
  blockId: string;
  type: string;
  content?: string | null | undefined;
  metadata?: Record<string, unknown> | undefined;
  complete: boolean;
}

export function KindBlockView({ type, content, metadata, complete }: KindBlockViewProps) {
  const envelope = readEnvelope(metadata);
  const kind = envelope?.root.kind ?? null;

  // The catalog loads asynchronously, and an agent may have minted this kind
  // moments ago. Without this subscription the block would keep its
  // pre-arrival decision — generic, or unrouted — for the rest of the session.
  const version = useContentIrKindVersion(kind, contentIrVersionSources);

  // Eager per-kind fetch (deduped, miss-latched inside the resolver): the
  // moment a kind is identified, pull ITS descriptor rather than waiting on a
  // wholesale catalog refresh. This is the guide's "request or refresh the
  // descriptor, then repaint" rule (§1).
  if (kind) componentRegistry.requestComponent(kind, CONTENT_IR_PLATFORM, "output");

  // No React Compiler here (Vite) — the route is a real function call and must
  // not re-execute on every unrelated parent render.
  const routed = useMemo(
    () =>
      applyIrKindRoute<IrRenderBlock>(
        {
          type,
          content: content ?? "",
          // `exactOptionalPropertyTypes`: omit an optional key, never widen it.
          ...(metadata !== undefined && { metadata }),
        },
        contentIrRouteEnv,
      ),
    // `version` is the repaint key: a late descriptor arrival changes it, and
    // only then is the decision remade.
    [type, content, metadata, version],
  );

  // ── A bundled component for this kind on this platform ───────────────────
  const Component = lookupKindComponent(routed.type);
  if (Component && envelope && kind) {
    return (
      <Component value={reconstructRegionValue(envelope)} kind={kind} complete={complete} />
    );
  }

  // ── A known shape with no component here — the honest floor (R6) ─────────
  return (
    <ContentIrHostBoundary>
      <GenericStructuredView
        content={content ?? ""}
        {...(routed.metadata !== undefined && { metadata: routed.metadata })}
        streamingIndicator={
          <div className="mb-1.5 text-[0.6875rem] uppercase tracking-wide text-muted-foreground">
            Still arriving…
          </div>
        }
      />
    </ContentIrHostBoundary>
  );
}
