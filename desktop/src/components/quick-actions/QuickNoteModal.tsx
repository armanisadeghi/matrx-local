import { useState, useCallback } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useDocuments } from "@/hooks/use-documents";
import type { EngineStatus } from "@/hooks/use-engine";

interface QuickNoteModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  engineStatus: EngineStatus;
  userId: string | null;
}

export function QuickNoteModal({
  open,
  onOpenChange,
  engineStatus,
  userId,
}: QuickNoteModalProps) {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [folderId, setFolderId] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const docs = useDocuments(userId, engineStatus);
  const folders = docs.tree?.folders ?? [];

  const handleSave = useCallback(async () => {
    if (!title.trim() && !content.trim()) return;
    setSaving(true);
    setSaveError(null);
    try {
      const folder = folders.find((f) => f.id === folderId);
      // createNote NEVER throws — it returns null on failure (and when the
      // engine isn't ready). Clearing the form on null silently lost the note.
      const note = await docs.createNote({
        label: title.trim() || "Untitled Note",
        content: content.trim(),
        folder_name: folder?.name,
        folder_id: folderId || undefined,
      });
      if (!note) {
        setSaveError(
          docs.error ?? "Could not save the note — the engine may still be starting.",
        );
        return;
      }
      setTitle("");
      setContent("");
      setFolderId("");
      onOpenChange(false);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [title, content, folderId, folders, docs, onOpenChange]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        handleSave();
      }
    },
    [handleSave],
  );

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) {
          setTitle("");
          setContent("");
          setFolderId("");
        }
        onOpenChange(v);
      }}
    >
      <DialogContent className="max-w-lg" onKeyDown={handleKeyDown}>
        <DialogHeader>
          <DialogTitle>Quick Note</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <Input
            placeholder="Title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            autoFocus
          />
          {folders.length > 0 && (
            <select
              value={folderId}
              onChange={(e) => setFolderId(e.target.value)}
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              <option value="">No folder (unfiled)</option>
              {folders.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </select>
          )}
          <Textarea
            placeholder="Write your note…"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={8}
            className="resize-none"
          />
        </div>
        {saveError && (
          <p className="text-xs text-red-400" role="alert">
            {saveError}
          </p>
        )}
        <DialogFooter>
          <span className="text-xs text-muted-foreground">
            {navigator.platform.includes("Mac") ? "\u2318" : "Ctrl"}+Enter to save
          </span>
          <Button onClick={handleSave} disabled={saving} size="sm">
            {saving ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
