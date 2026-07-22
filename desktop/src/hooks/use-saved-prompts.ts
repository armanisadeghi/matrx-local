import {
  engine,
  getPromptMatrixPaths,
  getPromptMatrixPrompts,
  putPromptMatrixPrompts,
} from "@/lib/api";
import {
  emptySavedPrompt,
  makePromptId,
  sanitizeSavedPrompts,
  type SavedPrompt,
} from "@/lib/saved-prompts/types";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const ENGINE_NOT_CONNECTED = "Engine not connected";

export interface SavedPromptsState {
  prompts: SavedPrompt[];
  promptsPath: string | null;
  loading: boolean;
  ready: boolean;
  error: string | null;
  saving: boolean;
}

export interface SavedPromptsActions {
  refresh: () => Promise<void>;
  createPrompt: (name?: string) => Promise<SavedPrompt | null>;
  updatePrompt: (
    id: string,
    patch: Partial<Pick<SavedPrompt, "name" | "prompt" | "negativePrompt">>,
  ) => Promise<boolean>;
  deletePrompt: (id: string) => Promise<boolean>;
  duplicatePrompt: (id: string) => Promise<SavedPrompt | null>;
  clearError: () => void;
}

export function useSavedPrompts(): [SavedPromptsState, SavedPromptsActions] {
  const [prompts, setPrompts] = useState<SavedPrompt[]>([]);
  const [promptsPath, setPromptsPath] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const promptsRef = useRef(prompts);
  const writeChainRef = useRef<Promise<void>>(Promise.resolve());
  promptsRef.current = prompts;

  const enqueueWrite = useCallback(
    <T,>(operation: () => Promise<T>): Promise<T> => {
      const result = writeChainRef.current.then(operation, operation);
      writeChainRef.current = result.then(
        () => undefined,
        () => undefined,
      );
      return result;
    },
    [],
  );

  const persist = useCallback(async (next: SavedPrompt[]): Promise<boolean> => {
    const baseUrl = engine.engineUrl;
    if (!baseUrl) {
      setError(ENGINE_NOT_CONNECTED);
      return false;
    }
    setSaving(true);
    try {
      const saved = await putPromptMatrixPrompts(baseUrl, next);
      const sanitized = sanitizeSavedPrompts(saved.prompts);
      // Keep imperative writers current before React commits the state update.
      // A serialized autosave may start its next write in the same microtask.
      promptsRef.current = sanitized;
      setPrompts(sanitized);
      setError(null);
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      return false;
    } finally {
      setSaving(false);
    }
  }, []);

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
        getPromptMatrixPrompts(baseUrl),
        getPromptMatrixPaths(baseUrl),
      ]);
      const sanitized = sanitizeSavedPrompts(payload.prompts);
      promptsRef.current = sanitized;
      setPrompts(sanitized);
      setPromptsPath(paths.prompts ?? null);
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

  const createPrompt = useCallback(
    async (name?: string): Promise<SavedPrompt | null> => {
      const row = emptySavedPrompt(name?.trim() || "New prompt");
      const ok = await enqueueWrite(() =>
        persist([row, ...promptsRef.current]),
      );
      return ok ? row : null;
    },
    [enqueueWrite, persist],
  );

  const updatePrompt = useCallback(
    async (
      id: string,
      patch: Partial<Pick<SavedPrompt, "name" | "prompt" | "negativePrompt">>,
    ): Promise<boolean> => {
      return enqueueWrite(() => {
        const now = Date.now();
        const next = promptsRef.current.map((row) => {
          if (row.id !== id) return row;
          return {
            ...row,
            ...patch,
            name: patch.name !== undefined ? patch.name : row.name,
            negativePrompt:
              patch.negativePrompt !== undefined
                ? patch.negativePrompt
                : row.negativePrompt,
            updatedAt: now,
          };
        });
        return persist(next);
      });
    },
    [enqueueWrite, persist],
  );

  const deletePrompt = useCallback(
    async (id: string): Promise<boolean> => {
      return enqueueWrite(() =>
        persist(promptsRef.current.filter((row) => row.id !== id)),
      );
    },
    [enqueueWrite, persist],
  );

  const duplicatePrompt = useCallback(
    async (id: string): Promise<SavedPrompt | null> => {
      return enqueueWrite(async () => {
        const source = promptsRef.current.find((row) => row.id === id);
        if (!source) return null;
        const now = Date.now();
        const copy: SavedPrompt = {
          id: makePromptId(),
          name: `${source.name} (copy)`,
          prompt: source.prompt,
          negativePrompt: source.negativePrompt,
          createdAt: now,
          updatedAt: now,
        };
        const ok = await persist([copy, ...promptsRef.current]);
        return ok ? copy : null;
      });
    },
    [enqueueWrite, persist],
  );

  const clearError = useCallback(() => setError(null), []);

  const state = useMemo(
    (): SavedPromptsState => ({
      prompts,
      promptsPath,
      loading,
      ready,
      error,
      saving,
    }),
    [prompts, promptsPath, loading, ready, error, saving],
  );

  const actions = useMemo(
    (): SavedPromptsActions => ({
      refresh,
      createPrompt,
      updatePrompt,
      deletePrompt,
      duplicatePrompt,
      clearError,
    }),
    [
      refresh,
      createPrompt,
      updatePrompt,
      deletePrompt,
      duplicatePrompt,
      clearError,
    ],
  );

  return [state, actions];
}
