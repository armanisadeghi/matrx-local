/**
 * The dialog a download shows when it needs something FROM THE USER.
 *
 * A download can fail because a key isn't set, a model license hasn't been
 * accepted, or the AI packages aren't installed. None of those are errors to
 * report at the user — they're questions to ask them. So the engine sends a
 * `resolution` (app/services/downloads/failures.py) describing what happened in
 * plain language plus the single action that fixes it, and this component
 * renders exactly that: a title, an explanation, and one button that works.
 *
 * Adding a new self-fixable failure means adding a constructor in failures.py
 * and (only if it needs a new KIND of action) a case in `runAction` below.
 */

import { useNavigate } from "react-router-dom";
import { AlertCircle } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import type { DownloadEntry, DownloadResolution } from "@/lib/downloads/types";
import { emitClientLog } from "@/hooks/use-unified-log";

export function DownloadActionDialog({
  entry,
  onClose,
  onRetry,
}: {
  /** The failed download whose `resolution` we're acting on; null = closed. */
  entry: DownloadEntry | null;
  onClose: () => void;
  onRetry?: (entry: DownloadEntry) => void;
}) {
  const navigate = useNavigate();
  const resolution: DownloadResolution | null = entry?.resolution ?? null;

  const runAction = async () => {
    if (!resolution || !entry) return;
    switch (resolution.action_kind) {
      case "settings_api_keys":
        navigate(
          `/settings?tab=api-keys${
            resolution.provider ? `&provider=${resolution.provider}` : ""
          }`,
        );
        break;
      case "open_url":
        if (resolution.action_url) {
          // The system browser — a model license can only be accepted while
          // signed in to Hugging Face, which only their site can do.
          const { open } = await import("@tauri-apps/plugin-shell");
          await open(resolution.action_url);
        }
        break;
      case "install_ai_packages":
        navigate("/media-generation");
        break;
      default: {
        // A resolution kind this build doesn't know — surface it rather than
        // silently doing nothing when the user clicks the button.
        const unknown: never = resolution.action_kind;
        emitClientLog(
          "error",
          `[downloads] unknown resolution action_kind: ${String(unknown)}`,
          "downloads",
        );
        return;
      }
    }
    onClose();
  };

  return (
    <Dialog open={resolution !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-[460px]">
        <DialogHeader>
          <div className="flex items-start gap-3">
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-amber-100 dark:bg-amber-950">
              <AlertCircle className="h-4 w-4 text-amber-600 dark:text-amber-500" />
            </span>
            <div className="min-w-0">
              <DialogTitle>{resolution?.title}</DialogTitle>
              <DialogDescription className="mt-1.5 leading-relaxed">
                {resolution?.message}
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        {entry && (
          <p className="truncate rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
            {entry.display_name || entry.filename}
          </p>
        )}

        <DialogFooter className="gap-2 sm:gap-2">
          {onRetry && entry && (
            <Button
              variant="ghost"
              onClick={() => {
                onRetry(entry);
                onClose();
              }}
            >
              Try again
            </Button>
          )}
          <Button variant="outline" onClick={onClose}>
            Not now
          </Button>
          <Button onClick={() => void runAction()}>
            {resolution?.action_label}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
