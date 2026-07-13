/**
 * The prompt-matrix UI, built ONCE in core/ so every layout variant (Classic,
 * Studio, Workspace, Gallery, Focus) inherits it instead of forking its own.
 *
 * Deliberately split in two, because the model's base settings belong BETWEEN
 * them — you set up the sweep, then the settings every run shares, and only
 * then do you see the count and commit:
 *
 *   PromptMatrixPanel     template with {{variables}} → options → strategy
 *   …the form's base settings (steps, size, LoRA, advanced)…
 *   PromptMatrixQueueBar  exact run count → pre-flight dialog → queue
 *
 * The count is live on every keystroke and EXACT (computed arithmetically, never
 * by materializing), so nobody is ever surprised by what they queued.
 */

import { useCallback, useMemo, useState } from "react";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  restrictToParentElement,
  restrictToVerticalAxis,
} from "@dnd-kit/modifiers";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { AlertCircle, Layers, Link2, RotateCcw, Save, Sparkles, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import { usePromptMatrixApp } from "@/contexts/PromptMatrixContext";
import type { PromptMatrixActions } from "@/hooks/use-prompt-matrix";
import {
  buildJobs,
  countPlan,
  MAX_BATCH_SIZE,
  variableKey,
  type MatrixVariable,
  type ParamAxis,
} from "@/lib/prompt-matrix";
import type { ImageGenerateInput } from "@/hooks/use-media-gen";
import type { ImageGenBatchJobSpec } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ImageGenController } from "../imageController";
import { BatchConfirmDialog } from "./BatchConfirmDialog";
import { StrategyControls } from "./StrategyControls";
import { TemplateEditor } from "./TemplateEditor";
import { VariableCard } from "./VariableCard";

const EMPTY_ERRORS: ReadonlyMap<string, string> = new Map();

/**
 * Live per-option validation against the axis each variable sweeps. Catching a
 * bad value here — rather than 40 minutes into the run — is the whole point.
 */
function useOptionErrors(): Map<string, Map<string, string>> {
  const { state, target } = usePromptMatrixApp();
  return useMemo(() => {
    const out = new Map<string, Map<string, string>>();
    for (const v of state.spec.variables) {
      if (v.binding.kind !== "param") continue;
      const axis = target.resolveAxis(v.binding.axisId);
      if (axis === null) continue;
      const errs = new Map<string, string>();
      for (const option of v.options) {
        if (!option.enabled) continue;
        const parsed = axis.parse(option.value);
        if (!parsed.ok) errs.set(option.id, parsed.error);
      }
      if (errs.size > 0) out.set(v.id, errs);
    }
    return out;
  }, [state.spec.variables, target]);
}

// ── Panel: template → variables → strategy ───────────────────────────────────

