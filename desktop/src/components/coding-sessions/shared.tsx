/**
 * The small pieces every coding-sessions tab shares.
 *
 * Split out of the 1,239-line page (audit 2026-09-14, UI-05) so the three tabs
 * can use the same stat card, the same clickable queue number and the same
 * reveal button without any of them redefining the voice of "unknown".
 */

import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { FolderOpen } from "lucide-react";

import { Button } from "@ai-matrx/design-system";
// THE package byte-size/relative-time formatters (`@ai-matrx/kit/format`).
import { formatRelativeTime } from "@ai-matrx/kit/format";

/**
 * A wrapper that BINDS the "0 means never stamped" guard and delegates the
 * voice to THE package formatter — `0` is an epoch timestamp to
 * `formatRelativeTime`, not an absence, so the guard cannot move into it.
 */
export function formatWhen(ms: number): string {
  if (!ms) return "—";
  return formatRelativeTime(ms);
}

export function formatStamp(value: string | null | undefined): string {
  if (!value) return "Never";
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? formatWhen(ms) : value;
}

export function whenLocal(value: string | null | undefined): string {
  if (!value) return "—";
  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) return value;
  return new Date(ms).toLocaleString();
}

export function Stat({
  value,
  label,
  hint,
  tone,
  selected,
  onClick,
}: {
  value: number;
  label: string;
  hint: string;
  tone?: string | undefined;
  selected?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      title={hint}
      aria-pressed={selected}
      onClick={onClick}
      className={`rounded-lg border px-4 py-3 text-left transition-colors hover:bg-muted/50 ${
        selected ? "border-primary ring-1 ring-primary" : ""
      }`}
    >
      <div className={`text-2xl font-semibold tabular-nums ${tone ?? ""}`}>
        {value.toLocaleString()}
      </div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </button>
  );
}

/** A queue number that opens the envelopes behind it. Zero is plain text. */
export function QueueCount({
  value,
  tone,
  onOpen,
  hint,
}: {
  value: number;
  tone?: string;
  onOpen: () => void;
  hint: string;
}) {
  if (value === 0) {
    return <span className="tabular-nums text-muted-foreground">0</span>;
  }
  return (
    <button
      type="button"
      title={hint}
      onClick={onOpen}
      className={`tabular-nums underline decoration-dotted underline-offset-4 hover:decoration-solid ${tone ?? ""}`}
    >
      {value.toLocaleString()}
    </button>
  );
}

/**
 * Reveal a durable folder in the OS file manager through the ONE validated
 * native command this app already exposes (`open_filesystem_path`, Rust
 * `filesystem.rs`) — never plugin-shell, which would hand the renderer an
 * arbitrary executable surface.
 */
async function revealInFinder(path: string): Promise<void> {
  await invoke("open_filesystem_path", { path, reveal: true });
}

export function RevealButton({
  path,
  label = "Reveal in Finder",
}: {
  path: string;
  label?: string;
}) {
  const [revealing, setRevealing] = useState(false);
  const [revealError, setRevealError] = useState<string | null>(null);
  return (
    <span className="inline-flex items-center gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={revealing}
        title={path}
        onClick={() => {
          setRevealing(true);
          setRevealError(null);
          revealInFinder(path)
            .catch((reason: unknown) => {
              setRevealError(reason instanceof Error ? reason.message : String(reason));
            })
            .finally(() => setRevealing(false));
        }}
      >
        <FolderOpen className="mr-2 h-4 w-4" />
        {label}
      </Button>
      {revealError && (
        <span className="max-w-64 truncate text-xs text-destructive" role="alert" title={revealError}>
          {revealError}
        </span>
      )}
    </span>
  );
}
