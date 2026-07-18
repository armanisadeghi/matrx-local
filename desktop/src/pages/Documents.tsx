import type { DocFolder } from "@/lib/api";
/**
 * Documents page — full document management with folder tree, markdown editor,
 * sync status, version history, sharing, and directory mappings.
 */

import { useState, useCallback, useEffect, useRef } from "react";
import {
  Plus,
  Search,
  FolderSync,
  History,
  Trash2,
  PanelLeftClose,
  PanelLeftOpen,
  FileText,
  AlertTriangle,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useDocuments } from "@/hooks/use-documents";
import { useAccessHealthContext } from "@/contexts/AccessHealthContext";
import { deriveAccessPresentation } from "@/hooks/use-access-health";
import { useRealtimeSync } from "@/hooks/use-realtime-sync";
import { FolderTree } from "@/components/documents/FolderTree";
import { NoteList } from "@/components/documents/NoteList";
import { NoteEditor } from "@/components/documents/NoteEditor";
import { NotesAccessPrompt } from "@/components/documents/NotesAccessPrompt";
import { SyncStatusBar } from "@/components/documents/SyncStatus";
import { VersionHistory } from "@/components/documents/VersionHistory";
import { DirectoryMappings } from "@/components/documents/DirectoryMappings";
import { ConflictResolver } from "@/components/documents/ConflictResolver";
import type { EngineStatus } from "@/hooks/use-engine";

interface DocumentsProps {
  engineStatus: EngineStatus;
  userId: string | null;
}

