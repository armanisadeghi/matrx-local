import type { ReactNode } from "react";
import { VariationBatchesCore } from "./core/variation-batches/VariationBatchesCore";
import { MediaGenSurface } from "./surfaces/MediaGenSurface";
import type { VariationBatch } from "@/lib/variation-batches/types";
import type { VariationQueueOptions } from "@/lib/media-gen/enqueue-variation-batch";
import type { VariationBatchesCoreProps } from "./core/variation-batches/VariationBatchesCore";

/** Full-page tab host for variation batches. */
export function VariationBatchesSection(
  props?: Omit<VariationBatchesCoreProps, "intent">,
) {
  return <VariationBatchesCore intent="manage" showStoragePath {...props} />;
}

export interface VariationBatchesSurfaceProps extends VariationBatchesCoreProps {
  surface: "dialog" | "popover";
  open: boolean;
  onOpenChange: (open: boolean) => void;
  trigger?: ReactNode;
  title?: string;
  description?: string;
  onQueueBatch?: (
    batch: VariationBatch,
    options: VariationQueueOptions,
  ) => void | Promise<void>;
  onQueued?: () => void;
}

/** Dialog or popover host — same core as the tab. */
export function VariationBatchesSurface({
  surface,
  open,
  onOpenChange,
  trigger,
  title = "Variation batch",
  description,
  intent = "pick",
  onQueueBatch,
  onQueued,
  ...coreProps
}: VariationBatchesSurfaceProps) {
  const handleQueue = async (
    batch: VariationBatch,
    options: VariationQueueOptions,
  ) => {
    await onQueueBatch?.(batch, options);
    onQueued?.();
    onOpenChange(false);
  };

  return (
    <MediaGenSurface
      kind={surface}
      open={open}
      onOpenChange={onOpenChange}
      trigger={trigger}
      title={title}
      description={
        description ??
        (intent === "pick"
          ? "Pick a batch to queue, or create one inline."
          : "Build template variations from lists.")
      }
      contentClassName="p-3"
    >
      <VariationBatchesCore
        intent={intent}
        onQueueBatch={handleQueue}
        showStoragePath={false}
        {...coreProps}
      />
    </MediaGenSurface>
  );
}
