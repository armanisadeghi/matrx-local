/**
 * VariationItemsManager — browse and CRUD individual variations in a batch.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Eye, EyeOff, Loader2, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { VariationBatchesActions } from "@/hooks/use-variation-batches";
import type {
  VariationBatch,
  VariationItem,
} from "@/lib/variation-batches/types";
import { FeedbackIconButton } from "../../surfaces/FeedbackIconButton";

const SHOW_ITEM_NEGATIVE_KEY = "matrx-variations-items-show-negative";

export interface VariationItemsManagerProps {
  batch: VariationBatch;
  generating: boolean;
  saving: boolean;
  actions: VariationBatchesActions;
}

function previewText(text: string, max = 72): string {
  const line = text.replace(/\s+/g, " ").trim();
  if (!line) return "Empty prompt";
  if (line.length <= max) return line;
  return `${line.slice(0, max)}…`;
}

function readShowNegative(): boolean {
  try {
    return localStorage.getItem(SHOW_ITEM_NEGATIVE_KEY) === "1";
  } catch {
    return false;
  }
}

function statusLabel(item: VariationItem): string {
  switch (item.status) {
    case "pending":
      return "Pending";
    case "generating":
      return "Generating";
    case "done":
      return "Done";
    case "failed":
      return "Failed";
    default:
      return item.status;
  }
}

function statusClass(item: VariationItem): string {
  switch (item.status) {
    case "done":
      return "bg-green-500/15 text-green-700 dark:text-green-400";
    case "generating":
      return "bg-amber-500/15 text-amber-700 dark:text-amber-400";
    case "failed":
      return "bg-red-500/15 text-red-700 dark:text-red-400";
    default:
      return "bg-muted text-muted-foreground";
  }
}

export function VariationItemsManager({
  batch,
  generating,
  saving,
  actions,
}: VariationItemsManagerProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Pick<
    VariationItem,
    "prompt" | "negativePrompt"
  > | null>(null);
  const [showNegative, setShowNegative] = useState(readShowNegative);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [feedbackKey, setFeedbackKey] = useState<string | null>(null);
  const saveTimer = useRef<number | null>(null);
  const loadedItemIdRef = useRef<string | null>(null);

  const selectedItem =
    batch.items.find((item) => item.id === selectedId) ?? null;

  useEffect(() => {
    if (selectedId && batch.items.some((item) => item.id === selectedId)) {
      return;
    }
    setSelectedId(batch.items[0]?.id ?? null);
  }, [batch.items, selectedId]);

  useEffect(() => {
    if (!selectedId) {
      loadedItemIdRef.current = null;
      setDraft(null);
      return;
    }
    if (loadedItemIdRef.current === selectedId) return;
    const item = batch.items.find((row) => row.id === selectedId);
    if (!item) return;
    loadedItemIdRef.current = selectedId;
    setDraft({
      prompt: item.prompt,
      negativePrompt: item.negativePrompt,
    });
  }, [selectedId, batch.items]);

  useEffect(() => {
    try {
      localStorage.setItem(SHOW_ITEM_NEGATIVE_KEY, showNegative ? "1" : "0");
    } catch {
      // ignore
    }
  }, [showNegative]);

  const flash = useCallback((key: string) => {
    setFeedbackKey(key);
    window.setTimeout(
      () => setFeedbackKey((v) => (v === key ? null : v)),
      1200,
    );
  }, []);

  const scheduleSave = useCallback(
    (
      itemId: string,
      next: Pick<VariationItem, "prompt" | "negativePrompt">,
    ) => {
      if (saveTimer.current !== null) {
        window.clearTimeout(saveTimer.current);
      }
      saveTimer.current = window.setTimeout(() => {
        void actions.updateVariationItem(batch.id, itemId, {
          prompt: next.prompt,
          negativePrompt: next.negativePrompt,
        });
      }, 400);
    },
    [actions, batch.id],
  );

  const patchDraft = useCallback(
    (patch: Partial<Pick<VariationItem, "prompt" | "negativePrompt">>) => {
      if (!selectedId) return;
      setDraft((prev) => {
        if (!prev) return prev;
        const next = { ...prev, ...patch };
        scheduleSave(selectedId, next);
        return next;
      });
    },
    [scheduleSave, selectedId],
  );

  const handleAdd = async () => {
    const item = await actions.addVariationItem(batch.id);
    if (item) {
      loadedItemIdRef.current = null;
      setSelectedId(item.id);
      flash("add");
    }
  };

  const handleDelete = async (itemId: string) => {
    const ok = await actions.deleteVariationItem(batch.id, itemId);
    if (ok) {
      setConfirmDeleteId(null);
      flash(`del:${itemId}`);
    }
  };

  if (batch.items.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 flex-col rounded-lg border bg-card">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <p className="text-xs font-medium">Variations</p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 gap-1 text-xs"
            disabled={generating || saving}
            onClick={() => void handleAdd()}
          >
            <Plus className="h-3.5 w-3.5" />
            Add variation
          </Button>
        </div>
        <div className="flex flex-1 items-center justify-center px-4 text-center text-xs text-muted-foreground">
          {generating
            ? "Generating…"
            : "No variations yet — generate from the template above or add one manually."}
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col rounded-lg border bg-card">
      <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
        <p className="text-xs font-medium">
          Variations
          <span className="ml-1.5 font-normal text-muted-foreground">
            ({batch.items.length})
          </span>
        </p>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 gap-1 text-xs"
            disabled={generating || saving}
            onClick={() => void handleAdd()}
          >
            {feedbackKey === "add" ? (
              <Check className="h-3.5 w-3.5 text-green-600" />
            ) : (
              <Plus className="h-3.5 w-3.5" />
            )}
            Add
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 gap-1 px-2 text-xs text-muted-foreground"
            onClick={() => setShowNegative((v) => !v)}
          >
            {showNegative ? (
              <EyeOff className="h-3.5 w-3.5" />
            ) : (
              <Eye className="h-3.5 w-3.5" />
            )}
            {showNegative ? "Hide negative" : "Show negative"}
          </Button>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(200px,36%)_1fr]">
        <div className="min-h-0 overflow-y-auto border-b lg:border-b-0 lg:border-r">
          {batch.items.map((item, index) => {
            const active = item.id === selectedId;
            return (
              <button
                key={item.id}
                type="button"
                className={`flex w-full items-start gap-2 border-b px-3 py-2 text-left last:border-b-0 ${
                  active ? "bg-muted/60" : "hover:bg-muted/30"
                }`}
                onClick={() => setSelectedId(item.id)}
              >
                <span className="mt-0.5 w-5 shrink-0 text-[10px] tabular-nums text-muted-foreground">
                  {index + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs">{previewText(item.prompt)}</p>
                  {showNegative && item.negativePrompt.trim() && (
                    <p className="mt-0.5 truncate text-[10px] text-muted-foreground">
                      − {previewText(item.negativePrompt, 48)}
                    </p>
                  )}
                </div>
                <span
                  className={`mt-0.5 shrink-0 rounded px-1 py-0.5 text-[9px] font-medium ${statusClass(item)}`}
                >
                  {item.status === "generating" ? (
                    <Loader2 className="inline h-2.5 w-2.5 animate-spin" />
                  ) : (
                    statusLabel(item)
                  )}
                </span>
              </button>
            );
          })}
        </div>

        <div className="flex min-h-0 flex-col">
          {!selectedItem || !draft ? (
            <div className="flex flex-1 items-center justify-center p-4 text-xs text-muted-foreground">
              Select a variation to edit.
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between border-b px-3 py-2">
                <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  Edit variation
                </p>
                {confirmDeleteId === selectedItem.id ? (
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    className="h-7 gap-1 text-xs"
                    onClick={() => void handleDelete(selectedItem.id)}
                  >
                    <Check className="h-3.5 w-3.5" />
                    Confirm delete
                  </Button>
                ) : (
                  <FeedbackIconButton
                    feedbackKey={`del:${selectedItem.id}`}
                    activeKey={feedbackKey}
                    icon={Trash2}
                    label="Delete variation"
                    destructive
                    onClick={() => setConfirmDeleteId(selectedItem.id)}
                  />
                )}
              </div>
              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
                <div className="space-y-1.5">
                  <label
                    htmlFor="variation-prompt"
                    className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    Prompt
                  </label>
                  <Textarea
                    id="variation-prompt"
                    value={draft.prompt}
                    onChange={(e) => patchDraft({ prompt: e.target.value })}
                    rows={5}
                    className="resize-y text-sm"
                    placeholder="Prompt text…"
                  />
                </div>
                {showNegative && (
                  <div className="space-y-1.5">
                    <label
                      htmlFor="variation-negative"
                      className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground"
                    >
                      Negative prompt
                    </label>
                    <Textarea
                      id="variation-negative"
                      value={draft.negativePrompt}
                      onChange={(e) =>
                        patchDraft({ negativePrompt: e.target.value })
                      }
                      rows={3}
                      className="resize-y text-sm"
                      placeholder="Optional…"
                    />
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export function readStoredBoolean(key: string, fallback: boolean): boolean {
  try {
    const stored = localStorage.getItem(key);
    if (stored === "1") return true;
    if (stored === "0") return false;
  } catch {
    // ignore
  }
  return fallback;
}

export function writeStoredBoolean(key: string, value: boolean): void {
  try {
    localStorage.setItem(key, value ? "1" : "0");
  } catch {
    // ignore
  }
}
