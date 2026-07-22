import {
  engine,
  getPromptMatrixPaths,
  getPromptMatrixVariationBatches,
  putPromptMatrixVariationBatches,
} from "@/lib/api";
import {
  expandPromptVariations,
  type VariableOptionMap,
  type VariationGenerateOrder,
} from "@/lib/variation-batches/expand";
import {
  cloneVariationBatchTemplate,
  emptyVariationBatch,
  emptyVariationItem,
  sanitizeVariationBatches,
  type VariationBatch,
  type VariationItem,
  type VariationItemStatus,
} from "@/lib/variation-batches/types";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const ENGINE_NOT_CONNECTED = "Engine not connected";

export interface VariationBatchesState {
  batches: VariationBatch[];
  batchesPath: string | null;
  loading: boolean;
  ready: boolean;
  error: string | null;
  saving: boolean;
  generatingBatchId: string | null;
}

export interface VariationBatchesActions {
  refresh: () => Promise<void>;
  createBatch: (name?: string) => Promise<VariationBatch | null>;
  updateBatch: (
    id: string,
    patch: Partial<
      Pick<
        VariationBatch,
        | "name"
        | "sourcePromptId"
        | "templatePrompt"
        | "templateNegative"
        | "variableListByName"
        | "items"
      >
    >,
  ) => Promise<boolean>;
  deleteBatch: (id: string) => Promise<boolean>;
  duplicateBatch: (id: string) => Promise<VariationBatch | null>;
  clearVariationItems: (batchId: string) => Promise<boolean>;
  generateVariations: (params: {
    batchId: string;
    name?: string;
    sourcePromptId: string | null;
    templatePrompt: string;
    templateNegative: string;
    variableListByName: Record<string, string>;
    variables: VariableOptionMap[];
    maxCount?: number;
    order?: VariationGenerateOrder;
  }) => Promise<{ ok: boolean; errors: string[] }>;
  updateItemStatus: (
    batchId: string,
    itemId: string,
    status: VariationItemStatus,
    error?: string,
  ) => Promise<boolean>;
  addVariationItem: (
    batchId: string,
    prompt?: string,
    negativePrompt?: string,
  ) => Promise<VariationItem | null>;
  updateVariationItem: (
    batchId: string,
    itemId: string,
    patch: Partial<Pick<VariationItem, "prompt" | "negativePrompt" | "status">>,
  ) => Promise<boolean>;
  deleteVariationItem: (batchId: string, itemId: string) => Promise<boolean>;
  clearError: () => void;
}

