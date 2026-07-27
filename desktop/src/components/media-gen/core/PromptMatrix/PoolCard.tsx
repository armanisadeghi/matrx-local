/**
 * PoolCard — one shared option pool for {{name#slot}} tokens.
 *
 * Options are declared once; every distinct slot in the prompt draws
 * independently from this list with replacement. Repeated uses of the same
 * slot stay bound to the same draw.
 */

import { useCallback, useState } from "react";
import {
  Anchor,
  BookMarked,
  ChevronDown,
  ChevronRight,
  Plus,
  Trash2,
  X,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { MatrixPool, StrategyKind } from "@/lib/prompt-matrix";
import type { PromptMatrixActions } from "@/hooks/use-prompt-matrix";
import { cn } from "@/lib/utils";

export function PoolCard({
  pool,
  slots,
  actions,
  strategy,
}: {
  pool: MatrixPool;
  /** Slot ids currently referenced in the template (sorted). */
  slots: readonly string[];
  actions: PromptMatrixActions;
  strategy: StrategyKind;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const enabledCount = pool.options.filter((o) => o.enabled).length;

  const handlePaste = useCallback(
    (e: React.ClipboardEvent<HTMLInputElement>, optionId: string) => {
      const text = e.clipboardData.getData("text");
      const lines = text
        .split(/\r?\n/)
        .map((l) => l.trim())
        .filter((l) => l.length > 0);
      if (lines.length < 2) return;
      e.preventDefault();
      const [first, ...rest] = lines;
      actions.updatePoolOption(pool.id, optionId, { value: first as string });
      actions.addPoolOptions(pool.id, rest);
    },
    [actions, pool.id],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter") {
        e.preventDefault();
        actions.addPoolOption(pool.id);
      }
    },
    [actions, pool.id],
  );

  return (
    <div
      className={cn("rounded-lg border bg-card", !pool.enabled && "opacity-60")}
    >
      <div className="flex items-center gap-1.5 px-2 py-1.5">
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6 shrink-0"
          onClick={() => setCollapsed((c) => !c)}
          aria-label={collapsed ? "Expand" : "Collapse"}
        >
          {collapsed ? (
            <ChevronRight className="h-3.5 w-3.5" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" />
          )}
        </Button>

        <code className="rounded bg-violet-500/15 px-1.5 py-0.5 text-xs font-medium text-violet-700 dark:text-violet-300">
          {`{{${pool.name}#…}}`}
        </code>

        <Badge variant="outline" className="h-5 shrink-0 px-1.5 text-[10px]">
          pool
        </Badge>

        {slots.length > 0 && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="hidden truncate text-[11px] text-muted-foreground sm:inline">
                {slots.map((s) => `#${s}`).join(" ")}
              </span>
            </TooltipTrigger>
            <TooltipContent>
              Slots in the prompt:{" "}
              {slots.map((s) => `{{${pool.name}#${s}}}`).join(", ")}
            </TooltipContent>
          </Tooltip>
        )}

        <span className="ml-auto shrink-0 text-xs tabular-nums text-muted-foreground">
          {enabledCount} {enabledCount === 1 ? "option" : "options"}
        </span>

        <Tooltip>
          <TooltipTrigger asChild>
            <span>
              <Switch
                checked={pool.enabled}
                onCheckedChange={(v) => actions.togglePool(pool.id, v)}
                aria-label={`Enable pool ${pool.name}`}
              />
            </span>
          </TooltipTrigger>
          <TooltipContent>
            {pool.enabled
              ? "Sweeping. Turn off to exclude this pool from the plan."
              : "Not sweeping."}
          </TooltipContent>
        </Tooltip>

        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6 shrink-0 text-muted-foreground hover:text-destructive"
          onClick={() => actions.removePool(pool.id)}
          aria-label={`Remove pool ${pool.name}`}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>

      {!collapsed && (
        <div className="space-y-2 border-t px-2 py-2">
          <p className="text-[11px] text-muted-foreground">
            Each numbered slot draws independently from this list. Repeating
            the same slot reuses its value; different slots may match by
            chance.
          </p>

          {pool.options.map((option, i) => {
            const isBaseline = pool.baselineOptionId === option.id;
            const baselineByDefault = pool.baselineOptionId === null && i === 0;
            return (
              <div key={option.id} className="flex items-center gap-1.5">
                <Switch
                  checked={option.enabled}
                  onCheckedChange={(v) =>
                    actions.updatePoolOption(pool.id, option.id, {
                      enabled: v,
                    })
                  }
                  aria-label={`Include option ${i + 1}`}
                  className="scale-75"
                />
                <Input
                  value={option.value}
                  onChange={(e) =>
                    actions.updatePoolOption(pool.id, option.id, {
                      value: e.target.value,
                    })
                  }
                  onPaste={(e) => handlePaste(e, option.id)}
                  onKeyDown={handleKeyDown}
                  placeholder={
                    i === 0
                      ? "Type a value, or paste a list (one per line)…"
                      : "Another value…"
                  }
                  className="h-7 flex-1 text-xs"
                />

                {strategy === "baseline" && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className={cn(
                          "h-6 w-6 shrink-0",
                          isBaseline || baselineByDefault
                            ? "text-primary"
                            : "text-muted-foreground/40 hover:text-foreground",
                        )}
                        onClick={() =>
                          actions.setPoolBaselineOption(
                            pool.id,
                            isBaseline ? null : option.id,
                          )
                        }
                        aria-label="Use as the baseline value"
                      >
                        <Anchor className="h-3.5 w-3.5" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>
                      {isBaseline || baselineByDefault
                        ? "Baseline — held while other axes take turns changing."
                        : "Make this the baseline value."}
                    </TooltipContent>
                  </Tooltip>
                )}

                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 shrink-0 text-muted-foreground hover:text-destructive"
                  onClick={() => actions.removePoolOption(pool.id, option.id)}
                  disabled={pool.options.length === 1}
                  aria-label="Remove option"
                >
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>
            );
          })}

          <div className="flex flex-wrap items-center gap-1 pt-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-6 gap-1 px-1.5 text-xs text-muted-foreground"
              onClick={() => actions.addPoolOption(pool.id)}
            >
              <Plus className="h-3 w-3" />
              Add option
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-6 gap-1 px-1.5 text-xs"
              onClick={() => void actions.savePoolToLibrary(pool.id)}
            >
              <BookMarked className="h-3 w-3" />
              Save to library
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
