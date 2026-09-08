/**
 * HOST BINDING ONLY — the Dialog implementation lives in
 * `@ai-matrx/design-system` (clamped to the viewport, sticky footer, portal
 * seam, auto `DialogTitle` for a11y, close control at a 44px target). This
 * file binds what is Matrx Local's and re-implements nothing:
 *
 * 1. THE GLASS SURFACE. The fork painted `.glass` (translucent fill, hairline
 *    border, 24px backdrop blur) with a `rounded-3xl` corner instead of the
 *    package's `bg-background` + `sm:rounded-lg`. Bound here as UTILITIES
 *    reading the same `--glass-*` tokens `.glass` reads, so tailwind-merge
 *    substitutes them for the package's and a caller's own still wins over
 *    both. The scrim is a token, not a rule: `index.css` sets
 *    `--matrx-overlay-scrim-soft` to this app's 40%/50% dim.
 * 2. `mobileSheet={false}`. The package turns a dialog into a bottom sheet
 *    below 768px, which is right for a phone browser and wrong for a Tauri
 *    desktop window the user has simply dragged narrow.
 *
 * Both are per-call overridable — a caller's className merges last, and
 * `mobileSheet` can be passed explicitly.
 */

import {
  DialogContent as PackageDialogContent,
  type DialogContentProps,
} from "@ai-matrx/design-system";
import * as React from "react";

import { cn } from "@/lib/utils";

export {
  Dialog,
  DialogClose,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
  DialogTrigger,
} from "@ai-matrx/design-system";
export type { DialogContentProps } from "@ai-matrx/design-system";

/** `.glass`'s properties, from the SAME `--glass-*` tokens, as utilities so
 *  tailwind-merge substitutes them for the package's. */
const GLASS_SURFACE =
  "bg-[var(--glass-bg)] border-[color:var(--glass-border)] shadow-glass " +
  "backdrop-blur-[24px] backdrop-saturate-150 rounded-3xl sm:rounded-3xl";

export const DialogContent = React.forwardRef<
  HTMLDivElement,
  DialogContentProps
>(({ className, mobileSheet = false, ...props }, ref) => (
  <PackageDialogContent
    ref={ref}
    mobileSheet={mobileSheet}
    className={cn(GLASS_SURFACE, className)}
    {...props}
  />
));
DialogContent.displayName = "DialogContent";