export function useVariationBatches(): [
  VariationBatchesState,
  VariationBatchesActions,
] {
  const [batches, setBatches] = useState<VariationBatch[]>([]);
  const [batchesPath, setBatchesPath] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [generatingBatchId, setGeneratingBatchId] = useState<string | null>(
    null,
  );
  const batchesRef = useRef(batches);
  const writeChainRef = useRef<Promise<void>>(Promise.resolve());
  batchesRef.current = batches;

  const enqueueWrite = useCallback(
    <T>(operation: () => Promise<T>): Promise<T> => {
      const result = writeChainRef.current.then(operation, operation);
      writeChainRef.current = result.then(
        () => undefined,
        () => undefined,
      );
      return result;
    },
    [],
  );

  const persist = useCallback(
    async (next: VariationBatch[]): Promise<boolean> => {
      const baseUrl = engine.engineUrl;
      if (!baseUrl) {
        setError(ENGINE_NOT_CONNECTED);
        return false;
      }
      setSaving(true);
      try {
        const saved = await putPromptMatrixVariationBatches(baseUrl, next);
        const sanitized = sanitizeVariationBatches(saved.batches);
        // Keep imperative writers current before React commits the state update.
        // A serialized autosave may start its next write in the same microtask.
        batchesRef.current = sanitized;
        setBatches(sanitized);
        setError(null);
        return true;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
        return false;
      } finally {
        setSaving(false);
      }
    },
    [],
  );

  const refresh = useCallback(async () => {
    const baseUrl = engine.engineUrl;
    if (!baseUrl) {
      setError(ENGINE_NOT_CONNECTED);
      setLoading(false);
      setReady(true);
      return;
    }
    setLoading(true);
    try {
      const [payload, paths] = await Promise.all([
        getPromptMatrixVariationBatches(baseUrl),
        getPromptMatrixPaths(baseUrl),
      ]);
      const sanitized = sanitizeVariationBatches(payload.batches);
      batchesRef.current = sanitized;
      setBatches(sanitized);
      setBatchesPath(paths.variationBatches ?? null);
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
    } finally {
      setLoading(false);
      setReady(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (error !== ENGINE_NOT_CONNECTED) return;
    const id = window.setInterval(() => {
      if (engine.engineUrl) void refresh();
    }, 3000);
    return () => window.clearInterval(id);
  }, [error, refresh]);

  const createBatch = useCallback(
    async (name?: string): Promise<VariationBatch | null> => {
      const row = emptyVariationBatch(name?.trim() || "New batch");
      const ok = await enqueueWrite(() =>
        persist([row, ...batchesRef.current]),
      );
      return ok ? row : null;
    },
    [enqueueWrite, persist],
  );

  const updateBatch = useCallback(
    async (
      id: string,
      patch: Partial<
        Pick<
          VariationBatch,
          | "name"
          | "sourcePromptId"
          | "templatePrompt"
          | "templateNegative"
          | "variableListByName"
          | "items"
        >
      >,
    ): Promise<boolean> => {
      return enqueueWrite(() => {
        const now = Date.now();
        const next = batchesRef.current.map((row) => {
          if (row.id !== id) return row;
          return {
            ...row,
            ...patch,
            name: patch.name !== undefined ? patch.name : row.name,
            updatedAt: now,
          };
        });
        return persist(next);
      });
    },
    [enqueueWrite, persist],
  );

  const deleteBatch = useCallback(
    async (id: string): Promise<boolean> => {
      return enqueueWrite(() =>
        persist(batchesRef.current.filter((row) => row.id !== id)),
      );
    },
    [enqueueWrite, persist],
  );

  const duplicateBatch = useCallback(
    async (id: string): Promise<VariationBatch | null> => {
      return enqueueWrite(async () => {
        const source = batchesRef.current.find((row) => row.id === id);
        if (!source) return null;
        const copy = cloneVariationBatchTemplate(source);
        const ok = await persist([copy, ...batchesRef.current]);
        return ok ? copy : null;
      });
    },
    [enqueueWrite, persist],
  );

  const clearVariationItems = useCallback(
    async (batchId: string): Promise<boolean> => {
      return enqueueWrite(() => {
        const batch = batchesRef.current.find((row) => row.id === batchId);
        if (!batch || batch.items.length === 0) return Promise.resolve(true);
        const now = Date.now();
        const next = batchesRef.current.map((row) =>
          row.id === batchId ? { ...row, items: [], updatedAt: now } : row,
        );
        return persist(next);
      });
    },
    [enqueueWrite, persist],
  );

  const updateItemStatus = useCallback(
    async (
      batchId: string,
      itemId: string,
      status: VariationItemStatus,
      itemError = "",
    ): Promise<boolean> => {
      return enqueueWrite(() => {
        const now = Date.now();
        const next = batchesRef.current.map((batch) => {
          if (batch.id !== batchId) return batch;
          return {
            ...batch,
            updatedAt: now,
            items: batch.items.map((item) => {
              if (item.id !== itemId) return item;
              return {
                ...item,
                status,
                error: itemError,
                updatedAt: now,
              };
            }),
          };
        });
        return persist(next);
      });
    },
    [enqueueWrite, persist],
  );

  const generateVariations = useCallback(
    async (params: {
      batchId: string;
      name?: string;
      sourcePromptId: string | null;
      templatePrompt: string;
      templateNegative: string;
      variableListByName: Record<string, string>;
      variables: VariableOptionMap[];
      maxCount?: number;
      order?: VariationGenerateOrder;
    }): Promise<{ ok: boolean; errors: string[] }> => {
      let expanded;
      try {
        expanded = expandPromptVariations(
          params.templatePrompt,
          params.templateNegative,
          params.variables,
          { maxCount: params.maxCount, order: params.order },
        );
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        return { ok: false, errors: [message] };
      }
      if (expanded.errors.length > 0) {
        return { ok: false, errors: expanded.errors };
      }

      setGeneratingBatchId(params.batchId);
      try {
        return await enqueueWrite(async () => {
          const now = Date.now();
          const items: VariationItem[] = expanded.variations.map((row) => ({
            ...emptyVariationItem(row.prompt, row.negativePrompt),
            status: "done" as const,
            updatedAt: now,
          }));
          let found = false;
          const next = batchesRef.current.map((batch) => {
            if (batch.id !== params.batchId) return batch;
            found = true;
            return {
              ...batch,
              name:
                params.name !== undefined && params.name.trim().length > 0
                  ? params.name
                  : batch.name,
              sourcePromptId: params.sourcePromptId,
              templatePrompt: params.templatePrompt,
              templateNegative: params.templateNegative,
              variableListByName: { ...params.variableListByName },
              items,
              updatedAt: now,
            };
          });

          if (!found) {
            return {
              ok: false,
              errors: ["Batch not found — create or select a batch first."],
            };
          }

          const saved = await persist(next);
          return saved
            ? { ok: true, errors: [] }
            : { ok: false, errors: ["Failed to save batch"] };
        });
      } finally {
        setGeneratingBatchId(null);
      }
    },
    [enqueueWrite, persist],
  );

  const deleteVariationItem = useCallback(
    async (batchId: string, itemId: string): Promise<boolean> => {
      return enqueueWrite(() => {
        const now = Date.now();
        const next = batchesRef.current.map((batch) => {
          if (batch.id !== batchId) return batch;
          return {
            ...batch,
            updatedAt: now,
            items: batch.items.filter((item) => item.id !== itemId),
          };
        });
        return persist(next);
      });
    },
    [enqueueWrite, persist],
  );

  const addVariationItem = useCallback(
    async (
      batchId: string,
      prompt = "",
      negativePrompt = "",
    ): Promise<VariationItem | null> => {
      return enqueueWrite(async () => {
        const now = Date.now();
        const item: VariationItem = {
          ...emptyVariationItem(prompt, negativePrompt),
          status: "done",
          updatedAt: now,
        };
        const next = batchesRef.current.map((batch) => {
          if (batch.id !== batchId) return batch;
          return {
            ...batch,
            updatedAt: now,
            items: [...batch.items, item],
          };
        });
        const ok = await persist(next);
        return ok ? item : null;
      });
    },
    [enqueueWrite, persist],
  );

  const updateVariationItem = useCallback(
    async (
      batchId: string,
      itemId: string,
      patch: Partial<
        Pick<VariationItem, "prompt" | "negativePrompt" | "status">
      >,
    ): Promise<boolean> => {
      return enqueueWrite(() => {
        const now = Date.now();
        const next = batchesRef.current.map((batch) => {
          if (batch.id !== batchId) return batch;
          return {
            ...batch,
            updatedAt: now,
            items: batch.items.map((item) => {
              if (item.id !== itemId) return item;
              return { ...item, ...patch, updatedAt: now };
            }),
          };
        });
        return persist(next);
      });
    },
    [enqueueWrite, persist],
  );

  const clearError = useCallback(() => setError(null), []);

  const state = useMemo(
    (): VariationBatchesState => ({
      batches,
      batchesPath,
      loading,
      ready,
      error,
      saving,
      generatingBatchId,
    }),
    [batches, batchesPath, loading, ready, error, saving, generatingBatchId],
  );

  const actions = useMemo(
    (): VariationBatchesActions => ({
      refresh,
      createBatch,
      updateBatch,
      deleteBatch,
      duplicateBatch,
      clearVariationItems,
      generateVariations,
      updateItemStatus,
      addVariationItem,
      updateVariationItem,
      deleteVariationItem,
      clearError,
    }),
    [
      refresh,
      createBatch,
      updateBatch,
      deleteBatch,
      duplicateBatch,
      clearVariationItems,
      generateVariations,
      updateItemStatus,
      addVariationItem,
      updateVariationItem,
      deleteVariationItem,
      clearError,
    ],
  );

  return [state, actions];
}
