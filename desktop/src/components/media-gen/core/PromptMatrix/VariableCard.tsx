/**
 * VariableCard — one variable, its options, and its role in the sweep.
 *
 * Drag order across cards IS the loop nesting: the top variable is the
 * outermost loop (the one held frozen while the others sweep), the bottom one
 * changes fastest. That is deliberately the same gesture people already know
 * from pivot tables and A1111's X/Y/Z axes — you don't configure "freeze", you
 * drag the thing you want frozen to the top.
 *
 * Options are a paste-first list: pasting ten lines makes ten options, because
 * typing them one at a time is the difference between a tool people use and a
 * tool people abandon.
 */

import { useCallback, useState } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  Anchor,
  BookMarked,
  ChevronDown,
  ChevronRight,
  GripVertical,
  Link2,
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
import type {
  MatrixVariable,
  ParamAxis,
  StrategyKind,
} from "@/lib/prompt-matrix";
import type { PromptMatrixActions } from "@/hooks/use-prompt-matrix";
import { cn } from "@/lib/utils";

export function VariableCard<TJob>({
  variable,
  axis,
  actions,
  strategy,
  /** Position in the loop nesting (1 = outermost / slowest-changing). */
  depth,
  totalVariables,
  /** Parse errors for this variable's option values, keyed by option id. */
  optionErrors,
}: {
  variable: MatrixVariable;
  /** The parameter axis, when this variable sweeps a setting rather than text. */
  axis: ParamAxis<TJob> | null;
  actions: PromptMatrixActions;
  strategy: StrategyKind;
  depth: number;
  totalVariables: number;
  optionErrors: ReadonlyMap<string, string>;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: variable.id });

  const [collapsed, setCollapsed] = useState(false);
  const enabledCount = variable.options.filter((o) => o.enabled).length;
  const isParam = variable.binding.kind === "param";

  /** Paste "a\nb\nc" into any option row → three options. */
  const handlePaste = useCallback(
    (e: React.ClipboardEvent<HTMLInputElement>, optionId: string) => {
      const text = e.clipboardData.getData("text");
      const lines = text
        .split(/\r?\n/)
        .map((l) => l.trim())
        .filter((l) => l.length > 0);
      if (lines.length < 2) return; // single line: let the browser paste it
      e.preventDefault();
      const [first, ...rest] = lines;
      actions.updateOption(variable.id, optionId, { value: first as string });
      actions.addOptions(variable.id, rest);
    },
    [actions, variable.id],
  );

  /** Enter at the end of a row adds the next one — keyboard-first entry. */
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter") {
        e.preventDefault();
        actions.addOption(variable.id);
      }
    },
    [actions, variable.id],
  );

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn(
        "rounded-lg border bg-card",
        isDragging && "z-10 opacity-80 shadow-lg",
        !variable.enabled && "opacity-60",
      )}
    >
      {/* ── header ─────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-1.5 px-2 py-1.5">
        <button
          type="button"
          className="cursor-grab touch-none text-muted-foreground hover:text-foreground active:cursor-grabbing"
          aria-label={`Reorder ${variable.name}`}
          {...attributes}
          {...listeners}
        >
          <GripVertical className="h-4 w-4" />
        </button>

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

        <code
          className={cn(
            "rounded px-1.5 py-0.5 text-xs font-medium",
            isParam
              ? "bg-amber-500/15 text-amber-700 dark:text-amber-400"
              : "bg-primary/15 text-primary",
          )}
        >
          {isParam ? variable.name : `{{${variable.name}}}`}
        </code>

        {strategy === "cartesian" && totalVariables > 1 && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge
                variant="outline"
                className="h-5 shrink-0 px-1.5 text-[10px]"
              >
                {depth === 1
                  ? "outer"
                  : depth === totalVariables
                    ? "inner"
                    : `#${depth}`}
              </Badge>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              {depth === 1
                ? "Outermost loop — this one is held while everything below it sweeps. Drag a variable here to freeze it."
                : depth === totalVariables
                  ? "Innermost loop — changes fastest, on every single run."
                  : `Loop level ${depth} of ${totalVariables}.`}
            </TooltipContent>
          </Tooltip>
        )}

        {variable.linkGroup !== null && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge
                variant="secondary"
                className="h-5 shrink-0 gap-1 px-1.5 text-[10px]"
              >
                <Link2 className="h-3 w-3" />
                {variable.linkGroup}
              </Badge>
            </TooltipTrigger>
            <TooltipContent>
              Linked: steps 1:1 with the others in this group instead of
              multiplying against them.
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
                checked={variable.enabled}
                onCheckedChange={(v) => actions.toggleVariable(variable.id, v)}
                aria-label={`Enable ${variable.name}`}
              />
            </span>
          </TooltipTrigger>
          <TooltipContent>
            {variable.enabled
              ? "Sweeping. Turn off to hold this variable at its first option."
              : "Not sweeping."}
          </TooltipContent>
        </Tooltip>

        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6 shrink-0 text-muted-foreground hover:text-destructive"
          onClick={() => actions.removeVariable(variable.id)}
          aria-label={`Remove ${variable.name}`}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>

      {/* ── options ────────────────────────────────────────────────────── */}
      {!collapsed && (
        <div className="space-y-1 border-t px-2 py-2">
          {axis?.hint !== undefined && (
            <p className="pb-1 text-[11px] text-muted-foreground">
              {axis.hint}
            </p>
          )}

          {variable.options.map((option, i) => {
            const error = optionErrors.get(option.id);
            const isBaseline = variable.baselineOptionId === option.id;
            const baselineByDefault =
              variable.baselineOptionId === null && i === 0;
            return (
              <div key={option.id} className="space-y-0.5">
                <div className="flex items-center gap-1.5">
                  <Switch
                    checked={option.enabled}
                    onCheckedChange={(v) =>
                      actions.updateOption(variable.id, option.id, {
                        enabled: v,
                      })
                    }
                    aria-label={`Include option ${i + 1}`}
                    className="scale-75"
                  />
                  <Input
                    value={option.value}
                    onChange={(e) =>
                      actions.updateOption(variable.id, option.id, {
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
                    className={cn(
                      "h-7 flex-1 text-xs",
                      error !== undefined &&
                        "border-destructive focus-visible:ring-destructive",
                    )}
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
                            actions.setBaselineOption(
                              variable.id,
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
                          ? "Baseline — the value this variable holds while the OTHERS take turns changing."
                          : "Make this the baseline value."}
                      </TooltipContent>
                    </Tooltip>
                  )}

                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6 shrink-0 text-muted-foreground hover:text-destructive"
                    onClick={() => actions.removeOption(variable.id, option.id)}
                    disabled={variable.options.length === 1}
                    aria-label="Remove option"
                  >
                    <X className="h-3.5 w-3.5" />
                  </Button>
                </div>
                {error !== undefined && (
                  <p className="pl-8 text-[11px] text-destructive">{error}</p>
                )}
              </div>
            );
          })}

          <div className="flex flex-wrap items-center gap-1 pt-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-6 gap-1 px-1.5 text-xs text-muted-foreground"
              onClick={() => actions.addOption(variable.id)}
            >
              <Plus className="h-3 w-3" />
              Add option
            </Button>

            {variable.binding.kind === "text" && (
              <Button
                variant="outline"
                size="sm"
                className="h-6 gap-1 px-1.5 text-xs"
                onClick={() => void actions.saveVariableToLibrary(variable.id)}
              >
                <BookMarked className="h-3 w-3" />
                Save to library
              </Button>
            )}

            {axis?.suggestions !== undefined && axis.suggestions.length > 0 && (
              <>
                <span className="text-[11px] text-muted-foreground">·</span>
                {axis.suggestions.slice(0, 6).map((s) => (
                  <Button
                    key={s === "" ? "__none__" : s}
                    variant="outline"
                    size="sm"
                    className="h-6 px-1.5 text-[11px] font-normal"
                    onClick={() => actions.addOptions(variable.id, [s])}
                  >
                    {s === "" ? "none" : s}
                  </Button>
                ))}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
