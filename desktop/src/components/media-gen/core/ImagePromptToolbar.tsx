/**
 * ImagePromptToolbar — canonical entry points for prompts, variations, lists,
 * and optional generate-form panels (negative, input image, LoRAs, advanced).
 */

import { useCallback, useState, type ReactNode } from "react";
import {
  ImagePlus,
  ListTree,
  MessageSquareText,
  MinusCircle,
  Shuffle,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import { enqueueVariationBatchForImageGen } from "@/lib/media-gen/enqueue-variation-batch";
import type { VariationQueueOptions } from "@/lib/media-gen/enqueue-variation-batch";
import type { SavedPrompt } from "@/lib/saved-prompts/types";
import type { VariationBatch } from "@/lib/variation-batches/types";
import { SavedPromptsSurface } from "../SavedPromptsSection";
import { VariationBatchesSurface } from "../VariationBatchesSection";
import { ListLibrarySurface } from "../ListLibrarySurface";
import type { ImageGenController } from "./imageController";
import { ErrorNote } from "../shared";

export type ImageFormPanelToggles = {
  showNegative: boolean;
  onToggleNegative: () => void;
  onRevealNegative: () => void;
  showInputImage?: boolean;
  onToggleInputImage?: () => void;
  showInputImageButton?: boolean;
  showLoras?: boolean;
  onToggleLoras?: () => void;
  activeLoraCount?: number;
  showAdvanced?: boolean;
  onToggleAdvanced?: () => void;
};

export function ImagePromptToolbar({
  ctl,
  compact = false,
  panels,
}: {
  ctl: ImageGenController;
  compact?: boolean;
  panels?: ImageFormPanelToggles;
}) {
  const [, actions] = useMediaGenApp();
  const { setImageForm, enqueueImageBatch } = actions;

  const [promptsOpen, setPromptsOpen] = useState(false);
  const [variationsOpen, setVariationsOpen] = useState(false);
  const [listsOpen, setListsOpen] = useState(false);
  const [queueError, setQueueError] = useState<string | null>(null);

  const applySavedPrompt = useCallback(
    (prompt: SavedPrompt) => {
      setImageForm({
        prompt: prompt.prompt,
        negativePrompt: prompt.negativePrompt,
      });
      if (prompt.negativePrompt.trim()) {
        panels?.onRevealNegative();
      }
    },
    [setImageForm, panels],
  );

  const queueVariationBatch = useCallback(
    async (batch: VariationBatch, options: VariationQueueOptions) => {
      setQueueError(null);
      const result = await enqueueVariationBatchForImageGen(
        batch,
        () => ctl.buildBatchBaseInput(),
        enqueueImageBatch,
        options,
      );
      if (!result.ok) {
        setQueueError(result.error);
        throw new Error(result.error);
      }
    },
    [ctl, enqueueImageBatch],
  );

  if (ctl.isRevision) return null;

  const btnClass = compact ? "h-6 gap-1 px-1.5 text-[10px]" : "h-8 gap-1.5";
  const iconClass = compact ? "h-3 w-3" : "h-3.5 w-3.5";

  const panelButton = (
    active: boolean | undefined,
    onClick: (() => void) | undefined,
    label: string,
    icon: ReactNode,
    title: string,
    badge?: number,
  ) => {
    if (!onClick) return null;
    return (
      <Button
        type="button"
        variant={active ? "secondary" : "outline"}
        size="sm"
        className={btnClass}
        title={title}
        onClick={onClick}
        aria-pressed={active ?? false}
      >
        {icon}
        {label}
        {badge !== undefined && badge > 0 && (
          <Badge variant="secondary" className="ml-0.5 h-4 px-1 text-[9px]">
            {badge}
          </Badge>
        )}
      </Button>
    );
  };

  const buttons = (
    <>
      <SavedPromptsSurface
        surface="popover"
        open={promptsOpen}
        onOpenChange={setPromptsOpen}
        intent="pick"
        onPick={applySavedPrompt}
        trigger={
          <Button
            type="button"
            variant="outline"
            size="sm"
            className={btnClass}
            title={compact ? "Saved prompts" : undefined}
          >
            <MessageSquareText className={iconClass} />
            {compact ? "Saved" : "Saved prompt"}
          </Button>
        }
      />

      <VariationBatchesSurface
        surface="popover"
        open={variationsOpen}
        onOpenChange={setVariationsOpen}
        intent="pick"
        onQueueBatch={queueVariationBatch}
        trigger={
          <Button
            type="button"
            variant="outline"
            size="sm"
            className={btnClass}
            title={compact ? "Variation batch" : undefined}
          >
            <Shuffle className={iconClass} />
            {compact ? "Variations" : "Variation batch"}
          </Button>
        }
      />

      <ListLibrarySurface
        surface="popover"
        open={listsOpen}
        onOpenChange={setListsOpen}
        trigger={
          <Button
            type="button"
            variant="outline"
            size="sm"
            className={btnClass}
            title={compact ? "Variable lists" : undefined}
          >
            <ListTree className={iconClass} />
            Lists
          </Button>
        }
      />

      {panels &&
        panelButton(
          panels.showNegative,
          panels.onToggleNegative,
          "Negative",
          <MinusCircle className={iconClass} />,
          "Negative prompt",
        )}

      {panels?.showInputImageButton &&
        panelButton(
          panels.showInputImage,
          panels.onToggleInputImage,
          "Input",
          <ImagePlus className={iconClass} />,
          "Input image",
        )}

      {panels &&
        panelButton(
          panels.showLoras,
          panels.onToggleLoras,
          "LoRAs",
          <Sparkles className={iconClass} />,
          "LoRA styles",
          panels.activeLoraCount,
        )}

      {panels &&
        panelButton(
          panels.showAdvanced,
          panels.onToggleAdvanced,
          "Advanced",
          <SlidersHorizontal className={iconClass} />,
          "Advanced parameters",
        )}
    </>
  );

  return (
    <div className={compact ? "flex shrink-0 items-center gap-1" : "space-y-2"}>
      {queueError && !compact && (
        <ErrorNote message={queueError} onDismiss={() => setQueueError(null)} />
      )}
      <div className="flex flex-wrap items-center gap-1">{buttons}</div>
      {queueError && compact && (
        <ErrorNote message={queueError} onDismiss={() => setQueueError(null)} />
      )}
    </div>
  );
}
