/**
 * THE canonical media contract.
 *
 * Every image/video anywhere in the app — a 24px icon, a filmstrip frame, a
 * gallery tile, a queue thumbnail, a fresh generation result, a vault item —
 * is described by exactly one shape: MediaDescriptor. Every action a user can
 * take on media is defined against that shape (see MediaActionsProvider), so a
 * user is NEVER stuck: whatever surface they reached an image from, they can
 * open it full-size, read its metadata, copy it, download it, delete it,
 * vault it, use it as an input, or remix it.
 *
 * Rule: no surface builds its own image UI, its own action, or its own
 * metadata dialog. It builds a descriptor and hands it to the canonical
 * components.
 */

import type {
  ImageGenJob,
  MediaLibraryItem,
  ToolImageData,
  ToolMediaArtifact,
  VideoGenJob,
} from "@/lib/api";
import type { GeneratedImageResult } from "@/hooks/use-media-gen";

/** Where the bytes actually live — decides which actions apply. */
export type MediaSource =
  /** Plaintext media library (~/.matrx/media/generated). */
  | "library"
  /** Encrypted Private vault. */
  | "vault"
  /** A completed image-queue job (its bytes ARE a library item). */
  | "job"
  /** A fresh one-shot generation result (may also be a library item). */
  | "result"
  /** A user-selected source image that has not been persisted by the engine. */
  | "input"
  /** A screenshot or other media value returned by a local tool. */
  | "tool";

export interface MediaDescriptor {
  /** Unique within its viewing set (library/vault item id, or job id). */
  id: string;
  kind: "image" | "video";
  /** blob:/data: URL ready for <img>/<video>. */
  url: string;
  /**
   * Media-library / vault item id — the id the ENGINE knows. Needed for
   * delete, vault move, remix and init-image fetch. Null for media the engine
   * did not persist (should not happen for generated media, but a descriptor
   * without one simply hides those actions rather than offering a dead click).
   */
  itemId: string | null;
  source: MediaSource;
  prompt?: string;
  negativePrompt?: string;
  seed?: number | null;
  modelId?: string;
  width?: number;
  height?: number;
  numFrames?: number | null | undefined;
  fps?: number | null | undefined;
  createdAt?: string;
  elapsedSeconds?: number;
  fileName?: string;
  /** Absolute on-disk path (plaintext library items only). */
  filePath?: string;
  fileSizeBytes?: number;
  /** The full resolved generation kwargs recorded by the engine. */
  params?: Record<string, unknown>;
  /**
   * True when this generation USED an img2img source image.
   *
   * Whether those bytes are still available is a separate question, answered
   * only by the engine (`GET /media-library/items/{id}/init-image`, which 404s
   * when it has none). Remix therefore ATTEMPTS the fetch whenever this is set
   * and tolerates a 404 — a descriptor built from a job or a fresh result knows
   * `has_init_image` from the recorded params but cannot know whether the file
   * was stored, and guessing "no" there is what made Remix silently drop the
   * input image on its most common entry point.
   */
  hasInitImage?: boolean;
  /**
   * The engine CONFIRMS it holds the source image (library/vault listings carry
   * `init_image_file`). Only these descriptors advertise "Remix restores the
   * input image" in the info dialog; job/result descriptors leave it undefined
   * and simply try.
   */
  initImageStored?: boolean;
}

/**
 * The identity used for viewer focus. Persisted media must anchor on the
 * engine's file id (`itemId`) even when the descriptor came from a queue job
 * whose display id is the job id.
 */
export function mediaFocusId(d: MediaDescriptor): string {
  return d.itemId ?? d.id;
}

export function mediaMatchesId(d: MediaDescriptor, id: string): boolean {
  return d.id === id || d.itemId === id;
}

export function findMediaIndexById(
  items: MediaDescriptor[],
  id: string | null | undefined,
): number {
  if (!id) return -1;
  return items.findIndex((d) => mediaMatchesId(d, id));
}

// ── Builders (the ONLY way a descriptor is constructed) ──────────────────────

export function descriptorFromToolArtifact(
  artifact: ToolMediaArtifact,
  localUrl: string,
): MediaDescriptor {
  return {
    id: artifact.artifact_id,
    kind: "image",
    url: localUrl,
    itemId: null,
    source: "tool",
    prompt: `${artifact.capture_source} screenshot`,
    width: artifact.source_width,
    height: artifact.source_height,
    fileName: artifact.file_name,
    fileSizeBytes: artifact.size_bytes,
    params: {
      artifact_id: artifact.artifact_id,
      availability: artifact.availability,
      checksum: artifact.checksum,
      ...(artifact.file_id ? { file_id: artifact.file_id } : {}),
      ...artifact.capture,
    },
  };
}

