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
  promptsRef.current = prompts;

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
      setPrompts(sanitizeSavedPrompts(payload.prompts));
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
      const ok = await persist([row, ...promptsRef.current]);
      return ok ? row : null;
    },
    [persist],
  );

  const updatePrompt = useCallback(
    async (
      id: string,
      patch: Partial<Pick<SavedPrompt, "name" | "prompt" | "negativePrompt">>,
    ): Promise<boolean> => {
      const now = Date.now();
      const next = promptsRef.current.map((row) => {
        if (row.id !== id) return row;
        return {
          ...row,
          ...patch,
          name:
            patch.name !== undefined
              ? patch.name.trim() || "Untitled"
              : row.name,
          negativePrompt:
            patch.negativePrompt !== undefined
              ? patch.negativePrompt
              : row.negativePrompt,
          updatedAt: now,
        };
      });
      return persist(next);
    },
    [persist],
  );

  const deletePrompt = useCallback(
    async (id: string): Promise<boolean> => {
      const next = promptsRef.current.filter((row) => row.id !== id);
      return persist(next);
    },
    [persist],
  );

  const duplicatePrompt = useCallback(
    async (id: string): Promise<SavedPrompt | null> => {
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
    },
    [persist],
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
