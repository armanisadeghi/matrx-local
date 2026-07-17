/**
 * StrategyControls — how option lists are combined and repeated.
 *
 * These two settings decide whether a sweep is a useful experiment or a pile of
 * noise, so they are explained in place rather than hidden behind jargon:
 *
 *  • Strategy answers "how many runs, and which ones".
 * Every submitted batch receives a fresh randomized combination order and
 * independent seed per image. That is intentionally not a user-tunable setting:
 * a deterministic escape hatch here would recreate ordered-looking batches.
 */

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { type MatrixSpec, type StrategyKind } from "@/lib/prompt-matrix";
import type { PromptMatrixActions } from "@/hooks/use-prompt-matrix";

const STRATEGIES: {
  kind: StrategyKind;
  label: string;
  blurb: string;
}[] = [
  {
    kind: "cartesian",
    label: "Every combination",
    blurb:
      "Runs the full product of every variable in a fresh random order.",
  },
  {
    kind: "baseline",
    label: "One at a time",
    blurb:
      "Holds every variable at its baseline and changes ONE at a time. Turns 3 × 10 into 12 runs instead of 30 — the escape hatch when the product explodes.",
  },
  {
    kind: "sample",
    label: "Random sample",
    blurb:
      "A fresh random subset of the full product. Use it to probe a huge space before committing to it.",
  },
  {
    kind: "zip",
    label: "In lockstep",
    blurb:
      "Every variable is paired by position, then the resulting runs are randomly ordered. Run count = the shortest option list.",
  },
];

export function StrategyControls({
  spec,
  actions,
  /** Total runs — shown against the sample size so it reads as "20 of 450". */
  cartesianTotal,
}: {
  spec: MatrixSpec;
  actions: PromptMatrixActions;
  cartesianTotal: number;
}) {
  const strategy = spec.strategy;
  const active = STRATEGIES.find((s) => s.kind === strategy.kind);

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {/* ── strategy ─────────────────────────────────────────────────────── */}
      <div className="space-y-1.5">
        <Label className="text-xs">Combine by</Label>
        <Select
          value={strategy.kind}
          onValueChange={(v) => actions.setStrategy(v as StrategyKind)}
        >
          <SelectTrigger className="h-8 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STRATEGIES.map((s) => (
              <SelectItem key={s.kind} value={s.kind} className="text-xs">
                {s.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {active !== undefined && (
          <p className="text-[11px] leading-snug text-muted-foreground">
            {active.blurb}
          </p>
        )}

        {strategy.kind === "sample" && (
          <div className="flex items-center gap-2 pt-1">
            <Label className="shrink-0 text-xs">Sample size</Label>
            <Input
              type="number"
              min={1}
              max={Math.max(1, cartesianTotal)}
              value={strategy.count}
              onChange={(e) => actions.setSampleCount(Number(e.target.value))}
              className="h-7 w-24 text-xs"
            />
            <span className="text-[11px] text-muted-foreground">
              of {cartesianTotal.toLocaleString()}
            </span>
          </div>
        )}
      </div>

      {/* ── randomized execution ────────────────────────────────────────── */}
      <div className="space-y-1.5">
        <Label className="text-xs">Batch randomness</Label>
        <p className="text-[11px] leading-snug text-muted-foreground">
          Every Preview or Queue action draws a fresh random order and a unique
          seed for every image. Preview freezes that one draw until you queue it.
        </p>

        <div className="flex items-center gap-2 pt-1">
          <Label className="shrink-0 text-xs">Runs each</Label>
          <Tooltip>
            <TooltipTrigger asChild>
              <Input
                type="number"
                min={1}
                max={50}
                value={spec.seed.repeats}
                onChange={(e) =>
                  actions.setSeedPolicy({
                    repeats: Math.max(1, Number(e.target.value)),
                  })
                }
                className="h-7 w-16 text-xs tabular-nums"
              />
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              How many images per combination (each gets its own seed). This
              MULTIPLIES the total — 2 runs each doubles the batch.
            </TooltipContent>
          </Tooltip>
        </div>
      </div>
    </div>
  );
}