/** Adapter for legacy tool images; all presentation still uses MediaThumb. */
export function descriptorFromToolImage(
  image: ToolImageData,
  id: string,
): MediaDescriptor {
  return {
    id,
    kind: "image",
    url: `data:${image.media_type};base64,${image.base64_data}`,
    itemId: null,
    source: "tool",
    prompt: "Tool image",
    params: { media_type: image.media_type },
  };
}

/** A local file/blob selected as a generation input. */
export function descriptorFromInputImage(
  url: string,
  fileName: string,
): MediaDescriptor {
  return {
    id: `input:${fileName}:${url}`,
    kind: "image",
    url,
    itemId: null,
    source: "input",
    fileName,
    prompt: `Input image: ${fileName}`,
  };
}

export function descriptorFromLibraryItem(
  item: MediaLibraryItem,
  url: string,
  source: "library" | "vault" = "library",
): MediaDescriptor {
  return {
    id: item.id,
    kind: item.media_type,
    url,
    itemId: item.id,
    source,
    prompt: item.prompt,
    ...(item.negative_prompt ? { negativePrompt: item.negative_prompt } : {}),
    hasInitImage: !!item.init_image_file,
    initImageStored: !!item.init_image_file,
    seed: item.seed,
    modelId: item.model_id,
    width: item.width,
    height: item.height,
    numFrames: item.num_frames,
    fps: item.fps,
    createdAt: item.created_at,
    elapsedSeconds: item.elapsed_seconds,
    fileName: item.file_name,
    // A vaulted item has NO plaintext file — carrying the stale path would let
    // the info dialog print (and offer to copy) a path that no longer exists.
    ...(source === "vault" ? {} : { filePath: item.file_path }),
    fileSizeBytes: item.file_size_bytes,
    params: item.params,
  };
}

export function descriptorFromJob(
  job: ImageGenJob,
  url: string,
): MediaDescriptor {
  // The job record carries the resolved pipeline kwargs, so the negative
  // prompt and the dimensions are recoverable from there. Without this, a
  // Remix off a queue thumbnail silently WIPED the negative prompt (the
  // descriptor had none, and remix writes `negativePrompt ?? ""`).
  const p = job.params ?? {};
  const negative = p["negative_prompt"];
  const width = numParam(p, "width");
  const height = numParam(p, "height");
  return {
    id: job.job_id,
    kind: "image",
    url,
    itemId: job.item_id ?? null,
    source: "job",
    prompt: job.prompt,
    ...(typeof negative === "string" && negative ? { negativePrompt: negative } : {}),
    seed: typeof job.seed === "number" ? job.seed : null,
    modelId: job.model_id,
    ...(width !== null ? { width } : {}),
    ...(height !== null ? { height } : {}),
    ...(typeof job.elapsed_seconds === "number"
      ? { elapsedSeconds: job.elapsed_seconds }
      : {}),
    ...(job.file_path ? { filePath: job.file_path } : {}),
    ...(job.params ? { params: job.params } : {}),
    ...(p["has_init_image"] === true ? { hasInitImage: true } : {}),
  };
}

/**
 * A completed video job. `url` is the fetched mp4 object URL.
 *
 * The video surface used to render a bare <video> with a hand-rolled download
 * link and nothing else — no info, no delete, no vault, no right-click. A
 * generated video is media like any other, so it gets a descriptor like any
 * other.
 */
export function descriptorFromVideoJob(
  job: VideoGenJob,
  url: string,
): MediaDescriptor {
  return {
    id: job.job_id,
    kind: "video",
    url,
    itemId: job.item_id ?? null,
    source: "job",
    prompt: job.prompt,
    seed: typeof job.seed === "number" ? job.seed : null,
    modelId: job.model_id,
    ...(job.width ? { width: job.width } : {}),
    ...(job.height ? { height: job.height } : {}),
    ...(job.num_frames ? { numFrames: job.num_frames } : {}),
    ...(job.fps ? { fps: job.fps } : {}),
    ...(typeof job.elapsed_seconds === "number"
      ? { elapsedSeconds: job.elapsed_seconds }
      : {}),
  };
}