export function PromptMatrixPanel({ ctl }: { ctl: ImageGenController }) {
  const { state, actions, target } = usePromptMatrixApp();
  const { spec } = state;
  const optionErrors = useOptionErrors();

  const knownVariables = useMemo(
    () => new Set(spec.variables.map((v) => variableKey(v.name))),
    [spec.variables],
  );

  const cartesianTotal = useMemo(
    () => countPlan({ ...spec, strategy: { kind: "cartesian" } }),
    [spec],
  );

  const availableAxes = useMemo(
    () =>
      target.axes.filter(
        (a) =>
          !spec.variables.some(
            (v) => v.binding.kind === "param" && v.binding.axisId === a.id,
          ),
      ),
    [target.axes, spec.variables],
  );

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      const { active, over } = event;
      if (over === null || active.id === over.id) return;
      const ids = spec.variables.map((v) => v.id);
      const from = ids.indexOf(String(active.id));
      const to = ids.indexOf(String(over.id));
      if (from < 0 || to < 0) return;
      const next = [...ids];
      next.splice(to, 0, ...next.splice(from, 1));
      actions.reorderVariables(next);
    },
    [spec.variables, actions],
  );

  const promptField = spec.fields.find((f) => f.id === "prompt");
  const negativeField = spec.fields.find((f) => f.id === "negative_prompt");

  return (
    <div className="space-y-4">
      {/* 1. the template */}
      <div className="space-y-3">
        {promptField !== undefined && (
          <TemplateEditor
            label="Prompt template"
            value={promptField.text}
            onChange={(text) => actions.setFieldText("prompt", text)}
            knownVariables={knownVariables}
            placeholder="a portrait of a {{subject}}, {{style}} style, dramatic lighting"
            hint={
              <p className="text-[11px] text-muted-foreground">
                Wrap anything you want to sweep in{" "}
                <code className="rounded bg-primary/15 px-1 text-primary">
                  {"{{double braces}}"}
                </code>
                . Each one becomes a variable below.
              </p>
            }
          />
        )}
        {negativeField !== undefined &&
          (ctl.defaults?.supportsNegativePrompt ?? false) && (
            <TemplateEditor
              label="Negative prompt template"
              value={negativeField.text}
              onChange={(text) => actions.setFieldText("negative_prompt", text)}
              knownVariables={knownVariables}
              placeholder="blurry, low quality"
              minHeightClass="min-h-[60px]"
            />
          )}
      </div>

      {/* 2. the variables */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <Label className="text-xs">Variables</Label>
          {spec.variables.length > 1 && spec.strategy.kind === "cartesian" && (
            <span className="hidden text-[11px] text-muted-foreground sm:inline">
              — drag to reorder; the top one is held while the rest sweep
            </span>
          )}
          <div className="ml-auto">
            <AddParamAxisMenu
              axes={availableAxes}
              onAdd={(axis) => actions.addParamVariable(axis.id, axis.label)}
            />
          </div>
        </div>

        {spec.variables.length === 0 ? (
          <div className="rounded-lg border border-dashed p-4 text-center">
            <p className="text-xs text-muted-foreground">
              No variables yet. Add{" "}
              <code className="rounded bg-primary/15 px-1 text-primary">
                {"{{like_this}}"}
              </code>{" "}
              to the prompt, or sweep a setting like Steps or Model.
            </p>
          </div>
        ) : (
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
            modifiers={[restrictToVerticalAxis, restrictToParentElement]}
          >
            <SortableContext
              items={spec.variables.map((v) => v.id)}
              strategy={verticalListSortingStrategy}
            >
              <div className="space-y-1.5">
                {spec.variables.map((v, i) => (
                  <VariableCard
                    key={v.id}
                    variable={v}
                    axis={
                      v.binding.kind === "param"
                        ? target.resolveAxis(v.binding.axisId)
                        : null
                    }
                    actions={actions}
                    strategy={spec.strategy.kind}
                    depth={i + 1}
                    totalVariables={spec.variables.length}
                    optionErrors={optionErrors.get(v.id) ?? EMPTY_ERRORS}
                  />
                ))}
              </div>
            </SortableContext>
          </DndContext>
        )}

        {spec.variables.length > 1 && (
          <LinkGroupControl variables={spec.variables} actions={actions} />
        )}
      </div>

      <Separator />

      {/* 3. how to combine */}
      <StrategyControls
        spec={spec}
        actions={actions}
        cartesianTotal={cartesianTotal}
      />
    </div>
  );
}

// ── Queue bar: count → pre-flight → queue ────────────────────────────────────

