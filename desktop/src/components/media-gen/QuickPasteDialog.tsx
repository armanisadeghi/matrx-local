/**
 * Quick paste dialog — paste arbitrary content; parser figures out the format.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { ClipboardPaste, Loader2 } from "lucide-react";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ListLibraryActions } from "@/hooks/use-list-library";
import type { NamedList } from "@/lib/list-library/types";
import {
  formatLabelForPaste,
  listCountForPaste,
  optionCountForPaste,
  parsePastedListContent,
} from "@/lib/list-library/parse-pasted-content";
import { ErrorNote } from "./shared";
import {
  PROMPT_TEXTAREA_KEYS,
  ResizablePromptTextarea,
} from "./prompts/ResizablePromptTextarea";

const NEW_LIST_TARGET = "__new__";

export interface QuickPasteDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  lists: readonly NamedList[];
  saving: boolean;
  actions: Pick<ListLibraryActions, "applyPastedContent">;
  /** Pre-select append target when opened from a list row. */
  initialTargetListId?: string | null;
  onApplied?: (summary: string) => void;
}

function defaultNameForPaste(
  parsed: ReturnType<typeof parsePastedListContent>,
): string {
  if (parsed.kind === "single-list") return parsed.list.name;
  return "Pasted list";
}

function PastePreview({
  parsed,
}: {
  parsed: ReturnType<typeof parsePastedListContent>;
}) {
  const format = formatLabelForPaste(parsed.format);
  const listCount = listCountForPaste(parsed);
  const optionCount = optionCountForPaste(parsed);

  let headline = "Nothing detected yet";
  if (parsed.kind === "options" && optionCount > 0) {
    headline = `${optionCount} option${optionCount === 1 ? "" : "s"} · ${format}`;
  } else if (parsed.kind === "single-list") {
    headline = `1 list · ${parsed.list.name} · ${optionCount} option${optionCount === 1 ? "" : "s"} · ${format}`;
  } else if (parsed.kind === "multi-list") {
    headline = `${listCount} lists · ${optionCount} total options · ${format}`;
  }

  const previewValues =
    parsed.kind === "options"
      ? parsed.options
      : parsed.kind === "single-list"
        ? parsed.list.options
        : parsed.lists.flatMap((list) => list.options);

  return (
    <div className="space-y-2 rounded-md border bg-muted/30 p-3">
      <p className="text-xs font-medium text-foreground">{headline}</p>
      {previewValues.length > 0 ? (
        <ol className="max-h-32 space-y-1 overflow-y-auto text-xs text-muted-foreground">
          {previewValues.slice(0, 12).map((value, index) => (
            <li key={`${index}:${value}`} className="truncate">
              {index + 1}. {value}
            </li>
          ))}
          {previewValues.length > 12 ? (
            <li>…and {previewValues.length - 12} more</li>
          ) : null}
        </ol>
      ) : (
        <p className="text-xs text-muted-foreground">
          Paste lines, comma-separated values, or JSON.
        </p>
      )}
    </div>
  );
}