export function descriptorFromResult(
  result: GeneratedImageResult,
  opts: {
    prompt?: string;
    negativePrompt?: string;
    modelId?: string;
    params?: Record<string, unknown>;
  } = {},
): MediaDescriptor {
  const request = result.request;
  const requestParams: Record<string, unknown> = {
    ...(request.steps !== undefined
      ? { num_inference_steps: request.steps }
      : {}),
    ...(request.guidance !== undefined
      ? { guidance_scale: request.guidance }
      : {}),
    ...(request.width !== undefined ? { width: request.width } : {}),
    ...(request.height !== undefined ? { height: request.height } : {}),
    ...(request.strength !== undefined ? { strength: request.strength } : {}),
    ...(request.loras !== undefined ? { loras: request.loras } : {}),
    ...(request.extra_params ?? {}),
    ...(request.revision
      ? {
          revision_parent_item_id: request.revision.parent_item_id,
          revision_root_item_id:
            request.revision.root_item_id ?? request.revision.parent_item_id,
        }
      : {}),
    has_init_image: request.has_init_image,
  };
  return {
    id: result.itemId ?? "generated-result",
    kind: "image",
    url: `data:image/png;base64,${result.b64}`,
    itemId: result.itemId,
    source: "result",
    seed: result.seed,
    width: result.width,
    height: result.height,
    elapsedSeconds: result.elapsed,
    ...(result.filePath ? { filePath: result.filePath } : {}),
    prompt: opts.prompt ?? request.prompt,
    ...(opts.negativePrompt !== undefined || request.negative_prompt !== undefined
      ? { negativePrompt: opts.negativePrompt ?? request.negative_prompt }
      : {}),
    modelId: opts.modelId ?? request.model_id,
    params: opts.params ?? requestParams,
    ...((opts.params?.["has_init_image"] === true || request.has_init_image)
      ? { hasInitImage: true }
      : {}),
  };
}

// ── Formatting (one implementation, used by every surface) ───────────────────

export function formatBytes(bytes: number | undefined): string {
  if (!bytes || bytes <= 0) return "—";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function formatDate(iso: string | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Human title for a descriptor (menus, dialogs, download names). */
export function mediaTitle(d: MediaDescriptor): string {
  return d.prompt?.trim() || d.fileName || d.id;
}

/** A safe download filename derived from the prompt/file name/id. */
export function downloadName(d: MediaDescriptor): string {
  if (d.fileName) return d.fileName;
  const base =
    mediaTitle(d)
      .slice(0, 48)
      .replace(/[^a-zA-Z0-9-_ ]+/g, "")
      .replace(/\s+/g, "-")
      .replace(/^-+|-+$/g, "") || "matrx-media";
  return `${base}.${d.kind === "video" ? "mp4" : "png"}`;
}

/**
 * The parameter keys that have their OWN first-class row in the info panel.
 * They are excluded from the raw-params dump so the prompt is never re-printed
 * inside a JSON blob (which is what produced the ten-page-wide horizontal
 * scroll in the info dialog).
 */
const PROMOTED_PARAM_KEYS = new Set([
  "prompt",
  "negative_prompt",
  "width",
  "height",
  "num_inference_steps",
  "guidance_scale",
]);

/** Params minus everything already shown as its own labeled row. */
export function extraParams(
  d: MediaDescriptor,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(d.params ?? {})) {
    if (!PROMOTED_PARAM_KEYS.has(k)) out[k] = v;
  }
  return out;
}

/** Number-ish param read (the sidecar records whatever the pipeline got). */
export function numParam(
  params: Record<string, unknown> | undefined,
  key: string,
): number | null {
  const v = params?.[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

// ── Capabilities (a menu never shows an action that cannot run) ──────────────

export interface MediaCapabilities {
  canDownload: boolean;
  canCopyImage: boolean;
  canCopyPrompt: boolean;
  canDelete: boolean;
  canVault: boolean;
  canRestore: boolean;
  canUseAsInput: boolean;
  canRemix: boolean;
  canIterate: boolean;
  canShowInFolder: boolean;
  canReuseSeed: boolean;
}

/** Z-Image and FLUX are the intentionally supported local revision families. */
export function descriptorSupportsRevision(d: MediaDescriptor): boolean {
  if (d.kind !== "image" || !d.itemId || !d.modelId) return false;
  const pipeline = d.params?.["pipeline_type"];
  if (
    pipeline === "z-image" ||
    pipeline === "flux" ||
    pipeline === "flux2-klein"
  ) {
    return true;
  }
  const modelId = d.modelId.toLowerCase();
  return modelId.includes("z-image") || modelId.includes("flux");
}

export function capabilitiesOf(d: MediaDescriptor): MediaCapabilities {
  const persisted = d.itemId !== null;
  return {
    canDownload: true,
    canCopyImage: d.kind === "image",
    canCopyPrompt: !!d.prompt?.trim(),
    canDelete: persisted,
    canVault: persisted && d.source !== "vault",
    canRestore: d.source === "vault",
    canUseAsInput: d.kind === "image",
    canRemix: persisted && !!d.modelId,
    canIterate: descriptorSupportsRevision(d),
    // Vaulted items have no plaintext file on disk.
    canShowInFolder: !!d.filePath && d.source !== "vault",
    canReuseSeed: typeof d.seed === "number",
  };
}