export function PromptMatrixQueueBar({ ctl }: { ctl: ImageGenController }) {
  const [mediaState, mediaActions] = useMediaGenApp();
  const { imageJobs, imageQueueState } = mediaState;
  const { enqueueImageBatch } = mediaActions;

  const { state, actions, target } = usePromptMatrixApp();
  const { spec, plan, total } = state;
  const optionErrors = useOptionErrors();

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [templateName, setTemplateName] = useState("");

  const baseInput = ctl.buildInput();

  // Median seconds/image from this machine's own completed jobs. An estimate
  // grounded in real history beats a hardcoded guess.
  const secondsPerRun = useMemo(() => {
    const times = imageJobs
      .filter((j) => j.status === "completed" && (j.elapsed_seconds ?? 0) > 0)
      .map((j) => j.elapsed_seconds as number)
      .sort((a, b) => a - b);
    return times.length > 0
      ? (times[Math.floor(times.length / 2)] ?? null)
      : null;
  }, [imageJobs]);

  const queuedAhead = useMemo(
    () =>
      imageJobs.filter((j) => j.status === "queued" || j.status === "running")
        .length,
    [imageJobs],
  );

  const tooLarge = total > MAX_BATCH_SIZE;
  const blockers: string[] = [
    ...plan.errors,
    ...(baseInput === null
      ? ["Pick a model and fill in the generation settings first."]
      : []),
    ...(optionErrors.size > 0
      ? ["Some option values are not valid for the setting they sweep."]
      : []),
    ...(tooLarge
      ? [
          `${total.toLocaleString()} runs is over the ${MAX_BATCH_SIZE.toLocaleString()} limit. Narrow the matrix, or take a random sample of it.`,
        ]
      : []),
    ...(total === 0 ? ["Nothing to run."] : []),
  ];
  const canQueue = blockers.length === 0 && !submitting;

  const handleConfirm = useCallback(async () => {
    if (baseInput === null) return;
    setSubmitting(true);
    setSubmitError(null);

    const built = buildJobs<ImageGenerateInput>(
      target,
      baseInput,
      plan.combinations,
      spec.variables,
    );
    if (built.errors.length > 0) {
      // The engine would reject these anyway. Failing here leaves the queue
      // untouched and names exactly which values are wrong.
      setSubmitError(built.errors.join(" "));
      setSubmitting(false);
      return;
    }

    const jobs: ImageGenBatchJobSpec[] = built.jobs.map((b) => ({
      ...b.job,
      variables: b.values,
      combo_label: b.label,
    }));

    const label =
      templateName.trim().length > 0
        ? templateName.trim()
        : summarizeMatrix(spec.variables);

    const result = await enqueueImageBatch(jobs, label);
    setSubmitting(false);
    if (result.ok) {
      setConfirmOpen(false);
      return;
    }
    setSubmitError(result.error);
  }, [
    baseInput,
    target,
    plan.combinations,
    spec.variables,
    templateName,
    enqueueImageBatch,
  ]);

  return (
    <div className="space-y-2">
      {blockers.length > 0 && total > 0 && (
        <ul className="space-y-1 rounded-md border border-destructive/40 bg-destructive/10 p-2.5 text-xs text-destructive">
          {blockers.map((b) => (
            <li key={b} className="flex gap-1.5">
              <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
              <span>{b}</span>
            </li>
          ))}
        </ul>
      )}

      {submitError !== null && (
        <div className="flex gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2.5 text-xs text-destructive">
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{submitError}</span>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <TemplateMenu
          templates={state.templates}
          name={templateName}
          onNameChange={setTemplateName}
          onSave={actions.saveAsTemplate}
          onLoad={actions.loadTemplate}
          onDelete={actions.removeTemplate}
        />

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 gap-1.5 text-xs text-muted-foreground"
              onClick={actions.reset}
            >
              <RotateCcw className="h-3.5 w-3.5" />
              Clear
            </Button>
          </TooltipTrigger>
          <TooltipContent>Clear the template and every variable.</TooltipContent>
        </Tooltip>

        <div className="ml-auto flex items-center gap-3">
          <div className="text-right">
            <p
              className={cn(
                "text-lg font-semibold leading-none tabular-nums",
                blockers.length > 0 && "text-muted-foreground",
              )}
            >
              {total.toLocaleString()}
            </p>
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
              {total === 1 ? "run" : "runs"}
            </p>
          </div>
          <Button
            className="gap-1.5"
            disabled={!canQueue}
            onClick={() => {
              setSubmitError(null);
              setConfirmOpen(true);
            }}
          >
            <Layers className="h-4 w-4" />
            Queue{total > 0 ? ` ${total.toLocaleString()}` : ""}
          </Button>
        </div>
      </div>

      {imageQueueState?.paused === true && total > 0 && (
        <p className="text-right text-[11px] text-amber-600 dark:text-amber-400">
          The queue is paused — this batch will wait until you resume it.
        </p>
      )}

      <BatchConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        plan={plan}
        secondsPerRun={secondsPerRun}
        queuedAhead={queuedAhead}
        submitting={submitting}
        onConfirm={() => void handleConfirm()}
      />
    </div>
  );
}

/** `subject × style × steps` — the default batch name. */
function summarizeMatrix(variables: readonly MatrixVariable[]): string {
  const names = variables.filter((v) => v.enabled).map((v) => v.name);
  return names.length > 0 ? names.join(" × ") : "Batch";
}

// ── sub-controls ─────────────────────────────────────────────────────────────

function AddParamAxisMenu({
  axes,
  onAdd,
}: {
  axes: ParamAxis<ImageGenerateInput>[];
  onAdd: (axis: ParamAxis<ImageGenerateInput>) => void;
}) {
  const [open, setOpen] = useState(false);
  const groups = useMemo(() => {
    const out = new Map<string, ParamAxis<ImageGenerateInput>[]>();
    for (const axis of axes) {
      const list = out.get(axis.group) ?? [];
      list.push(axis);
      out.set(axis.group, list);
    }
    return [...out.entries()];
  }, [axes]);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="h-7 gap-1 text-xs"
          disabled={axes.length === 0}
        >
          <Sparkles className="h-3.5 w-3.5" />
          Sweep a setting
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-56 p-1">
        <p className="px-2 py-1.5 text-[11px] text-muted-foreground">
          Sweep a generation setting instead of prompt text.
        </p>
        {groups.map(([group, groupAxes]) => (
          <div key={group}>
            <p className="px-2 pb-0.5 pt-1.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              {group}
            </p>
            {groupAxes.map((axis) => (
              <button
                key={axis.id}
                type="button"
                className="w-full rounded px-2 py-1 text-left text-xs hover:bg-accent"
                onClick={() => {
                  onAdd(axis);
                  setOpen(false);
                }}
              >
                {axis.label}
              </button>
            ))}
          </div>
        ))}
      </PopoverContent>
    </Popover>
  );
}

/**
 * Link groups — a relationship BETWEEN variables (they step together instead of
 * multiplying), so it lives here rather than on any single variable's card.
 */
function LinkGroupControl({
  variables,
  actions,
}: {
  variables: MatrixVariable[];
  actions: PromptMatrixActions;
}) {
  const [open, setOpen] = useState(false);
  const linked = variables.filter((v) => v.linkGroup !== null);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 gap-1 px-1.5 text-[11px] text-muted-foreground"
        >
          <Link2 className="h-3 w-3" />
          {linked.length > 0
            ? `${linked.length} linked`
            : "Link variables (step together)"}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 space-y-2 p-3">
        <div>
          <p className="text-xs font-medium">Link variables</p>
          <p className="text-[11px] leading-snug text-muted-foreground">
            Linked variables advance together (1st with 1st, 2nd with 2nd) rather
            than multiplying. Pair a style with its LoRA and 3 × 3 becomes 3 runs,
            not 9.
          </p>
        </div>
        <div className="space-y-1">
          {variables.map((v) => (
            <div key={v.id} className="flex items-center gap-2">
              <code className="flex-1 truncate text-[11px]">{v.name}</code>
              <Input
                value={v.linkGroup ?? ""}
                onChange={(e) =>
                  actions.setLinkGroup(
                    v.id,
                    e.target.value.trim() === "" ? null : e.target.value.trim(),
                  )
                }
                placeholder="group name"
                className="h-6 w-28 text-[11px]"
              />
            </div>
          ))}
        </div>
        <p className="text-[10px] text-muted-foreground">
          Give two variables the same group name to link them.
        </p>
      </PopoverContent>
    </Popover>
  );
}

