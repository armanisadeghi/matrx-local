/**
 * ListLibrarySection — named option lists (compact toolbar, cards or table view).
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent,
} from "react";
import type { LucideIcon } from "lucide-react";
import {
  Check,
  ClipboardPaste,
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
import { variableTokenForList } from "@/lib/list-variables";
import { makeId } from "@/lib/prompt-matrix/storage";
import { parsePastedListContent } from "@/lib/list-library/parse-pasted-content";
import { ErrorNote } from "./shared";
import { QuickPasteDialog } from "./QuickPasteDialog";

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

function enabledOptionValues(list: NamedList): string[] {
  return list.options
    .filter((o) => o.enabled && o.value.trim().length > 0)
    .map((o) => o.value.trim());
}

function ListOptionItems({
  list,
  compact,
}: {
  list: NamedList;
  compact?: boolean;
}) {
  const options = enabledOptionValues(list);

  if (options.length === 0) {
    return (
      <div className="rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground">
        No enabled options
      </div>
    );
  }

  return (
    <ol
      className={`divide-y divide-border overflow-hidden rounded-md border bg-background/60 ${
        compact ? "max-h-48" : "max-h-64"
      } overflow-y-auto`}
    >
      {options.map((option, index) => (
        <li
          key={`${index}:${option}`}
          className={`grid grid-cols-[2rem_minmax(0,1fr)] gap-2 text-foreground ${
            compact ? "px-2 py-1.5 text-xs" : "px-3 py-2 text-sm"
          }`}
        >
          <span
            aria-hidden="true"
            className="font-mono text-[11px] leading-relaxed text-foreground"
          >
            {index + 1}.
          </span>
          <span className="whitespace-pre-wrap break-words leading-relaxed">
            {option}
          </span>
        </li>
      ))}
    </ol>
  );
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

function ListTokenCopyButton({
  list,
  feedbackKey,
  activeKey,
  onCopy,
}: {
  list: NamedList;
  feedbackKey: string;
  activeKey: string | null;
  onCopy: (token: string) => Promise<void>;
}) {
  const token = variableTokenForList(list.name);
  if (token === null) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-6 w-6"
              disabled
              aria-label="Rename this list before using it as a variable"
            >
              <Copy className="h-3 w-3" />
            </Button>
          </span>
        </TooltipTrigger>
        <TooltipContent>
          Rename this list before using it as a variable
        </TooltipContent>
      </Tooltip>
    );
  }
  return (
    <FeedbackIconButton
      feedbackKey={feedbackKey}
      activeKey={activeKey}
      icon={Copy}
      label={`Copy ${token}`}
      activeLabel="Variable copied"
      onClick={() => onCopy(token)}
      className="h-6 w-6"
    />
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

  const mergePastedOptions = (raw: string, replace = false) => {
    const parsed = parsePastedListContent(raw);
    const values =
      parsed.kind === "options"
        ? parsed.options
        : parsed.kind === "single-list"
          ? parsed.list.options
          : parsed.lists.flatMap((row) => row.options);
    if (values.length === 0) {
      setLocalError("No options found in pasted content.");
      return;
    }
    if (parsed.kind === "single-list" && name.trim().length === 0) {
      setName(parsed.list.name);
    }
    setOptionsText((prev) => {
      const existing = replace
        ? []
        : prev.split("\n").filter((line) => line.trim());
      return [...existing, ...values].join("\n");
    });
    setLocalError(null);
  };

  const handleSmartPaste = async () => {
    try {
      const clip = await navigator.clipboard.readText();
      if (!clip.trim()) {
        setLocalError("Clipboard is empty.");
        return;
      }
      mergePastedOptions(clip);
    } catch {
      setLocalError("Could not read clipboard — paste into the field with ⌘V.");
    }
  };

  const handleOptionsPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const pasted = event.clipboardData.getData("text");
    if (!pasted.trim()) return;
    const parsed = parsePastedListContent(pasted);
    const isPlainMultiLine =
      parsed.kind === "options" &&
      parsed.format === "lines" &&
      pasted.includes("\n");
    if (isPlainMultiLine) return;
    if (
      parsed.kind === "options" &&
      (parsed.format === "comma-separated" ||
        parsed.format === "semicolon-separated" ||
        parsed.format === "json-array" ||
        parsed.format === "single-value")
    ) {
      event.preventDefault();
      mergePastedOptions(pasted);
      return;
    }
    if (parsed.kind === "single-list" || parsed.kind === "multi-list") {
      event.preventDefault();
      mergePastedOptions(pasted);
    }
  };

  if (!list) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[min(760px,88vh)] w-[min(900px,94vw)] max-w-none flex-col overflow-hidden">
        <DialogHeader>
          <DialogTitle>Edit list</DialogTitle>
          <DialogDescription>
            One option per line — or paste comma-separated / JSON and it will
            split automatically.
          </DialogDescription>
        </DialogHeader>
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="list-name">Name</Label>
            <Input
              id="list-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Colors"
            />
          </div>
          <div className="flex min-h-0 flex-1 flex-col gap-1.5">
            <div className="flex items-center justify-between gap-2">
              <Label htmlFor="list-options">Options</Label>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-7 gap-1.5 text-xs"
                onClick={() => void handleSmartPaste()}
              >
                <ClipboardPaste className="h-3.5 w-3.5" />
                Smart paste
              </Button>
            </div>
            <Textarea
              id="list-options"
              value={optionsText}
              onChange={(e) => setOptionsText(e.target.value)}
              onPaste={handleOptionsPaste}
              className="min-h-0 flex-1 resize-none font-mono text-sm leading-6 text-foreground"
              placeholder={"Blue\nRed\nGreen\n\nor paste: Red, Green, Blue"}
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
  onPaste,
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
  onPaste: () => void;
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
        feedbackKey={`${feedbackKey}:paste`}
        activeKey={activeKey}
        icon={ClipboardPaste}
        label="Quick paste into this list"
        onClick={onPaste}
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
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteTargetId, setPasteTargetId] = useState<string | null>(null);
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

  const copyText = async (key: string, text: string) => {
    await navigator.clipboard.writeText(text);
    flash(key);
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
    const key = `list:${list.id}`;

    if (compact) {
      return (
        <div key={list.id} className="space-y-2 border-b p-2.5 last:border-b-0">
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex min-w-0 items-center gap-1">
                <p className="truncate text-sm font-medium text-foreground">
                  {list.name}
                </p>
                <ListTokenCopyButton
                  list={list}
                  feedbackKey={`${key}:token`}
                  activeKey={feedbackKey}
                  onCopy={(token) => copyText(`${key}:token`, token)}
                />
              </div>
              <p className="mt-0.5 text-[11px] text-muted-foreground">
                {count} option{count === 1 ? "" : "s"}
              </p>
            </div>
            <ListActions
              list={list}
              feedbackKey={key}
              activeKey={feedbackKey}
              confirmDeleteId={confirmDeleteId}
              onEdit={() => setEditing(list)}
              onPaste={() => {
                setPasteTargetId(list.id);
                setPasteOpen(true);
              }}
              onDuplicate={() => handleDuplicate(list.id)}
              onCopyAi={() => {
                const json = actions.exportOneForAi(list.id);
                if (json) return copyText(`${key}:copy`, json);
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
          <ListOptionItems list={list} compact />
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
            <div className="flex min-w-0 items-center gap-1">
              <p className="truncate font-medium text-foreground">
                {list.name}
              </p>
              <ListTokenCopyButton
                list={list}
                feedbackKey={`${key}:token`}
                activeKey={feedbackKey}
                onCopy={(token) => copyText(`${key}:token`, token)}
              />
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground">
              {count} option{count === 1 ? "" : "s"}
            </p>
          </div>
          <ListActions
            list={list}
            feedbackKey={key}
            activeKey={feedbackKey}
            confirmDeleteId={confirmDeleteId}
            onEdit={() => setEditing(list)}
            onPaste={() => {
              setPasteTargetId(list.id);
              setPasteOpen(true);
            }}
            onDuplicate={() => handleDuplicate(list.id)}
            onCopyAi={() => {
              const json = actions.exportOneForAi(list.id);
              if (json) return copyText(`${key}:copy`, json);
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
        <div className="mt-3 min-h-0 flex-1">
          <ListOptionItems list={list} />
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
          onClick={() => {
            setPasteTargetId(null);
            setPasteOpen(true);
          }}
        >
          <ClipboardPaste className="h-3.5 w-3.5" />
          Paste
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
          onClick={() => void copyText("all:copy", actions.exportAllForAi())}
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
          <div className="grid items-start gap-3 lg:grid-cols-2">
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

      <QuickPasteDialog
        open={pasteOpen}
        onOpenChange={(open) => {
          setPasteOpen(open);
          if (!open) setPasteTargetId(null);
        }}
        lists={state.lists}
        saving={state.saving}
        actions={actions}
        initialTargetListId={pasteTargetId}
        onApplied={() => flash("paste")}
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
