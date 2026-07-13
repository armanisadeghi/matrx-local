/**
 * usePromptMatrix — React state for a prompt matrix, bound to the pure engine
 * in lib/prompt-matrix.
 *
 * The hook owns ONLY the spec (template text, variables, strategy, seeds) and
 * derives everything else. It knows nothing about images: it takes a
 * MatrixTarget, so the same hook drives a video or text-prompt matrix the day
 * those targets exist.
 *
 * React rules (repo CLAUDE.md → React Patterns): every handler is
 * useCallback'd, `actions` is useMemo'd, and the persistence effect is gated on
 * the spec itself — never on `actions`.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  countPlan,
  expandMatrix,
  extractVariableNames,
  syncVariablesWithTokens,
  type MatrixOption,
  type MatrixPlan,
  type MatrixSpec,
  type MatrixStrategy,
  type MatrixTarget,
  type MatrixVariable,
  type SeedPolicy,
  type StrategyKind,
} from "@/lib/prompt-matrix";
import { randomSeed } from "@/lib/prompt-matrix";
import {
  deleteTemplate,
  emptySpec,
  loadTemplates,
  loadWorkingSpec,
  makeId,
  saveTemplate,
  saveWorkingSpec,
  type SavedTemplate,
} from "@/lib/prompt-matrix/storage";

export interface PromptMatrixState {
  spec: MatrixSpec;
  /** Ordered runs + the EXACT total (which may exceed the materialized list). */
  plan: MatrixPlan;
  /** Exact run count. Cheap — never materializes the product. */
  total: number;
  /** True when the matrix cannot be run (errors are in `plan.errors`). */
  blocked: boolean;
  /** Named templates saved for this target, newest first. */
  templates: SavedTemplate[];
}

export interface PromptMatrixActions {
  setFieldText: (fieldId: string, text: string) => void;
  /** Wrap the current selection (or insert) as a new {{variable}}. */
  insertVariable: (fieldId: string, name: string) => void;

  addParamVariable: (axisId: string, label: string) => void;
  removeVariable: (variableId: string) => void;
  renameVariable: (variableId: string, name: string) => void;
  toggleVariable: (variableId: string, enabled: boolean) => void;
  /** Reorder variables — THIS is the loop-nesting / "which one is frozen" control. */
  reorderVariables: (orderedIds: string[]) => void;
  setLinkGroup: (variableId: string, group: string | null) => void;
  setBaselineOption: (variableId: string, optionId: string | null) => void;

  addOption: (variableId: string, value?: string) => void;
  /** Paste-friendly: one option per line. */
  addOptions: (variableId: string, values: string[]) => void;
  updateOption: (
    variableId: string,
    optionId: string,
    patch: Partial<MatrixOption>,
  ) => void;
  removeOption: (variableId: string, optionId: string) => void;

  setStrategy: (kind: StrategyKind) => void;
  setSampleCount: (count: number) => void;
  setSeedPolicy: (patch: Partial<SeedPolicy>) => void;
  rerollSeeds: () => void;

  reset: () => void;
  loadTemplate: (id: string) => void;
  saveAsTemplate: (name: string) => void;
  removeTemplate: (id: string) => void;
}

