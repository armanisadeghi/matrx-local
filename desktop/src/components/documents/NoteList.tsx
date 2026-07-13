import { useState, useRef, useEffect, memo } from "react";
import {
  FileText,
  Clock,
  Tag,
  Pencil,
  Trash2,
  FolderInput,
  MoreHorizontal,
  Check,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { DocNote, DocFolder } from "@/lib/api";

interface NoteListProps {
  notes: DocNote[];
  folders: DocFolder[];
  activeNoteId: string | null;
  onSelect: (noteId: string) => void;
  onDelete: (noteId: string) => void;
  onRename: (noteId: string, newLabel: string) => void;
  onMove: (noteId: string, folderId: string | null, folderName: string) => void;
}

function timeAgo(dateStr: string): string {
  const d = new Date(dateStr);
  const now = Date.now();
  const diff = now - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return d.toLocaleDateString();
}

interface ContextMenuState {
  noteId: string;
  x: number;
  y: number;
  submenu: "move" | null;
}

interface InlineRenameState {
  noteId: string;
  value: string;
}

// memo: the Documents page re-renders on every editor keystroke (activeNote
// updates). With stable list props (use-documents callbacks are stateRef-based)
// this skips re-rendering the whole note list per keystroke.
export const NoteList = memo(function NoteList({
  notes,
  folders,
  activeNoteId,
  onSelect,
  onDelete,
  onRename,
  onMove,
}: NoteListProps) {
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [inlineRename, setInlineRename] = useState<InlineRenameState | null>(
    null,
  );
  const menuRef = useRef<HTMLDivElement>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!contextMenu) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setContextMenu(null);
      }
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setContextMenu(null);
    };
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [contextMenu]);

  useEffect(() => {
    if (inlineRename) {
      renameInputRef.current?.select();
    }
  }, [inlineRename]);

  const openContextMenu = (e: React.MouseEvent, noteId: string) => {
    e.preventDefault();
    e.stopPropagation();
    const { clientX, clientY } = e;
    setContextMenu({ noteId, x: clientX, y: clientY, submenu: null });
  };

  const handleRenameStart = (note: DocNote) => {
    setContextMenu(null);
    setInlineRename({ noteId: note.id, value: note.label });
  };

  const commitRename = (noteId: string) => {
    if (!inlineRename) return;
    const trimmed = inlineRename.value.trim();
    if (trimmed) {
      onRename(noteId, trimmed);
    }
    setInlineRename(null);
  };

  const handleDelete = (noteId: string) => {
    setContextMenu(null);
    const note = notes.find((n) => n.id === noteId);
    if (note && confirm(`Delete "${note.label}"? This cannot be undone.`)) {
      onDelete(noteId);
    }
  };

  const handleMove = (
    noteId: string,
    folderId: string | null,
    folderName: string,
  ) => {
    setContextMenu(null);
    onMove(noteId, folderId, folderName);
  };

  if (notes.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <FileText className="h-8 w-8 mb-2 opacity-40" />
        <p className="text-sm">No notes yet</p>
        <p className="text-xs mt-1">Create one to get started</p>
      </div>
    );
  }

  return (
    <>
      <div className="flex flex-col gap-0.5">
        {notes.map((note) => (
          <div
            key={note.id}
            className={cn(
              "group relative flex items-start gap-0 rounded-md transition-colors",
              "hover:bg-accent",
              activeNoteId === note.id &&
                "bg-primary/10 border-l-2 border-primary",
            )}
            onContextMenu={(e) => openContextMenu(e, note.id)}
          >
            {inlineRename?.noteId === note.id ? (
              <div className="flex flex-1 items-center gap-2 px-3 py-2.5">
                <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <input
                  ref={renameInputRef}
                  className="flex-1 bg-transparent text-sm font-medium outline-none border-b border-primary"
                  value={inlineRename.value}
                  onChange={(e) =>
                    setInlineRename({ ...inlineRename, value: e.target.value })
                  }
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitRename(note.id);
                    if (e.key === "Escape") setInlineRename(null);
                  }}
                  onBlur={() => commitRename(note.id)}
                />
                <button
                  onMouseDown={(e) => {
                    e.preventDefault();
                    commitRename(note.id);
                  }}
                  className="shrink-0 text-primary hover:text-primary/80"
                >
                  <Check className="h-3.5 w-3.5" />
                </button>
                <button
                  onMouseDown={(e) => {
                    e.preventDefault();
                    setInlineRename(null);
                  }}
                  className="shrink-0 text-muted-foreground hover:text-foreground"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ) : (
              <>
                <button
                  onClick={() => onSelect(note.id)}
                  className="flex flex-col gap-1 flex-1 px-3 py-2.5 text-left min-w-0"
                >
                  <div className="flex items-center gap-2">
                    <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    <span className="text-sm font-medium truncate flex-1">
                      {note.label}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 pl-5">
                    <span className="flex items-center gap-1 text-xs text-muted-foreground">
                      <Clock className="h-3 w-3" />
                      {timeAgo(note.updated_at)}
                    </span>
                    {note.folder_name && note.folder_name !== "General" && (
                      <span className="text-xs text-muted-foreground truncate">
                        {note.folder_name}
                      </span>
                    )}
                    {note.tags && note.tags.length > 0 && (
                      <span className="flex items-center gap-1 text-xs text-muted-foreground">
                        <Tag className="h-3 w-3" />
                        {note.tags.length}
                      </span>
                    )}
                  </div>
                </button>

                {/* Hover action button */}
                <button
                  onClick={(e) => openContextMenu(e, note.id)}
                  className={cn(
                    "shrink-0 self-center mr-1.5 rounded p-1",
                    "opacity-0 group-hover:opacity-100 transition-opacity",
                    "hover:bg-background/60 text-muted-foreground hover:text-foreground",
                  )}
                  title="More actions"
                >
                  <MoreHorizontal className="h-3.5 w-3.5" />
                </button>
              </>
            )}
          </div>
        ))}
      </div>

      {/* Context menu portal */}
      {contextMenu &&
        (() => {
          const note = notes.find((n) => n.id === contextMenu.noteId);
          if (!note) return null;

          const viewportH = window.innerHeight;
          const menuH = contextMenu.submenu === "move" ? 280 : 148;
          const top =
            contextMenu.y + menuH > viewportH
              ? contextMenu.y - menuH
              : contextMenu.y;

          return (
            <div
              ref={menuRef}
              style={{ top, left: contextMenu.x }}
              className="fixed z-50 min-w-[180px] rounded-lg border border-border bg-popover shadow-xl py-1 text-sm"
            >
              {contextMenu.submenu === "move" ? (
                <>
                  <div className="px-3 py-1.5 text-xs font-medium text-muted-foreground flex items-center justify-between">
                    <span>Move to folder</span>
                    <button
                      onClick={() =>
                        setContextMenu({ ...contextMenu, submenu: null })
                      }
                      className="hover:text-foreground"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                  <div className="border-t border-border my-1" />
                  <button
                    onClick={() => handleMove(note.id, null, "General")}
                    className={cn(
                      "flex w-full items-center gap-2 px-3 py-1.5 hover:bg-accent",
                      !note.folder_id && "text-primary font-medium",
                    )}
                  >
                    <FileText className="h-3.5 w-3.5 shrink-0" />
                    <span className="truncate">General (unfiled)</span>
                    {!note.folder_id && (
                      <Check className="h-3 w-3 ml-auto shrink-0" />
                    )}
                  </button>
                  {folders.map((f) => (
                    <button
                      key={f.id}
                      onClick={() => handleMove(note.id, f.id, f.name)}
                      className={cn(
                        "flex w-full items-center gap-2 px-3 py-1.5 hover:bg-accent",
                        note.folder_id === f.id && "text-primary font-medium",
                      )}
                    >
                      <FolderInput className="h-3.5 w-3.5 shrink-0 text-amber-500" />
                      <span className="truncate">{f.name}</span>
                      {note.folder_id === f.id && (
                        <Check className="h-3 w-3 ml-auto shrink-0" />
                      )}
                    </button>
                  ))}
                </>
              ) : (
                <>
                  <button
                    onClick={() => handleRenameStart(note)}
                    className="flex w-full items-center gap-2 px-3 py-2 hover:bg-accent"
                  >
                    <Pencil className="h-3.5 w-3.5 text-muted-foreground" />
                    Rename
                  </button>
                  <button
                    onClick={() =>
                      setContextMenu({ ...contextMenu, submenu: "move" })
                    }
                    className="flex w-full items-center gap-2 px-3 py-2 hover:bg-accent"
                  >
                    <FolderInput className="h-3.5 w-3.5 text-muted-foreground" />
                    Move to folder
                  </button>
                  <div className="border-t border-border my-1" />
                  <button
                    onClick={() => handleDelete(note.id)}
                    className="flex w-full items-center gap-2 px-3 py-2 hover:bg-accent text-destructive"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    Delete
                  </button>
                </>
              )}
            </div>
          );
        })()}
    </>
  );
});
