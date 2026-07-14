/**
 * usePromptMatrix — React state for a prompt matrix, bound to the pure engine
 * in lib/prompt-matrix.
 *
 * The hook owns the working spec (template text, variables, pools, strategy,
 * seeds) plus the on-disk library/templates loaded from the engine
 * (`~/.matrx/prompt-matrix/*.json`). It knows nothing about images: it takes
 * a MatrixTarget, so the same hook drives a video or text-prompt matrix the
 * day those targets exist.
 *
 * React rules (repo CLAUDE.md → React Patterns): every handler is
 * useCallback'd, `actions` is useMemo'd, and the persistence effect is gated on
 * the spec itself — never on `actions`.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  engine,
  getPromptMatrixLibrary,
  getPromptMatrixPaths,
  getPromptMatrixTemplates,
  putPromptMatrixLibrary,
  putPromptMatrixTemplates,
} from "@/lib/api";
import {
  countPlan,
  expandMatrix,
  extractPoolRefs,
  extractVariableNames,
  libraryEntryFromPool,
  libraryEntryFromVariable,
  parseMatrixImport,
  randomSeed,
  sanitizeLibraryEntries,
  syncPoolsWithTokens,
  syncVariablesWithTokens,
  type LibraryEntry,
  type MatrixImportResult,
  type MatrixOption,
  type MatrixPlan,
  type MatrixPool,
  type MatrixSpec,
  type MatrixStrategy,
  type MatrixTarget,
  type MatrixVariable,
  type PoolAssign,
  type SeedPolicy,
  type StrategyKind,
} from "@/lib/prompt-matrix";
import {
  coerceSpec,
  emptySpec,
  loadTemplates,
  loadWorkingSpec,
  makeId,
  replaceTemplatesCache,
  sanitizeSavedTemplates,
  saveWorkingSpec,
  type SavedTemplate,
} from "@/lib/prompt-matrix/storage";
import {
  insertLibraryEntryInSpec,
  renameVariableInSpec,
} from "@/lib/prompt-matrix/edit";

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
  /** Reusable pools/variables from on-disk library.json. */
  library: LibraryEntry[];
  /** Absolute path to library.json when the engine reported it. */
  libraryPath: string | null;
  /** True once the first disk load attempt finished (ok or error). */
  libraryReady: boolean;
  /** Loud disk/engine error — never swallowed. */
  libraryError: string | null;
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

  removePool: (poolId: string) => void;
  togglePool: (poolId: string, enabled: boolean) => void;
  setPoolAssign: (poolId: string, assign: PoolAssign) => void;
  setPoolBaselineOption: (poolId: string, optionId: string | null) => void;
  addPoolOption: (poolId: string, value?: string) => void;
  addPoolOptions: (poolId: string, values: string[]) => void;
  updatePoolOption: (
    poolId: string,
    optionId: string,
    patch: Partial<MatrixOption>,
  ) => void;
  removePoolOption: (poolId: string, optionId: string) => void;

  setStrategy: (kind: StrategyKind) => void;
  setSampleCount: (count: number) => void;
  setSeedPolicy: (patch: Partial<SeedPolicy>) => void;
  rerollSeeds: () => void;

  reset: () => void;
  loadTemplate: (id: string) => void;
  saveAsTemplate: (name: string) => void;
  removeTemplate: (id: string) => void;
  /** Load a MatrixSpec from exported / agent-edited JSON. */
  importFromJson: (text: string) => MatrixImportResult;

  /** Persist a pool's option list to the on-disk library. */
  savePoolToLibrary: (poolId: string, name?: string) => Promise<void>;
  /** Persist a variable's option list to the on-disk library. */
  saveVariableToLibrary: (variableId: string, name?: string) => Promise<void>;
  /** Insert a library entry into the current matrix. */
  insertLibraryEntry: (entryId: string) => void;
  removeLibraryEntry: (entryId: string) => Promise<void>;
  refreshLibrary: () => Promise<void>;
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
  const [library, setLibrary] = useState<LibraryEntry[]>([]);
  const [libraryPath, setLibraryPath] = useState<string | null>(null);
  const [libraryReady, setLibraryReady] = useState(false);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const diskSynced = useRef(false);

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

  const persistLibrary = useCallback(async (entries: LibraryEntry[]) => {
    const base = engine.engineUrl;
    if (base === null) {
      throw new Error("Engine not connected — cannot write library.json.");
    }
    await putPromptMatrixLibrary(base, entries);
    setLibrary(entries);
    setLibraryError(null);
  }, []);

  const persistTemplates = useCallback(
    async (all: SavedTemplate[]) => {
      const base = engine.engineUrl;
      if (base === null) {
        throw new Error("Engine not connected — cannot write templates.json.");
      }
      await putPromptMatrixTemplates(base, all);
      replaceTemplatesCache(all);
      setTemplates(all.filter((t) => t.targetId === targetId));
      setLibraryError(null);
    },
    [targetId],
  );

  const refreshLibrary = useCallback(async () => {
    const base = engine.engineUrl;
    if (base === null) {
      setLibraryError(
        "Engine not connected — library is on disk via the engine. Start the engine to load/save it.",
      );
      setLibraryReady(true);
      return;
    }
    try {
      const [paths, libDoc, tmplDoc] = await Promise.all([
        getPromptMatrixPaths(base),
        getPromptMatrixLibrary(base),
        getPromptMatrixTemplates(base),
      ]);
      setLibraryPath(paths.library);

      let diskTemplates = sanitizeSavedTemplates(tmplDoc.templates);
      // One-time migrate: localStorage → disk when the file is empty.
      if (diskTemplates.length === 0) {
        const local = loadTemplates(targetId);
        // loadTemplates filters by target — pull the full cache via a re-read
        // of every target by merging what we have for this target only if disk
        // is empty. Broader migrate: if ANY local templates exist and disk is
        // empty, write them.
        const localAll = (() => {
          try {
            const raw = localStorage.getItem("matrx-prompt-matrix-templates");
            if (raw === null) return [] as SavedTemplate[];
            const parsed: unknown = JSON.parse(raw);
            return sanitizeSavedTemplates(Array.isArray(parsed) ? parsed : []);
          } catch {
            return [] as SavedTemplate[];
          }
        })();
        if (localAll.length > 0) {
          await putPromptMatrixTemplates(base, localAll);
          diskTemplates = localAll;
        } else if (local.length > 0) {
          await putPromptMatrixTemplates(base, local);
          diskTemplates = local;
        }
      }
      replaceTemplatesCache(diskTemplates);
      setTemplates(diskTemplates.filter((t) => t.targetId === targetId));
      setLibrary(sanitizeLibraryEntries(libDoc.entries));
      setLibraryError(null);
      diskSynced.current = true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setLibraryError(`Could not load on-disk library: ${msg}`);
    } finally {
      setLibraryReady(true);
    }
  }, [targetId]);

  // Load from disk once the engine URL is available (and retry when it appears).
  useEffect(() => {
    if (diskSynced.current) return;
    void refreshLibrary();
    const id = window.setInterval(() => {
      if (diskSynced.current) {
        window.clearInterval(id);
        return;
      }
      if (engine.engineUrl !== null) void refreshLibrary();
    }, 2000);
    return () => window.clearInterval(id);
  }, [refreshLibrary]);

  /**
   * Keep variables + pools in step with the {{tokens}} in the template.
   * Runs inside the setter (not an effect) so typing never causes a
   * render → effect → setState → render loop.
   */
  const withTokenSync = useCallback((next: MatrixSpec): MatrixSpec => {
    const texts = next.fields.map((f) => f.text);
    const names = extractVariableNames(texts);
    const poolRefs = extractPoolRefs(texts);
    const variables = syncVariablesWithTokens(next.variables, names, makeId);
    const pools = syncPoolsWithTokens(next.pools ?? [], poolRefs, makeId);
    if (variables === next.variables && pools === (next.pools ?? [])) {
      return next.pools === undefined ? { ...next, pools: [] } : next;
    }
    return { ...next, variables, pools };
  }, []);

  const patchVariable = useCallback(
    (variableId: string, fn: (v: MatrixVariable) => MatrixVariable) => {
      setSpec((prev) => ({
        ...prev,
        variables: prev.variables.map((v) => (v.id === variableId ? fn(v) : v)),
      }));
    },
    [],
  );

  const patchPool = useCallback(
    (poolId: string, fn: (p: MatrixPool) => MatrixPool) => {
      setSpec((prev) => ({
        ...prev,
        pools: (prev.pools ?? []).map((p) => (p.id === poolId ? fn(p) : p)),
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
          pools: prev.pools ?? [],
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
          pools: prev.pools ?? [],
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
      return {
        ...prev,
        pools: prev.pools ?? [],
        variables: [...prev.variables, variable],
      };
    });
  }, []);

  const removeVariable = useCallback((variableId: string) => {
    setSpec((prev) => ({
      ...prev,
      pools: prev.pools ?? [],
      variables: prev.variables.filter((v) => v.id !== variableId),
    }));
  }, []);

  const renameVariable = useCallback(
    (variableId: string, name: string) => {
      setSpec((prev) => renameVariableInSpec(prev, variableId, name));
    },
    [],
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
      return { ...prev, pools: prev.pools ?? [], variables: next };
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

  // ── variable options ──────────────────────────────────────────────────────

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

  // ── pools ─────────────────────────────────────────────────────────────────

  const removePool = useCallback((poolId: string) => {
    setSpec((prev) => ({
      ...prev,
      pools: (prev.pools ?? []).filter((p) => p.id !== poolId),
    }));
  }, []);

  const togglePool = useCallback(
    (poolId: string, enabled: boolean) => {
      patchPool(poolId, (p) => ({ ...p, enabled }));
    },
    [patchPool],
  );

  const setPoolAssign = useCallback(
    (poolId: string, assign: PoolAssign) => {
      patchPool(poolId, (p) => ({ ...p, assign }));
    },
    [patchPool],
  );

  const setPoolBaselineOption = useCallback(
    (poolId: string, optionId: string | null) => {
      patchPool(poolId, (p) => ({ ...p, baselineOptionId: optionId }));
    },
    [patchPool],
  );

  const addPoolOption = useCallback(
    (poolId: string, value = "") => {
      patchPool(poolId, (p) => ({
        ...p,
        options: [...p.options, { id: makeId(), value, enabled: true }],
      }));
    },
    [patchPool],
  );

  const addPoolOptions = useCallback(
    (poolId: string, values: string[]) => {
      const fresh = values.map((value) => ({
        id: makeId(),
        value,
        enabled: true,
      }));
      if (fresh.length === 0) return;
      patchPool(poolId, (p) => {
        const existing =
          p.options.length === 1 && p.options[0]?.value.trim() === ""
            ? []
            : p.options;
        return { ...p, options: [...existing, ...fresh] };
      });
    },
    [patchPool],
  );

  const updatePoolOption = useCallback(
    (poolId: string, optionId: string, patch: Partial<MatrixOption>) => {
      patchPool(poolId, (p) => ({
        ...p,
        options: p.options.map((o) =>
          o.id === optionId ? { ...o, ...patch } : o,
        ),
      }));
    },
    [patchPool],
  );

  const removePoolOption = useCallback(
    (poolId: string, optionId: string) => {
      patchPool(poolId, (p) => ({
        ...p,
        options: p.options.filter((o) => o.id !== optionId),
        baselineOptionId:
          p.baselineOptionId === optionId ? null : p.baselineOptionId,
      }));
    },
    [patchPool],
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
      return { ...prev, pools: prev.pools ?? [], strategy };
    });
  }, []);

  const setSampleCount = useCallback((count: number) => {
    setSpec((prev) =>
      prev.strategy.kind === "sample"
        ? {
            ...prev,
            pools: prev.pools ?? [],
            strategy: {
              ...prev.strategy,
              count: Math.max(1, Math.trunc(count)),
            },
          }
        : prev,
    );
  }, []);

  const setSeedPolicy = useCallback((patch: Partial<SeedPolicy>) => {
    setSpec((prev) => ({
      ...prev,
      pools: prev.pools ?? [],
      seed: { ...prev.seed, ...patch },
    }));
  }, []);

  const rerollSeeds = useCallback(() => {
    setSpec((prev) => ({
      ...prev,
      pools: prev.pools ?? [],
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
      setSpec(
        coerceSpec({
          ...found.spec,
          fields: targetFields.map((f) => ({
            ...f,
            text: byId.get(f.id) ?? "",
          })),
        }),
      );
    },
    [templates, targetFields],
  );

  const saveAsTemplate = useCallback(
    (name: string) => {
      if (name.trim().length === 0) return;
      const trimmed = name.trim();
      const now = Date.now();
      void (async () => {
        const base = engine.engineUrl;
        if (base === null) {
          setLibraryError(
            "Engine not connected — cannot save template to disk.",
          );
          return;
        }
        try {
          const doc = await getPromptMatrixTemplates(base);
          const all = sanitizeSavedTemplates(doc.templates);
          const existing = all.find(
            (t) =>
              t.targetId === targetId &&
              t.name.toLowerCase() === trimmed.toLowerCase(),
          );
          const saved: SavedTemplate = existing
            ? { ...existing, spec: coerceSpec(spec), updatedAt: now }
            : {
                id: makeId(),
                name: trimmed,
                targetId,
                spec: coerceSpec(spec),
                createdAt: now,
                updatedAt: now,
              };
          const next = [...all.filter((t) => t.id !== saved.id), saved].sort(
            (a, b) => b.updatedAt - a.updatedAt,
          );
          await persistTemplates(next);
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          setLibraryError(`Could not save template: ${msg}`);
        }
      })();
    },
    [spec, targetId, persistTemplates],
  );

  const removeTemplate = useCallback(
    (id: string) => {
      void (async () => {
        const base = engine.engineUrl;
        if (base === null) {
          setLibraryError(
            "Engine not connected — cannot delete template on disk.",
          );
          return;
        }
        try {
          const doc = await getPromptMatrixTemplates(base);
          const all = sanitizeSavedTemplates(doc.templates).filter(
            (t) => t.id !== id,
          );
          await persistTemplates(all);
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          setLibraryError(`Could not delete template: ${msg}`);
        }
      })();
    },
    [persistTemplates],
  );

  const savePoolToLibrary = useCallback(
    async (poolId: string, name?: string) => {
      const pool = (spec.pools ?? []).find((p) => p.id === poolId);
      if (pool === undefined) return;
      const entry = libraryEntryFromPool(pool, name);
      const next = [
        entry,
        ...library.filter(
          (e) =>
            !(
              e.kind === "pool" &&
              e.name.toLowerCase() === entry.name.toLowerCase()
            ),
        ),
      ];
      try {
        await persistLibrary(next);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setLibraryError(`Could not save pool to library: ${msg}`);
      }
    },
    [spec.pools, library, persistLibrary],
  );

  const saveVariableToLibrary = useCallback(
    async (variableId: string, name?: string) => {
      const variable = spec.variables.find((v) => v.id === variableId);
      if (variable === undefined) return;
      const entry = libraryEntryFromVariable(variable, name);
      const next = [
        entry,
        ...library.filter(
          (e) =>
            !(
              e.kind === "variable" &&
              e.name.toLowerCase() === entry.name.toLowerCase()
            ),
        ),
      ];
      try {
        await persistLibrary(next);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setLibraryError(`Could not save variable to library: ${msg}`);
      }
    },
    [spec.variables, library, persistLibrary],
  );

  const insertLibraryEntry = useCallback(
    (entryId: string) => {
      const entry = library.find((e) => e.id === entryId);
      if (entry === undefined) return;
      setSpec((prev) => insertLibraryEntryInSpec(prev, entry));
    },
    [library],
  );

  const removeLibraryEntry = useCallback(
    async (entryId: string) => {
      const next = library.filter((e) => e.id !== entryId);
      try {
        await persistLibrary(next);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setLibraryError(`Could not remove library entry: ${msg}`);
      }
    },
    [library, persistLibrary],
  );

  const importFromJson = useCallback(
    (text: string): MatrixImportResult => {
      const parsed = parseMatrixImport(text);
      if (!parsed.ok) return parsed;

      if (parsed.targetId !== targetId) {
        return {
          ok: false,
          error: `This export is for "${parsed.targetId}" — this panel is "${targetId}".`,
        };
      }

      const byId = new Map(parsed.spec.fields.map((f) => [f.id, f.text]));
      setSpec(
        coerceSpec({
          ...parsed.spec,
          fields: targetFields.map((f) => ({
            ...f,
            text: byId.get(f.id) ?? "",
          })),
        }),
      );
      return parsed;
    },
    [targetFields, targetId],
  );

  // ── derived ───────────────────────────────────────────────────────────────

  const normalizedSpec = useMemo(() => coerceSpec(spec), [spec]);
  const plan = useMemo(() => expandMatrix(normalizedSpec), [normalizedSpec]);
  const total = plan.total;

  const state = useMemo<PromptMatrixState>(
    () => ({
      spec: normalizedSpec,
      plan,
      total,
      blocked: plan.errors.length > 0,
      templates,
      library,
      libraryPath,
      libraryReady,
      libraryError,
    }),
    [
      normalizedSpec,
      plan,
      total,
      templates,
      library,
      libraryPath,
      libraryReady,
      libraryError,
    ],
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
      removePool,
      togglePool,
      setPoolAssign,
      setPoolBaselineOption,
      addPoolOption,
      addPoolOptions,
      updatePoolOption,
      removePoolOption,
      setStrategy,
      setSampleCount,
      setSeedPolicy,
      rerollSeeds,
      reset,
      loadTemplate: loadTemplateById,
      saveAsTemplate,
      removeTemplate,
      importFromJson,
      savePoolToLibrary,
      saveVariableToLibrary,
      insertLibraryEntry,
      removeLibraryEntry,
      refreshLibrary,
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
      removePool,
      togglePool,
      setPoolAssign,
      setPoolBaselineOption,
      addPoolOption,
      addPoolOptions,
      updatePoolOption,
      removePoolOption,
      setStrategy,
      setSampleCount,
      setSeedPolicy,
      rerollSeeds,
      reset,
      loadTemplateById,
      saveAsTemplate,
      removeTemplate,
      importFromJson,
      savePoolToLibrary,
      saveVariableToLibrary,
      insertLibraryEntry,
      removeLibraryEntry,
      refreshLibrary,
    ],
  );

  return [state, actions];
}
