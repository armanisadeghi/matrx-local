/**
 * NotesAccessPrompt — first-class, gentle full-page state shown when the
 * canonical access-health authority reports the notes directory degraded.
 *
 * Pure RENDERER: state, polling, and stale-response protection live in
 * AccessHealthContext (the app's single poll owner — it actively re-probes
 * every 10s while degraded, so this prompt auto-dismisses the moment access
 * is granted). Copy comes from deriveAccessPresentation and is evidence-
 * based: the definitive "Full Disk Access" claim renders only when the
 * engine positively established the denial.
 */

import { useCallback, useState } from "react";
import { FolderLock, FolderPlus, RefreshCw, Settings } from "lucide-react";
import type { AccessResourceHealth } from "@/lib/api";
import type { AccessPresentation } from "@/hooks/use-access-health";
import { usePermissionsContext } from "@/contexts/PermissionsContext";

interface NotesAccessPromptProps {
  resource: AccessResourceHealth;
  presentation: AccessPresentation;
  checking: boolean;
  /** Re-probe access on the engine; resolves null on transient failure,
   * otherwise whether THIS resource is still degraded. */
  onRecheck: (opts?: {
    createMissing?: boolean;
  }) => Promise<{ degraded: boolean } | null>;
}

export function NotesAccessPrompt({
  resource,
  presentation,
  checking,
  onRecheck,
}: NotesAccessPromptProps) {
  const { openSettings } = usePermissionsContext();
  const [busy, setBusy] = useState<"recheck" | "create" | null>(null);
  const [lastCheckFailed, setLastCheckFailed] = useState(false);

  const isMissingDir = presentation.primaryAction === "create_folder";

  const runRecheck = useCallback(
    async (createMissing: boolean) => {
      setBusy(createMissing ? "create" : "recheck");
      try {
        const result = await onRecheck(createMissing ? { createMissing: true } : undefined);
        // Still degraded after an explicit click → tell the user the check
        // ran (otherwise the unchanged screen looks like a dead button).
        setLastCheckFailed(result === null || result.degraded);
      } finally {
        setBusy(null);
      }
    },
    [onRecheck],
  );

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

        <h2 className="text-base font-semibold">{presentation.title}</h2>

        <p className="mt-2 text-sm text-muted-foreground">{presentation.body}</p>

        <p className="mt-3 rounded-md bg-muted px-3 py-1.5 font-mono text-xs text-muted-foreground break-all">
          {resource.root}
        </p>

        <div className="mt-6 flex flex-col items-center gap-2">
          {presentation.showFdaAction && (
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
              disabled={busy !== null || checking}
              className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
            >
              <FolderPlus className="h-4 w-4" />
              {busy === "create" ? "Creating folder..." : "Create folder"}
            </button>
          )}

          <button
            onClick={() => void runRecheck(false)}
            disabled={busy !== null || checking}
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
