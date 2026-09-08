/**
 * HOST BINDING ONLY — the Popover implementation lives in
 * `@ai-matrx/design-system` (portal seam, viewport-capped height with its own
 * scroll, dialog-safe z layer). This file binds one thing: this app's popovers
 * SIZE TO THEIR CONTENT.
 *
 * The package defaults to a fixed `w-72` because that is the shadcn shape most
 * hosts wanted; every popover in Matrx Local — the account menu, the
 * notification tray, the quick-action pickers — was `min-w-[12rem]` with an
 * auto width and `p-3`. Forcing 288px on all thirteen of them would be a
 * regression, so the width and padding are bound here and nothing else moves.
 *
 * A caller's className is merged last, so `w-96` (or any padding) still wins.
 */

import {
  PopoverContent as PackagePopoverContent,
  type PopoverContentProps,
} from "@ai-matrx/design-system";
import * as React from "react";

import { cn } from "@/lib/utils";

export { Popover, PopoverAnchor, PopoverTrigger } from "@ai-matrx/design-system";
export type { PopoverContentProps } from "@ai-matrx/design-system";

export const PopoverContent = React.forwardRef<
  HTMLDivElement,
  PopoverContentProps
>(({ className, ...props }, ref) => (
  <PackagePopoverContent
    ref={ref}
    className={cn("w-auto min-w-[12rem] p-3", className)}
    {...props}
  />
));
PopoverContent.displayName = "PopoverContent";
