import {
  engine,
  getPromptMatrixLists,
  getPromptMatrixPaths,
  putPromptMatrixLists,
} from "@/lib/api";
import {
  emptyNamedList,
  exportNamedLists,
  makeListId,
  sanitizeNamedLists,
  type NamedList,
} from "@/lib/list-library/types";
import {
  applyAiListImport,
  buildAiExportForAll,
  buildAiExportForList,
  LIST_LIBRARY_AI_KIND,
} from "@/lib/list-library/ai-export";
import { parsePastedListContent } from "@/lib/list-library/parse-pasted-content";
import type { MatrixOption } from "@/lib/prompt-matrix/types";
import { makeId } from "@/lib/prompt-matrix/storage";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const ENGINE_NOT_CONNECTED = "Engine not connected";

export interface ListLibraryState {
  lists: NamedList[];
  listsPath: string | null;
  loading: boolean;
  ready: boolean;
  error: string | null;
  saving: boolean;
}

export interface ListLibraryActions {
  refresh: () => Promise<void>;
  createList: (name?: string) => Promise<NamedList | null>;
  updateList: (
    id: string,
    patch: Partial<Pick<NamedList, "name" | "description" | "options">>,
  ) => Promise<boolean>;
  deleteList: (id: string) => Promise<boolean>;
  duplicateList: (id: string) => Promise<NamedList | null>;
  importFromJson: (text: string, mode?: "merge" | "replace") => Promise<number>;
  exportAllJson: () => string;
  exportOneJson: (id: string) => string | null;
  /** AI interchange JSON with embedded import instructions. */
  exportAllForAi: () => string;
  exportOneForAi: (id: string) => string | null;
  addOptionsFromText: (id: string, text: string) => Promise<boolean>;
  applyPastedContent: (
    text: string,
    opts?: {
      targetListId?: string;
      newListName?: string;
      replaceOptions?: boolean;
      importMode?: "merge" | "replace";
    },
  ) => Promise<{ ok: boolean; summary: string }>;
  clearError: () => void;
}

function optionsFromStrings(values: readonly string[]): MatrixOption[] {
  return values.map((value) => ({
    id: makeId(),
    value,
    enabled: true,
  }));
}

function shapeToNamedList(
  shape: { name: string; description?: string; options: string[] },
  existing?: NamedList,
): NamedList {
  const now = Date.now();
  return {
    id: existing?.id ?? makeListId(),
    name: shape.name.trim() || existing?.name || "Pasted list",
    description: shape.description?.trim() ?? existing?.description ?? "",
    options: optionsFromStrings(shape.options),
    createdAt: existing?.createdAt ?? now,
    updatedAt: now,
  };
}

function cloneOptions(options: readonly MatrixOption[]): MatrixOption[] {
  return options.map((o) => ({ ...o, id: makeId() }));
}

