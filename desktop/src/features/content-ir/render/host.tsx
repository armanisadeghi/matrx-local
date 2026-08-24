/**
 * THE DESKTOP'S CONTENT IR HOST — the four things
 * `@ai-matrx/content-ir-react` refuses to decide, supplied once.
 *
 * A boundary component rather than a root provider, for the reason
 * matrx-frontend made the same call: the host object is a module singleton
 * with a referentially stable identity, so nesting the boundary wherever a
 * kind renders costs nothing and cannot go stale.
 */

import { useMemo, type ReactNode } from "react";
import { Info } from "lucide-react";
import { ContentIrRenderProvider, type ContentIrHost } from "@ai-matrx/content-ir-react";
import { kindRegistry, componentRegistry } from "../runtime/registry";
import { reportContentIrError } from "../runtime/diagnostics";
import { CONTENT_IR_PLATFORM } from "../platform";
import { StructuredValue } from "./StructuredValue";
import { KindBlockView } from "./KindBlockView";

export const contentIrHost: ContentIrHost = {
  platform: CONTENT_IR_PLATFORM,
  kinds: kindRegistry,
  components: componentRegistry,
  reportError: reportContentIrError,

  renderBlock: (block) => (
    <KindBlockView
      blockId="nested"
      type={block.type}
      content={block.content}
      metadata={block.metadata}
      complete
    />
  ),

  renderValue: ({ value, kind, note, footer }) => (
    <StructuredValue
      value={value}
      {...(kind === undefined ? {} : { kind })}
      {...(note === undefined ? {} : { note })}
      {...(footer === undefined ? {} : { footer })}
    />
  ),

  renderShimmer: (text) => (
    <span className="animate-pulse text-xs text-muted-foreground">{text}</span>
  ),

  renderNotice: (text) => (
    <div className="flex items-center gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-1.5 text-xs text-amber-800 dark:text-amber-200">
      <Info className="h-3.5 w-3.5 shrink-0" />
      {text}
    </div>
  ),
};

export function ContentIrHostBoundary({ children }: { children: ReactNode }) {
  // Stable by construction (module singleton); the memo only documents that.
  const host = useMemo(() => contentIrHost, []);
  return <ContentIrRenderProvider host={host}>{children}</ContentIrRenderProvider>;
}
