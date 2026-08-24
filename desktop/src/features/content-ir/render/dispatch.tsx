/**
 * THE DISPATCH TABLE — `KindComponentPolicy` from
 * docs/CONTENT_IR_CONSUMER_GUIDE.md, and the ONLY thing about kind rendering
 * this app decides.
 *
 * The guide's renderer order (§4), implemented exactly:
 *   1. Bundled component — a Local-owned component registered for the exact
 *      kind/component contract. That is this map, and a kind reaches it only
 *      because a `content_ir.kind_component` row with `platform='desktop'`
 *      names a key that appears here. TWO EXPLICIT HALVES, no silent fallback.
 *   2. Vetted custom-component shell — NOT SUPPORTED HERE. `source='db'` rows
 *      carry user-authored code; this app has no reviewed sandbox protocol, so
 *      it declines (`componentSource: null` in the registry) rather than
 *      running remote JavaScript. Fail closed on executable presentation.
 *   3. Generic structured fallback — the honest floor, which SAYS it is one.
 *
 * Keys are not the web app's keys, deliberately: `kind_component.platform`
 * exists so a desktop window and a 1200px web page can draw the same kind
 * differently.
 */

import type { ComponentType } from "react";
import { MarkdownKind } from "./MarkdownKind";
import { SearchResultsKind } from "./SearchResultsKind";
import { FlashcardSetKind } from "./FlashcardSetKind";
import { QuizSetKind } from "./QuizSetKind";
import type { KindComponentProps } from "./types";

export const KIND_COMPONENTS: Record<string, ComponentType<KindComponentProps>> = {
  markdown_desktop: MarkdownKind,
  search_results_desktop: SearchResultsKind,
  flashcard_set_desktop: FlashcardSetKind,
  quiz_set_desktop: QuizSetKind,
};

export function lookupKindComponent(
  componentKey: string,
): ComponentType<KindComponentProps> | null {
  return KIND_COMPONENTS[componentKey] ?? null;
}
