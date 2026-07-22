/**
 * VariationBatchesCore — canonical variation-batch UI (manage or pick/queue).
 *
 * Form edits live in a local draft (like SavedPromptsCore). The draft is loaded
 * only when the selected batch changes — never when the store refreshes after
 * generate/save, which previously wiped in-progress edits and list mappings.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronRight,
  CopyPlus,
  FolderOpen,
  Loader2,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NumberInput } from "@/components/ui/number-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useListLibraryApp } from "@/contexts/ListLibraryContext";
import { useVariationBatchesApp } from "@/contexts/VariationBatchesContext";
import { useDebouncedSave } from "@/hooks/use-debounced-save";
import { readyVariationItems } from "@/lib/media-gen/enqueue-variation-batch";
import type { NamedList } from "@/lib/list-library/types";
import type {
  VariationQueueOptions,
  VariationQueueOrder,
} from "@/lib/media-gen/enqueue-variation-batch";
import type { SavedPrompt } from "@/lib/saved-prompts/types";
import type { VariationBatch } from "@/lib/variation-batches/types";
import {
  countPromptVariations,
  extractTemplateVariableNames,
  type VariationGenerateOrder,
} from "@/lib/variation-batches/expand";
import {
  resolveListForVariableName,
  variableNameForList,
} from "@/lib/list-variables";
import { variableKey } from "@/lib/prompt-matrix";
import { CollapsibleOptionalField } from "../../prompts/CollapsibleOptionalField";
import { LabelWithInfo } from "../../prompts/LabelWithInfo";
import {
  PromptVariablePreview,
  VariableInsertButton,
  VariablePromptField,
  VariablePromptInput,
} from "../../prompts/VariablePromptTools";
import { ErrorNote } from "../../shared";
import { FeedbackIconButton } from "../../surfaces/FeedbackIconButton";
import { NO_LIST_ID, NO_SAVED_PROMPT_ID } from "../../pickers/constants";
import { SavedPromptPicker } from "../../pickers/SavedPromptPicker";
import { VariableListMappingTable } from "../../pickers/VariableListMappingTable";
import {
  readStoredBoolean,
  VariationItemsManager,
  writeStoredBoolean,
} from "./VariationItemsManager";

const SHOW_NEGATIVE_KEY = "matrx-variations-show-negative";
const GENERATOR_OPEN_KEY = "matrx-variations-generator-open";
const GENERATE_MAX_COUNT_KEY = "matrx-variations-generate-max-count";
const GENERATE_ORDER_KEY = "matrx-variations-generate-order";
const DEFAULT_GENERATE_MAX_COUNT = 100;

const GENERATE_ORDERS: ReadonlyArray<{
  value: VariationGenerateOrder;
  label: string;
}> = [
  { value: "random", label: "Random" },
  { value: "sequence", label: "Sequence" },
  { value: "reverse", label: "Reverse" },
];

export type VariationBatchesIntent = "manage" | "pick";

export interface VariationBatchesCoreProps {
  intent?: VariationBatchesIntent;
  /** pick mode: queue the selected batch through the canonical enqueue path */
  onQueueBatch?: (
    batch: VariationBatch,
    options: VariationQueueOptions,
  ) => void | Promise<void>;
  showStoragePath?: boolean;
  className?: string;
}

interface BatchDraft {
  batchId: string;
  name: string;
  sourcePromptId: string;
  templatePrompt: string;
  templateNegative: string;
  listByVariable: Record<string, string>;
}

function previewTemplate(text: string, max = 56): string {
  const line = text.replace(/\s+/g, " ").trim();
  if (!line) return "No template";
  if (line.length <= max) return line;
  return `${line.slice(0, max)}…`;
}

function listOptionsFromLibrary(
  lists: ReturnType<typeof useListLibraryApp>[0]["lists"],
  listId: string,
): { name: string; options: string[] } | null {
  if (listId === NO_LIST_ID) return null;
  const row = lists.find((item) => item.id === listId);
  if (!row) return null;
  return {
    name: row.name,
    options: row.options
      .filter((o) => o.enabled && o.value.trim().length > 0)
      .map((o) => o.value.trim()),
  };
}