export function QuickPasteDialog({
  open,
  onOpenChange,
  lists,
  saving,
  actions,
  initialTargetListId = null,
  onApplied,
}: QuickPasteDialogProps) {
  const [text, setText] = useState("");
  const [target, setTarget] = useState(NEW_LIST_TARGET);
  const [newListName, setNewListName] = useState("Pasted list");
  const [replaceOptions, setReplaceOptions] = useState(false);
  const [importMode, setImportMode] = useState<"merge" | "replace">("merge");
  const [localError, setLocalError] = useState<string | null>(null);

  const parsed = useMemo(() => parsePastedListContent(text), [text]);
  const isMultiList = parsed.kind === "multi-list";

  const reset = useCallback(() => {
    setText("");
    setTarget(initialTargetListId ?? NEW_LIST_TARGET);
    setNewListName("Pasted list");
    setReplaceOptions(false);
    setImportMode("merge");
    setLocalError(null);
  }, [initialTargetListId]);

  useEffect(() => {
    if (!open) return;
    setTarget(initialTargetListId ?? NEW_LIST_TARGET);
    setLocalError(null);
  }, [open, initialTargetListId]);

  useEffect(() => {
    if (parsed.kind === "single-list") {
      setNewListName((prev) =>
        prev === "Pasted list" ? parsed.list.name : prev,
      );
    }
  }, [parsed]);

  const handlePasteFromClipboard = async () => {
    try {
      const clip = await navigator.clipboard.readText();
      if (clip.trim()) setText(clip);
    } catch {
      setLocalError("Could not read clipboard — paste manually with ⌘V.");
    }
  };

  const handleApply = async () => {
    setLocalError(null);
    const result = await actions.applyPastedContent(text, {
      ...(target !== NEW_LIST_TARGET ? { targetListId: target } : {}),
      newListName,
      replaceOptions,
      importMode,
    });
    if (!result.ok) {
      setLocalError(result.summary);
      return;
    }
    onApplied?.(result.summary);
    onOpenChange(false);
    reset();
  };

  const canApply =
    text.trim().length > 0 &&
    (parsed.kind !== "options" || parsed.options.length > 0) &&
    (parsed.kind !== "single-list" || parsed.list.options.length > 0) &&
    (parsed.kind !== "multi-list" || parsed.lists.length > 0);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent className="flex max-h-[min(760px,88vh)] w-[min(720px,94vw)] max-w-none flex-col overflow-hidden">
        <DialogHeader>
          <DialogTitle>Quick paste</DialogTitle>
          <DialogDescription>
            Paste anything — one option per line, comma-separated, JSON array,
            or a full list object. The parser figures out the format.
          </DialogDescription>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor="quick-paste-text">Content</Label>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 gap-1.5 text-xs"
              onClick={() => void handlePasteFromClipboard()}
            >
              <ClipboardPaste className="h-3.5 w-3.5" />
              From clipboard
            </Button>
          </div>
          <ResizablePromptTextarea
            id="quick-paste-text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            resizeStorageKey={PROMPT_TEXTAREA_KEYS.listQuickPaste}
            placeholder={
              'Red, Green, Blue\n\nor\n\n["Red","Green","Blue"]\n\nor\n\n{"name":"Colors","options":["Red","Blue"]}'
            }
            className="font-mono text-sm leading-6"
          />

          <PastePreview parsed={parsed} />

          {!isMultiList ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="quick-paste-target">Apply to</Label>
                <Select value={target} onValueChange={setTarget}>
                  <SelectTrigger id="quick-paste-target">
                    <SelectValue placeholder="Choose target" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NEW_LIST_TARGET}>New list</SelectItem>
                    {lists.map((list) => (
                      <SelectItem key={list.id} value={list.id}>
                        Append to {list.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {target === NEW_LIST_TARGET ? (
                <div className="space-y-1.5">
                  <Label htmlFor="quick-paste-name">List name</Label>
                  <Input
                    id="quick-paste-name"
                    value={newListName}
                    onChange={(e) => setNewListName(e.target.value)}
                    placeholder={defaultNameForPaste(parsed)}
                  />
                </div>
              ) : (
                <div className="flex items-end">
                  <label className="flex items-center gap-2 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={replaceOptions}
                      onChange={(e) => setReplaceOptions(e.target.checked)}
                      className="rounded border"
                    />
                    Replace existing options (instead of append)
                  </label>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-1.5">
              <Label>Multiple lists detected</Label>
              <div className="flex gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant={importMode === "merge" ? "default" : "outline"}
                  onClick={() => setImportMode("merge")}
                >
                  Merge with library
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={importMode === "replace" ? "default" : "outline"}
                  onClick={() => setImportMode("replace")}
                >
                  Replace all lists
                </Button>
              </div>
            </div>
          )}

          {localError && <ErrorNote message={localError} />}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => void handleApply()}
            disabled={saving || !canApply}
          >
            {saving ? (
              <>
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                Saving…
              </>
            ) : (
              "Apply"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