export function usePromptMatrix<TJob>(
  target: MatrixTarget<TJob>,
): [PromptMatrixState, PromptMatrixActions] {
  const targetId = target.id;
  const targetFields = target.fields;

  const [spec, setSpec] = useState<MatrixSpec>(() =>
    loadWorkingSpec(targetId, targetFields),
  );
  const [templates, setTemplates] = useState<SavedTemplate[]>(() =>
    loadTemplates(targetId),
  );

  // Persist the working spec (debounced — this fires on every keystroke).
  const saveTimer = useRef<number | null>(null);
  useEffect(() => {
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      saveWorkingSpec(targetId, spec);
    }, 400);
    return () => {
      if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    };
  }, [spec, targetId]);

  /**
   * Keep the variable list in step with the {{tokens}} in the template.
   * Runs inside the setter (not an effect) so typing never causes a
   * render → effect → setState → render loop.
   */
  const withTokenSync = useCallback((next: MatrixSpec): MatrixSpec => {
    const names = extractVariableNames(next.fields.map((f) => f.text));
    const variables = syncVariablesWithTokens(next.variables, names, makeId);
    return variables === next.variables ? next : { ...next, variables };
  }, []);

  const patchVariable = useCallback(
    (variableId: string, fn: (v: MatrixVariable) => MatrixVariable) => {
      setSpec((prev) => ({
        ...prev,
        variables: prev.variables.map((v) =>
          v.id === variableId ? fn(v) : v,
        ),
      }));
    },
    [],
  );

  // ── template text ─────────────────────────────────────────────────────────

  const setFieldText = useCallback(
    (fieldId: string, text: string) => {
      setSpec((prev) =>
        withTokenSync({
          ...prev,
          fields: prev.fields.map((f) =>
            f.id === fieldId ? { ...f, text } : f,
          ),
        }),
      );
    },
    [withTokenSync],
  );

  const insertVariable = useCallback(
    (fieldId: string, name: string) => {
      const token = `{{${name.trim()}}}`;
      setSpec((prev) =>
        withTokenSync({
          ...prev,
          fields: prev.fields.map((f) =>
            f.id === fieldId
              ? { ...f, text: f.text.length > 0 ? `${f.text} ${token}` : token }
              : f,
          ),
        }),
      );
    },
    [withTokenSync],
  );

  // ── variables ─────────────────────────────────────────────────────────────

  const addParamVariable = useCallback((axisId: string, label: string) => {
    setSpec((prev) => {
      // One variable per axis — a second "Steps" sweep would multiply against
      // itself and produce contradictory runs.
      if (
        prev.variables.some(
          (v) => v.binding.kind === "param" && v.binding.axisId === axisId,
        )
      ) {
        return prev;
      }
      const variable: MatrixVariable = {
        id: makeId(),
        name: label,
        binding: { kind: "param", axisId },
        options: [{ id: makeId(), value: "", enabled: true }],
        baselineOptionId: null,
        linkGroup: null,
        enabled: true,
      };
      return { ...prev, variables: [...prev.variables, variable] };
    });
  }, []);

  const removeVariable = useCallback((variableId: string) => {
    setSpec((prev) => ({
      ...prev,
      variables: prev.variables.filter((v) => v.id !== variableId),
    }));
  }, []);

  const renameVariable = useCallback(
    (variableId: string, name: string) => {
      patchVariable(variableId, (v) => ({ ...v, name }));
    },
    [patchVariable],
  );

  const toggleVariable = useCallback(
    (variableId: string, enabled: boolean) => {
      patchVariable(variableId, (v) => ({ ...v, enabled }));
    },
    [patchVariable],
  );

  const reorderVariables = useCallback((orderedIds: string[]) => {
    setSpec((prev) => {
      const byId = new Map(prev.variables.map((v) => [v.id, v]));
      const next = orderedIds
        .map((id) => byId.get(id))
        .filter((v): v is MatrixVariable => v !== undefined);
      // Anything the caller didn't mention keeps its place at the end, so a
      // stale drag can never silently drop a variable.
      for (const v of prev.variables) {
        if (!orderedIds.includes(v.id)) next.push(v);
      }
      return { ...prev, variables: next };
    });
  }, []);

  const setLinkGroup = useCallback(
    (variableId: string, group: string | null) => {
      patchVariable(variableId, (v) => ({ ...v, linkGroup: group }));
    },
    [patchVariable],
  );

  const setBaselineOption = useCallback(
    (variableId: string, optionId: string | null) => {
      patchVariable(variableId, (v) => ({ ...v, baselineOptionId: optionId }));
    },
    [patchVariable],
  );

  // ── options ───────────────────────────────────────────────────────────────

  const addOption = useCallback(
    (variableId: string, value = "") => {
      patchVariable(variableId, (v) => ({
        ...v,
        options: [...v.options, { id: makeId(), value, enabled: true }],
      }));
    },
    [patchVariable],
  );

  const addOptions = useCallback(
    (variableId: string, values: string[]) => {
      const fresh = values.map((value) => ({
        id: makeId(),
        value,
        enabled: true,
      }));
      if (fresh.length === 0) return;
      patchVariable(variableId, (v) => {
        // A pasted list replaces the lone empty starter option rather than
        // landing under it.
        const existing =
          v.options.length === 1 && v.options[0]?.value.trim() === ""
            ? []
            : v.options;
        return { ...v, options: [...existing, ...fresh] };
      });
    },
    [patchVariable],
  );

  const updateOption = useCallback(
    (variableId: string, optionId: string, patch: Partial<MatrixOption>) => {
      patchVariable(variableId, (v) => ({
        ...v,
        options: v.options.map((o) =>
          o.id === optionId ? { ...o, ...patch } : o,
        ),
      }));
    },
    [patchVariable],
  );

  const removeOption = useCallback(
    (variableId: string, optionId: string) => {
      patchVariable(variableId, (v) => ({
        ...v,
        options: v.options.filter((o) => o.id !== optionId),
        baselineOptionId:
          v.baselineOptionId === optionId ? null : v.baselineOptionId,
      }));
    },
    [patchVariable],
  );

  // ── strategy + seeds ──────────────────────────────────────────────────────

  const setStrategy = useCallback((kind: StrategyKind) => {
    setSpec((prev) => {
      if (prev.strategy.kind === kind) return prev;
      const strategy: MatrixStrategy =
        kind === "sample"
          ? {
              kind,
              // Default the sample to something useful, not to the whole product.
              count: Math.min(20, Math.max(1, countPlan(prev))),
              seed: randomSeed(),
            }
          : { kind };
      return { ...prev, strategy };
    });
  }, []);

  const setSampleCount = useCallback((count: number) => {
    setSpec((prev) =>
      prev.strategy.kind === "sample"
        ? {
            ...prev,
            strategy: { ...prev.strategy, count: Math.max(1, Math.trunc(count)) },
          }
        : prev,
    );
  }, []);

  const setSeedPolicy = useCallback((patch: Partial<SeedPolicy>) => {
    setSpec((prev) => ({ ...prev, seed: { ...prev.seed, ...patch } }));
  }, []);

  const rerollSeeds = useCallback(() => {
    setSpec((prev) => ({
      ...prev,
      seed: { ...prev.seed, baseSeed: randomSeed(), rngSeed: randomSeed() },
      strategy:
        prev.strategy.kind === "sample"
          ? { ...prev.strategy, seed: randomSeed() }
          : prev.strategy,
    }));
  }, []);

  // ── templates ─────────────────────────────────────────────────────────────

  const reset = useCallback(() => {
    setSpec(emptySpec(targetFields));
  }, [targetFields]);

  const loadTemplateById = useCallback(
    (id: string) => {
      const found = templates.find((t) => t.id === id);
      if (found === undefined) return;
      // Re-key the loaded spec's fields onto the target's current fields.
      const byId = new Map(found.spec.fields.map((f) => [f.id, f.text]));
      setSpec({
        ...found.spec,
        fields: targetFields.map((f) => ({ ...f, text: byId.get(f.id) ?? "" })),
      });
    },
    [templates, targetFields],
  );

  const saveAsTemplate = useCallback(
    (name: string) => {
      if (name.trim().length === 0) return;
      saveTemplate(targetId, name, spec);
      setTemplates(loadTemplates(targetId));
    },
    [spec, targetId],
  );

  const removeTemplate = useCallback(
    (id: string) => {
      deleteTemplate(id);
      setTemplates(loadTemplates(targetId));
    },
    [targetId],
  );

  // ── derived ───────────────────────────────────────────────────────────────

  const plan = useMemo(() => expandMatrix(spec), [spec]);
  const total = plan.total;

  const state = useMemo<PromptMatrixState>(
    () => ({
      spec,
      plan,
      total,
      blocked: plan.errors.length > 0,
      templates,
    }),
    [spec, plan, total, templates],
  );

  const actions = useMemo<PromptMatrixActions>(
    () => ({
      setFieldText,
      insertVariable,
      addParamVariable,
      removeVariable,
      renameVariable,
      toggleVariable,
      reorderVariables,
      setLinkGroup,
      setBaselineOption,
      addOption,
      addOptions,
      updateOption,
      removeOption,
      setStrategy,
      setSampleCount,
      setSeedPolicy,
      rerollSeeds,
      reset,
      loadTemplate: loadTemplateById,
      saveAsTemplate,
      removeTemplate,
    }),
    [
      setFieldText,
      insertVariable,
      addParamVariable,
      removeVariable,
      renameVariable,
      toggleVariable,
      reorderVariables,
      setLinkGroup,
      setBaselineOption,
      addOption,
      addOptions,
      updateOption,
      removeOption,
      setStrategy,
      setSampleCount,
      setSeedPolicy,
      rerollSeeds,
      reset,
      loadTemplateById,
      saveAsTemplate,
      removeTemplate,
    ],
  );

  return [state, actions];
}
