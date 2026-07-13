/**
 * MediaInfoDialog — THE info/metadata view for any image or video.
 *
 * One dialog replaces the two drifted detail dialogs (Library's and Gallery's)
 * and is reachable from every surface (thumbnail info button, "⋯" menu,
 * right-click, the lightbox's info panel).
 *
 * The layout bug this fixes: the engine records the FULL pipeline kwargs —
 * which include the prompt — and the old dialogs dumped them through
 * JSON.stringify(…, 2) into a <pre> with `overflow-x-auto` and no wrapping. A
 * long prompt became one unbreakable line, so the dialog scrolled sideways for
 * ten page-widths and the image was off screen. Here:
 *   - the prompt and negative prompt are FIRST-CLASS blocks (wrapped, copyable,
 *     never truncated, never hidden behind a "title" attribute);
 *   - the raw params dump EXCLUDES everything already shown as its own row
 *     (see extraParams), so the prompt is never re-printed inside JSON;
 *   - every long value wraps (`whitespace-pre-wrap break-words`), and the
 *     dialog body is `overflow-x-hidden` so nothing can ever scroll it
 *     sideways again.
 */

import { useState } from "react";
import { Check, Copy, Film, Image as ImageIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useMediaActions } from "./MediaActionsProvider";
import { buildMediaMenu } from "./MediaMenuItems";
import {
  extraParams,
  formatBytes,
  formatDate,
  type MediaDescriptor,
} from "./types";

/** Copy-to-clipboard button with a transient confirmation. */
export function CopyButton({
  value,
  label,
  className = "",
}: {
  value: string;
  label: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        void navigator.clipboard.writeText(value).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        });
      }}
      aria-label={label}
      title={label}
      className={`shrink-0 text-muted-foreground transition-colors hover:text-foreground ${className}`}
    >
      {copied ? (
        <Check className="h-3.5 w-3.5 text-green-500" />
      ) : (
        <Copy className="h-3.5 w-3.5" />
      )}
    </button>
  );
}

/**
 * A prompt block: heading, copy button, and the FULL text — wrapped, selectable
 * and never truncated. Every place the app shows a prompt uses this, which is
 * why "the prompt is cut off and there is no way to get it" cannot recur.
 */
export function PromptBlock({
  label,
  text,
  className = "",
}: {
  label: string;
  text: string;
  className?: string;
}) {
  return (
    <div className={`min-w-0 space-y-1 ${className}`}>
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        {text && <CopyButton value={text} label={`Copy ${label.toLowerCase()}`} />}
      </div>
      <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">
        {text || "(none)"}
      </p>
    </div>
  );
}

function MetaRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 text-xs">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 break-words text-right">{value}</span>
    </div>
  );
}

export function MediaInfoDialog({
  item,
  onClose,
}: {
  item: MediaDescriptor | null;
  onClose: () => void;
}) {
  return (
    <Dialog open={item !== null} onOpenChange={(open) => !open && onClose()}>
      {/* keyed on the item id so the confirm-delete arming never leaks across
          items — opening a different image always starts unarmed. */}
      {item && <InfoBody key={item.id} item={item} />}
    </Dialog>
  );
}

