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
 *
 * ## THE SHADOWED LANE (`@ai-matrx/content-ir-react` 0.11.1)
 *
 * A producer that shadows its text channel never hands this client a verified
 * `__ir`. What it does hand over is a `superseded` terminal on
 * `metadata.__ir_partial` — its own proof that the announced kind completed —
 * plus the block's closed JSON. `resolveSupersededKindRender` reconstructs a
 * render-local COMPLETE envelope from exactly that pair, verifies the root
 * `__kind` matches the confirmed kind, and routes it through this same
 * component path. Without it the block renders its raw Shape JSON as text,
 * which is what this desktop did until now.
 *
 * It is asked FIRST and declines on its own whenever a verified envelope is
 * present, so the ordinary route still owns every non-shadowed block.
 */

import { useMemo } from "react";
import {
  applyIrKindRoute,
  GenericStructuredView,
  resolveSupersededKindRender,
  useContentIrKindVersion,
  type IrRenderBlock,
} from "@ai-matrx/content-ir-react";
import { readEnvelope, reconstructRegionValue } from "@ai-matrx/content-ir/core";
import { isProvisionalKind, readPartialKindEvent } from "@ai-matrx/content-ir/wire";
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
  const verifiedEnvelope = readEnvelope(metadata);
  // On a shadowed lane the announced kind is the ONLY identity the block
  // carries, so it is what the repaint subscription and the eager component
  // fetch below must key on there.
  const partial = readPartialKindEvent(metadata);
  const announcedKind =
    partial === null ? null : isProvisionalKind(partial) ? partial.root.kind : partial.kind;
  const kind = verifiedEnvelope?.root.kind ?? announcedKind;

  // The catalog loads asynchronously, and an agent may have minted this kind
  // moments ago. Without this subscription the block would keep its
  // pre-arrival decision — generic, or unrouted — for the rest of the session.
  const version = useContentIrKindVersion(kind, contentIrVersionSources);

  // Eager per-kind fetch (deduped, miss-latched inside the resolver): the
  // moment a kind is identified, pull ITS descriptor rather than waiting on a
  // wholesale catalog refresh. This is the guide's "request or refresh the
  // descriptor, then repaint" rule (§1).
  if (kind) componentRegistry.requestComponent(kind, CONTENT_IR_PLATFORM, "output");

  // `exactOptionalPropertyTypes`: omit an optional key, never widen it.
  const source: IrRenderBlock = {
    type,
    content: content ?? "",
    ...(metadata !== undefined && { metadata }),
  };

  // No React Compiler here (Vite) — both routes are real function calls and
  // must not re-execute on every unrelated parent render. `version` is the
  // repaint key on each: a late descriptor arrival changes it, and only then
  // is the decision remade.
  const superseded = useMemo(
    () => resolveSupersededKindRender<IrRenderBlock>(source, contentIrRouteEnv),
    [type, content, metadata, version],
  );

  const routed = useMemo(
    () => superseded?.block ?? applyIrKindRoute<IrRenderBlock>(source, contentIrRouteEnv),
    [type, content, metadata, version, superseded],
  );

  const envelope = superseded?.envelope ?? verifiedEnvelope;
  // A `superseded` terminal IS the producer's proof that the region closed.
  const isComplete = superseded !== null || complete;

  // ── A bundled component for this kind on this platform ───────────────────
  const Component = lookupKindComponent(routed.type);
  if (Component && envelope && kind) {
    return (
      <Component value={reconstructRegionValue(envelope)} kind={kind} complete={isComplete} />
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
