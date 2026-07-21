import {
  engine,
  getPromptMatrixPaths,
  getPromptMatrixVariationBatches,
  putPromptMatrixVariationBatches,
} from "@/lib/api";
import {
  expandPromptVariations,
  type VariableOptionMap,
} from "@/lib/variation-batches/expand";
import {
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
  generateVariations: (params: {
    batchId: string;
    name?: string;
    sourcePromptId: string | null;
    templatePrompt: string;
    templateNegative: string;
    variableListByName: Record<string, string>;
    variables: VariableOptionMap[];
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
  batchesRef.current = batches;

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
      setBatches(sanitizeVariationBatches(payload.batches));
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
      const ok = await persist([row, ...batchesRef.current]);
      return ok ? row : null;
    },
    [persist],
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
      const now = Date.now();
      const next = batchesRef.current.map((row) => {
        if (row.id !== id) return row;
        return {
          ...row,
          ...patch,
          name:
            patch.name !== undefined
              ? patch.name.trim() || "Untitled batch"
              : row.name,
          updatedAt: now,
        };
      });
      return persist(next);
    },
    [persist],
  );

  const deleteBatch = useCallback(
    async (id: string): Promise<boolean> => {
      const next = batchesRef.current.filter((row) => row.id !== id);
      return persist(next);
    },
    [persist],
  );

  const updateItemStatus = useCallback(
    async (
      batchId: string,
      itemId: string,
      status: VariationItemStatus,
      itemError = "",
    ): Promise<boolean> => {
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
    },
    [persist],
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
    }): Promise<{ ok: boolean; errors: string[] }> => {
      let expanded;
      try {
        expanded = expandPromptVariations(
          params.templatePrompt,
          params.templateNegative,
          params.variables,
        );
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        return { ok: false, errors: [message] };
      }
      if (expanded.errors.length > 0) {
        return { ok: false, errors: expanded.errors };
      }

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
          name: params.name?.trim() || batch.name,
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

      const previous = batchesRef.current;
      setGeneratingBatchId(params.batchId);
      const saved = await persist(next);
      setGeneratingBatchId(null);
      if (!saved) {
        setBatches(previous);
        return { ok: false, errors: ["Failed to save batch"] };
      }

      return { ok: true, errors: [] };
    },
    [persist],
  );

  const deleteVariationItem = useCallback(
    async (batchId: string, itemId: string): Promise<boolean> => {
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
    },
    [persist],
  );

  const addVariationItem = useCallback(
    async (
      batchId: string,
      prompt = "",
      negativePrompt = "",
    ): Promise<VariationItem | null> => {
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
    },
    [persist],
  );

  const updateVariationItem = useCallback(
    async (
      batchId: string,
      itemId: string,
      patch: Partial<
        Pick<VariationItem, "prompt" | "negativePrompt" | "status">
      >,
    ): Promise<boolean> => {
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
    },
    [persist],
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
