/**
 * Bridge completed image-gen jobs → GeneratedImageResult for the preview pane.
 * Every generation path (one-shot, queued, batch) ends as a job; the preview
 * always tracks the latest terminal completion.
 */

import type { ImageGenJob } from "@/lib/api";
import type {
  GeneratedImageRequest,
  GeneratedImageResult,
} from "@/hooks/use-media-gen";

export function findLatestCompletedImageJob(
  jobs: readonly ImageGenJob[],
): ImageGenJob | null {
  let best: ImageGenJob | null = null;
  for (const job of jobs) {
    if (job.status !== "completed" || !job.item_id) continue;
    const seq = job.finished_sequence ?? 0;
    const bestSeq = best?.finished_sequence ?? 0;
    if (!best || seq > bestSeq) {
      best = job;
    }
  }
  return best;
}

function numFromParams(
  params: Record<string, unknown>,
  key: string,
): number | undefined {
  const value = params[key];
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

export function generatedImageRequestFromJob(
  job: ImageGenJob,
): GeneratedImageRequest {
  const params = job.params ?? {};
  const steps =
    numFromParams(params, "num_inference_steps") ??
    numFromParams(params, "steps");
  const guidance =
    numFromParams(params, "guidance_scale") ??
    numFromParams(params, "guidance");
  const width = numFromParams(params, "width");
  const height = numFromParams(params, "height");
  const strength = numFromParams(params, "strength");
  const negative =
    typeof params["negative_prompt"] === "string"
      ? params["negative_prompt"]
      : "";
  const loras = Array.isArray(params["loras"])
    ? (params["loras"] as GeneratedImageRequest["loras"])
    : undefined;
  const extraParams = { ...params };
  for (const key of [
    "prompt",
    "negative_prompt",
    "has_init_image",
    "num_inference_steps",
    "guidance_scale",
    "width",
    "height",
    "strength",
    "init_image_sha256",
    "revision_parent_item_id",
    "revision_root_item_id",
    "loras",
    "text_encoder_id",
    "steps",
    "guidance",
  ]) {
    delete extraParams[key];
  }
  const hasInitImage = params["has_init_image"] === true;
  const revisionParent =
    typeof params["revision_parent_item_id"] === "string"
      ? params["revision_parent_item_id"]
      : (job.revision_parent_item_id ?? undefined);
  const revisionRoot =
    typeof params["revision_root_item_id"] === "string"
      ? params["revision_root_item_id"]
      : (job.revision_root_item_id ?? undefined);

  return {
    prompt: job.prompt,
    model_id: job.model_id,
    ...(steps !== undefined ? { steps } : {}),
    ...(guidance !== undefined ? { guidance } : {}),
    ...(width !== undefined ? { width } : {}),
    ...(height !== undefined ? { height } : {}),
    ...(negative ? { negative_prompt: negative } : {}),
    ...(strength !== undefined ? { strength } : {}),
    ...(loras !== undefined ? { loras } : {}),
    ...(job.text_encoder_id ? { text_encoder_id: job.text_encoder_id } : {}),
    ...(Object.keys(extraParams).length > 0
      ? { extra_params: extraParams }
      : {}),
    has_init_image: hasInitImage,
    ...(revisionParent
      ? {
          revision: {
            parent_item_id: revisionParent,
            root_item_id: revisionRoot ?? revisionParent,
          },
        }
      : {}),
  };
}

export function generatedImageResultFromJob(
  job: ImageGenJob,
  b64: string,
): GeneratedImageResult {
  const params = job.params ?? {};
  const width =
    numFromParams(params, "width") ?? numFromParams(params, "image_width") ?? 0;
  const height =
    numFromParams(params, "height") ??
    numFromParams(params, "image_height") ??
    0;
  return {
    b64,
    elapsed: job.elapsed_seconds ?? 0,
    width,
    height,
    seed: job.seed ?? null,
    itemId: job.item_id ?? null,
    filePath: job.file_path ?? null,
    request: generatedImageRequestFromJob(job),
  };
}

export async function blobToBase64Png(blob: Blob): Promise<string> {
  const buffer = await blob.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}
