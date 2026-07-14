/**
 * Document management hook — state management for the Documents page.
 *
 * LOCAL FIRST. Always.
 *
 * userId is optional for all read/write operations — the engine serves local
 * files without auth. userId is only used for cloud-sync operations (trigger
 * sync, version history, sharing). If userId is null the page works fully in
 * local-only mode.
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { engine } from "@/lib/api";
import { markNoteEditing, markNoteIdle } from "@/hooks/use-realtime-sync";
import type {
  DocTree,
  DocNote,
  DocVersion,
  CreateNoteData,
  NotesAccessStatus,
  SyncStatus,
  SyncResult,
  ConflictDetail,
} from "@/lib/api";
import type { EngineStatus } from "@/hooks/use-engine";

export type SyncMode = "push" | "pull" | "bidirectional";

export interface DocumentsState {
  tree: DocTree | null;
  notes: DocNote[];
  activeNote: DocNote | null;
  versions: DocVersion[];
  syncStatus: SyncStatus | null;
  /**
   * Notes-directory access health. When `degraded` the page renders the
   * first-class access prompt (Full Disk Access / folder permissions /
   * missing folder) instead of empty lists — see NotesAccessPrompt.
   */
  notesAccess: NotesAccessStatus | null;
  conflicts: ConflictDetail[];
  activeFolderId: string | null;
  searchQuery: string;
  loading: boolean;
  saving: boolean;
  syncing: boolean;
  error: string | null;
  lastSyncResult: SyncResult | null;
}

const INITIAL_STATE: DocumentsState = {
  tree: null,
  notes: [],
  activeNote: null,
  versions: [],
  syncStatus: null,
  notesAccess: null,
  conflicts: [],
  activeFolderId: null,
  searchQuery: "",
  loading: true,
  saving: false,
  syncing: false,
  error: null,
  lastSyncResult: null,
};