function InfoBody({ item }: { item: MediaDescriptor }) {
  const actions = useMediaActions();
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const rest = extraParams(item);
  const restJson =
    Object.keys(rest).length > 0 ? JSON.stringify(rest, null, 2) : null;
  // "Open full size" and "Info" are pointless here — the image IS shown and
  // this IS the info view.
  const entries = buildMediaMenu(item, actions, { omit: ["info"] });

  return (
      <DialogContent className="max-h-[88vh] max-w-3xl overflow-y-auto overflow-x-hidden">
        <DialogHeader className="min-w-0">
          <DialogTitle className="flex flex-wrap items-center gap-2 text-base">
            {item.kind === "video" ? (
              <Film className="h-4 w-4 text-violet-500" />
            ) : (
              <ImageIcon className="h-4 w-4 text-violet-500" />
            )}
            {item.kind === "video" ? "Generated video" : "Generated image"}
            {item.modelId && (
              <Badge
                variant="outline"
                className="max-w-full truncate text-[10px]"
              >
                {item.modelId}
              </Badge>
            )}
          </DialogTitle>
          <DialogDescription className="break-words text-xs">
            {formatDate(item.createdAt)}
            {item.width && item.height ? ` · ${item.width}×${item.height}` : ""}
            {item.fileSizeBytes ? ` · ${formatBytes(item.fileSizeBytes)}` : ""}
            {typeof item.elapsedSeconds === "number"
              ? ` · ${item.elapsedSeconds.toFixed(1)}s`
              : ""}
          </DialogDescription>
        </DialogHeader>

        <button
          type="button"
          onClick={() => actions.openOne(item)}
          className="block w-full cursor-zoom-in"
          title="Open full size"
          aria-label="Open full size"
        >
          {item.kind === "video" ? (
            <video
              src={item.url}
              className="max-h-[50vh] w-full rounded-lg border bg-black/5 object-contain"
              controls
              playsInline
            />
          ) : (
            <img
              src={item.url}
              alt={item.prompt?.slice(0, 80) ?? "Generated image"}
              className="max-h-[50vh] w-full rounded-lg border bg-black/5 object-contain"
            />
          )}
        </button>

        <div className="min-w-0 space-y-3">
          <PromptBlock label="Prompt" text={item.prompt ?? ""} />
          {item.negativePrompt && (
            <PromptBlock label="Negative prompt" text={item.negativePrompt} />
          )}

          <div className="grid gap-x-6 gap-y-1.5 rounded-lg border bg-muted/20 p-3 sm:grid-cols-2">
            {item.modelId && <MetaRow label="Model" value={item.modelId} />}
            <MetaRow
              label="Seed"
              value={
                typeof item.seed === "number" ? (
                  <span className="inline-flex items-center gap-1.5">
                    <span className="font-mono tabular-nums">{item.seed}</span>
                    <CopyButton value={String(item.seed)} label="Copy seed" />
                  </span>
                ) : (
                  "not recorded"
                )
              }
            />
            {item.width && item.height && (
              <MetaRow
                label="Dimensions"
                value={`${item.width}×${item.height}`}
              />
            )}
            {typeof item.elapsedSeconds === "number" && (
              <MetaRow
                label="Generation time"
                value={`${item.elapsedSeconds.toFixed(1)}s`}
              />
            )}
            {typeof item.numFrames === "number" && (
              <MetaRow label="Frames" value={String(item.numFrames)} />
            )}
            {typeof item.fps === "number" && (
              <MetaRow label="FPS" value={String(item.fps)} />
            )}
            {!!item.fileSizeBytes && (
              <MetaRow
                label="File size"
                value={formatBytes(item.fileSizeBytes)}
              />
            )}
            {item.createdAt && (
              <MetaRow label="Created" value={formatDate(item.createdAt)} />
            )}
            {item.hasInitImage && (
              <MetaRow
                label="Input image"
                value={
                  item.initImageStored
                    ? "stored — Remix restores it"
                    : "used (Remix will try to restore it)"
                }
              />
            )}
          </div>

          {restJson && (
            <div className="min-w-0 space-y-1">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-medium text-muted-foreground">
                  Parameters
                </p>
                <CopyButton value={restJson} label="Copy parameters JSON" />
              </div>
              {/* whitespace-pre-wrap + break-words: a long value wraps inside
                  the dialog instead of stretching it ten screens wide. */}
              <pre className="max-h-72 overflow-y-auto overflow-x-hidden whitespace-pre-wrap break-words rounded-lg border bg-muted/20 p-3 font-mono text-[11px] leading-relaxed">
                {restJson}
              </pre>
            </div>
          )}

          {item.filePath && (
            <div className="min-w-0 space-y-1">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-medium text-muted-foreground">
                  File path
                </p>
                <CopyButton value={item.filePath} label="Copy file path" />
              </div>
              <p className="break-all font-mono text-[11px] text-muted-foreground">
                {item.filePath}
              </p>
            </div>
          )}

          <div className="flex flex-wrap gap-2 pt-1">
            {entries.map((e) => {
              const armed = e.danger && confirmingDelete;
              return (
                <Button
                  key={e.id}
                  size="sm"
                  variant={armed ? "destructive" : "outline"}
                  className={
                    e.danger && !armed
                      ? "text-destructive hover:bg-destructive/10 hover:text-destructive"
                      : ""
                  }
                  onClick={() => {
                    if (e.danger && !armed) {
                      setConfirmingDelete(true);
                      return;
                    }
                    e.run();
                  }}
                >
                  <span className="mr-1.5">{e.icon}</span>
                  {armed ? "Confirm delete" : e.label}
                </Button>
              );
            })}
          </div>
        </div>
      </DialogContent>
  );
}
