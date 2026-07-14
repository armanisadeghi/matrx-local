/**
 * NotesAccessPrompt — first-class, gentle full-page state shown when the
 * engine reports that it cannot access the notes directory.
 *
 * This replaces the old failure mode (silently empty folder/note lists and
 * console errors) with a plain-English explanation and the exact actions
 * that fix it:
 *
 *   - macOS permission denial → "Open System Settings" deep-links straight
 *     to Privacy & Security → Full Disk Access via the existing permissions
 *     system (usePermissionsContext.openSettings — never hand-rolled).
 *   - Missing notes folder (any OS) → "Create folder" asks the engine to
 *     create it (POST /notes/access/recheck { create_dir: true }).
 *   - "Check again" re-probes on demand, and a quiet 10s poll auto-dismisses
 *     the prompt the moment access is granted — no restart, no extra click.
 *
 * The engine treats access-degraded as a STATE (notes_access_guard), so this
 * component is purely a renderer of that state plus the recheck trigger.
 */

import { useCallback, useEffect, useState } from "react";
import { FolderLock, FolderPlus, RefreshCw, Settings } from "lucide-react";
import type { NotesAccessStatus } from "@/lib/api";
import { usePermissionsContext } from "@/contexts/PermissionsContext";

interface NotesAccessPromptProps {
  access: NotesAccessStatus;
  /** Re-probe access on the engine; resolves with the fresh state. */
  onRecheck: (opts?: { createDir?: boolean }) => Promise<NotesAccessStatus | null>;
}

/** How often to quietly re-probe while the prompt is visible. */
const AUTO_RECHECK_MS = 10_000;

export function NotesAccessPrompt({ access, onRecheck }: NotesAccessPromptProps) {
  const { openSettings } = usePermissionsContext();
  const [busy, setBusy] = useState<"recheck" | "create" | null>(null);
  const [lastCheckFailed, setLastCheckFailed] = useState(false);

  const isMacPermission = access.platform === "darwin" && access.kind === "permission";
  const isMissingDir = access.kind === "missing_dir";

  const runRecheck = useCallback(
    async (createDir: boolean) => {
      setBusy(createDir ? "create" : "recheck");
      try {
        const result = await onRecheck(createDir ? { createDir: true } : undefined);
        // Still degraded after an explicit click → tell the user the check
        // ran (otherwise the unchanged screen looks like a dead button).
        setLastCheckFailed(result === null || result.degraded);
      } finally {
        setBusy(null);
      }
    },
    [onRecheck],
  );

  // Quiet auto-poll: the user typically grants Full Disk Access in System
  // Settings and switches back — the prompt should already be gone. Gated on
  // the stable onRecheck callback only; cleaned up on unmount (the parent
  // unmounts this component as soon as access.degraded is false).
  useEffect(() => {
    const id = setInterval(() => {
      void onRecheck();
    }, AUTO_RECHECK_MS);
    return () => clearInterval(id);
  }, [onRecheck]);

  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="w-full max-w-md rounded-xl border bg-card p-8 text-center shadow-sm">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
          {isMissingDir ? (
            <FolderPlus className="h-7 w-7 text-primary" />
          ) : (
            <FolderLock className="h-7 w-7 text-primary" />
          )}
        </div>

        <h2 className="text-base font-semibold">
          {isMissingDir
            ? "Your notes folder is missing"
            : "Matrx needs access to your notes folder"}
        </h2>

        <p className="mt-2 text-sm text-muted-foreground">
          {isMacPermission
            ? "Matrx needs Full Disk Access to read and sync your notes folder. Grant it once in System Settings and your notes will appear automatically."
            : (access.reason ??
              "The notes folder can't be read right now. Fix the folder's permissions and check again.")}
        </p>

        <p className="mt-3 rounded-md bg-muted px-3 py-1.5 font-mono text-xs text-muted-foreground break-all">
          {access.base_dir}
        </p>

        <div className="mt-6 flex flex-col items-center gap-2">
          {isMacPermission && (
            <button
              onClick={() => void openSettings("full_disk_access")}
              className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              <Settings className="h-4 w-4" />
              Open System Settings
            </button>
          )}

          {isMissingDir && (
            <button
              onClick={() => void runRecheck(true)}
              disabled={busy !== null}
              className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
            >
              <FolderPlus className="h-4 w-4" />
              {busy === "create" ? "Creating folder..." : "Create folder"}
            </button>
          )}

          <button
            onClick={() => void runRecheck(false)}
            disabled={busy !== null}
            className="flex w-full items-center justify-center gap-2 rounded-md border px-4 py-2 text-sm font-medium hover:bg-accent disabled:opacity-60"
          >
            <RefreshCw
              className={busy === "recheck" ? "h-4 w-4 animate-spin" : "h-4 w-4"}
            />
            {busy === "recheck" ? "Checking..." : "Check again"}
          </button>
        </div>

        {lastCheckFailed && busy === null && (
          <p className="mt-3 text-xs text-muted-foreground">
            Still no access — this screen will update automatically once it's
            granted.
          </p>
        )}
      </div>
    </div>
  );
}