function syncListByVariableForTokens(
  tokenNames: readonly string[],
  prev: Record<string, string>,
  lists: readonly NamedList[] = [],
): Record<string, string> {
  const next: Record<string, string> = {};
  for (const name of tokenNames) {
    const priorMapping = Object.entries(prev).find(
      ([priorName]) => variableKey(priorName) === variableKey(name),
    );
    if (priorMapping !== undefined) {
      next[name] = priorMapping[1] ?? NO_LIST_ID;
      continue;
    }
    const matched = resolveListForVariableName(lists, name);
    next[name] = matched?.id ?? NO_LIST_ID;
  }
  return next;
}

function batchToDraft(batch: VariationBatch): BatchDraft {
  const tokenNames = extractTemplateVariableNames(
    batch.templatePrompt,
    batch.templateNegative,
  );
  return {
    batchId: batch.id,
    name: batch.name,
    sourcePromptId: batch.sourcePromptId ?? NO_SAVED_PROMPT_ID,
    templatePrompt: batch.templatePrompt,
    templateNegative: batch.templateNegative,
    listByVariable: syncListByVariableForTokens(
      tokenNames,
      batch.variableListByName,
    ),
  };
}

function listMappingsEqual(
  a: Record<string, string>,
  b: Record<string, string>,
): boolean {
  const aKeys = Object.keys(a);
  const bKeys = Object.keys(b);
  if (aKeys.length !== bKeys.length) return false;
  return aKeys.every((key) => a[key] === b[key]);
}

function readStoredNumber(key: string, fallback: number): number {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback;
    const parsed = Number.parseInt(raw, 10);
    return Number.isFinite(parsed) && parsed >= 1 ? parsed : fallback;
  } catch {
    return fallback;
  }
}

function writeStoredNumber(key: string, value: number): void {
  try {
    localStorage.setItem(key, String(Math.max(1, Math.trunc(value))));
  } catch {
    // ignore quota / private mode
  }
}

function readStoredOrder(): VariationGenerateOrder {
  try {
    const raw = localStorage.getItem(GENERATE_ORDER_KEY);
    if (raw === "random" || raw === "sequence" || raw === "reverse") {
      return raw;
    }
  } catch {
    // ignore
  }
  return "random";
}

function writeStoredOrder(value: VariationGenerateOrder): void {
  try {
    localStorage.setItem(GENERATE_ORDER_KEY, value);
  } catch {
    // ignore quota / private mode
  }
}

