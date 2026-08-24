/**
 * The route environment for `applyIrKindRoute` — registries + platform + the
 * diagnostics seam, bound once.
 *
 * React-free so the stream loop and `lib/chat-blocks.ts` can classify a block
 * without a provider in scope. The route decides a block's TYPE; turning that
 * type into pixels is `render/dispatch.tsx`.
 */

import type { KindRouteEnv, KindVersionSources } from "@ai-matrx/content-ir-react";
import { kindRegistry, componentRegistry } from "./registry";
import { CONTENT_IR_PLATFORM } from "../platform";
import { reportContentIrError } from "./diagnostics";

export const contentIrRouteEnv: KindRouteEnv = {
  kinds: kindRegistry,
  components: componentRegistry,
  reportError: reportContentIrError,
  platform: CONTENT_IR_PLATFORM,
};

/**
 * Repaint sources for `useContentIrKindVersion`, passed EXPLICITLY because a
 * chat block draws below no provider — the host boundary wraps only the
 * generic floor, the one place a package component needs the seams.
 */
export const contentIrVersionSources: KindVersionSources = {
  kinds: kindRegistry,
  components: componentRegistry,
};
