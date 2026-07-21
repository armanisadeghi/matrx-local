/**
 * ListLibrarySection — named option lists (compact toolbar, cards or table view).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Check,
  Copy,
  CopyPlus,
  Download,
  FolderOpen,
  LayoutGrid,
  List,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  Upload,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useListLibraryApp } from "@/contexts/ListLibraryContext";
import type { NamedList } from "@/lib/list-library/types";
import type { MatrixOption } from "@/lib/prompt-matrix/types";
import { enabledOptionCountForList } from "@/lib/list-library/display";
import { makeId } from "@/lib/prompt-matrix/storage";
import { ErrorNote } from "./shared";

const VIEW_KEY = "matrx-list-library-view";
type ViewMode = "cards" | "compact";

function readViewMode(): ViewMode {
  try {
    const raw = localStorage.getItem(VIEW_KEY);
    if (raw === "compact" || raw === "cards") return raw;
  } catch {
    // ignore
  }
  return "cards";
}

function previewText(list: NamedList, max = 6): string {
  return list.options
    .filter((o) => o.enabled && o.value.trim().length > 0)
    .slice(0, max)
    .map((o) => o.value.trim())
    .join(", ");
}

function FeedbackIconButton({
  feedbackKey,
  activeKey,
  icon: Icon,
  activeIcon: ActiveIcon = Check,
  label,
  activeLabel,
  onClick,
  className,
  variant = "ghost",
  destructive,
}: {
  feedbackKey: string;
  activeKey: string | null;
  icon: LucideIcon;
  activeIcon?: LucideIcon;
  label: string;
  activeLabel?: string;
  onClick: () => void | Promise<void>;
  className?: string;
  variant?: "ghost" | "outline" | "destructive";
  destructive?: boolean;
}) {
  const active = activeKey === feedbackKey;
  const Active = active ? ActiveIcon : Icon;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant={variant}
          size="icon"
          className={`h-7 w-7 ${destructive ? "text-muted-foreground hover:text-destructive" : ""} ${className ?? ""}`}
          onClick={() => void onClick()}
          aria-label={active ? (activeLabel ?? label) : label}
        >
          <Active
            className={`h-3.5 w-3.5 ${active ? "text-green-600 dark:text-green-400" : ""}`}
          />
        </Button>
      </TooltipTrigger>
      <TooltipContent>
        {active ? (activeLabel ?? "Done") : label}
      </TooltipContent>
    </Tooltip>
  );
}

function ListEditorDialog({
  open,
  list,
  saving,
  onOpenChange,
  onSave,
}: {
  open: boolean;
  list: NamedList | null;
  saving: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (
    patch: Partial<Pick<NamedList, "name" | "options">>,
  ) => Promise<boolean>;
}) {
  const [name, setName] = useState("");
  const [optionsText, setOptionsText] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  const resetFromList = useCallback((row: NamedList | null) => {
    if (!row) {
      setName("");
      setOptionsText("");
      return;
    }
    setName(row.name);
    setOptionsText(row.options.map((o) => o.value).join("\n"));
    setLocalError(null);
  }, []);

  useEffect(() => {
    if (open && list) resetFromList(list);
  }, [open, list, resetFromList]);

  const handleSave = async () => {
    if (!list) return;
    const lines = optionsText.split("\n");
    const options: MatrixOption[] = lines.map((line) => ({
      id: makeId(),
      value: line,
      enabled: line.trim().length > 0,
    }));
    const ok = await onSave({ name, options });
    if (ok) onOpenChange(false);
    else setLocalError("Could not save — check engine connection.");
  };

  if (!list) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Edit list</DialogTitle>
          <DialogDescription>
            One option per line. Empty lines are ignored on save.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="list-name">Name</Label>
            <Input
              id="list-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Colors"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="list-options">Options</Label>
            <Textarea
              id="list-options"
              value={optionsText}
              onChange={(e) => setOptionsText(e.target.value)}
              rows={12}
              className="font-mono text-xs"
              placeholder={"Blue\nRed\nGreen\nPurple\nYellow"}
            />
          </div>
          {localError && <ErrorNote message={localError} />}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => void handleSave()} disabled={saving}>
            {saving ? (
              <>
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                Saving…
              </>
            ) : (
              "Save"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ListActions({
  list,
  feedbackKey,
  activeKey,
  confirmDeleteId,
  onEdit,
  onDuplicate,
  onCopyAi,
  onExportAi,
  onDeleteRequest,
  onDeleteConfirm,
  compact,
}: {
  list: NamedList;
  feedbackKey: string;
  activeKey: string | null;
  confirmDeleteId: string | null;
  onEdit: () => void;
  onDuplicate: () => Promise<void>;
  onCopyAi: () => Promise<void>;
  onExportAi: () => void;
  onDeleteRequest: () => void;
  onDeleteConfirm: () => void;
  compact?: boolean;
}) {
  return (
    <div
      className={`flex shrink-0 gap-0.5 ${compact ? "" : "flex-wrap justify-end"}`}
    >
      <FeedbackIconButton
        feedbackKey={`${feedbackKey}:edit`}
        activeKey={activeKey}
        icon={Pencil}
        label="Edit"
        onClick={onEdit}
      />
      <FeedbackIconButton
        feedbackKey={`${feedbackKey}:dup`}
        activeKey={activeKey}
        icon={CopyPlus}
        label="Duplicate"
        activeLabel="Duplicated"
        onClick={onDuplicate}
      />
      <FeedbackIconButton
        feedbackKey={`${feedbackKey}:copy`}
        activeKey={activeKey}
        icon={Copy}
        label="Copy for AI"
        activeLabel="Copied"
        onClick={onCopyAi}
      />
      <FeedbackIconButton
        feedbackKey={`${feedbackKey}:export`}
        activeKey={activeKey}
        icon={Download}
        label="Export for AI"
        activeLabel="Exported"
        onClick={onExportAi}
      />
      {confirmDeleteId === list.id ? (
        <Button
          variant="destructive"
          size="sm"
          className="h-7 px-2 text-[11px]"
          onClick={onDeleteConfirm}
        >
          Confirm
        </Button>
      ) : (
        <FeedbackIconButton
          feedbackKey={`${feedbackKey}:del`}
          activeKey={activeKey}
          icon={Trash2}
          label="Delete"
          onClick={onDeleteRequest}
          destructive
        />
      )}
    </div>
  );
}

export function ListLibraryCore() {
  const [state, actions] = useListLibraryApp();
  const [query, setQuery] = useState("");
  const [view, setView] = useState<ViewMode>(readViewMode);
  const [editing, setEditing] = useState<NamedList | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [importMode, setImportMode] = useState<"merge" | "replace">("merge");
  const [importError, setImportError] = useState<string | null>(null);
  const [feedbackKey, setFeedbackKey] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const feedbackTimerRef = useRef<number | null>(null);

  const flash = useCallback((key: string, ms = 2000) => {
    setFeedbackKey(key);
    if (feedbackTimerRef.current !== null) {
      window.clearTimeout(feedbackTimerRef.current);
    }
    feedbackTimerRef.current = window.setTimeout(() => {
      setFeedbackKey(null);
      feedbackTimerRef.current = null;
    }, ms);
  }, []);

  const setViewMode = (mode: ViewMode) => {
    setView(mode);
    try {
      localStorage.setItem(VIEW_KEY, mode);
    } catch {
      // ignore
    }
  };

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return state.lists;
    return state.lists.filter(
      (row) =>
        row.name.toLowerCase().includes(q) ||
        row.options.some((o) => o.value.toLowerCase().includes(q)),
    );
  }, [query, state.lists]);

  const handleCreate = async () => {
    const created = await actions.createList("New list");
    if (created) {
      flash("new");
      setEditing(created);
    }
  };

  const handleDuplicate = async (id: string) => {
    const copy = await actions.duplicateList(id);
    if (copy) flash(`list:${id}:dup`);
  };

  const copyAi = async (key: string, text: string) => {
    await navigator.clipboard.writeText(text);
    flash(`${key}:copy`);
  };

  const handleExportAi = (id: string) => {
    const json = actions.exportOneForAi(id);
    if (!json) return;
    const row = state.lists.find((item) => item.id === id);
    const slug = (row?.name ?? "list")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "");
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${slug || "list"}-for-ai.json`;
    a.click();
    URL.revokeObjectURL(url);
    flash(`list:${id}:export`);
  };

  const handleExportAllForAi = () => {
    const blob = new Blob([actions.exportAllForAi()], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "matrx-lists-for-ai.json";
    a.click();
    URL.revokeObjectURL(url);
    flash("all:export");
  };

  const handleImportConfirm = async () => {
    setImportError(null);
    try {
      const count = await actions.importFromJson(importText, importMode);
      if (count > 0) {
        flash("import");
        setImportOpen(false);
        setImportText("");
      }
    } catch (err) {
      setImportError(err instanceof Error ? err.message : String(err));
    }
  };

  const renderRow = (list: NamedList, compact: boolean) => {
    const count = enabledOptionCountForList(list);
    const preview = previewText(list, compact ? 3 : 6);
    const key = `list:${list.id}`;

    if (compact) {
      return (
        <div
          key={list.id}
          className="flex items-center gap-2 border-b px-2 py-1.5 last:border-b-0 hover:bg-muted/40"
        >
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-medium">{list.name}</p>
            <p className="truncate text-[10px] text-muted-foreground">
              {count} · {preview || "—"}
            </p>
          </div>
          <ListActions
            list={list}
            feedbackKey={key}
            activeKey={feedbackKey}
            confirmDeleteId={confirmDeleteId}
            onEdit={() => setEditing(list)}
            onDuplicate={() => handleDuplicate(list.id)}
            onCopyAi={() => {
              const json = actions.exportOneForAi(list.id);
              if (json) return copyAi(key, json);
              return Promise.resolve();
            }}
            onExportAi={() => handleExportAi(list.id)}
            onDeleteRequest={() => setConfirmDeleteId(list.id)}
            onDeleteConfirm={() => {
              void actions.deleteList(list.id);
              setConfirmDeleteId(null);
              flash(`${key}:del`);
            }}
            compact
          />
        </div>
      );
    }

    return (
      <div
        key={list.id}
        className="flex flex-col rounded-lg border bg-card p-3 shadow-sm"
      >
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <p className="truncate font-medium">{list.name}</p>
            <p className="mt-1 text-[11px] text-muted-foreground">
              {count} option{count === 1 ? "" : "s"}
              {preview ? ` — ${preview}${count > 6 ? "…" : ""}` : ""}
            </p>
          </div>
          <ListActions
            list={list}
            feedbackKey={key}
            activeKey={feedbackKey}
            confirmDeleteId={confirmDeleteId}
            onEdit={() => setEditing(list)}
            onDuplicate={() => handleDuplicate(list.id)}
            onCopyAi={() => {
              const json = actions.exportOneForAi(list.id);
              if (json) return copyAi(key, json);
              return Promise.resolve();
            }}
            onExportAi={() => handleExportAi(list.id)}
            onDeleteRequest={() => setConfirmDeleteId(list.id)}
            onDeleteConfirm={() => {
              void actions.deleteList(list.id);
              setConfirmDeleteId(null);
              flash(`${key}:del`);
            }}
          />
        </div>
      </div>
    );
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search…"
          className="h-8 w-40 text-xs"
        />
        <div className="flex rounded-md border p-0.5">
          <Button
            variant={view === "cards" ? "secondary" : "ghost"}
            size="icon"
            className="h-7 w-7"
            onClick={() => setViewMode("cards")}
            aria-label="Card view"
          >
            <LayoutGrid className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant={view === "compact" ? "secondary" : "ghost"}
            size="icon"
            className="h-7 w-7"
            onClick={() => setViewMode("compact")}
            aria-label="Compact list view"
          >
            <List className="h-3.5 w-3.5" />
          </Button>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="h-8 w-8 p-0"
          onClick={() => {
            void actions.refresh().then(() => flash("refresh"));
          }}
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
        <Button
          variant="outline"
          size="sm"
          className="h-8 gap-1.5"
          onClick={() => fileInputRef.current?.click()}
        >
          {feedbackKey === "import-open" ? (
            <Check className="h-3.5 w-3.5 text-green-600" />
          ) : (
            <Upload className="h-3.5 w-3.5" />
          )}
          Import
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) {
              void file.text().then((text) => {
                setImportText(text);
                setImportOpen(true);
              });
            }
            e.target.value = "";
          }}
        />
        <Button
          variant="outline"
          size="sm"
          className="h-8 gap-1.5"
          onClick={() =>
            void copyAi("all", actions.exportAllForAi()).then(() =>
              flash("all:copy"),
            )
          }
          disabled={state.lists.length === 0}
        >
          {feedbackKey === "all:copy" ? (
            <Check className="h-3.5 w-3.5 text-green-600" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
          Copy for AI
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-8 gap-1.5"
          onClick={handleExportAllForAi}
          disabled={state.lists.length === 0}
        >
          {feedbackKey === "all:export" ? (
            <Check className="h-3.5 w-3.5 text-green-600" />
          ) : (
            <Download className="h-3.5 w-3.5" />
          )}
          Export for AI
        </Button>
        <Button
          size="sm"
          className="h-8 gap-1.5"
          onClick={() => void handleCreate()}
        >
          {feedbackKey === "new" ? (
            <Check className="h-3.5 w-3.5" />
          ) : (
            <Plus className="h-3.5 w-3.5" />
          )}
          New
        </Button>
      </div>

      {state.error && (
        <ErrorNote message={state.error} onDismiss={actions.clearError} />
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {state.loading && state.lists.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center rounded-xl border border-dashed py-16">
            <Button
              size="sm"
              className="gap-1.5"
              onClick={() => void handleCreate()}
            >
              <Plus className="h-3.5 w-3.5" />
              New list
            </Button>
          </div>
        ) : view === "compact" ? (
          <div className="rounded-lg border bg-card">
            {filtered.map((list) => renderRow(list, true))}
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {filtered.map((list) => renderRow(list, false))}
          </div>
        )}
      </div>

      {state.listsPath && (
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              className="flex max-w-full items-center gap-1 self-start truncate text-[10px] text-muted-foreground hover:text-foreground"
              onClick={() => {
                void navigator.clipboard
                  .writeText(state.listsPath ?? "")
                  .then(() => flash("path"));
              }}
            >
              {feedbackKey === "path" ? (
                <Check className="h-3 w-3 shrink-0 text-green-600" />
              ) : (
                <FolderOpen className="h-3 w-3 shrink-0" />
              )}
              <span className="truncate">{state.listsPath}</span>
            </button>
          </TooltipTrigger>
          <TooltipContent>Copy storage path</TooltipContent>
        </Tooltip>
      )}

      <ListEditorDialog
        open={editing !== null}
        list={editing}
        saving={state.saving}
        onOpenChange={(open) => {
          if (!open) setEditing(null);
        }}
        onSave={(patch) =>
          editing
            ? actions.updateList(editing.id, patch)
            : Promise.resolve(false)
        }
      />

      <Dialog open={importOpen} onOpenChange={setImportOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Import</DialogTitle>
            <DialogDescription>
              AI interchange JSON, a single list,{" "}
              <code className="text-xs">{`{"lists":[…]}`}</code>, or an array.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                variant={importMode === "merge" ? "default" : "outline"}
                onClick={() => setImportMode("merge")}
              >
                Merge
              </Button>
              <Button
                type="button"
                size="sm"
                variant={importMode === "replace" ? "default" : "outline"}
                onClick={() => setImportMode("replace")}
              >
                Replace all
              </Button>
            </div>
            <Textarea
              value={importText}
              onChange={(e) => setImportText(e.target.value)}
              rows={10}
              className="font-mono text-xs"
            />
            {importError && <ErrorNote message={importError} />}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setImportOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => void handleImportConfirm()}
              disabled={state.saving}
            >
              {feedbackKey === "import" ? (
                <Check className="mr-1.5 h-3.5 w-3.5" />
              ) : null}
              Import
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** Tab route alias — same canonical component as ListLibraryCore. */
export function ListLibrarySection() {
  return <ListLibraryCore />;
}
