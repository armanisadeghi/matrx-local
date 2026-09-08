/**
 * HOST BINDING ONLY — the Select implementation lives in
 * `@ai-matrx/design-system` (portal seam, trigger size scale, `hideArrow`,
 * two-line items with a `description`, a viewport capped to the space Radix
 * measured). This file binds one thing: the popup wears this app's glass.
 *
 * The fork painted `.glass` on the content surface; the package paints
 * `bg-popover` + `shadow-md`. Bound here as UTILITIES reading the same
 * `--glass-*` tokens `.glass` reads, so tailwind-merge substitutes them for the
 * package's and a caller's own still wins over both.
 *
 * A caller's className is merged last.
 */

import {
  SelectContent as PackageSelectContent,
  type SelectContentProps,
} from "@ai-matrx/design-system";
import * as React from "react";

import { cn } from "@/lib/utils";

export {
  Select,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectScrollDownButton,
  SelectScrollUpButton,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
  selectTriggerVariants,
} from "@ai-matrx/design-system";
export type {
  SelectContentProps,
  SelectTriggerProps,
} from "@ai-matrx/design-system";

/** `.glass`'s properties, from the SAME `--glass-*` tokens, as utilities so
 *  tailwind-merge substitutes them for the package's. */
const GLASS_SURFACE =
  "bg-[var(--glass-bg)] border-[color:var(--glass-border)] shadow-glass " +
  "backdrop-blur-[24px] backdrop-saturate-150 rounded-lg";

export const SelectContent = React.forwardRef<
  HTMLDivElement,
  SelectContentProps
>(({ className, ...props }, ref) => (
  <PackageSelectContent
    ref={ref}
    className={cn(GLASS_SURFACE, className)}
    {...props}
  />
));
SelectContent.displayName = "SelectContent";
