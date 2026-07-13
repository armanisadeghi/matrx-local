import { useState, useRef, useEffect, useCallback, memo } from "react";
import {
  Folder,
  FolderOpen,
  ChevronRight,
  ChevronDown,
  Plus,
  Trash2,
  FileText,
  Pencil,
  MoreHorizontal,
  Check,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { DocFolder } from "@/lib/api";

interface FolderTreeProps {
  folders: DocFolder[];
  activeFolderId: string | null;
  unfiledCount: number;
  onSelect: (folderId: string | null) => void;
  onCreate: (name: string, parentId?: string) => void;
  onDelete: (folderId: string) => void;
  onRename: (folderId: string, newName: string) => void;
}

interface ContextMenuState {
  folderId: string;
  folderName: string;
  x: number;
  y: number;
}

interface InlineRenameState {
  folderId: string;
  value: string;
}

// memo: skips per-keystroke re-renders from the Documents page (see NoteList).
export const FolderTree = memo(function FolderTree({
  folders,
  activeFolderId,
  unfiledCount,
  onSelect,
  onCreate,
  onDelete,
  onRename,
}: FolderTreeProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [newFolderName, setNewFolderName] = useState("");
  const [showNewInput, setShowNewInput] = useState(false);
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

  const toggle = useCallback((id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const handleCreate = () => {
    if (newFolderName.trim()) {
      onCreate(newFolderName.trim());
      setNewFolderName("");
      setShowNewInput(false);
    }
  };

  const openContextMenu = (
    e: React.MouseEvent,
    folderId: string,
    folderName: string,
  ) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ folderId, folderName, x: e.clientX, y: e.clientY });
  };

  const handleRenameStart = (folderId: string, folderName: string) => {
    setContextMenu(null);
    setInlineRename({ folderId, value: folderName });
  };

  const commitRename = (folderId: string) => {
    if (!inlineRename) return;
    const trimmed = inlineRename.value.trim();
    if (trimmed && trimmed !== inlineRename.value) {
      onRename(folderId, trimmed);
    } else if (trimmed) {
      onRename(folderId, trimmed);
    }
    setInlineRename(null);
  };

  const handleDelete = (folderId: string, folderName: string) => {
    setContextMenu(null);
    if (
      confirm(
        `Delete folder "${folderName}"? Notes inside will become unfiled.`,
      )
    ) {
      onDelete(folderId);
    }
  };

  const renderFolder = (folder: DocFolder, depth = 0) => {
    const hasChildren = folder.children && folder.children.length > 0;
    const isExpanded = expanded.has(folder.id);
    const isActive = activeFolderId === folder.id;
    const isRenaming = inlineRename?.folderId === folder.id;

    return (
      <div key={folder.id}>
        <div
          className={cn(
            "group relative flex w-full items-center rounded-md transition-colors",
            "hover:bg-accent",
            isActive && "bg-primary/10 text-primary font-medium",
          )}
          style={{ paddingLeft: `${depth * 16}px` }}
          onContextMenu={(e) => openContextMenu(e, folder.id, folder.name)}
        >
          {/* Expand chevron */}
          <button
            onClick={(e) => {
              e.stopPropagation();
              if (hasChildren) toggle(folder.id);
            }}
            className="shrink-0 flex items-center justify-center w-5 h-8"
          >
            {hasChildren ? (
              isExpanded ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )
            ) : (
              <span className="w-3.5" />
            )}
          </button>

          {/* Folder icon + name */}
          {isRenaming ? (
            <div className="flex flex-1 items-center gap-1.5 py-1 pr-1.5 min-w-0">
              {isExpanded ? (
                <FolderOpen className="h-4 w-4 shrink-0 text-amber-500" />
              ) : (
                <Folder className="h-4 w-4 shrink-0 text-amber-500" />
              )}
              <input
                ref={renameInputRef}
                className="flex-1 min-w-0 bg-transparent text-sm outline-none border-b border-primary"
                value={inlineRename!.value}
                onChange={(e) =>
                  setInlineRename({ ...inlineRename!, value: e.target.value })
                }
                onKeyDown={(e) => {
                  if (e.key === "Enter") commitRename(folder.id);
                  if (e.key === "Escape") setInlineRename(null);
                }}
                onBlur={() => commitRename(folder.id)}
              />
              <button
                onMouseDown={(e) => {
                  e.preventDefault();
                  commitRename(folder.id);
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
            <button
              onClick={() => onSelect(folder.id)}
              className="flex flex-1 items-center gap-1.5 py-1.5 pr-1 text-sm text-left min-w-0"
            >
              {isExpanded ? (
                <FolderOpen className="h-4 w-4 shrink-0 text-amber-500" />
              ) : (
                <Folder className="h-4 w-4 shrink-0 text-amber-500" />
              )}
              <span className="truncate flex-1">{folder.name}</span>
              {folder.note_count !== undefined && folder.note_count > 0 && (
                <span className="text-xs text-muted-foreground mr-1">
                  {folder.note_count}
                </span>
              )}
            </button>
          )}

          {/* Hover action button */}
          {!isRenaming && (
            <button
              onClick={(e) => openContextMenu(e, folder.id, folder.name)}
              className={cn(
                "shrink-0 mr-1 rounded p-1",
                "opacity-0 group-hover:opacity-100 transition-opacity",
                "hover:bg-background/60 text-muted-foreground hover:text-foreground",
              )}
              title="More actions"
            >
              <MoreHorizontal className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        {isExpanded &&
          hasChildren &&
          folder.children!.map((child) => renderFolder(child, depth + 1))}
      </div>
    );
  };

  return (
    <>
      <div className="flex flex-col gap-0.5">
        {/* All Notes */}
        <button
          onClick={() => onSelect(null)}
          className={cn(
            "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
            "hover:bg-accent",
            activeFolderId === null && "bg-primary/10 text-primary font-medium",
          )}
        >
          <FileText className="h-4 w-4 shrink-0" />
          <span className="flex-1 text-left">All Notes</span>
          {unfiledCount > 0 && (
            <span className="text-xs text-muted-foreground">
              {unfiledCount}
            </span>
          )}
        </button>

        {/* Folder list */}
        {folders.map((f) => renderFolder(f))}

        {/* New folder input */}
        {showNewInput ? (
          <div className="flex items-center gap-1.5 px-2 py-1 mt-0.5">
            <Folder className="h-4 w-4 shrink-0 text-amber-500" />
            <input
              autoFocus
              value={newFolderName}
              onChange={(e) => setNewFolderName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleCreate();
                if (e.key === "Escape") setShowNewInput(false);
              }}
              onBlur={() => {
                if (!newFolderName.trim()) setShowNewInput(false);
              }}
              placeholder="Folder name..."
              className="flex-1 bg-transparent text-sm outline-none border-b border-border"
            />
          </div>
        ) : (
          <button
            onClick={() => setShowNewInput(true)}
            className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
          >
            <Plus className="h-4 w-4" />
            <span>New Folder</span>
          </button>
        )}
      </div>

      {/* Context menu */}
      {contextMenu &&
        (() => {
          const viewportH = window.innerHeight;
          const menuH = 110;
          const top =
            contextMenu.y + menuH > viewportH
              ? contextMenu.y - menuH
              : contextMenu.y;

          return (
            <div
              ref={menuRef}
              style={{ top, left: contextMenu.x }}
              className="fixed z-50 min-w-[160px] rounded-lg border border-border bg-popover shadow-xl py-1 text-sm"
            >
              <button
                onClick={() =>
                  handleRenameStart(
                    contextMenu.folderId,
                    contextMenu.folderName,
                  )
                }
                className="flex w-full items-center gap-2 px-3 py-2 hover:bg-accent"
              >
                <Pencil className="h-3.5 w-3.5 text-muted-foreground" />
                Rename
              </button>
              <div className="border-t border-border my-1" />
              <button
                onClick={() =>
                  handleDelete(contextMenu.folderId, contextMenu.folderName)
                }
                className="flex w-full items-center gap-2 px-3 py-2 hover:bg-accent text-destructive"
              >
                <Trash2 className="h-3.5 w-3.5" />
                Delete
              </button>
            </div>
          );
        })()}
    </>
  );
});
