/**
 * Supabase Realtime subscription for notes sync.
 *
 * Subscribes to changes on `workbench.notes` / `workbench.note_folders`
 * (NOT `public` — the notes tables moved to the workbench schema in the
 * 2026-06 cloud reorganization; both tables are in the supabase_realtime
 * publication and RLS-filtered to the signed-in user via `created_by`).
 * On an event, the engine pulls the note to the local file and the UI
 * refreshes.
 *
 * Echo suppression: every engine push stamps `last_device_id` on the cloud
 * row. An incoming event whose `last_device_id` matches THIS device is our
 * own save bouncing back — the file pull is skipped (the UI still gets a
 * cheap refresh notification for list ordering).
 *
 * Critical invariant: a Realtime pull must NEVER overwrite a local edit that
 * is currently in-flight (debounce timer pending). Callers signal this by
 * calling `markNoteEditing(noteId)` / `markNoteIdle(noteId)`. While a note
 * is marked as editing, remote pull events for that note are deferred and
 * re-checked after a short backoff instead of being applied immediately.
 * (The engine has its own last-resort guard: pulls of rows last written by
 * this device are skipped server-side too.)
 *
 * Catch-up: on every successful (re)subscribe, one incremental
 * `pullChanges` runs so edits made while this device was offline are not
 * lost to the missed-events window.
 */

import { useEffect, useRef, useCallback } from "react";
import supabase from "@/lib/supabase";
import { engine } from "@/lib/api";
import type { RealtimeChannel } from "@supabase/supabase-js";
import type { SyncResult } from "@/lib/api";

interface UseRealtimeSyncOptions {
  userId: string | null;
  enabled: boolean;
  /** This device's sync id (SyncStatus.device_id) — used to skip self-echoes. */
  deviceId: string | null;
  /** selfEcho=true means the event is this device's own push bouncing back. */
  onNoteChange?: (noteId: string, eventType: string, selfEcho: boolean) => void;
  onFolderChange?: (folderId: string, eventType: string) => void;
  /** Fired after the on-subscribe catch-up pull completes with changes. */
  onCatchUp?: (result: SyncResult) => void;
}

/** Notes currently being edited locally — Realtime pulls are suppressed for these. */
const _editingNotes = new Set<string>();

/** Mark a note as actively being edited (suppresses Realtime pull for that note). */
export function markNoteEditing(noteId: string): void {
  _editingNotes.add(noteId);
}

/** Mark a note as idle (re-enables Realtime pulls for that note). */
export function markNoteIdle(noteId: string): void {
  _editingNotes.delete(noteId);
}

/** How long to wait before retrying a deferred pull (ms). */
const DEFERRED_PULL_DELAY_MS = 2000;

export function useRealtimeSync({
  userId,
  enabled,
  deviceId,
  onNoteChange,
  onFolderChange,
  onCatchUp,
}: UseRealtimeSyncOptions) {
  const channelRef = useRef<RealtimeChannel | null>(null);
  const callbacksRef = useRef({ onNoteChange, onFolderChange, onCatchUp });
  callbacksRef.current = { onNoteChange, onFolderChange, onCatchUp };
  const deviceIdRef = useRef(deviceId);
  deviceIdRef.current = deviceId;

  const cleanup = useCallback(() => {
    if (channelRef.current) {
      supabase.removeChannel(channelRef.current);
      channelRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!enabled || !userId) {
      cleanup();
      return;
    }

    const channel = supabase
      .channel("notes-sync")
      .on(
        "postgres_changes",
        {
          event: "*",
          schema: "workbench",
          table: "notes",
          filter: `created_by=eq.${userId}`,
        },
        async (payload) => {
          const newRow = payload.new as Record<string, unknown> | null;
          const noteId =
            (newRow?.id as string) ||
            ((payload.old as Record<string, unknown>)?.id as string);

          if (!noteId) return;

          // Soft deletes arrive as UPDATEs with deleted_at set. Handle them
          // BEFORE the self-echo check: pulling a tombstone is idempotent
          // (the engine's delete branch is safe to re-run), and deletes made
          // on other devices can carry OUR device id when we were the last
          // content writer — the echo check would wrongly swallow them.
          if (newRow && newRow.deleted_at != null) {
            try {
              await engine.pullNote(noteId, userId);
            } catch {
              // 404 = already gone locally — fine.
            }
            callbacksRef.current.onNoteChange?.(noteId, "DELETE", false);
            return;
          }

          // Self-echo: this device's own push bouncing back. The local file
          // already has this content — skip the pull; the UI gets a cheap
          // list-refresh signal flagged as a self-echo.
          const device = deviceIdRef.current;
          if (device && newRow && newRow.last_device_id === device) {
            callbacksRef.current.onNoteChange?.(noteId, payload.eventType, true);
            return;
          }

          if (payload.eventType === "DELETE") {
            callbacksRef.current.onNoteChange?.(noteId, payload.eventType, false);
            return;
          }

          // If the user is actively editing this note, defer the pull so we
          // don't overwrite keystrokes that haven't been flushed yet.
          if (_editingNotes.has(noteId)) {
            setTimeout(async () => {
              // Only pull if the note is idle again after the backoff.
              if (_editingNotes.has(noteId)) return;
              try {
                await engine.pullNote(noteId, userId);
              } catch {
                // Non-critical: will sync on next full sync
              }
              callbacksRef.current.onNoteChange?.(noteId, payload.eventType, false);
            }, DEFERRED_PULL_DELAY_MS);
            return;
          }

          // Pull the update through the engine (writes to local file).
          try {
            await engine.pullNote(noteId, userId);
          } catch {
            // Non-critical: will sync on next full sync
          }

          callbacksRef.current.onNoteChange?.(noteId, payload.eventType, false);
        },
      )
      .on(
        "postgres_changes",
        {
          event: "*",
          schema: "workbench",
          table: "note_folders",
          filter: `created_by=eq.${userId}`,
        },
        (payload) => {
          const folderId =
            ((payload.new as Record<string, unknown>)?.id as string) ||
            ((payload.old as Record<string, unknown>)?.id as string);

          if (folderId) {
            callbacksRef.current.onFolderChange?.(folderId, payload.eventType);
          }
        },
      )
      .subscribe((status) => {
        if (status === "SUBSCRIBED") {
          console.log("[realtime] Subscribed to workbench notes changes");
          // Catch up on anything missed while offline/unsubscribed.
          engine
            .pullChanges(userId)
            .then((result) => {
              if (
                (result.pulled ?? 0) > 0 ||
                (result.conflicts ?? 0) > 0 ||
                (result.deleted ?? 0) > 0
              ) {
                callbacksRef.current.onCatchUp?.(result);
              }
            })
            .catch(() => {
              // Non-critical: manual sync remains available.
            });
        } else if (status === "CHANNEL_ERROR") {
          console.warn("[realtime] Channel error — will retry");
        }
      });

    channelRef.current = channel;

    return cleanup;
  }, [enabled, userId, cleanup]);

  return { cleanup };
}
