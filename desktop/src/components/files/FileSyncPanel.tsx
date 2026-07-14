/**
 * FileSyncPanel — status surface for file sync (the desktop replica of the
 * user's matrx-files cloud tree).
 *
 * Rendered inside a Card on the Configurations page ("File Sync" section).
 * Gentle-prompt doctrine: plain language, no red error walls, no jargon.
 *
 * The mode selector applies immediately (it's an action, not a form draft):
 * useFileSync.setMode persists the `fileSyncMode` setting and applies it
 * live on the engine via POST /file-sync/mode.
 */

import {
  RefreshCw,
  Loader2,
  CloudOff,
  CheckCircle2,
  FileWarning,
  Monitor,
  Laptop,
  Cloud,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useFileSync } from "@/hooks/use-file-sync";
import type { FileSyncMode } from "@/lib/api";

const MODE_OPTIONS: {
  value: FileSyncMode;
  label: string;
  description: string;
}[] = [
  { value: "off", label: "Off", description: "No sync" },
  {
    value: "pointers",
    label: "Pointers",
    description: "Your cloud files appear here, downloaded when used",
  },
  {
    value: "full",
    label: "Full",
    description: "Every file stored on this machine",
  },
];

function StatChip({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex flex-col items-center rounded-md border bg-muted/40 px-3 py-1.5">
      <span className="text-sm font-medium tabular-nums">{value}</span>
      <span className="text-[10px] text-muted-foreground">{label}</span>
    </div>
  );
}

export function FileSyncPanel() {
  const [state, actions] = useFileSync();
  const { status, conflicts, loading, syncing, lastCycle, notice } = state;

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Checking file sync…
      </div>
    );
  }

  const counts = status?.counts;
  const lastCycleAt = status?.last_cycle
    ? new Date(status.last_cycle * 1000).toLocaleTimeString()
    : null;
  const mode = status?.mode ?? "pointers";

  return (
    <div className="space-y-3">
      {/* Mode selector — applies immediately */}
      <div className="flex items-center justify-between gap-4 py-2">
        <div className="flex-1 min-w-0">
          <Label className="text-sm font-medium">Sync mode</Label>
          <p className="text-xs text-muted-foreground mt-0.5">
            Off — no sync; Pointers — your cloud files appear here, downloaded
            when used; Full — every file stored on this machine
          </p>
        </div>
        <Select
          value={mode}
          onValueChange={(v) => void actions.setMode(v as FileSyncMode)}
        >
          <SelectTrigger className="w-40 flex-shrink-0">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {MODE_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                <div className="flex flex-col">
                  <span>{opt.label}</span>
                  <span className="text-[10px] text-muted-foreground">
                    {opt.description}
                  </span>
                </div>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <Separator />

      {/* Status line */}
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        {status?.configured ? (
          <span className="flex items-center gap-1">
            <CheckCircle2 className="h-3 w-3 text-emerald-500" />
            Connected
          </span>
        ) : (
          <span className="flex items-center gap-1">
            <CloudOff className="h-3 w-3 text-amber-500" />
            Waiting for sign-in
          </span>
        )}
        {status?.root && (
          <span
            className="flex items-center gap-1 truncate max-w-[16rem]"
            title={status.root}
          >
            <Monitor className="h-3 w-3" />
            {status.root}
          </span>
        )}
        <span>
          Last sync: {lastCycleAt ?? "Not yet"}
          {status?.last_sync_status && status.last_sync_status !== "ok"
            ? ` (${status.last_sync_status})`
            : ""}
        </span>
      </div>

      {/* Counts */}
      {counts && (
        <div className="flex flex-wrap gap-2">
          <StatChip label="Tracked" value={counts.tracked} />
          <StatChip label="Synced" value={counts.synced ?? 0} />
          <StatChip label="Pointers" value={counts.pointer ?? 0} />
          <StatChip
            label="Pending"
            value={(counts.pending_push ?? 0) + counts.pending_ops}
          />
          <StatChip label="Conflicts" value={counts.conflict ?? 0} />
        </div>
      )}

      {/* Sync now + result */}
      <div className="flex items-center gap-3">
        <Button
          size="sm"
          variant="outline"
          className="gap-1.5"
          disabled={syncing || mode === "off"}
          onClick={() => void actions.syncNow()}
        >
          {syncing ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          {syncing ? "Syncing…" : "Sync Now"}
        </Button>
        {lastCycle && !syncing && (
          <span className="text-xs text-emerald-500">
            {lastCycle.pulled ? `↓${lastCycle.pulled} ` : ""}
            {lastCycle.pushed ? `↑${lastCycle.pushed} ` : ""}
            {!lastCycle.pulled && !lastCycle.pushed ? "Up to date" : ""}
          </span>
        )}
        {mode === "off" && (
          <span className="text-xs text-muted-foreground">
            Sync is off — pick Pointers or Full to start.
          </span>
        )}
      </div>

      {/* Gentle notices */}
      {notice && <p className="text-xs text-amber-500">{notice}</p>}
      {status?.last_sync_error && (
        <p className="text-xs text-amber-500">
          The last sync hit a snag — it will keep trying automatically.
        </p>
      )}

      {/* Conflicts */}
      {conflicts.length > 0 && (
        <div className="space-y-2">
          <Separator />
          <div className="flex items-center gap-1.5 text-xs font-medium text-amber-500">
            <FileWarning className="h-3.5 w-3.5" />
            {conflicts.length === 1
              ? "One file changed in both places"
              : `${conflicts.length} files changed in both places`}
          </div>
          <div className="space-y-1.5">
            {conflicts.map((c) => (
              <div
                key={c.file_id}
                className="flex items-center justify-between gap-3 rounded-md border bg-muted/30 px-3 py-2"
              >
                <span
                  className="min-w-0 flex-1 truncate text-xs"
                  title={c.rel_path}
                >
                  {c.rel_path}
                </span>
                <div className="flex flex-shrink-0 items-center gap-1.5">
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 gap-1 px-2 text-xs"
                    onClick={() =>
                      void actions.resolveConflict(c.file_id, "keep_local")
                    }
                  >
                    <Laptop className="h-3 w-3" />
                    Keep Local
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 gap-1 px-2 text-xs"
                    onClick={() =>
                      void actions.resolveConflict(c.file_id, "keep_remote")
                    }
                  >
                    <Cloud className="h-3 w-3" />
                    Keep Cloud
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
