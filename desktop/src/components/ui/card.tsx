/**
 * HOST BINDING ONLY — the Card implementation lives in
 * `@ai-matrx/design-system`. This file binds the two things that are this
 * app's, and re-implements nothing.
 *
 * 1. DENSITY. Four apps forked Card and the difference was padding; the
 *    package settled it with `size` declared once on the root and read from
 *    context by every section. This app's fork padded `p-6`, which is the
 *    package's `lg`.
 * 2. THE GLASS SURFACE. Matrx Local's visual language is Apple glass, not a
 *    flat card: the fork painted `.glass-subtle` (translucent fill, hairline
 *    light border, diffused shadow) instead of `bg-card` + `border` +
 *    `shadow-sm`. Bound here as UTILITIES reading the same `--glass-*` tokens
 *    `.glass-subtle` reads, so tailwind-merge SUBSTITUTES them for the
 *    package's `bg-card` / `shadow-sm` — deterministic, and a caller's own
 *    `border-destructive/50` then substitutes for the glass border in turn.
 *    (The class `.glass-subtle` itself would not work here: it sits in
 *    Tailwind's `components` layer and loses to the package's utilities.)
 *
 * Pass `size` on any individual card that wants a different density — a
 * caller's className is merged last.
 */

import { Card as PackageCard, type CardProps } from "@ai-matrx/design-system";
import * as React from "react";

import { cn } from "@/lib/utils";

export {
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@ai-matrx/design-system";
export type { CardProps, CardSize } from "@ai-matrx/design-system";

/** `.glass-subtle`'s three properties, from the SAME `--glass-*` tokens, as
 *  utilities so tailwind-merge substitutes them for the package's. */
const GLASS_SUBTLE_SURFACE =
  "bg-[var(--glass-bg)] border-[color:var(--glass-border)] shadow-glass";

export const Card = React.forwardRef<HTMLDivElement, CardProps>(
  ({ size = "lg", className, ...props }, ref) => (
    <PackageCard
      ref={ref}
      size={size}
      className={cn(GLASS_SUBTLE_SURFACE, className)}
      {...props}
    />
  ),
);
Card.displayName = "Card";
