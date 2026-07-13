/**
 * StrategyControls — how the option lists get combined, and how seeds are set.
 *
 * These two settings decide whether a sweep is a useful experiment or a pile of
 * noise, so they are explained in place rather than hidden behind jargon:
 *
 *  • Strategy answers "how many runs, and which ones".
 *  • Seed policy answers "is the variable actually the only thing that changed?"
 *    A fixed seed is what makes a comparison a comparison — with a random seed
 *    per run you cannot tell whether the image changed because of your variable
 *    or because of the noise it started from. It is the default for that reason.
 */

import { Dices, Info } from "lucide-react";

import { Button } from "@/components/ui/button";
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
import { MAX_SEED, type MatrixSpec, type SeedMode, type StrategyKind } from "@/lib/prompt-matrix";
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
      "Runs the full product of every variable. Drag a variable to the top to hold it while the others sweep.",
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
      "A reproducible random subset of the full product. Use it to probe a huge space before committing to it.",
  },
  {
    kind: "zip",
    label: "In lockstep",
    blurb:
      "Every variable steps together (1st with 1st, 2nd with 2nd…). Run count = the shortest option list, not the product.",
  },
];

const SEED_MODES: { mode: SeedMode; label: string; blurb: string }[] = [
  {
    mode: "fixed",
    label: "Same seed for every run",
    blurb:
      "The only honest way to compare: your variable becomes the sole difference between the images.",
  },
  {
    mode: "increment",
    label: "Step the seed each run",
    blurb: "Seed + 1 per run — variety, still fully reproducible.",
  },
  {
    mode: "random",
    label: "Random seed each run",
    blurb:
      "Maximum variety. Drawn from this plan's own generator, so the same plan still replays identically.",
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
  const seedMode = SEED_MODES.find((s) => s.mode === spec.seed.mode);

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

      {/* ── seeds ────────────────────────────────────────────────────────── */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-1">
          <Label className="text-xs">Seed</Label>
          <Tooltip>
            <TooltipTrigger asChild>
              <Info className="h-3 w-3 text-muted-foreground" />
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              With a fixed seed, two runs that differ only in your variable
              differ only because of your variable. With a random seed you
              cannot tell your change apart from the noise.
            </TooltipContent>
          </Tooltip>
        </div>
        <Select
          value={spec.seed.mode}
          onValueChange={(v) => actions.setSeedPolicy({ mode: v as SeedMode })}
        >
          <SelectTrigger className="h-8 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SEED_MODES.map((s) => (
              <SelectItem key={s.mode} value={s.mode} className="text-xs">
                {s.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {seedMode !== undefined && (
          <p className="text-[11px] leading-snug text-muted-foreground">
            {seedMode.blurb}
          </p>
        )}

        <div className="flex items-center gap-2 pt-1">
          {spec.seed.mode !== "random" && (
            <>
              <Label className="shrink-0 text-xs">Base</Label>
              <Input
                type="number"
                min={0}
                max={MAX_SEED}
                value={spec.seed.baseSeed}
                onChange={(e) =>
                  actions.setSeedPolicy({ baseSeed: Number(e.target.value) })
                }
                className="h-7 w-32 text-xs tabular-nums"
              />
            </>
          )}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                size="icon"
                className="h-7 w-7 shrink-0"
                onClick={actions.rerollSeeds}
                aria-label="Reroll seeds"
              >
                <Dices className="h-3.5 w-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Reroll the seeds.</TooltipContent>
          </Tooltip>

          <Label className="ml-auto shrink-0 text-xs">Runs each</Label>
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