/** Save / load / delete named templates. */
function TemplateMenu({
  templates,
  name,
  onNameChange,
  onSave,
  onLoad,
  onDelete,
}: {
  templates: { id: string; name: string }[];
  name: string;
  onNameChange: (name: string) => void;
  onSave: (name: string) => void;
  onLoad: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs">
          <Save className="h-3.5 w-3.5" />
          Templates
          {templates.length > 0 && (
            <Badge variant="secondary" className="h-4 px-1 text-[10px]">
              {templates.length}
            </Badge>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 space-y-3 p-3">
        <div className="space-y-1.5">
          <Label className="text-xs">Save this matrix</Label>
          <div className="flex gap-1.5">
            <Input
              value={name}
              onChange={(e) => onNameChange(e.target.value)}
              placeholder="Portrait sweep"
              className="h-7 text-xs"
              onKeyDown={(e) => {
                if (e.key === "Enter" && name.trim().length > 0) onSave(name);
              }}
            />
            <Button
              size="sm"
              className="h-7 shrink-0 text-xs"
              disabled={name.trim().length === 0}
              onClick={() => onSave(name)}
            >
              Save
            </Button>
          </div>
          <p className="text-[10px] text-muted-foreground">
            Also used as the batch name in the queue.
          </p>
        </div>

        {templates.length > 0 && (
          <>
            <Separator />
            <div className="space-y-0.5">
              <Label className="text-xs">Saved</Label>
              {templates.map((t) => (
                <div key={t.id} className="flex items-center gap-1">
                  <button
                    type="button"
                    className="flex-1 truncate rounded px-1.5 py-1 text-left text-xs hover:bg-accent"
                    onClick={() => {
                      onLoad(t.id);
                      onNameChange(t.name);
                      setOpen(false);
                    }}
                  >
                    {t.name}
                  </button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6 shrink-0 text-muted-foreground hover:text-destructive"
                    onClick={() => onDelete(t.id)}
                    aria-label={`Delete ${t.name}`}
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
              ))}
            </div>
          </>
        )}
      </PopoverContent>
    </Popover>
  );
}