export function Documents({ engineStatus, userId }: DocumentsProps) {
  const docs = useDocuments(userId, engineStatus);
  const [showSidebar, setShowSidebar] = useState(true);
  const [showRightPanel, setShowRightPanel] = useState(false);
  const [rightPanelTab, setRightPanelTab] = useState<
    "versions" | "tags" | "info"
  >("versions");
  const [showMappingsDialog, setShowMappingsDialog] = useState(false);
  const [showConflictResolver, setShowConflictResolver] = useState(false);
  const [searchInput, setSearchInput] = useState("");
  const [errorDismissed, setErrorDismissed] = useState(false);

  // Pull stable function refs out of the hook so the Realtime callbacks don't
  // capture the entire `docs` object (new reference every render → stale closures
  // and needless re-subscriptions).
  const { loadTree, refreshNotes, selectNote, loadConflicts } = docs;

  // Keep the current activeNote ID in a ref so the Realtime callback always
  // has access to the latest value without being in the dep array.
  const activeNoteIdRef = useRef<string | null>(docs.activeNote?.id ?? null);
  activeNoteIdRef.current = docs.activeNote?.id ?? null;

  useEffect(() => {
    if (docs.error) setErrorDismissed(false);
  }, [docs.error]);

  // Access health comes from the app-wide store (single poll owner). Two
  // page-level concerns remain:
  //   1. Access can degrade MID-session (a save 503s, background sync flags
  //      it) — nudge the shared store to refresh so the first-class prompt
  //      takes over instead of a raw error banner.
  //   2. When access RECOVERS, reload everything the degraded state was
  //      blocking so the page comes alive without a restart.
  // Gated on specific booleans, not broad objects (CLAUDE.md React rules).
  const access = useAccessHealthContext();
  const { refresh: refreshAccess } = access.actions;
  const syncSaysDegraded = docs.syncStatus?.notes_access_degraded === true;
  const hasError = docs.error !== null;
  useEffect(() => {
    if (syncSaysDegraded || hasError) void refreshAccess();
  }, [syncSaysDegraded, hasError, refreshAccess]);

  const notesDegraded = access.notesResource?.status === "degraded";
  const { reloadAfterAccessRestored } = docs;
  const wasDegradedRef = useRef(false);
  useEffect(() => {
    if (wasDegradedRef.current && !notesDegraded) {
      void reloadAfterAccessRestored();
    }
    wasDegradedRef.current = notesDegraded;
  }, [notesDegraded, reloadAfterAccessRestored]);

  useRealtimeSync({
    userId,
    enabled: engineStatus === "connected" && !!userId,
    // Own-push echoes are identified by the device id stamped on every cloud
    // write (SyncStatus.device_id); loads on init via loadSyncStatus.
    deviceId: docs.syncStatus?.device_id ?? null,
    onNoteChange: useCallback(
      (noteId: string, _eventType: string, selfEcho: boolean) => {
        loadTree();
        refreshNotes();
        // Reload the active note only when IT changed and the change is not
        // this device's own save echoing back — refetching on self-echoes
        // raced in-flight keystrokes with slightly stale file content.
        if (!selfEcho && noteId === activeNoteIdRef.current) {
          selectNote(noteId);
        }
      },
      [loadTree, refreshNotes, selectNote],
    ),
    onFolderChange: useCallback(() => {
      loadTree();
    }, [loadTree]),
    // Catch-up pull found changes made while this device was offline.
    onCatchUp: useCallback(() => {
      loadTree();
      refreshNotes();
      loadConflicts();
    }, [loadTree, refreshNotes, loadConflicts]),
  });

  const { updateNote } = docs;

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    docs.search(searchInput);
  };

  const handleCreateNote = async () => {
    // Flatten — nested folders are selectable in the tree, but a top-level
    // find() sent folder_name "General" with the NESTED folder_id (an
    // internally inconsistent payload).
    const flatten = (folders: DocFolder[]): DocFolder[] =>
      folders.flatMap((f) => [f, ...flatten(f.children ?? [])]);
    const folderName =
      flatten(docs.tree?.folders ?? []).find((f) => f.id === docs.activeFolderId)
        ?.name ?? "General";
    await docs.createNote({
      label: "New Note",
      content: "",
      folder_name: folderName,
      ...(docs.activeFolderId ? { folder_id: docs.activeFolderId } : {}),
    });
  };

  const handleContentChange = useCallback(
    (content: string) => {
      const activeId = activeNoteIdRef.current;
      if (activeId) {
        updateNote(activeId, { content });
      }
    },
    [updateNote],
  );

  const handleLabelChange = useCallback(
    (label: string) => {
      const activeId = activeNoteIdRef.current;
      if (activeId) {
        // NoteEditor already debounces label changes internally; pass through
        // without the immediate flag so the rename goes through the same
        // debounce path as content edits (and is protected by flush-on-unmount).
        updateNote(activeId, { label });
      }
    },
    [updateNote],
  );

  if (engineStatus !== "connected") {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-center text-muted-foreground">
          <FileText className="h-12 w-12 mx-auto mb-3 opacity-30" />
          <p className="text-sm">Waiting for engine connection...</p>
          <p className="text-xs mt-1">
            Documents require the engine to be running
          </p>
        </div>
      </div>
    );
  }

  // Notes-directory access is degraded: show the first-class prompt instead
  // of empty lists and console errors. The shared store's 10s degraded poll
  // auto-clears it the moment access is granted.
  if (notesDegraded && access.notesResource && access.health) {
    const resource = access.notesResource;
    return (
      <NotesAccessPrompt
        resource={resource}
        presentation={deriveAccessPresentation(
          resource,
          access.health,
          access.parentFdaProbe,
        )}
        checking={access.checking}
        onRecheck={async (opts) => {
          const health = await access.actions.recheck({
            resourceIds: [resource.resource_id],
            createMissing: opts?.createMissing ?? false,
          });
          if (!health) return null;
          const fresh = health.resources.find(
            (r) => r.resource_id === resource.resource_id,
          );
          return { degraded: fresh?.status === "degraded" };
        }}
      />
    );
  }

  const allFolders = docs.tree?.folders ?? [];

  return (
    <div className="flex h-full flex-col">
      {/* Top bar */}
      <div className="flex items-center justify-between border-b px-4 py-2">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowSidebar(!showSidebar)}
            className="rounded-md p-1.5 hover:bg-accent"
            title={showSidebar ? "Hide sidebar" : "Show sidebar"}
          >
            {showSidebar ? (
              <PanelLeftClose className="h-4 w-4" />
            ) : (
              <PanelLeftOpen className="h-4 w-4" />
            )}
          </button>
          <h1 className="text-sm font-semibold">Documents</h1>
        </div>

        <div className="flex items-center gap-2">
          <form onSubmit={handleSearch} className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
            <input
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search notes..."
              className="rounded-md border bg-background pl-8 pr-3 py-1.5 text-sm outline-none focus:ring-1 focus:ring-primary w-48"
            />
          </form>

          <button
            onClick={handleCreateNote}
            className="flex items-center gap-1 rounded-md bg-primary px-2.5 py-1.5 text-sm text-primary-foreground hover:bg-primary/90"
          >
            <Plus className="h-3.5 w-3.5" />
            New Note
          </button>

          <button
            onClick={() => setShowMappingsDialog(true)}
            className="rounded-md p-1.5 hover:bg-accent text-muted-foreground hover:text-foreground"
            title="Directory mappings"
          >
            <FolderSync className="h-4 w-4" />
          </button>

          {docs.activeNote && (
            <>
              {/* Note sharing UI removed 2026-07-13: the cloud note_shares
                  table was retired; sharing returns via the platform iam
                  permission system (backend /shares routes are 501). */}
              <button
                onClick={() => setShowRightPanel(!showRightPanel)}
                className={cn(
                  "rounded-md p-1.5 hover:bg-accent transition-colors",
                  showRightPanel
                    ? "text-primary"
                    : "text-muted-foreground hover:text-foreground",
                )}
                title="Version history & details"
              >
                <History className="h-4 w-4" />
              </button>

              <button
                onClick={() => {
                  if (docs.activeNote) {
                    if (
                      confirm(
                        `Delete "${docs.activeNote.label}"? This cannot be undone.`,
                      )
                    ) {
                      docs.deleteNote(docs.activeNote.id);
                    }
                  }
                }}
                className="rounded-md p-1.5 hover:bg-accent text-muted-foreground hover:text-destructive"
                title="Delete note"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </>
          )}
        </div>
      </div>

      {/* Sync status bar */}
      {userId ? (
        <div className="border-b px-4 py-1.5">
          <SyncStatusBar
            status={docs.syncStatus}
            syncing={docs.syncing}
            lastResult={docs.lastSyncResult}
            onSync={(mode) => docs.triggerSync(mode)}
          />
        </div>
      ) : (
        <div className="border-b px-4 py-1.5 flex items-center gap-2 text-xs text-muted-foreground">
          <FileText className="h-3.5 w-3.5 shrink-0" />
          <span>
            Local mode — files stored on this device. Sign in to enable cloud
            sync.
          </span>
        </div>
      )}

      {/* Conflict banner */}
      {docs.conflicts.length > 0 && (
        <button
          onClick={() => {
            docs.loadConflicts();
            setShowConflictResolver(true);
          }}
          className="flex w-full items-center gap-2 border-b bg-amber-500/10 px-4 py-2 text-sm text-amber-600 dark:text-amber-400 hover:bg-amber-500/15 transition-colors"
        >
          <AlertTriangle className="h-4 w-4" />
          <span>
            {docs.conflicts.length} sync conflict
            {docs.conflicts.length > 1 ? "s" : ""} detected — click to review
            and resolve.
          </span>
        </button>
      )}

      {/* Main content area */}
      <div className="flex flex-1 overflow-hidden">
        {showSidebar && (
          <div className="flex w-72 shrink-0 flex-col border-r overflow-hidden">
            <div className="border-b p-3 overflow-auto max-h-[40%]">
              <FolderTree
                folders={allFolders}
                activeFolderId={docs.activeFolderId}
                unfiledCount={docs.tree?.unfiled_notes ?? 0}
                onSelect={docs.selectFolder}
                onCreate={docs.createFolder}
                onDelete={docs.deleteFolder}
                onRename={docs.renameFolder}
              />
            </div>

            <div className="flex-1 overflow-auto p-2">
              <NoteList
                notes={docs.notes}
                folders={allFolders}
                activeNoteId={docs.activeNote?.id ?? null}
                onSelect={docs.selectNote}
                onDelete={docs.deleteNote}
                onRename={docs.renameNote}
                onMove={docs.moveNote}
              />
            </div>
          </div>
        )}

        <div className="flex-1 overflow-hidden">
          {docs.activeNote ? (
            <NoteEditor
              note={docs.activeNote}
              saving={docs.saving}
              onChange={handleContentChange}
              onLabelChange={handleLabelChange}
            />
          ) : (
            <div className="flex h-full items-center justify-center">
              <div className="text-center text-muted-foreground">
                <FileText className="h-12 w-12 mx-auto mb-3 opacity-30" />
                <p className="text-sm">Select a note or create a new one</p>
                <button
                  onClick={handleCreateNote}
                  className="mt-3 flex items-center gap-1 mx-auto rounded-md bg-primary/10 px-3 py-1.5 text-sm text-primary hover:bg-primary/20"
                >
                  <Plus className="h-3.5 w-3.5" />
                  New Note
                </button>
              </div>
            </div>
          )}
        </div>

        {showRightPanel && docs.activeNote && (
          <div className="w-60 shrink-0 border-l overflow-auto p-3">
            <div className="flex items-center gap-1 mb-3">
              <button
                onClick={() => setRightPanelTab("versions")}
                className={cn(
                  "rounded-md px-2 py-1 text-xs transition-colors",
                  rightPanelTab === "versions"
                    ? "bg-primary/15 text-primary"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                History
              </button>
              <button
                onClick={() => setRightPanelTab("tags")}
                className={cn(
                  "rounded-md px-2 py-1 text-xs transition-colors",
                  rightPanelTab === "tags"
                    ? "bg-primary/15 text-primary"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                Tags
              </button>
              <button
                onClick={() => setRightPanelTab("info")}
                className={cn(
                  "rounded-md px-2 py-1 text-xs transition-colors",
                  rightPanelTab === "info"
                    ? "bg-primary/15 text-primary"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                Info
              </button>
            </div>

            {rightPanelTab === "versions" && (
              <VersionHistory
                versions={docs.versions}
                onRevert={(v) => docs.revertNote(docs.activeNote!.id, v)}
              />
            )}

            {rightPanelTab === "tags" && (
              <div className="text-xs">
                <h4 className="font-medium text-muted-foreground mb-2">Tags</h4>
                <div className="flex flex-wrap gap-1">
                  {docs.activeNote.tags.map((tag) => (
                    <span
                      key={tag}
                      className="rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary"
                    >
                      {tag}
                    </span>
                  ))}
                  {docs.activeNote.tags.length === 0 && (
                    <span className="text-muted-foreground">No tags</span>
                  )}
                </div>
              </div>
            )}

            {rightPanelTab === "info" && (
              <div className="text-xs space-y-2">
                <h4 className="font-medium text-muted-foreground">Details</h4>
                <div className="space-y-1.5">
                  <div>
                    <span className="text-muted-foreground">Folder: </span>
                    {docs.activeNote.folder_name}
                  </div>
                  <div>
                    <span className="text-muted-foreground">File: </span>
                    <span className="font-mono">
                      {docs.activeNote.file_path ?? "—"}
                    </span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Sync: </span>
                    <span
                      className={cn(
                        "font-medium",
                        docs.activeNote.sync_status === "synced" &&
                          "text-emerald-500",
                        docs.activeNote.sync_status === "pending_push" &&
                          "text-blue-400",
                        docs.activeNote.sync_status === "excluded" &&
                          "text-red-400",
                        docs.activeNote.sync_status === "never_synced" &&
                          "text-muted-foreground",
                      )}
                    >
                      {docs.activeNote.sync_status ?? "never_synced"}
                    </span>
                  </div>
                  {docs.activeNote.created_at && (
                    <div>
                      <span className="text-muted-foreground">Created: </span>
                      {new Date(docs.activeNote.created_at).toLocaleString()}
                    </div>
                  )}
                  {docs.activeNote.updated_at && (
                    <div>
                      <span className="text-muted-foreground">Updated: </span>
                      {new Date(docs.activeNote.updated_at).toLocaleString()}
                    </div>
                  )}
                  {docs.activeNote.content_hash && (
                    <div>
                      <span className="text-muted-foreground">Hash: </span>
                      <span className="font-mono">
                        {docs.activeNote.content_hash.substring(0, 12)}...
                      </span>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Directory mappings dialog */}
      {showMappingsDialog && (
        <DirectoryMappings
          userId={userId ?? "local"}
          folders={allFolders}
          onClose={() => setShowMappingsDialog(false)}
        />
      )}

      {/* Conflict resolution dialog */}
      {showConflictResolver && docs.conflicts.length > 0 && (
        <ConflictResolver
          conflicts={docs.conflicts}
          onResolve={docs.resolveConflict}
          onClose={() => setShowConflictResolver(false)}
        />
      )}

      {/* Error toast with auto-dismiss */}
      {docs.error && !errorDismissed && (
        <div className="fixed bottom-4 right-4 z-50 flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 dark:bg-red-400/10 px-4 py-3 text-sm text-red-600 dark:text-red-400 shadow-lg">
          <span>{docs.error}</span>
          <button
            onClick={() => setErrorDismissed(true)}
            className="rounded p-0.5 hover:bg-red-500/20"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}
