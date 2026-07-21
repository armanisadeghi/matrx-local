/**
 * Canonical path: variation batch → image-gen queue.
 * Every UI (popover pick, tab manage, future automation) must call this.
 */

import type { ImageGenBatchJobSpec } from "@/lib/api";
import type {
  EnqueueBatchResult,
  ImageGenerateInput,
} from "@/hooks/use-media-gen";
import type {
  VariationBatch,
  VariationItem,
} from "@/lib/variation-batches/types";

export type VariationQueueOrder = "start" | "end" | "random";

export interface VariationQueueOptions {
  count: number;
  order: VariationQueueOrder;
}

export function readyVariationItems(batch: VariationBatch): VariationItem[] {
  return batch.items.filter(
    (item) => item.status === "done" && item.prompt.trim().length > 0,
  );
}

function shuffleCopy<T>(items: readonly T[]): T[] {
  const out = [...items];
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    const tmp = out[i];
    out[i] = out[j] as T;
    out[j] = tmp as T;
  }
  return out;
}

/** Pick which variation rows to queue, honoring count + order. */
export function selectVariationItemsForQueue(
  batch: VariationBatch,
  options: VariationQueueOptions,
): VariationItem[] {
  const ready = readyVariationItems(batch);
  if (ready.length === 0) return [];

  const count = Math.min(
    ready.length,
    Math.max(1, Math.floor(options.count) || ready.length),
  );

  switch (options.order) {
    case "end":
      return ready.slice(-count);
    case "random":
      return shuffleCopy(ready).slice(0, count);
    case "start":
    default:
      return ready.slice(0, count);
  }
}

export function buildImageJobsFromVariationBatch(
  baseInput: ImageGenerateInput,
  batch: VariationBatch,
  queueOptions?: VariationQueueOptions,
): { jobs: ImageGenBatchJobSpec[]; errors: string[] } {
  const items = queueOptions
    ? selectVariationItemsForQueue(batch, queueOptions)
    : readyVariationItems(batch);

  if (items.length === 0) {
    return {
      jobs: [],
      errors: [
        "This batch has no ready variations. Generate variations first, then queue.",
      ],
    };
  }

  const jobs: ImageGenBatchJobSpec[] = items.map((item, index) => {
    const negative =
      item.negativePrompt.trim().length > 0
        ? item.negativePrompt.trim()
        : baseInput.negative_prompt;
    return {
      ...baseInput,
      prompt: item.prompt.trim(),
      ...(negative !== undefined ? { negative_prompt: negative } : {}),
      combo_label: `${batch.name} · ${index + 1}`,
      variables: {},
    };
  });

  return { jobs, errors: [] };
}

export async function enqueueVariationBatchForImageGen(
  batch: VariationBatch,
  buildInput: () => ImageGenerateInput | null,
  enqueueImageBatch: (
    jobs: ImageGenBatchJobSpec[],
    label?: string,
  ) => Promise<EnqueueBatchResult>,
  queueOptions?: VariationQueueOptions,
): Promise<EnqueueBatchResult> {
  const baseInput = buildInput();
  if (baseInput === null) {
    return {
      ok: false,
      error: "Pick a model and fill in the generation settings first.",
    };
  }

  const built = buildImageJobsFromVariationBatch(
    baseInput,
    batch,
    queueOptions,
  );
  if (built.errors.length > 0) {
    return { ok: false, error: built.errors[0] ?? "Cannot queue batch" };
  }

  return enqueueImageBatch(built.jobs, batch.name.trim() || undefined);
}
