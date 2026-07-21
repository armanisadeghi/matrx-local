/**
 * ImageRevisionVersionPicker — choose an older revision in the active branch.
 *
 * Shown from ImageRevisionBanner while revision mode is active. Loads every
 * image in the branch (root-first) and lets the user switch which version is
 * pinned as the parent for the next Apply.
 */

import { useCallback, useEffect, useState } from "react";
import { Check, ChevronDown, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { useMediaLibraryApp } from "@/contexts/MediaLibraryContext";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import {
  engine,
  listMediaRevisionBranch,
  type MediaLibraryItem,
} from "@/lib/api";
import type { ImageGenController } from "./imageController";
import { pickedImageFromUrl } from "./pickedImage";

function previewPrompt(text: string, max = 56): string {
  const line = text.replace(/\s+/g, " ").trim();
  if (!line) return "No prompt recorded";
  if (line.length <= max) return line;
  return `${line.slice(0, max)}…`;
}

function formatVersionTime(iso: string | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function ImageRevisionVersionPicker({
  ctl,
}: {
  ctl: ImageGenController;
}) {
  const [, actions] = useMediaGenApp();
  const [, libraryActions] = useMediaLibraryApp();
  const revision = ctl.form.revision;
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [versions, setVersions] = useState<MediaLibraryItem[]>([]);
  const [switchingId, setSwitchingId] = useState<string | null>(null);
  const [thumbUrls, setThumbUrls] = useState<Record<string, string>>({});

  const loadBranch = useCallback(async () => {
    const rootItemId = revision?.rootItemId;
    const baseUrl = engine.engineUrl;
    if (!rootItemId || !baseUrl) {
      setVersions([]);
      setError(baseUrl ? null : "Engine not connected");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const resp = await listMediaRevisionBranch(baseUrl, rootItemId);
      setVersions(resp.items);
    } catch (e) {
      setVersions([]);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [revision?.rootItemId]);

  useEffect(() => {
    if (!open) return;
    void loadBranch();
  }, [open, loadBranch]);

  useEffect(() => {
    if (!open || versions.length === 0) return;
    let cancelled = false;
    void (async () => {
      const next: Record<string, string> = {};
      for (const row of versions) {
        const url = await libraryActions.getThumbUrl(row.id);
        if (url) next[row.id] = url;
      }
      if (!cancelled) setThumbUrls(next);
    })();
    return () => {
      cancelled = true;
    };
  }, [open, versions, libraryActions]);

  const switchToVersion = useCallback(
    async (row: MediaLibraryItem) => {
      if (!revision) return;
      if (row.id === revision.parentItemId) {
        setOpen(false);
        return;
      }
      setSwitchingId(row.id);
      setError(null);
      try {
        const fileUrl = await libraryActions.getFileUrl(row.id);
        if (!fileUrl) {
          throw new Error("Could not load the selected image.");
        }
        const picked = await pickedImageFromUrl(
          fileUrl,
          row.file_name || `${row.id}.png`,
          (msg) => {
            throw new Error(msg);
          },
        );
        if (!picked) return;

        actions.beginImageRevision(picked, row.id, revision.rootItemId);
        const isInstructionEdit = ctl.model?.pipeline_type === "flux2-klein";
        actions.setImageForm({
          prompt: isInstructionEdit ? "" : row.prompt,
          negativePrompt: row.negative_prompt ?? "",
        });
        setOpen(false);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setSwitchingId(null);
      }
    },
    [actions, ctl.model?.pipeline_type, libraryActions, revision],
  );

  if (!revision) return null;

  const currentIndex = versions.findIndex(
    (v) => v.id === revision.parentItemId,
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-7 shrink-0 gap-1 px-2 text-xs"
        >
          Versions
          {versions.length > 0 && (
            <span className="text-muted-foreground">
              {currentIndex >= 0 ? currentIndex + 1 : "?"}/{versions.length}
            </span>
          )}
          <ChevronDown className="h-3 w-3 opacity-60" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="border-b px-3 py-2">
          <p className="text-xs font-medium">Revision versions</p>
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            Pick an older version to continue editing from that image.
          </p>
        </div>
        <div className="max-h-72 overflow-y-auto p-1">
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading versions…
            </div>
          ) : error ? (
            <p className="px-2 py-4 text-xs text-destructive">{error}</p>
          ) : versions.length === 0 ? (
            <p className="px-2 py-4 text-xs text-muted-foreground">
              No saved versions found for this branch.
            </p>
          ) : (
            versions.map((row, index) => {
              const isCurrent = row.id === revision.parentItemId;
              const isSwitching = switchingId === row.id;
              return (
                <button
                  key={row.id}
                  type="button"
                  disabled={isSwitching || switchingId !== null}
                  onClick={() => void switchToVersion(row)}
                  className={`flex w-full items-start gap-2 rounded-md px-2 py-2 text-left transition-colors hover:bg-accent disabled:opacity-60 ${
                    isCurrent ? "bg-accent/70" : ""
                  }`}
                >
                  <div className="relative h-12 w-12 shrink-0 overflow-hidden rounded border bg-muted">
                    {thumbUrls[row.id] ? (
                      <img
                        src={thumbUrls[row.id]}
                        alt=""
                        className="h-full w-full object-cover"
                      />
                    ) : (
                      <div className="flex h-full w-full items-center justify-center text-[10px] text-muted-foreground">
                        …
                      </div>
                    )}
                    {isCurrent && (
                      <span className="absolute bottom-0 right-0 rounded-tl bg-violet-600 px-1 py-0.5 text-[9px] font-medium text-white">
                        Current
                      </span>
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="text-xs font-medium">v{index + 1}</span>
                      {isSwitching && (
                        <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
                      )}
                      {isCurrent && !isSwitching && (
                        <Check className="h-3 w-3 text-violet-500" />
                      )}
                    </div>
                    <p className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-muted-foreground">
                      {previewPrompt(row.prompt)}
                    </p>
                    <p className="mt-0.5 text-[10px] text-muted-foreground/70">
                      {formatVersionTime(row.created_at)}
                    </p>
                  </div>
                </button>
              );
            })
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