export function useDocuments(
  userId: string | null,
  engineStatus?: EngineStatus,
) {
  const [state, setState] = useState<DocumentsState>(INITIAL_STATE);
  // Callbacks read transient state (activeNote, activeFolderId) through this
  // ref instead of closing over `state`. Closing over state put it in every
  // useCallback dep array, so EVERY keystroke (which updates activeNote)
  // recreated every action — cascading re-renders through NoteList/FolderTree
  // and defeating memoization (a real contributor to the paste-freeze bug).
  const stateRef = useRef(state);
  stateRef.current = state;
  const mountedRef = useRef(true);
  const lastSelectFailedRef = useRef(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Track pending flush so we can await it on unmount and not discard unsaved edits
  const pendingSaveRef = useRef<{
    noteId: string;
    data: Partial<CreateNoteData>;
  } | null>(null);

  const update = useCallback((partial: Partial<DocumentsState>) => {
    if (mountedRef.current) {
      setState((prev) => ({ ...prev, ...partial }));
    }
  }, []);

  // engineReady is true when: the engine URL is known AND the engine status is
  // "connected" (or not provided — legacy callers that don't pass engineStatus).
  // This ensures the hook re-fires when the engine finishes starting up.
  const engineReady =
    !!engine.engineUrl &&
    (engineStatus === undefined || engineStatus === "connected");

  // ── Load folder tree — local filesystem, no auth required ───────────────

  const loadTree = useCallback(async () => {
    if (!engineReady) return;
    try {
      const tree = await engine.getDocTree(userId ?? "local");
      update({ tree });
    } catch (err) {
      console.warn("[docs] Failed to load tree:", err);
      update({ tree: { folders: [], total_notes: 0, unfiled_notes: 0 } });
    }
  }, [engineReady, userId, update]);

  // ── Load notes — local filesystem, no auth required ─────────────────────

  const loadNotes = useCallback(
    async (folderId?: string | null, search?: string) => {
      if (!engineReady) return;
      try {
        update({ loading: true, error: null });
        const notes = await engine.listNotes(userId ?? "local", {
          ...(folderId != null ? { folder_id: folderId } : {}),
          ...(search !== undefined ? { search } : {}),
        });
        update({ notes, loading: false });
      } catch (err) {
        update({
          loading: false,
          error: err instanceof Error ? err.message : "Failed to load notes",
        });
      }
    },
    [engineReady, userId, update],
  );

  // Refresh the list under the CURRENT filter (folder + search). Realtime
  // callbacks must use this instead of bare loadNotes(), which lists ALL
  // notes and silently discards the active folder/search filter.
  const refreshNotes = useCallback(async () => {
    const { activeFolderId, searchQuery } = stateRef.current;
    await loadNotes(activeFolderId, searchQuery || undefined);
  }, [loadNotes]);

  // ── Select folder ────────────────────────────────────────────────────────

  const selectFolder = useCallback(
    (folderId: string | null) => {
      update({ activeFolderId: folderId, activeNote: null, searchQuery: "" });
      loadNotes(folderId);
    },
    [update, loadNotes],
  );

  // ── Search ───────────────────────────────────────────────────────────────

  const search = useCallback(
    (query: string) => {
      update({ searchQuery: query, activeFolderId: null, activeNote: null });
      loadNotes(null, query);
    },
    [update, loadNotes],
  );

  // ── Select note ──────────────────────────────────────────────────────────

  const selectNote = useCallback(
    async (noteId: string) => {
      if (!engineReady) return;
      try {
        const note = await engine.getNote(noteId, userId ?? "local");
        update({ activeNote: note });
        lastSelectFailedRef.current = false;

        // Load version history — works locally now, no userId required
        engine
          .listVersions(noteId, userId ?? "local")
          .then((versions) => {
            if (mountedRef.current) update({ versions });
          })
          .catch(() => {
            if (mountedRef.current) update({ versions: [] });
          });
      } catch (err) {
        // A vanished note (deleted remotely, pulled tombstone) must not stay
        // open in the editor — the next keystroke would recreate it.
        const msg = err instanceof Error ? err.message : "Failed to load note";
        if (msg.includes("Note not found")) {
          lastSelectFailedRef.current = true;
          if (stateRef.current.activeNote?.id === noteId) {
            update({ activeNote: null, versions: [] });
          }
        } else {
          update({ error: msg });
        }
      }
    },
    [engineReady, userId, update],
  );

  // ── Create note ──────────────────────────────────────────────────────────

  const createNote = useCallback(
    async (data: CreateNoteData) => {
      if (!engineReady) return null;
      try {
        update({ saving: true, error: null });
        const note = await engine.createNote(userId ?? "local", data);
        await loadTree();
        await loadNotes(stateRef.current.activeFolderId);
        update({ saving: false, activeNote: note });
        return note;
      } catch (err) {
        update({
          saving: false,
          error: err instanceof Error ? err.message : "Failed to create note",
        });
        return null;
      }
    },
    [engineReady, userId, update, loadTree, loadNotes],
  );

  // ── Update note (debounced for content, immediate for metadata) ──────────

  const updateNote = useCallback(
    async (
      noteId: string,
      data: Partial<CreateNoteData>,
      immediate = false,
    ) => {
      if (!engineReady) return;

      // Deliberately NOT echoed into activeNote.content: the editor owns the
      // draft. Echoing every keystroke back through the note prop advanced
      // NoteEditor's last-synced marker to the draft on each render, which
      // made its "user is mid-edit, keep the draft" guard unreachable — a
      // stale refetch could then overwrite visible keystrokes.

      // Merge any pending debounced partial for the same note into this
      // update. Without this, typing content then renaming within the 1s
      // debounce window cancels the pending {content} save and only PUTs
      // {label} — silent data loss.
      const pending = pendingSaveRef.current;
      if (pending && pending.noteId === noteId) {
        data = { ...pending.data, ...data };
      } else if (pending && saveTimerRef.current) {
        // Switching notes mid-debounce: flush the OTHER note's pending save
        // NOW. Overwriting the shared timer used to silently drop that save
        // and leak the note in the realtime editing-suppression set forever.
        clearTimeout(saveTimerRef.current);
        saveTimerRef.current = null;
        pendingSaveRef.current = null;
        engine
          .updateNote(pending.noteId, userId ?? "local", pending.data)
          .catch((err) => {
            console.warn("[docs] Cross-note flush save failed:", err);
          })
          .finally(() => {
            markNoteIdle(pending.noteId);
          });
      }

      if (immediate) {
        // Clear any pending debounced save for this note — the immediate save
        // supersedes it (e.g. label change after content change).
        if (saveTimerRef.current) {
          clearTimeout(saveTimerRef.current);
          saveTimerRef.current = null;
          pendingSaveRef.current = null;
          markNoteIdle(noteId);
        }
        try {
          update({ saving: true });
          await engine.updateNote(noteId, userId ?? "local", data);
          update({ saving: false });
          if (data.label || data.folder_id || data.folder_name) {
            await loadTree();
            await loadNotes(stateRef.current.activeFolderId);
          }
        } catch (err) {
          update({
            saving: false,
            error: err instanceof Error ? err.message : "Failed to save",
          });
        }
        return;
      }

      // Record what's pending so cleanup can flush it before unmounting.
      pendingSaveRef.current = { noteId, data };

      // Tell Realtime to suppress pulls for this note while we're editing.
      markNoteEditing(noteId);

      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      saveTimerRef.current = setTimeout(async () => {
        pendingSaveRef.current = null;
        try {
          update({ saving: true });
          await engine.updateNote(noteId, userId ?? "local", data);
          update({ saving: false });
        } catch (err) {
          update({
            saving: false,
            error: err instanceof Error ? err.message : "Failed to save",
          });
        } finally {
          // Re-enable Realtime pulls now that the local save is complete.
          markNoteIdle(noteId);
        }
      }, 1000);
    },
    [engineReady, userId, update, loadTree, loadNotes],
  );

  // ── Delete note ──────────────────────────────────────────────────────────

  const deleteNote = useCallback(
    async (noteId: string) => {
      if (!engineReady) return;
      try {
        await engine.deleteNote(noteId, userId ?? "local");
        update({ activeNote: null, versions: [] });
        await loadTree();
        await loadNotes(stateRef.current.activeFolderId);
      } catch (err) {
        update({
          error: err instanceof Error ? err.message : "Failed to delete",
        });
      }
    },
    [engineReady, userId, update, loadTree, loadNotes],
  );

  // ── Create folder ────────────────────────────────────────────────────────

  const createFolder = useCallback(
    async (name: string, parentId?: string) => {
      if (!engineReady) return null;
      try {
        const folder = await engine.createFolder(userId ?? "local", {
          name,
          ...(parentId !== undefined ? { parent_id: parentId } : {}),
        });
        await loadTree();
        return folder;
      } catch (err) {
        update({
          error: err instanceof Error ? err.message : "Failed to create folder",
        });
        return null;
      }
    },
    [engineReady, userId, update, loadTree],
  );

  // ── Rename folder ────────────────────────────────────────────────────────

  const renameFolder = useCallback(
    async (folderId: string, name: string) => {
      if (!engineReady) return;
      try {
        await engine.updateFolder(folderId, userId ?? "local", { name });
        await loadTree();
      } catch (err) {
        update({
          error: err instanceof Error ? err.message : "Failed to rename folder",
        });
      }
    },
    [engineReady, userId, update, loadTree],
  );

  // ── Move note to a different folder ──────────────────────────────────────

  const moveNote = useCallback(
    async (noteId: string, folderId: string | null, folderName: string) => {
      if (!engineReady) return;
      try {
        const updatedNote = await engine.updateNote(noteId, userId ?? "local", {
          ...(folderId != null ? { folder_id: folderId } : {}),
          folder_name: folderName,
        });
        if (stateRef.current.activeNote?.id === noteId) {
          update({ activeNote: updatedNote });
        }
        await loadTree();
        await loadNotes(stateRef.current.activeFolderId);
      } catch (err) {
        update({
          error: err instanceof Error ? err.message : "Failed to move note",
        });
      }
    },
    [engineReady, userId, update, loadTree, loadNotes],
  );

  // ── Rename note ──────────────────────────────────────────────────────────

  const renameNote = useCallback(
    async (noteId: string, label: string) => {
      if (!engineReady) return;
      try {
        const updatedNote = await engine.updateNote(noteId, userId ?? "local", {
          label,
        });
        if (stateRef.current.activeNote?.id === noteId) {
          update({ activeNote: updatedNote });
        }
        await loadNotes(stateRef.current.activeFolderId);
        await loadTree();
      } catch (err) {
        update({
          error: err instanceof Error ? err.message : "Failed to rename note",
        });
      }
    },
    [engineReady, userId, update, loadNotes, loadTree],
  );

  // ── Delete folder ────────────────────────────────────────────────────────

  const deleteFolder = useCallback(
    async (folderId: string) => {
      if (!engineReady) return;
      try {
        await engine.deleteFolder(folderId, userId ?? "local");
        if (stateRef.current.activeFolderId === folderId) {
          update({ activeFolderId: null, activeNote: null });
        }
        await loadTree();
        await loadNotes(null);
      } catch (err) {
        update({
          error: err instanceof Error ? err.message : "Failed to delete folder",
        });
      }
    },
    [engineReady, userId, update, loadTree, loadNotes],
  );

  // ── Revert note — requires cloud sync (needs userId) ────────────────────

  const revertNote = useCallback(
    async (noteId: string, versionNumber: number) => {
      if (!engineReady) return;
      try {
        const note = await engine.revertNote(
          noteId,
          userId ?? "local",
          versionNumber,
        );
        update({ activeNote: note });
        const versions = await engine.listVersions(noteId, userId ?? "local");
        update({ versions });
      } catch (err) {
        update({
          error: err instanceof Error ? err.message : "Failed to revert",
        });
      }
    },
    [engineReady, userId, update],
  );

  // ── Sync operations — require userId ────────────────────────────────────

  const triggerSync = useCallback(
    async (mode: SyncMode = "bidirectional") => {
      if (!engineReady || !userId) return null;
      try {
        update({ syncing: true, error: null });
        const result = await engine.triggerSync(userId, mode);
        await loadTree();
        await loadNotes(stateRef.current.activeFolderId);
        const syncStatus = await engine.getSyncStatus(userId);
        update({ syncing: false, syncStatus, lastSyncResult: result });
        return result;
      } catch (err) {
        update({
          syncing: false,
          error: err instanceof Error ? err.message : "Sync failed",
        });
        return null;
      }
    },
    [engineReady, userId, update, loadTree, loadNotes],
  );

  const loadSyncStatus = useCallback(async () => {
    if (!engineReady || !userId) return;
    try {
      const syncStatus = await engine.getSyncStatus(userId);
      update({ syncStatus });
    } catch {
      // Non-critical
    }
  }, [engineReady, userId, update]);

  // ── Notes-directory access health — works signed-out (local dir) ─────────

  const loadNotesAccess = useCallback(async () => {
    if (!engineReady) return;
    try {
      const notesAccess = await engine.getNotesAccess();
      update({ notesAccess });
    } catch {
      // Non-critical — an old engine without /notes/access simply never
      // shows the prompt (previous behavior).
    }
  }, [engineReady, update]);

  const loadConflicts = useCallback(async () => {
    if (!engineReady) return;
    try {
      const result = await engine.getConflicts(userId ?? "local");
      update({ conflicts: result.conflicts });
    } catch {
      // Non-critical
    }
  }, [engineReady, userId, update]);

  const resolveConflict = useCallback(
    async (
      noteId: string,
      resolution:
        | "keep_local"
        | "keep_remote"
        | "merge"
        | "append"
        | "split"
        | "exclude",
      mergedContent?: string,
    ) => {
      if (!engineReady) return;
      try {
        await engine.resolveConflict(
          noteId,
          userId ?? "local",
          resolution,
          mergedContent,
        );
        await loadConflicts();
        await loadSyncStatus();
        await loadTree();
        await loadNotes(stateRef.current.activeFolderId);
      } catch (err) {
        update({
          error:
            err instanceof Error ? err.message : "Failed to resolve conflict",
        });
      }
    },
    [
      engineReady,
      userId,
      update,
      loadConflicts,
      loadSyncStatus,
      loadTree,
      loadNotes,
    ],
  );

  /**
   * "Check again" / "Create folder": actively re-probe access on the engine.
   * When access has been restored, reload everything the degraded state was
   * blocking so the page comes alive without a restart.
   */
  const recheckAccess = useCallback(
    async (opts?: { createDir?: boolean }) => {
      if (!engineReady) return null;
      try {
        const notesAccess = await engine.recheckNotesAccess(opts);
        const wasDegraded = stateRef.current.notesAccess?.degraded ?? false;
        update({ notesAccess });
        if (wasDegraded && !notesAccess.degraded) {
          await loadTree();
          await loadNotes(stateRef.current.activeFolderId);
          if (userId) {
            await loadSyncStatus();
            await loadConflicts();
          }
        }
        return notesAccess;
      } catch {
        return null; // transient engine hiccup — prompt stays, user can retry
      }
    },
    [engineReady, userId, update, loadTree, loadNotes, loadSyncStatus, loadConflicts],
  );

  const setNoteExcluded = useCallback(
    async (noteId: string, excluded: boolean) => {
      if (!engineReady) return;
      try {
        await engine.setNoteExcluded(noteId, userId ?? "local", excluded);
        await loadNotes(stateRef.current.activeFolderId);
      } catch (err) {
        update({
          error:
            err instanceof Error
              ? err.message
              : "Failed to update sync setting",
        });
      }
    },
    [engineReady, userId, update, loadNotes],
  );

  // ── Initial load — engine connection is all that's needed ────────────────

  useEffect(() => {
    mountedRef.current = true;

    if (engineReady) {
      loadTree();
      loadNotes();
      loadNotesAccess();
      if (userId) {
        loadSyncStatus();
        loadConflicts();
      }
    } else {
      update({ loading: false });
    }

    return () => {
      mountedRef.current = false;
      // Flush any in-flight debounced save BEFORE clearing the timer.
      // Without this, navigating away mid-debounce silently discards the user's
      // last edit — the most frequent real data-loss scenario.
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current);
        saveTimerRef.current = null;
        const pending = pendingSaveRef.current;
        if (pending) {
          pendingSaveRef.current = null;
          // Fire-and-forget: component is unmounting so we can't await here,
          // but the save still reaches the engine.
          engine
            .updateNote(pending.noteId, userId ?? "local", pending.data)
            .catch((err) => {
              console.warn("[docs] Flush-on-unmount save failed:", err);
            })
            .finally(() => {
              markNoteIdle(pending.noteId);
            });
        }
      }
    };
    // engineStatus is intentionally included so the effect re-runs when the
    // engine transitions from "starting"/"discovering" → "connected".
  }, [
    engineReady,
    engineStatus,
    userId,
    loadTree,
    loadNotes,
    loadNotesAccess,
    loadSyncStatus,
    loadConflicts,
    update,
  ]);

  return {
    ...state,
    isLocalOnly: !userId,
    loadTree,
    loadNotes,
    refreshNotes,
    selectFolder,
    search,
    selectNote,
    createNote,
    updateNote,
    deleteNote,
    createFolder,
    renameFolder,
    deleteFolder,
    moveNote,
    renameNote,
    revertNote,
    triggerSync,
    loadSyncStatus,
    loadNotesAccess,
    recheckAccess,
    loadConflicts,
    resolveConflict,
    setNoteExcluded,
  };
}