export function VariationBatchesCore({
  intent = "manage",
  onQueueBatch,
  showStoragePath = true,
  className,
}: VariationBatchesCoreProps) {
  const [batchState, batchActions] = useVariationBatchesApp();
  const [listState] = useListLibraryApp();

  const [pickManageOpen, setPickManageOpen] = useState(false);
  const showManage = intent === "manage" || pickManageOpen;

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<BatchDraft | null>(null);
  const [generateErrors, setGenerateErrors] = useState<string[]>([]);
  const [feedbackKey, setFeedbackKey] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [queueing, setQueueing] = useState(false);
  const [queueCount, setQueueCount] = useState(1);
  const [queueOrder, setQueueOrder] = useState<VariationQueueOrder>("start");
  const [generatorOpen, setGeneratorOpen] = useState(true);
  const [generateMaxCount, setGenerateMaxCount] = useState(() =>
    readStoredNumber(GENERATE_MAX_COUNT_KEY, DEFAULT_GENERATE_MAX_COUNT),
  );
  const [generateOrder, setGenerateOrder] =
    useState<VariationGenerateOrder>(readStoredOrder);
  const loadedBatchIdRef = useRef<string | null>(null);
  const updateBatch = batchActions.updateBatch;
  const saveBatch = useCallback(
    async (next: BatchDraft) => {
      await updateBatch(next.batchId, {
        name: next.name,
        sourcePromptId:
          next.sourcePromptId === NO_SAVED_PROMPT_ID
            ? null
            : next.sourcePromptId,
        templatePrompt: next.templatePrompt,
        templateNegative: next.templateNegative,
        variableListByName: next.listByVariable,
      });
    },
    [updateBatch],
  );
  const {
    schedule: scheduleSave,
    flush: flushSave,
    cancel: cancelSave,
  } = useDebouncedSave(saveBatch);

  const selectedBatch = useMemo(
    () => batchState.batches.find((row) => row.id === selectedId) ?? null,
    [batchState.batches, selectedId],
  );

  const readyCount = selectedBatch
    ? readyVariationItems(selectedBatch).length
    : 0;

  const queueCountClamped = Math.min(
    readyCount || 1,
    Math.max(1, Number.isFinite(queueCount) ? Math.floor(queueCount) : 1),
  );

  useEffect(() => {
    setQueueCount(readyCount > 0 ? readyCount : 1);
  }, [selectedId, readyCount]);

  const tokenNames = useMemo(
    () =>
      draft
        ? extractTemplateVariableNames(
            draft.templatePrompt,
            draft.templateNegative,
          )
        : [],
    [draft?.templatePrompt, draft?.templateNegative],
  );

  const mappedVariables = useMemo(() => {
    if (!draft) return [];
    return tokenNames.map((name) => {
      const listId = draft.listByVariable[name] ?? NO_LIST_ID;
      const mapped = listOptionsFromLibrary(listState.lists, listId);
      return { name, options: mapped?.options ?? [] };
    });
  }, [draft, tokenNames, listState.lists]);

  const totalOptions = useMemo(() => {
    if (!draft) return null;
    return countPromptVariations(
      draft.templatePrompt,
      draft.templateNegative,
      mappedVariables,
    );
  }, [draft, mappedVariables]);

  const generateMaxCountClamped = Math.max(
    1,
    Number.isFinite(generateMaxCount)
      ? Math.floor(generateMaxCount)
      : DEFAULT_GENERATE_MAX_COUNT,
  );

  useEffect(() => {
    if (selectedId && batchState.batches.some((row) => row.id === selectedId)) {
      return;
    }
    const first = batchState.batches[0] ?? null;
    setSelectedId(first?.id ?? null);
  }, [batchState.batches, selectedId]);

  // Load draft when switching batches, or when the batch first appears in the store.
  // Never reload while editing the same batch — store updates after generate must not wipe the draft.
  useEffect(() => {
    if (!selectedId) {
      loadedBatchIdRef.current = null;
      setDraft(null);
      return;
    }
    if (loadedBatchIdRef.current === selectedId) return;
    void flushSave();
    const batch = batchState.batches.find((row) => row.id === selectedId);
    if (!batch) return;
    loadedBatchIdRef.current = selectedId;
    setDraft(batchToDraft(batch));
  }, [selectedId, batchState.batches, flushSave]);

  useEffect(() => {
    if (!draft) return;
    const nextLists = syncListByVariableForTokens(
      tokenNames,
      draft.listByVariable,
      listState.lists,
    );
    if (listMappingsEqual(nextLists, draft.listByVariable)) return;
    const next = { ...draft, listByVariable: nextLists };
    setDraft(next);
    scheduleSave(next);
  }, [draft, tokenNames, listState.lists, scheduleSave]);

  useEffect(() => {
    if (!selectedBatch) return;
    setGeneratorOpen(
      readStoredBoolean(GENERATOR_OPEN_KEY, selectedBatch.items.length === 0),
    );
  }, [selectedBatch?.id]);

  const setGeneratorOpenPersisted = useCallback((open: boolean) => {
    setGeneratorOpen(open);
    writeStoredBoolean(GENERATOR_OPEN_KEY, open);
  }, []);

  const flash = useCallback((key: string) => {
    setFeedbackKey(key);
    window.setTimeout(
      () => setFeedbackKey((v) => (v === key ? null : v)),
      1200,
    );
  }, []);

  const patchDraft = useCallback(
    (patch: Partial<Omit<BatchDraft, "batchId">>) => {
      setDraft((prev) => {
        if (!prev) return prev;
        const nextBase = { ...prev, ...patch };
        const nextTokenNames = extractTemplateVariableNames(
          nextBase.templatePrompt,
          nextBase.templateNegative,
        );
        const next = {
          ...nextBase,
          listByVariable: syncListByVariableForTokens(
            nextTokenNames,
            nextBase.listByVariable,
            listState.lists,
          ),
        };
        scheduleSave(next);
        return next;
      });
    },
    [listState.lists, scheduleSave],
  );

  const handleSavedPromptChange = useCallback(
    (prompt: SavedPrompt | null) => {
      if (!draft) return;
      if (!prompt) {
        patchDraft({ sourcePromptId: NO_SAVED_PROMPT_ID });
        return;
      }
      const namePatch =
        !draft.name.trim() || draft.name === "New batch"
          ? { name: `${prompt.name} variations` }
          : {};
      patchDraft({
        sourcePromptId: prompt.id,
        templatePrompt: prompt.prompt,
        templateNegative: prompt.negativePrompt,
        ...namePatch,
      });
    },
    [draft, patchDraft],
  );

  const handleListChange = useCallback(
    (name: string, listId: string) => {
      setDraft((prev) => {
        if (!prev) return prev;
        const next = {
          ...prev,
          listByVariable: { ...prev.listByVariable, [name]: listId },
        };
        scheduleSave(next);
        return next;
      });
    },
    [scheduleSave],
  );

  const patchTemplateWithList = useCallback(
    (
      field: "templatePrompt" | "templateNegative",
      list: NamedList,
      value: string,
    ) => {
      const variableName = variableNameForList(list.name);
      if (variableName === null) return;
      setDraft((prev) => {
        if (!prev) return prev;
        const nextBase = {
          ...prev,
          [field]: value,
        };
        const nextTokenNames = extractTemplateVariableNames(
          nextBase.templatePrompt,
          nextBase.templateNegative,
        );
        const canonicalName =
          nextTokenNames.find(
            (name) => variableKey(name) === variableKey(variableName),
          ) ?? variableName;
        const listByVariable = Object.fromEntries(
          Object.entries(prev.listByVariable).filter(
            ([name]) => variableKey(name) !== variableKey(canonicalName),
          ),
        );
        listByVariable[canonicalName] = list.id;
        const next = {
          ...nextBase,
          listByVariable: syncListByVariableForTokens(
            nextTokenNames,
            listByVariable,
            listState.lists,
          ),
        };
        scheduleSave(next);
        return next;
      });
    },
    [listState.lists, scheduleSave],
  );

  const handleCreateBatch = async () => {
    await flushSave();
    const row = await batchActions.createBatch();
    if (row) {
      setSelectedId(row.id);
      flash("create");
    }
  };

  const handleDuplicateBatch = useCallback(
    async (id: string) => {
      await flushSave();
      const row = await batchActions.duplicateBatch(id);
      if (row) {
        setSelectedId(row.id);
        flash(`batch:${row.id}:dup`);
      }
    },
    [batchActions, flushSave, flash],
  );

  const handleSelectBatch = useCallback(
    (id: string) => {
      void flushSave();
      setSelectedId(id);
    },
    [flushSave],
  );

  const handleGenerate = async () => {
    if (!draft) return;
    // generateVariations persists the complete current draft itself.
    cancelSave();
    setGenerateErrors([]);
    try {
      const variables = mappedVariables;
      const result = await batchActions.generateVariations({
        batchId: draft.batchId,
        name: draft.name,
        sourcePromptId:
          draft.sourcePromptId === NO_SAVED_PROMPT_ID
            ? null
            : draft.sourcePromptId,
        templatePrompt: draft.templatePrompt,
        templateNegative: draft.templateNegative,
        variableListByName: draft.listByVariable,
        variables,
        maxCount: generateMaxCountClamped,
        order: generateOrder,
      });
      if (!result.ok) {
        setGenerateErrors(result.errors);
        return;
      }
      setGeneratorOpenPersisted(false);
      flash("generate");
    } catch (err) {
      setGenerateErrors([
        err instanceof Error ? err.message : "Generate failed unexpectedly",
      ]);
    }
  };

  const handleQueue = async () => {
    if (!selectedBatch || !onQueueBatch) return;
    setQueueError(null);
    setQueueing(true);
    try {
      await onQueueBatch(selectedBatch, {
        count: queueCountClamped,
        order: queueOrder,
      });
      flash("queue");
    } catch (err) {
      setQueueError(err instanceof Error ? err.message : String(err));
    } finally {
      setQueueing(false);
    }
  };

  const generating =
    draft !== null && batchState.generatingBatchId === draft.batchId;

  const totalCount = selectedBatch?.items.length ?? 0;

  if (intent === "pick" && !showManage) {
    return (
      <div className={`flex h-full min-h-0 flex-col gap-3 ${className ?? ""}`}>
        {batchState.error && (
          <ErrorNote
            message={batchState.error}
            onDismiss={batchActions.clearError}
          />
        )}
        {queueError && <ErrorNote message={queueError} />}

        <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border bg-card">
          {batchState.batches.length === 0 ? (
            <p className="px-3 py-8 text-center text-xs text-muted-foreground">
              No variation batches yet.
            </p>
          ) : (
            batchState.batches.map((row) => {
              const ready = readyVariationItems(row).length;
              const active = row.id === selectedId;
              return (
                <button
                  key={row.id}
                  type="button"
                  className={`flex w-full items-start gap-2 border-b px-3 py-2.5 text-left last:border-b-0 ${
                    active ? "bg-muted/60" : "hover:bg-muted/30"
                  }`}
                  onClick={() => handleSelectBatch(row.id)}
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs font-medium">{row.name}</p>
                    <p className="mt-0.5 text-[10px] text-muted-foreground">
                      {ready > 0
                        ? `${ready} ready to queue`
                        : "No ready variations"}
                    </p>
                  </div>
                  {active && (
                    <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-green-600" />
                  )}
                </button>
              );
            })
          )}
        </div>

        {readyCount > 0 && (
          <div className="shrink-0 space-y-3 rounded-lg border bg-card p-3">
            <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              Queue options
            </p>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <label
                  htmlFor="queue-count"
                  className="text-xs text-muted-foreground"
                >
                  Count
                </label>
                <div className="flex items-center gap-2">
                  <NumberInput
                    id="queue-count"
                    min={1}
                    max={readyCount || 1}
                    integer
                    value={queueCount}
                    onChange={setQueueCount}
                    emptyValue={1}
                    className="h-8"
                  />
                  <span className="shrink-0 text-xs text-muted-foreground">
                    of {readyCount}
                  </span>
                </div>
              </div>
              <div className="space-y-1.5">
                <label
                  htmlFor="queue-order"
                  className="text-xs text-muted-foreground"
                >
                  Order
                </label>
                <Select
                  value={queueOrder}
                  onValueChange={(value) =>
                    setQueueOrder(value as VariationQueueOrder)
                  }
                >
                  <SelectTrigger id="queue-order" className="h-8 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="start">From start</SelectItem>
                    <SelectItem value="end">From end</SelectItem>
                    <SelectItem value="random">Random selections</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>
        )}

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Button
            className="gap-1.5"
            disabled={!selectedBatch || readyCount === 0 || queueing}
            onClick={() => void handleQueue()}
          >
            {queueing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Sparkles className="h-3.5 w-3.5" />
            )}
            Queue {readyCount > 0 ? queueCountClamped : ""} image
            {queueCountClamped === 1 ? "" : "s"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPickManageOpen(true)}
          >
            Create or edit…
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className={`flex h-full min-h-0 flex-col gap-2 ${className ?? ""}`}>
      {intent === "pick" && pickManageOpen && (
        <Button
          variant="ghost"
          size="sm"
          className="h-7 w-fit gap-1 px-2 text-xs"
          onClick={() => setPickManageOpen(false)}
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to pick batch
        </Button>
      )}

      {(batchState.error || generateErrors.length > 0) && (
        <div className="space-y-1">
          {batchState.error && (
            <ErrorNote
              message={batchState.error}
              onDismiss={batchActions.clearError}
            />
          )}
          {generateErrors.map((msg) => (
            <ErrorNote key={msg} message={msg} />
          ))}
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(220px,280px)_1fr] gap-4">
        <div className="flex min-h-0 flex-col rounded-lg border bg-card">
          <div className="flex items-center gap-2 border-b px-2 py-2">
            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8 shrink-0"
              onClick={() =>
                void flushSave()
                  .then(() => batchActions.refresh())
                  .then(() => flash("refresh"))
              }
              disabled={batchState.loading}
              aria-label="Refresh"
            >
              {feedbackKey === "refresh" && !batchState.loading ? (
                <Check className="h-3.5 w-3.5 text-green-600" />
              ) : (
                <RefreshCw
                  className={`h-3.5 w-3.5 ${batchState.loading ? "animate-spin" : ""}`}
                />
              )}
            </Button>
            <Button
              size="sm"
              className="h-8 flex-1 gap-1.5"
              onClick={() => void handleCreateBatch()}
              disabled={batchState.saving}
            >
              <Plus className="h-3.5 w-3.5" />
              New batch
            </Button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {batchState.loading && batchState.batches.length === 0 ? (
              <div className="flex items-center justify-center py-10">
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              </div>
            ) : batchState.batches.length === 0 ? (
              <p className="px-3 py-6 text-center text-xs text-muted-foreground">
                No variation batches yet.
              </p>
            ) : (
              batchState.batches.map((row) => {
                const active = row.id === selectedId;
                const done = row.items.filter(
                  (i) => i.status === "done",
                ).length;
                const key = `batch:${row.id}`;
                return (
                  <div
                    key={row.id}
                    className={`flex items-start gap-1 border-b px-2 py-2 last:border-b-0 ${
                      active ? "bg-muted/60" : "hover:bg-muted/30"
                    }`}
                  >
                    <button
                      type="button"
                      className="min-w-0 flex-1 text-left"
                      onClick={() => handleSelectBatch(row.id)}
                    >
                      <p className="truncate text-xs font-medium">{row.name}</p>
                      <p className="mt-0.5 text-[10px] text-muted-foreground">
                        {row.items.length === 0
                          ? "No variations"
                          : `${done}/${row.items.length} done`}
                      </p>
                    </button>
                    <FeedbackIconButton
                      feedbackKey={`${key}:dup`}
                      activeKey={feedbackKey}
                      icon={CopyPlus}
                      label="Duplicate batch"
                      onClick={() => void handleDuplicateBatch(row.id)}
                    />
                    {confirmDeleteId === row.id ? (
                      <Button
                        variant="destructive"
                        size="icon"
                        className="h-7 w-7 shrink-0"
                        onClick={() => {
                          void flushSave().then(async () => {
                            await batchActions.deleteBatch(row.id);
                            setConfirmDeleteId(null);
                            flash(`${key}:del`);
                          });
                        }}
                        aria-label="Confirm delete"
                      >
                        <Check className="h-3.5 w-3.5" />
                      </Button>
                    ) : (
                      <FeedbackIconButton
                        feedbackKey={`${key}:del`}
                        activeKey={feedbackKey}
                        icon={Trash2}
                        label="Delete batch"
                        destructive
                        onClick={() => setConfirmDeleteId(row.id)}
                      />
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>

        <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
          {!draft || !selectedBatch ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-2 rounded-lg border bg-card text-center text-muted-foreground">
              <p className="text-sm">Create a batch to generate variations.</p>
              <Button size="sm" onClick={() => void handleCreateBatch()}>
                <Plus className="mr-1.5 h-3.5 w-3.5" />
                New batch
              </Button>
            </div>
          ) : (
            <>
              <div className="shrink-0 rounded-lg border bg-card">
                <button
                  type="button"
                  className="flex w-full items-center gap-2 px-3 py-2.5 text-left hover:bg-muted/30"
                  onClick={() => setGeneratorOpenPersisted(!generatorOpen)}
                >
                  {generatorOpen ? (
                    <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                  )}
                  <span className="text-xs font-medium">
                    Generate from template
                  </span>
                  {!generatorOpen && draft.templatePrompt.trim() && (
                    <span className="min-w-0 flex-1 truncate text-xs font-normal text-muted-foreground">
                      {previewTemplate(draft.templatePrompt)}
                    </span>
                  )}
                </button>

                {generatorOpen && (
                  <VariablePromptField
                    value={draft.templatePrompt}
                    onChange={(templatePrompt) =>
                      patchDraft({ templatePrompt })
                    }
                    onVariableInsert={(list, value) =>
                      patchTemplateWithList("templatePrompt", list, value)
                    }
                  >
                    <div className="space-y-3 border-t p-4">
                      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-end">
                        <div className="space-y-1.5">
                          <LabelWithInfo
                            htmlFor="batch-name"
                            label="Batch name"
                            info="Label for this variation run."
                          />
                          <Input
                            id="batch-name"
                            value={draft.name}
                            onChange={(e) =>
                              patchDraft({ name: e.target.value })
                            }
                            className="h-9"
                          />
                        </div>

                        <div className="space-y-1.5">
                          <LabelWithInfo
                            label="From saved prompt"
                            info="Pick a saved prompt to fill the template fields, or type your own."
                          />
                          <SavedPromptPicker
                            value={draft.sourcePromptId}
                            onChange={handleSavedPromptChange}
                          />
                        </div>

                        <VariableInsertButton className="w-full sm:w-auto" />
                      </div>

                      <div className="space-y-1.5">
                        <LabelWithInfo
                          htmlFor="template-prompt"
                          label="Template prompt"
                          info="Use {{variable}} tokens to sweep options from lists. Without tokens, one variation is created."
                        />
                        <VariablePromptInput
                          id="template-prompt"
                          rows={4}
                          className="min-h-[6rem] resize-y text-sm"
                        />
                      </div>

                      <CollapsibleOptionalField
                        storageKey={SHOW_NEGATIVE_KEY}
                        label="Template negative"
                        info="Optional negative prompt template; supports the same {{variable}} tokens."
                        value={draft.templateNegative}
                        onChange={(value) =>
                          patchDraft({ templateNegative: value })
                        }
                        placeholder="Optional…"
                        rows={2}
                        enableVariables
                        onVariableInsert={(list, value) =>
                          patchTemplateWithList("templateNegative", list, value)
                        }
                      />

                      <PromptVariablePreview
                        fields={[
                          { label: "Prompt", text: draft.templatePrompt },
                          {
                            label: "Negative prompt",
                            text: draft.templateNegative,
                          },
                        ]}
                        listIdByVariable={draft.listByVariable}
                      />

                      <VariableListMappingTable
                        tokenNames={tokenNames}
                        listByVariable={draft.listByVariable}
                        onListChange={handleListChange}
                      />

                      <div className="flex flex-wrap items-center gap-3">
                        <Button
                          type="button"
                          onClick={() => void handleGenerate()}
                          disabled={
                            generating ||
                            batchState.saving ||
                            !draft.templatePrompt.trim()
                          }
                          className="gap-1.5"
                        >
                          {generating ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : feedbackKey === "generate" ? (
                            <Check className="h-3.5 w-3.5" />
                          ) : (
                            <Sparkles className="h-3.5 w-3.5" />
                          )}
                          Generate variations
                        </Button>
                        <div className="flex items-center gap-2">
                          <label
                            htmlFor="generate-order"
                            className="shrink-0 text-xs text-muted-foreground"
                          >
                            Order
                          </label>
                          <Select
                            value={generateOrder}
                            onValueChange={(value) => {
                              const next = value as VariationGenerateOrder;
                              setGenerateOrder(next);
                              writeStoredOrder(next);
                            }}
                          >
                            <SelectTrigger
                              id="generate-order"
                              className="h-8 w-[6.75rem] text-xs"
                            >
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              {GENERATE_ORDERS.map((row) => (
                                <SelectItem
                                  key={row.value}
                                  value={row.value}
                                  className="text-xs"
                                >
                                  {row.label}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                        <div className="flex items-center gap-2">
                          <label
                            htmlFor="generate-max-count"
                            className="shrink-0 text-xs text-muted-foreground"
                          >
                            Max count
                          </label>
                          <NumberInput
                            id="generate-max-count"
                            min={1}
                            {...(totalOptions !== null
                              ? { max: Math.max(1, totalOptions) }
                              : {})}
                            integer
                            value={generateMaxCount}
                            onChange={(next) => {
                              setGenerateMaxCount(next);
                              writeStoredNumber(GENERATE_MAX_COUNT_KEY, next);
                            }}
                            emptyValue={DEFAULT_GENERATE_MAX_COUNT}
                            className="h-8 w-24 text-xs tabular-nums"
                          />
                        </div>
                        <span className="text-xs text-muted-foreground">
                          {totalOptions === null
                            ? "Map all variables to see total options"
                            : `${totalOptions.toLocaleString()} total options`}
                        </span>
                        {totalCount > 0 && (
                          <span className="text-xs text-muted-foreground">
                            Replaces existing {totalCount} variation
                            {totalCount === 1 ? "" : "s"}
                          </span>
                        )}
                      </div>
                    </div>
                  </VariablePromptField>
                )}
              </div>

              <VariationItemsManager
                batch={selectedBatch}
                generating={generating}
                saving={batchState.saving}
                actions={batchActions}
                onBeforeBatchMutation={flushSave}
              />
            </>
          )}
        </div>
      </div>

      {showStoragePath && batchState.batchesPath && (
        <p className="flex items-center gap-1.5 truncate text-[10px] text-muted-foreground">
          <FolderOpen className="h-3 w-3 shrink-0" />
          {batchState.batchesPath}
        </p>
      )}
    </div>
  );
}