export function useListLibrary(): [ListLibraryState, ListLibraryActions] {
  const [lists, setLists] = useState<NamedList[]>([]);
  const [listsPath, setListsPath] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const listsRef = useRef(lists);
  listsRef.current = lists;

  const persist = useCallback(async (next: NamedList[]): Promise<boolean> => {
    const baseUrl = engine.engineUrl;
    if (!baseUrl) {
      setError(ENGINE_NOT_CONNECTED);
      return false;
    }
    setSaving(true);
    try {
      const saved = await putPromptMatrixLists(baseUrl, next);
      const sanitized = sanitizeNamedLists(saved.lists);
      setLists(sanitized);
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
      const [listsPayload, paths] = await Promise.all([
        getPromptMatrixLists(baseUrl),
        getPromptMatrixPaths(baseUrl),
      ]);
      setLists(sanitizeNamedLists(listsPayload.lists));
      setListsPath(paths.lists ?? null);
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

  const createList = useCallback(
    async (name?: string): Promise<NamedList | null> => {
      const list = emptyNamedList(name?.trim() || "New list");
      const next = [list, ...listsRef.current];
      const ok = await persist(next);
      return ok ? list : null;
    },
    [persist],
  );

  const updateList = useCallback(
    async (
      id: string,
      patch: Partial<Pick<NamedList, "name" | "description" | "options">>,
    ): Promise<boolean> => {
      const now = Date.now();
      const next = listsRef.current.map((row) => {
        if (row.id !== id) return row;
        return {
          ...row,
          ...patch,
          name:
            patch.name !== undefined
              ? patch.name.trim() || "Untitled list"
              : row.name,
          description:
            patch.description !== undefined
              ? patch.description.trim()
              : row.description,
          updatedAt: now,
        };
      });
      return persist(next);
    },
    [persist],
  );

  const deleteList = useCallback(
    async (id: string): Promise<boolean> => {
      const next = listsRef.current.filter((row) => row.id !== id);
      return persist(next);
    },
    [persist],
  );

  const duplicateList = useCallback(
    async (id: string): Promise<NamedList | null> => {
      const source = listsRef.current.find((row) => row.id === id);
      if (!source) return null;
      const now = Date.now();
      const copy: NamedList = {
        id: makeListId(),
        name: `${source.name} (copy)`,
        description: source.description,
        options: cloneOptions(source.options),
        createdAt: now,
        updatedAt: now,
      };
      const ok = await persist([copy, ...listsRef.current]);
      return ok ? copy : null;
    },
    [persist],
  );

  const importFromJson = useCallback(
    async (
      text: string,
      mode: "merge" | "replace" = "merge",
    ): Promise<number> => {
      const result = applyAiListImport(listsRef.current, text);
      let next = result.lists;

      if (result.replaceAll || mode === "replace") {
        next = result.lists;
      } else if (
        mode === "merge" &&
        !text.includes('"operation"') &&
        !text.includes(LIST_LIBRARY_AI_KIND)
      ) {
        // Legacy bare bundle — keep existing lists not present in import.
        next = [
          ...result.lists,
          ...listsRef.current.filter(
            (existing) =>
              !result.lists.some(
                (row) =>
                  row.id === existing.id ||
                  row.name.trim().toLowerCase() ===
                    existing.name.trim().toLowerCase(),
              ),
          ),
        ];
      }

      const ok = await persist(next);
      if (!ok) return 0;
      return result.lists.length;
    },
    [persist],
  );

  const exportAllJson = useCallback((): string => {
    return exportNamedLists(listsRef.current);
  }, []);

  const exportOneJson = useCallback((id: string): string | null => {
    const row = listsRef.current.find((item) => item.id === id);
    if (!row) return null;
    return JSON.stringify(row, null, 2);
  }, []);

  const exportAllForAi = useCallback((): string => {
    return buildAiExportForAll(listsRef.current);
  }, []);

  const exportOneForAi = useCallback((id: string): string | null => {
    const row = listsRef.current.find((item) => item.id === id);
    if (!row) return null;
    return buildAiExportForList(row);
  }, []);

  const addOptionsFromText = useCallback(
    async (id: string, text: string): Promise<boolean> => {
      const row = listsRef.current.find((item) => item.id === id);
      if (!row) return false;
      const parsed = parsePastedListContent(text);
      if (parsed.kind !== "options" || parsed.options.length === 0) {
        return false;
      }
      const added = optionsFromStrings(parsed.options);
      return updateList(id, { options: [...row.options, ...added] });
    },
    [updateList],
  );

  const applyPastedContent = useCallback(
    async (
      text: string,
      opts?: {
        targetListId?: string;
        newListName?: string;
        replaceOptions?: boolean;
        importMode?: "merge" | "replace";
      },
    ): Promise<{ ok: boolean; summary: string }> => {
      const parsed = parsePastedListContent(text);
      const importMode = opts?.importMode ?? "merge";

      if (parsed.kind === "multi-list") {
        if (
          parsed.format === "ai-envelope" ||
          parsed.format === "json-lists-bundle"
        ) {
          try {
            const count = await importFromJson(text, importMode);
            return {
              ok: count > 0,
              summary:
                count > 0
                  ? `Imported ${count} list${count === 1 ? "" : "s"}`
                  : "Nothing to import",
            };
          } catch {
            // Fall through to manual merge below.
          }
        }
        const incoming = parsed.lists.map((shape) => shapeToNamedList(shape));
        const next =
          importMode === "replace"
            ? incoming
            : [
                ...incoming,
                ...listsRef.current.filter(
                  (existing) =>
                    !incoming.some(
                      (row) =>
                        row.name.trim().toLowerCase() ===
                        existing.name.trim().toLowerCase(),
                    ),
                ),
              ];
        const ok = await persist(next);
        return {
          ok,
          summary: ok
            ? `Added ${incoming.length} list${incoming.length === 1 ? "" : "s"}`
            : "Could not save pasted lists",
        };
      }

      if (parsed.kind === "single-list") {
        const shape = {
          ...parsed.list,
          name: opts?.newListName?.trim() || parsed.list.name,
        };
        if (opts?.targetListId) {
          const existing = listsRef.current.find(
            (row) => row.id === opts.targetListId,
          );
          if (!existing) {
            return { ok: false, summary: "Target list not found" };
          }
          const next = shapeToNamedList(shape, existing);
          const ok = await updateList(existing.id, {
            name: next.name,
            description: next.description,
            options: next.options,
          });
          return {
            ok,
            summary: ok
              ? `Updated "${next.name}" with ${next.options.length} options`
              : "Could not update list",
          };
        }
        const created = shapeToNamedList(shape);
        const ok = await persist([created, ...listsRef.current]);
        return {
          ok,
          summary: ok
            ? `Created "${created.name}" with ${created.options.length} options`
            : "Could not create list",
        };
      }

      if (parsed.options.length === 0) {
        return { ok: false, summary: "No options found in pasted content" };
      }

      if (opts?.targetListId) {
        const existing = listsRef.current.find(
          (row) => row.id === opts.targetListId,
        );
        if (!existing) {
          return { ok: false, summary: "Target list not found" };
        }
        const added = optionsFromStrings(parsed.options);
        const options = opts.replaceOptions
          ? added
          : [...existing.options, ...added];
        const ok = await updateList(existing.id, { options });
        return {
          ok,
          summary: ok
            ? `${opts.replaceOptions ? "Replaced with" : "Added"} ${parsed.options.length} option${parsed.options.length === 1 ? "" : "s"}`
            : "Could not update list",
        };
      }

      const name = opts?.newListName?.trim() || "Pasted list";
      const created = shapeToNamedList({ name, options: parsed.options });
      const ok = await persist([created, ...listsRef.current]);
      return {
        ok,
        summary: ok
          ? `Created "${created.name}" with ${created.options.length} options`
          : "Could not create list",
      };
    },
    [importFromJson, persist, updateList],
  );

  const clearError = useCallback(() => setError(null), []);

  const state = useMemo(
    (): ListLibraryState => ({
      lists,
      listsPath,
      loading,
      ready,
      error,
      saving,
    }),
    [lists, listsPath, loading, ready, error, saving],
  );

  const actions = useMemo(
    (): ListLibraryActions => ({
      refresh,
      createList,
      updateList,
      deleteList,
      duplicateList,
      importFromJson,
      exportAllJson,
      exportOneJson,
      exportAllForAi,
      exportOneForAi,
      addOptionsFromText,
      applyPastedContent,
      clearError,
    }),
    [
      refresh,
      createList,
      updateList,
      deleteList,
      duplicateList,
      importFromJson,
      exportAllJson,
      exportOneJson,
      exportAllForAi,
      exportOneForAi,
      addOptionsFromText,
      applyPastedContent,
      clearError,
    ],
  );

  return [state, actions];
}
