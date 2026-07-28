/**
 * SavedPromptsCore — canonical saved-prompt UI (manage or pick).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  CopyPlus,
  FolderOpen,
  Loader2,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useSavedPromptsApp } from "@/contexts/SavedPromptsContext";
import { useDebouncedSave } from "@/hooks/use-debounced-save";
import type { SavedPrompt } from "@/lib/saved-prompts/types";
import { CollapsibleOptionalField } from "../../prompts/CollapsibleOptionalField";
import { LabelWithInfo } from "../../prompts/LabelWithInfo";
import {
  PromptVariablePreview,
  VariablePromptTextarea,
} from "../../prompts/VariablePromptTools";
import { PROMPT_TEXTAREA_KEYS } from "../../prompts/ResizablePromptTextarea";
import { ErrorNote } from "../../shared";
import { FeedbackIconButton } from "../../surfaces/FeedbackIconButton";

const SHOW_NEGATIVE_KEY = "matrx-saved-prompts-show-negative";

export type SavedPromptsIntent = "manage" | "pick";

export interface SavedPromptsCoreProps {
  intent?: SavedPromptsIntent;
  onPick?: (prompt: SavedPrompt) => void;
  showStoragePath?: boolean;
  className?: string;
}

function previewText(prompt: string, max = 80): string {
  const line = prompt.replace(/\s+/g, " ").trim();
  if (line.length <= max) return line;
  return `${line.slice(0, max)}…`;
}

export function SavedPromptsCore({
  intent = "manage",
  onPick,
  showStoragePath = true,
  className,
}: SavedPromptsCoreProps) {
  const [state, actions] = useSavedPromptsApp();
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<SavedPrompt | null>(null);
  const [feedbackKey, setFeedbackKey] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const loadedPromptIdRef = useRef<string | null>(null);
  const isPick = intent === "pick";

  const updatePrompt = actions.updatePrompt;
  const savePrompt = useCallback(
    async (next: SavedPrompt) => {
      await updatePrompt(next.id, {
        name: next.name,
        prompt: next.prompt,
        negativePrompt: next.negativePrompt,
      });
    },
    [updatePrompt],
  );
  const {
    schedule: scheduleSave,
    flush: flushSave,
  } = useDebouncedSave(savePrompt);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return state.prompts;
    return state.prompts.filter(
      (row) =>
        row.name.toLowerCase().includes(q) ||
        row.prompt.toLowerCase().includes(q),
    );
  }, [state.prompts, query]);

  useEffect(() => {
    if (isPick) return;
    if (selectedId && state.prompts.some((row) => row.id === selectedId)) {
      return;
    }
    const first = filtered[0] ?? state.prompts[0] ?? null;
    setSelectedId(first?.id ?? null);
  }, [filtered, selectedId, state.prompts, isPick]);

  // Hydrate only when selection changes. A persistence response updates the
  // shared store, but must never replace the live draft under the cursor.
  useEffect(() => {
    if (isPick || !selectedId) {
      loadedPromptIdRef.current = null;
      if (!isPick && !selectedId) setDraft(null);
      return;
    }
    if (loadedPromptIdRef.current === selectedId) return;
    void flushSave();
    const row = state.prompts.find((item) => item.id === selectedId) ?? null;
    loadedPromptIdRef.current = row?.id ?? null;
    setDraft(row ? { ...row } : null);
  }, [selectedId, state.prompts, isPick, flushSave]);

  const flash = useCallback((key: string) => {
    setFeedbackKey(key);
    window.setTimeout(
      () => setFeedbackKey((v) => (v === key ? null : v)),
      1200,
    );
  }, []);

  const patchDraft = useCallback(
    (
      patch: Partial<Pick<SavedPrompt, "name" | "prompt" | "negativePrompt">>,
    ) => {
      setDraft((prev) => {
        if (!prev) return prev;
        const next = { ...prev, ...patch };
        scheduleSave(next);
        return next;
      });
    },
    [scheduleSave],
  );

  const handleCreate = async () => {
    await flushSave();
    const row = await actions.createPrompt();
    if (row) {
      setSelectedId(row.id);
      flash("create");
    }
  };

  const handleDuplicate = async (id: string) => {
    await flushSave();
    const row = await actions.duplicatePrompt(id);
    if (row) {
      setSelectedId(row.id);
      flash(`dup:${id}`);
    }
  };

  const handleRowClick = (row: SavedPrompt) => {
    if (isPick) {
      onPick?.(row);
      return;
    }
    void flushSave();
    setSelectedId(row.id);
  };

  return (
    <div className={`flex h-full min-h-0 flex-col gap-2 ${className ?? ""}`}>
      {state.error && (
        <ErrorNote message={state.error} onDismiss={actions.clearError} />
      )}

      <div
        className={`grid min-h-0 flex-1 gap-4 ${
          isPick ? "grid-cols-1" : "grid-cols-[minmax(220px,280px)_1fr]"
        }`}
      >
        <div className="flex min-h-0 flex-col rounded-lg border bg-card">
          <div className="flex flex-wrap items-center gap-2 border-b px-2 py-2">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search…"
              className="h-8 flex-1 text-xs"
            />
            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8 shrink-0"
              onClick={() =>
                void flushSave()
                  .then(() => actions.refresh())
                  .then(() => flash("refresh"))
              }
              disabled={state.loading}
              aria-label="Refresh"
            >
              {feedbackKey === "refresh" && !state.loading ? (
                <Check className="h-3.5 w-3.5 text-green-600" />
              ) : (
                <RefreshCw
                  className={`h-3.5 w-3.5 ${state.loading ? "animate-spin" : ""}`}
                />
              )}
            </Button>
            {!isPick && (
              <Button
                size="sm"
                className="h-8 gap-1.5"
                onClick={() => void handleCreate()}
                disabled={state.saving}
              >
                <Plus className="h-3.5 w-3.5" />
                New
              </Button>
            )}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {state.loading && state.prompts.length === 0 ? (
              <div className="flex items-center justify-center py-10 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
              </div>
            ) : filtered.length === 0 ? (
              <p className="px-3 py-6 text-center text-xs text-muted-foreground">
                No saved prompts yet.
              </p>
            ) : (
              filtered.map((row) => {
                const active = !isPick && row.id === selectedId;
                const key = `prompt:${row.id}`;
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
                      onClick={() => handleRowClick(row)}
                    >
                      <p className="truncate text-xs font-medium">{row.name}</p>
                      <p className="mt-0.5 truncate text-[10px] text-muted-foreground">
                        {previewText(row.prompt) || "Empty prompt"}
                      </p>
                    </button>
                    {!isPick && (
                      <div className="flex shrink-0 items-center">
                        <FeedbackIconButton
                          feedbackKey={`dup:${row.id}`}
                          activeKey={feedbackKey}
                          icon={CopyPlus}
                          label="Duplicate"
                          onClick={() => handleDuplicate(row.id)}
                        />
                        {confirmDeleteId === row.id ? (
                          <Button
                            variant="destructive"
                            size="icon"
                            className="h-7 w-7"
                            onClick={() => {
                              void flushSave().then(async () => {
                                await actions.deletePrompt(row.id);
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
                            label="Delete"
                            destructive
                            onClick={() => setConfirmDeleteId(row.id)}
                          />
                        )}
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>

        {!isPick && (
          <div className="flex min-h-0 flex-col rounded-lg border bg-card p-4">
            {!draft ? (
              <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center text-muted-foreground">
                <p className="text-sm">Select a prompt or create one.</p>
                <Button size="sm" onClick={() => void handleCreate()}>
                  <Plus className="mr-1.5 h-3.5 w-3.5" />
                  New prompt
                </Button>
              </div>
            ) : (
              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
                <div className="space-y-1.5">
                  <LabelWithInfo
                    htmlFor="prompt-name"
                    label="Name"
                    info="A short label for this saved prompt."
                  />
                  <Input
                    id="prompt-name"
                    value={draft.name}
                    onChange={(e) => patchDraft({ name: e.target.value })}
                    className="h-9"
                  />
                </div>

                <div className="flex min-h-0 flex-col gap-1.5">
                  <LabelWithInfo
                    htmlFor="prompt-text"
                    label="Prompt"
                    info="The main generation prompt text."
                  />
                  <VariablePromptTextarea
                    id="prompt-text"
                    value={draft.prompt}
                    onChange={(prompt) => patchDraft({ prompt })}
                    resizeStorageKey={PROMPT_TEXTAREA_KEYS.savedPromptMain}
                    className="text-sm"
                  />
                </div>

                <CollapsibleOptionalField
                  storageKey={SHOW_NEGATIVE_KEY}
                  label="Negative prompt"
                  info="Optional text describing what to avoid in generation."
                  value={draft.negativePrompt}
                  onChange={(negativePrompt) => patchDraft({ negativePrompt })}
                  placeholder="Optional…"
                  enableVariables
                />

                <PromptVariablePreview
                  fields={[
                    { label: "Prompt", text: draft.prompt },
                    {
                      label: "Negative prompt",
                      text: draft.negativePrompt,
                    },
                  ]}
                />

                <p
                  className="h-3 text-[10px] text-muted-foreground"
                  aria-live="polite"
                >
                  {state.saving ? "Saving…" : ""}
                </p>
              </div>
            )}
          </div>
        )}
      </div>

      {showStoragePath && state.promptsPath && (
        <p className="flex items-center gap-1.5 truncate text-[10px] text-muted-foreground">
          <FolderOpen className="h-3 w-3 shrink-0" />
          {state.promptsPath}
        </p>
      )}
    </div>
  );
}
