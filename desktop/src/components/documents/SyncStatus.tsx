import { useState } from "react";
import {
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  CloudOff,
  Loader2,
  Monitor,
  Eye,
  Upload,
  Download,
  ArrowUpDown,
  ChevronDown,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { SyncStatus as SyncStatusType, SyncResult } from "@/lib/api";
import type { SyncMode } from "@/hooks/use-documents";

interface SyncStatusProps {
  status: SyncStatusType | null;
  syncing: boolean;
  lastResult: SyncResult | null;
  onSync: (mode: SyncMode) => void;
}

export function SyncStatusBar({ status, syncing, lastResult, onSync }: SyncStatusProps) {
  const [showMenu, setShowMenu] = useState(false);

  const handleSync = (mode: SyncMode) => {
    setShowMenu(false);
    onSync(mode);
  };

  if (!status) {
    return (
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <div className="relative">
          <button
            onClick={() => handleSync("bidirectional")}
            disabled={syncing}
            className="flex items-center gap-1.5 rounded-md px-2 py-1 transition-colors hover:bg-accent hover:text-foreground disabled:opacity-60"
            title="Sync now"
          >
            {syncing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            <span>{syncing ? "Syncing..." : "Sync"}</span>
          </button>
        </div>
        <span className="flex items-center gap-1">
          <CloudOff className="h-3 w-3 text-muted-foreground/60" />
          Not configured
        </span>
      </div>
    );
  }

  const lastSync = status.last_full_sync
    ? new Date(status.last_full_sync * 1000).toLocaleTimeString()
    : "Never";

  return (
    <div className="flex items-center gap-3 text-xs text-muted-foreground">
      {/* Sync controls with mode selection */}
      <div className="relative flex items-center">
        <button
          onClick={() => handleSync("bidirectional")}
          disabled={syncing}
          className={cn(
            "flex items-center gap-1.5 rounded-l-md px-2 py-1 transition-colors",
            "hover:bg-accent hover:text-foreground",
            syncing && "opacity-60",
          )}
          title="Bidirectional sync"
        >
          {syncing ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <ArrowUpDown className="h-3.5 w-3.5" />
          )}
          <span>{syncing ? "Syncing..." : "Sync"}</span>
        </button>

        <button
          onClick={() => setShowMenu(!showMenu)}
          disabled={syncing}
          className={cn(
            "flex items-center rounded-r-md border-l px-1 py-1 transition-colors",
            "hover:bg-accent hover:text-foreground",
            syncing && "opacity-60",
          )}
          title="Sync options"
        >
          <ChevronDown className="h-3 w-3" />
        </button>

        {showMenu && (
          <>
            <div className="fixed inset-0 z-40" onClick={() => setShowMenu(false)} />
            <div className="absolute left-0 top-full z-50 mt-1 min-w-[180px] rounded-lg border bg-popover p-1 shadow-lg">
              <button
                onClick={() => handleSync("pull")}
                className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-xs hover:bg-accent"
              >
                <Download className="h-3.5 w-3.5" />
                <div className="text-left">
                  <div className="font-medium">Pull from Server</div>
                  <div className="text-muted-foreground">Import cloud notes</div>
                </div>
              </button>
              <button
                onClick={() => handleSync("push")}
                className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-xs hover:bg-accent"
              >
                <Upload className="h-3.5 w-3.5" />
                <div className="text-left">
                  <div className="font-medium">Push to Server</div>
                  <div className="text-muted-foreground">Export local notes</div>
                </div>
              </button>
              <button
                onClick={() => handleSync("bidirectional")}
                className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-xs hover:bg-accent"
              >
                <ArrowUpDown className="h-3.5 w-3.5" />
                <div className="text-left">
                  <div className="font-medium">Bidirectional Sync</div>
                  <div className="text-muted-foreground">Merge both directions</div>
                </div>
              </button>
            </div>
          </>
        )}
      </div>

      {/* Status indicators */}
      {status.configured ? (
        <span className="flex items-center gap-1">
          <CheckCircle2 className="h-3 w-3 text-emerald-500" />
          Connected
        </span>
      ) : (
        <span className="flex items-center gap-1">
          <CloudOff className="h-3 w-3 text-amber-500" />
          Offline
        </span>
      )}

      {/* Conflict warning */}
      {status.conflict_count > 0 && (
        <span className="flex items-center gap-1 text-amber-500">
          <AlertTriangle className="h-3 w-3" />
          {status.conflict_count} conflict{status.conflict_count > 1 ? "s" : ""}
        </span>
      )}

      {/* Pending push count */}
      {(status.pending_push_count ?? 0) > 0 && (
        <span className="flex items-center gap-1 text-blue-700 dark:text-blue-400">
          <Upload className="h-3 w-3" />
          {status.pending_push_count} pending
        </span>
      )}

      {/* Watcher status */}
      {status.watcher_active && (
        <span className="flex items-center gap-1">
          <Eye className="h-3 w-3 text-blue-700 dark:text-blue-400" />
          Watching
        </span>
      )}

      {/* Tracked files */}
      <span className="flex items-center gap-1">
        <Monitor className="h-3 w-3" />
        {status.tracked_files} files
      </span>

      {/* Last sync time */}
      <span>Last sync: {lastSync}</span>

      {/* Last sync result toast */}
      {lastResult && !syncing && (
        <span className="text-emerald-500">
          {lastResult.pushed ? `↑${lastResult.pushed}` : ""}
          {lastResult.pulled ? ` ↓${lastResult.pulled}` : ""}
          {lastResult.conflicts ? ` ⚠${lastResult.conflicts}` : ""}
          {!lastResult.pushed && !lastResult.pulled && !lastResult.conflicts ? "Up to date" : ""}
        </span>
      )}
    </div>
  );
}
