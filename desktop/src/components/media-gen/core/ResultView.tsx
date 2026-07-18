/**
 * ResultView — the canonical image-result pane: the fresh GeneratedImageView
 * (seed chip, lightbox, download, Use-as-input) plus the generating / idle
 * placeholders every layout previously re-drew.
 */

import { Image as ImageIcon, Loader2 } from "lucide-react";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import {
  GeneratedImageView,
  StillWorkingNote,
} from "@/components/media-gen/shared";

/**
 * The image result area. Renders (in priority order): the fresh result, the
 * generating placeholder, or the idle placeholder (unless `hideIdle`).
 */
export function ImageResultPane({
  hideIdle = false,
  placeholderClassName = "flex flex-col items-center justify-center rounded-lg border border-dashed aspect-square gap-3 text-muted-foreground",
  onOpenLightbox,
}: {
  hideIdle?: boolean;
  placeholderClassName?: string;
  /** Overrides the result's click-to-expand (e.g. Studio's multi-item set). */
  onOpenLightbox?: () => void;
}) {
  const [state, actions] = useMediaGenApp();
  const {
    imageResult,
    imageGenerating,
    imageGenStartedAt,
  } = state;
  const { clearImageResult, setImageForm, useImageAsInput } = actions;

  if (imageResult) {
    return (
      <GeneratedImageView
        result={imageResult}
        onClear={clearImageResult}
        onReuseSeed={(seed) => setImageForm({ seedText: String(seed) })}
        {...(onOpenLightbox !== undefined ? { onOpenLightbox } : {})}
        onUseAsInput={() =>
          useImageAsInput({
            name: "generated.png",
            base64: imageResult.b64,
            previewUrl: `data:image/png;base64,${imageResult.b64}`,
          })
        }
      />
    );
  }
  if (imageGenerating) {
    return (
      <div className={placeholderClassName}>
        <Loader2 className="h-8 w-8 animate-spin text-violet-500" />
        <span className="text-sm">Generating image…</span>
        <StillWorkingNote startedAt={imageGenStartedAt} />
        <span className="text-xs">
          Can take minutes on CPU — use Cancel to stop at any time
        </span>
      </div>
    );
  }
  if (hideIdle) return null;
  return (
    <div className={placeholderClassName}>
      <ImageIcon className="h-10 w-10 opacity-20" />
      <span className="text-sm">Your generated image will appear here</span>
    </div>
  );
}
