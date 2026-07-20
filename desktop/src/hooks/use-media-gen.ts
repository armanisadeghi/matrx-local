/**
 * useMediaGen — single source of truth for the media-generation experience
 * (image + video).  Lives behind `MediaGenProvider` (App.tsx) so that a
 * running video job, the image job queue, any generated results AND the whole
 * generate-form state (prompt, params, sub-tab) SURVIVE tab switches and page
 * navigation.  A 10-minute video job must never be orphaned — and a
 * half-written prompt must never be lost — by navigating away.
 *
 * React rules obeyed strictly (see repo CLAUDE.md → React Patterns):
 *  - `actions` is wrapped in useMemo and its callbacks are stable (useCallback).
 *  - Init fetches live here in the hook, on [] deps — never in a page effect.
 *  - Poll intervals (video job, image job queue) are gated on the SPECIFIC
 *    booleans being watched, always clean up, and never restart on unrelated
 *    re-renders.
 *  - No focus/visibility re-initialization.
 *
 * Settings doctrine: EVERY parameter the engine accepts is exposed.  The
 * params endpoints (`/image-gen/params/{id}`, `/video-gen/params/{id}`)
 * provide the common defaults plus every advanced pipeline kwarg; failures
 * are surfaced LOUDLY (paramsError state) — never silently defaulted.
 */

import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import {
  engine,
  getImageGenStatus,
  listImageGenModels,
  listImageGenPresets,
  loadImageGenModel as apiLoadImageGenModel,
  unloadImageGenModel as apiUnloadImageGenModel,
  downloadImageGenModel as apiDownloadImageGenModel,
  downloadImageGenTextEncoder as apiDownloadImageGenTextEncoder,
  generateImage as apiGenerateImage,
  generateImageFromWorkflow as apiGenerateImageWorkflow,
  enqueueImageGenJob as apiEnqueueImageGenJob,
  listImageGenJobs as apiListImageGenJobs,
  cancelImageGenJob as apiCancelImageGenJob,
  enqueueImageGenBatch as apiEnqueueImageGenBatch,
  listImageGenBatches as apiListImageGenBatches,
  cancelImageGenBatch as apiCancelImageGenBatch,
  getImageGenQueueState as apiGetImageGenQueueState,
  setImageGenQueuePaused as apiSetImageGenQueuePaused,
  reorderImageGenQueue as apiReorderImageGenQueue,
  retryImageGenJob as apiRetryImageGenJob,
  clearFinishedImageGenJobs as apiClearFinishedImageGenJobs,
  cancelImageGeneration as apiCancelImageGeneration,
  cancelVideoGenJob as apiCancelVideoGenJob,
  fetchMediaLibraryThumb as apiFetchMediaLibraryThumb,
  MediaFileError,
  getVideoGenStatus,
  listVideoGenModels,
  listVideoGenJobs,
  loadVideoGenModel as apiLoadVideoGenModel,
  unloadVideoGenModel as apiUnloadVideoGenModel,
  downloadVideoGenModel as apiDownloadVideoGenModel,
  generateVideo as apiGenerateVideo,
  getVideoGenJob as apiGetVideoGenJob,
  fetchVideoGenResult as apiFetchVideoGenResult,
  getImageGenParams as apiGetImageGenParams,
  getVideoGenParams as apiGetVideoGenParams,
  listImageGenLoras as apiListImageGenLoras,
  downloadImageGenLora as apiDownloadImageGenLora,
  deleteImageGenLora as apiDeleteImageGenLora,
  inspectCustomImageModel as apiInspectCustomImageModel,
  registerCustomImageModel as apiRegisterCustomImageModel,
  deleteCustomImageModel as apiDeleteCustomImageModel,
  classifyLoraRef,
  MediaGenHttpError,
  getMediaRuntimeStatus,
  ensureMediaRuntime as apiEnsureMediaRuntime,
  repairMediaRuntime as apiRepairMediaRuntime,
  streamMediaRuntimeStatus,
} from "@/lib/api";
import type {
  ImageGenStatus,
  ImageGenModelInfo,
  ImageGenWorkflowPreset,
  ImageGenJob,
  ImageGenBatch,
  ImageGenBatchJobSpec,
  ImageGenQueueState,
  ImageGenLoraList,
  MediaGenParams,
  MediaLoadResult,
  VideoGenStatus,
  VideoGenModelInfo,
  VideoGenJob,
  VideoGenRequest,
  CustomImageModelEntry,
  CustomImageModelInspectResult,
  MediaRuntimeStatus,
} from "@/lib/api";
import { emitClientLog } from "@/hooks/use-unified-log";
import { VAULT_UNLOCKED_EVENT } from "@/hooks/use-media-vault";
import { onMediaItemsRemoved, onVaultLocked } from "@/lib/media-events";
import {
  acceptsRuntimeSnapshot,
  isRuntimeActive,
  isRuntimeReady,
} from "@/lib/image-gen/runtime-state";
import { disableIncompatibleLoraSelections } from "@/lib/image-gen/lora-compatibility";
import { isTauri, restartSidecar } from "@/lib/sidecar";

const ENGINE_NOT_CONNECTED = "Engine not connected";
// User-facing message for ACTION paths (download/load/generate/…) that cannot
// run because the engine URL is null. Distinct from ENGINE_NOT_CONNECTED, which
// is the status-error sentinel the reconnect effect matches on — do NOT reuse
// that here or you would trip the retry loop for a one-shot action failure.
const ENGINE_NOT_CONNECTED_ACTION =
  "Engine not connected — check the engine status and try again";
const MEDIA_RUNTIME_NOT_READY =
  "The managed AI runtime is not ready. Complete installation or repair, then try again.";

// Explicit, visible fallbacks used ONLY when the params endpoint fails for a
// video model (the video catalog carries no recommended steps/guidance).  The
// UI shows a loud banner whenever these are in effect.
const VIDEO_FALLBACK_STEPS = 30;
const VIDEO_FALLBACK_GUIDANCE = 5;

/**
 * Log an action that was blocked because the engine URL is null. console.error
 * survives an engine outage; emitClientLog does NOT (it needs the engine), so a
 * silent no-op here is exactly the invisible failure we are killing.
 */
function logEngineNotConnected(action: string): void {
  console.error(
    `[media-gen] ${action} blocked — engine not connected (engine.engineUrl is null)`,
  );
}

/**
 * Map a 404 from the custom-model routes (engine build without them) to a
 * clear user-facing message; pass every other failure through unchanged.
 */
function customModelUnsupported(e: unknown): Error {
  if (e instanceof MediaGenHttpError && e.status === 404) {
    return new Error(
      "Custom model support is not available on this engine build yet (the /image-gen/custom-models endpoint was not found). Update Matrx Local, then try again.",
    );
  }
  return e instanceof Error ? e : new Error(String(e));
}

export interface ImageRevisionRequest {
  parent_item_id: string;
  root_item_id?: string;
}

export interface ImageGenerateInput {
  prompt: string;
  model_id: string;
  negative_prompt?: string;
  steps?: number;
  guidance?: number;
  width?: number;
  height?: number;
  seed?: number;
  /** Base64-encoded input image (no data: prefix) for img2img. */
  init_image_b64?: string;
  /** img2img denoise strength (0..1) — how much to change the input. */
  strength?: number;
  /** Durable lineage for a user-applied revision. */
  revision?: ImageRevisionRequest;
  /** Enabled LoRA adapters with their scales. */
  loras?: { id: string; scale: number }[];
  /** Optional model-compatible replacement text encoder. */
  text_encoder_id?: string;
  /**
   * Extra diffusers pipeline kwargs (advanced settings) merged into the call.
   * Only CHANGED keys should be sent — the UI diffs against the defaults from
   * the params endpoint before building this object.
   */
  extra_params?: Record<string, unknown>;
}

/** Immutable request metadata kept with a result (without duplicate image bytes). */
export type GeneratedImageRequest = Omit<ImageGenerateInput, "init_image_b64"> & {
  has_init_image: boolean;
};

export interface GeneratedImageResult {
  /** Base64-encoded PNG (no data: prefix). */
  b64: string;
  elapsed: number;
  width: number;
  height: number;
  /** The concrete seed used (engine-reported; falls back to the sent seed). */
  seed: number | null;
  /** Media-library item id, when the engine persisted the result. */
  itemId: string | null;
  /** On-disk path of the saved image, when the engine persisted the result. */
  filePath: string | null;
  /** Exact request metadata that produced this result, immune to later form edits. */
  request: GeneratedImageRequest;
}

export interface ImageWorkflowInput {
  preset_id: string;
  subject: string;
  model_id?: string;
  seed?: number;
}

// ── Persistent generate-form state ──────────────────────────────────────────

/** Sub-tab inside the Images / Video sections. */
export type MediaGenView = "generate" | "models";

/** The resolved per-model defaults every "reset" affordance goes back to. */
export interface ImageFormDefaults {
  modelId: string;
  steps: number;
  guidance: number;
  width: number;
  height: number;
  negativePrompt: string;
  /** Every remaining pipeline kwarg with its default (params endpoint). */
  advanced: Record<string, unknown>;
  supportsNegativePrompt: boolean;
  /** True when the model accepts an input image (img2img). */
  supportsImg2Img: boolean;
  /** Model-default img2img strength (params endpoint), or null when unknown. */
  strength: number | null;
}

/** An in-memory picked image (img2img input / video source image). */
export interface PickedImage {
  name: string;
  /** Base64 without the data: prefix — exactly what the engine expects. */
  base64: string;
  /** data: or blob: URL for the preview thumbnail. */
  previewUrl: string;
}

/** A LoRA selected in the generate form. Only ENABLED entries are sent. */
export interface SelectedLora {
  id: string;
  scale: number;
  enabled: boolean;
}

/** Fallback img2img strength when the params endpoint carries no default. */
export const IMG2IMG_DEFAULT_STRENGTH = 0.6;

/**
 * Outcome of a batch enqueue. All-or-nothing on purpose: the engine validates
 * every run before it queues any, so a rejected batch leaves the queue exactly
 * as it was and the caller gets the reason to show.
 */
export type EnqueueBatchResult =
  | { ok: true; batchId: string; count: number }
  | { ok: false; error: string };

export interface ImageFormState {
  view: MediaGenView;
  prompt: string;
  negativePrompt: string;
  steps: number;
  guidance: number;
  width: number;
  height: number;
  seedText: string;
  /** Editable JSON of the advanced pipeline kwargs. */
  advancedText: string;
  paramsLoading: boolean;
  /** LOUD params-endpoint failure — banner in the UI, never silent. */
  paramsError: string | null;
  defaults: ImageFormDefaults | null;
  /** img2img input image (only sent when the model supports it). */
  initImage: PickedImage | null;
  /** img2img denoise strength (0..1). */
  strength: number;
  /** LoRA selections (persist across model switches; mismatches are warned). */
  loras: SelectedLora[];
  /** null = the stock encoder bundled with the selected model. */
  textEncoderId: string | null;
  /** Active button-driven revision branch, or null for normal generation. */
  revision: {
    parentItemId: string;
    rootItemId: string;
  } | null;
}

export interface VideoFormDefaults extends Omit<
  ImageFormDefaults,
  "supportsImg2Img" | "strength"
> {
  numFrames: number;
  fps: number;
}

export interface VideoFormState extends Omit<
  ImageFormState,
  | "defaults"
  | "initImage"
  | "strength"
  | "loras"
  | "textEncoderId"
  | "revision"
> {
  numFrames: number;
  fps: number;
  sourceImage: PickedImage | null;
  defaults: VideoFormDefaults | null;
}

const INITIAL_IMAGE_FORM: ImageFormState = {
  view: "models",
  prompt: "",
  negativePrompt: "",
  steps: 20,
  guidance: 7,
  width: 1024,
  height: 1024,
  seedText: "",
  advancedText: "",
  paramsLoading: false,
  paramsError: null,
  defaults: null,
  initImage: null,
  strength: IMG2IMG_DEFAULT_STRENGTH,
  loras: [],
  textEncoderId: null,
  revision: null,
};

const INITIAL_VIDEO_FORM: VideoFormState = {
  view: "models",
  prompt: "",
  negativePrompt: "",
  steps: VIDEO_FALLBACK_STEPS,
  guidance: VIDEO_FALLBACK_GUIDANCE,
  width: 832,
  height: 480,
  numFrames: 48,
  fps: 16,
  seedText: "",
  advancedText: "",
  paramsLoading: false,
  paramsError: null,
  sourceImage: null,
  defaults: null,
};

function advancedJsonOf(advanced: Record<string, unknown>): string {
  return Object.keys(advanced).length > 0
    ? JSON.stringify(advanced, null, 2)
    : "";
}

// ── Remix ────────────────────────────────────────────────────────────────────

/**
 * The recorded generation of a past image — everything the engine's sidecar
 * kept. Feeding this to `remixImageForm` rebuilds the form into exactly the
 * state that produced it.
 */
export interface ImageRemixRecord {
  prompt?: string;
  negativePrompt?: string;
  seed?: number | null;
  width?: number;
  height?: number;
  /** The FULL resolved pipeline kwargs the engine recorded. */
  params?: Record<string, unknown>;
}

/**
 * Params the form models with a dedicated control. They are applied to those
 * controls and MUST NOT also land in the advanced-JSON editor — sending a key
 * as both a common param and an extra_param is a double-send the engine
 * rejects.
 */
const FORM_OWNED_PARAM_KEYS = new Set([
  "prompt",
  "negative_prompt",
  "width",
  "height",
  "num_inference_steps",
  "guidance_scale",
  "strength",
  "loras",
  "text_encoder_id",
  "has_init_image",
  "init_image_sha256",
]);

function numParamOf(
  params: Record<string, unknown> | undefined,
  key: string,
): number | null {
  const v = params?.[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/**
 * True when a recorded param value is NOT safe to replay.
 *
 * The engine's sidecar records every kwarg it passed to the pipeline, and
 * `json_sanitize` turns anything non-JSON (a torch dtype, a scheduler object)
 * into its Python `repr` — e.g. `"<class 'torch.float16'>"`. Feeding that
 * string back as an extra_param would 400 the generation. A remix keeps the
 * model's real default for those keys instead of replaying a repr.
 */
function isUnreplayableValue(v: unknown): boolean {
  return typeof v === "string" && /^<.*>$/.test(v.trim());
}

/** The LoRA selections recorded on a past generation (all enabled). */
function recordedLoras(
  params: Record<string, unknown> | undefined,
): SelectedLora[] {
  const raw = params?.["loras"];
  if (!Array.isArray(raw)) return [];
  const out: SelectedLora[] = [];
  for (const entry of raw) {
    if (entry && typeof entry === "object") {
      const rec = entry as Record<string, unknown>;
      const id = rec["id"];
      if (typeof id === "string") {
        const scale = rec["scale"];
        out.push({
          id,
          scale: typeof scale === "number" ? scale : 1,
          enabled: true,
        });
      }
    }
  }
  return out;
}

function recordedTextEncoder(
  params: Record<string, unknown> | undefined,
): string | null {
  const value = params?.["text_encoder_id"];
  return typeof value === "string" && value.trim() ? value : null;
}

export interface MediaGenState {
  // ── Shared managed runtime ───────────────────────────────────────────────
  /** Authoritative server-owned lifecycle shared by image and video. */
  mediaRuntime: MediaRuntimeStatus | null;
  mediaRuntimeLoading: boolean;
  mediaRuntimeError: string | null;

  // ── Image ──────────────────────────────────────────────────────────────
  imageStatus: ImageGenStatus | null;
  imageModels: ImageGenModelInfo[];
  imagePresets: ImageGenWorkflowPreset[];
  imageStatusLoading: boolean;
  imageStatusError: string | null;
  /**
   * True while ANY image model load is in flight. Prefer
   * `loadingImageModelId === model.model_id` for per-card spinners — a shared
   * boolean spun every downloaded card's Load button (media-gen bug, 2026-07-09).
   */
  imageModelLoading: boolean;
  /** Model id currently being loaded into memory, or null. */
  loadingImageModelId: string | null;
  imageGenerating: boolean;
  /** True while a one-shot image cancel request is settling. */
  imageCancelling: boolean;
  /** Epoch ms when the in-flight one-shot image generation started. */
  imageGenStartedAt: number | null;
  /** Epoch ms when the in-flight image model load started (elapsed readout). */
  imageLoadStartedAt: number | null;
  imageGenError: string | null;
  /**
   * Informational (non-error) notice set when a Generate click was
   * transparently redirected into the job queue because another generation
   * was already running ("queued as next"). Dismissible; cleared
   * automatically when a fresh one-shot actually starts.
   */
  imageQueueNotice: string | null;
  imageResult: GeneratedImageResult | null;
  /** The model the user is currently working with (survives tab switches). */
  selectedImageModelId: string | null;
  /** Persistent generate-form state (survives navigation). */
  imageForm: ImageFormState;
  /** Pending queue followed by terminal history in exact completion order. */
  imageJobs: ImageGenJob[];
  imageJobsError: string | null;
  /** True when an older terminal-history page can be requested. */
  canLoadMoreImageHistory: boolean;
  /**
   * Whether the queue is draining, and how much is left. Null before the first
   * fetch. `paused` is the master switch for unattended runs: the running job
   * finishes, nothing new starts.
   */
  imageQueueState: ImageGenQueueState | null;
  /** Per-batch roll-up (a prompt-matrix sweep is ONE row), newest first. */
  imageBatches: ImageGenBatch[];
  /** jobId → object URL of a completed job's image (thumbnails). */
  imageJobThumbs: Record<string, string>;
  /** Installed + catalog LoRA adapters, or null before the first fetch. */
  loraList: ImageGenLoraList | null;
  /**
   * LOUD LoRA-endpoint failure (404 until the backend lands, engine down…).
   * The Styles (LoRA) section renders this — never a silent empty list.
   */
  loraError: string | null;
  /** repo_id → DownloadManager download id for in-flight LoRA downloads. */
  loraDownloads: Record<string, string>;
  /**
   * True when the last LoRA download failed with 401 on a Civitai reference —
   * the UI shows a "Set your Civitai API key" affordance (Settings → API
   * Keys).  Cleared on the next download attempt / success.
   */
  loraNeedsCivitaiKey: boolean;

  // ── Video ──────────────────────────────────────────────────────────────
  videoStatus: VideoGenStatus | null;
  videoModels: VideoGenModelInfo[];
  videoStatusLoading: boolean;
  videoStatusError: string | null;
  /** True while ANY video model load is in flight. Prefer `loadingVideoModelId`. */
  videoModelLoading: boolean;
  /** Model id currently being loaded into memory, or null. */
  loadingVideoModelId: string | null;
  videoGenerating: boolean;
  /** True while a video cancel request is settling / being honored. */
  videoCancelling: boolean;
  /** Epoch ms when the in-flight video model load started (elapsed readout). */
  videoLoadStartedAt: number | null;
  videoGenError: string | null;
  /** The job currently being watched (running, or the most recent one). */
  activeJob: VideoGenJob | null;
  /** Recent jobs, newest first. */
  jobs: VideoGenJob[];
  /** jobId → object URL of the fetched mp4 (survives tab switches). */
  videoResults: Record<string, string>;
  /** Persistent generate-form state (survives navigation). */
  videoForm: VideoFormState;
}

export interface MediaGenActions {
  // Shared managed runtime
  refreshMediaRuntime: () => Promise<void>;
  ensureMediaRuntime: () => Promise<void>;
  repairMediaRuntime: () => Promise<void>;
  restartMediaRuntime: () => Promise<void>;
  // Image
  refreshImage: () => Promise<void>;
  /** The latest fetched image model catalog (see getImageModels impl). */
  getImageModels: () => ImageGenModelInfo[];
  loadImageModel: (modelId: string) => Promise<MediaLoadResult>;
  unloadImageModel: () => Promise<void>;
  downloadImageModel: (modelId: string) => Promise<boolean>;
  /** Start the persistent on-demand download for a model-compatible encoder. */
  downloadTextEncoder: (
    modelId: string,
    textEncoderId: string,
  ) => Promise<boolean>;
  /** Resolves true only when a result was produced; false on any failure. */
  generateImage: (input: ImageGenerateInput) => Promise<boolean>;
  /** Resolves true only when a result was produced; false on any failure. */
  generateImageWorkflow: (input: ImageWorkflowInput) => Promise<boolean>;
  /**
   * Cancel the in-flight one-shot image generation (regular or workflow).
   * ALWAYS resolves the local awaiting state — even when the engine's cancel
   * endpoint fails — so the UI can never be stuck on a spinner.
   */
  cancelImageGeneration: () => Promise<void>;
  setSelectedImageModelId: (modelId: string | null) => void;
  clearImageResult: () => void;
  clearImageGenError: () => void;
  /** Dismiss the "queued as next" informational notice. */
  clearImageQueueNotice: () => void;
  /** Patch the persistent image generate-form state. */
  setImageForm: (patch: Partial<ImageFormState>) => void;
  /**
   * Enter the generate view for a model: selects it, fetches its FULL
   * parameter schema and resets the form to the model's defaults (the prompt
   * text is preserved).  On params failure the catalog fallbacks are applied
   * and `imageForm.paramsError` is set — loud, never silent.
   */
  prepareImageGenerate: (model: ImageGenModelInfo) => Promise<void>;
  /**
   * REMIX: rebuild the image form into exactly the state that produced a past
   * generation — prompt, negative prompt, seed, size, steps, guidance, img2img
   * strength, LoRAs and every advanced pipeline kwarg the engine recorded.
   *
   * Call it AFTER `prepareImageGenerate(model)` has resolved (that fetches the
   * model's parameter schema); this patch is applied on top of those defaults,
   * so a parameter the model no longer accepts falls back to its default
   * instead of poisoning the request. The input image is restored separately by
   * the caller (its bytes come from the engine, not the sidecar).
   */
  remixImageForm: (record: ImageRemixRecord) => void;
  /** Reset the common controls to the current model's defaults. */
  resetImageCommon: () => void;
  /** Reset the advanced JSON to the current model's defaults. */
  resetImageAdvanced: () => void;
  /** Master reset: common + advanced (prompt text preserved). */
  resetImageAll: () => void;
  // Image queue
  refreshImageJobs: () => Promise<void>;
  /** Fetch the next bounded page of older terminal history. */
  loadMoreImageHistory: () => Promise<void>;
  /**
   * Enqueue a job (same body as generate). True when accepted.
   * `priority: "next"` puts the job at the FRONT of the pending queue.
   */
  enqueueImageJob: (
    input: ImageGenerateInput,
    priority?: "normal" | "next",
  ) => Promise<boolean>;
  /** Cancel a queued job / remove a finished one. */
  cancelImageJob: (jobId: string) => Promise<void>;
  // Batches (prompt matrix) + queue control
  /**
   * Enqueue N runs as ONE batch — what the prompt matrix submits.
   * The engine validates EVERY run before queueing any, so this either queues
   * the whole sweep or none of it (`ok: false` with the reason).
   */
  enqueueImageBatch: (
    jobs: ImageGenBatchJobSpec[],
    label?: string,
  ) => Promise<EnqueueBatchResult>;
  /** Re-fetch the paused flag + per-batch roll-ups. */
  refreshImageQueue: () => Promise<void>;
  /** Pause/resume the queue. The RUNNING job always finishes. */
  setImageQueuePaused: (paused: boolean) => Promise<void>;
  /** Reorder the QUEUED jobs (drag-and-drop); the running job never moves. */
  reorderImageQueue: (jobIds: string[]) => Promise<void>;
  /** Requeue a failed/cancelled job with a fresh attempt budget. */
  retryImageJob: (jobId: string) => Promise<void>;
  /** Cancel every unfinished job of a batch. Finished images are kept. */
  cancelImageBatch: (batchId: string) => Promise<void>;
  /** Drop finished job records. Media-library items are NOT deleted. */
  clearFinishedImageJobs: () => Promise<void>;
  // LoRA adapters
  /** Re-fetch installed + catalog LoRAs. Failures land in loraError (loud). */
  refreshLoras: () => Promise<void>;
  /**
   * Start a LoRA download.  `ref` may be a catalog repo id, a pasted HF repo
   * id/URL, or a Civitai link / numeric id (classified automatically unless
   * `weightName` is given, which pins it as an HF repo).  Returns the
   * DownloadManager download id, or null on failure (loraError is set; a 401
   * on a Civitai ref also sets loraNeedsCivitaiKey).
   */
  downloadLora: (ref: string, weightName?: string) => Promise<string | null>;
  /** Delete an installed LoRA (also drops it from the form selection). */
  deleteLora: (loraId: string) => Promise<void>;
  // Custom models (Hugging Face / Civitai checkpoints)
  /**
   * Resolve a pasted HF repo / Civitai link into a proposed model entry with
   * warnings + the registerable gate.  THROWS with a user-presentable
   * message on failure (400 with the engine's reason, 404 when the engine
   * build lacks custom-model support) — callers render it verbatim.
   */
  inspectCustomModel: (ref: string) => Promise<CustomImageModelInspectResult>;
  /**
   * Register a confirmed custom model: the engine queues the weights
   * download (standard DownloadManager progress) and the model list is
   * refreshed so the entry appears in the picker.  THROWS on failure.
   */
  registerCustomModel: (entry: CustomImageModelEntry) => Promise<void>;
  /** Unregister a custom model and refresh the model list.  THROWS on failure. */
  deleteCustomModel: (modelId: string) => Promise<void>;
  /**
   * Route an image into the img2img input slot (the "Use as input" action on
   * lightbox items, library cards and result views).
   */
  useImageAsInput: (image: PickedImage) => void;
  /** Start a durable, button-driven revision branch from a persisted image. */
  beginImageRevision: (
    image: PickedImage,
    parentItemId: string,
    rootItemId?: string,
  ) => void;
  /** Leave revision mode and remove its pinned parent image. */
  endImageRevision: () => void;
  // Video
  refreshVideo: () => Promise<void>;
  loadVideoModel: (modelId: string) => Promise<MediaLoadResult>;
  unloadVideoModel: () => Promise<void>;
  downloadVideoModel: (modelId: string) => Promise<boolean>;
  generateVideo: (
    req: VideoGenRequest,
  ) => Promise<{ ok: boolean; error?: string }>;
  /** Cancel the active video generation (the watched queued/running job). */
  cancelVideoGeneration: () => Promise<void>;
  /** Cancel a specific queued/running video job. */
  cancelVideoJob: (jobId: string) => Promise<void>;
  fetchVideoResult: (jobId: string) => Promise<string | null>;
  clearActiveJob: () => void;
  clearVideoGenError: () => void;
  /** Patch the persistent video generate-form state. */
  setVideoForm: (patch: Partial<VideoFormState>) => void;
  /** Video twin of prepareImageGenerate. */
  prepareVideoGenerate: (model: VideoGenModelInfo) => Promise<void>;
  resetVideoCommon: () => void;
  resetVideoAdvanced: () => void;
  resetVideoAll: () => void;
  // Shared
  /**
   * Fetch the full parameter schema (common defaults + advanced pipeline
   * kwargs) for a model.  THROWS on failure (404, engine down, …) with a
   * user-presentable message — callers must catch and surface it visibly.
   * Never silently returns a default schema.
   */
  fetchParams: (
    modelId: string,
    kind: "image" | "video",
  ) => Promise<MediaGenParams>;
}

export function useMediaGen(): [MediaGenState, MediaGenActions] {
  // ── Shared managed runtime state ─────────────────────────────────────────
  const [mediaRuntime, setMediaRuntime] = useState<MediaRuntimeStatus | null>(null);
  const [mediaRuntimeLoading, setMediaRuntimeLoading] = useState(true);
  const [mediaRuntimeError, setMediaRuntimeError] = useState<string | null>(null);

  // ── Image state ──────────────────────────────────────────────────────────
  const [imageStatus, setImageStatus] = useState<ImageGenStatus | null>(null);
  const [imageModels, setImageModels] = useState<ImageGenModelInfo[]>([]);
  const [imagePresets, setImagePresets] = useState<ImageGenWorkflowPreset[]>(
    [],
  );
  const [imageStatusLoading, setImageStatusLoading] = useState(true);
  const [imageStatusError, setImageStatusError] = useState<string | null>(null);
  const [loadingImageModelId, setLoadingImageModelId] = useState<string | null>(
    null,
  );
  const [imageGenerating, setImageGenerating] = useState(false);
  const [imageCancelling, setImageCancelling] = useState(false);
  const [imageGenStartedAt, setImageGenStartedAt] = useState<number | null>(
    null,
  );
  const [imageLoadStartedAt, setImageLoadStartedAt] = useState<number | null>(
    null,
  );
  const [imageGenError, setImageGenError] = useState<string | null>(null);
  const [imageQueueNotice, setImageQueueNotice] = useState<string | null>(null);
  const [imageResult, setImageResult] = useState<GeneratedImageResult | null>(
    null,
  );
  const [selectedImageModelId, setSelectedImageModelIdState] = useState<
    string | null
  >(null);
  const [imageForm, setImageFormState] =
    useState<ImageFormState>(INITIAL_IMAGE_FORM);
  const [imageJobs, setImageJobs] = useState<ImageGenJob[]>([]);
  const [imageJobsError, setImageJobsError] = useState<string | null>(null);
  const [imageHistoryLimit, setImageHistoryLimit] = useState(50);
  const [imageQueueState, setImageQueueState] =
    useState<ImageGenQueueState | null>(null);
  const [imageBatches, setImageBatches] = useState<ImageGenBatch[]>([]);
  const [imageJobThumbs, setImageJobThumbs] = useState<Record<string, string>>(
    {},
  );
  /** Bumped when a vault unlock makes previously-locked thumbnails readable —
   * the only thing that re-arms the thumbnail fetch effect. */
  const [thumbRetryNonce, setThumbRetryNonce] = useState(0);
  const [loraList, setLoraList] = useState<ImageGenLoraList | null>(null);
  const [loraError, setLoraError] = useState<string | null>(null);
  const [loraDownloads, setLoraDownloads] = useState<Record<string, string>>(
    {},
  );
  const [loraNeedsCivitaiKey, setLoraNeedsCivitaiKey] = useState(false);

  // ── Video state ──────────────────────────────────────────────────────────
  const [videoStatus, setVideoStatus] = useState<VideoGenStatus | null>(null);
  const [videoModels, setVideoModels] = useState<VideoGenModelInfo[]>([]);
  const [videoStatusLoading, setVideoStatusLoading] = useState(true);
  const [videoStatusError, setVideoStatusError] = useState<string | null>(null);
  const [loadingVideoModelId, setLoadingVideoModelId] = useState<string | null>(
    null,
  );
  const [videoGenerating, setVideoGenerating] = useState(false);
  const [videoCancelling, setVideoCancelling] = useState(false);
  const [videoLoadStartedAt, setVideoLoadStartedAt] = useState<number | null>(
    null,
  );
  const [videoGenError, setVideoGenError] = useState<string | null>(null);
  const [activeJob, setActiveJob] = useState<VideoGenJob | null>(null);
  const [jobs, setJobs] = useState<VideoGenJob[]>([]);
  const [videoResults, setVideoResults] = useState<Record<string, string>>({});
  const [videoForm, setVideoFormState] =
    useState<VideoFormState>(INITIAL_VIDEO_FORM);

  // Refs to read latest values inside stable callbacks without stale closures.
  const activeJobRef = useRef<VideoGenJob | null>(null);
  const videoResultsRef = useRef<Record<string, string>>({});
  const imageJobThumbsRef = useRef<Record<string, string>>({});
  // Job ids whose thumbnail could not be fetched, and why. "pending" = in
  // flight, "locked" = in the locked vault (retry after unlock), "gone" =
  // permanently unfetchable (never retry). This is the negative cache that
  // stops a dead id from being re-requested on every single render/mount.
  const imageJobThumbFailuresRef = useRef<
    Record<string, "pending" | "locked" | "gone">
  >({});
  // Job ids whose thumbnail bytes were served out of the (unlocked) vault —
  // i.e. the fetch previously 423'd and only succeeded after an unlock. These
  // are the decrypted images that must disappear the moment the vault locks.
  const vaultBackedJobsRef = useRef<Set<string>>(new Set());
  // Job ids whose thumbnail fetch has EVER been refused with a 423 — the marker
  // that the item lives in the vault, not the plaintext library.
  const imageJobThumbWasLockedRef = useRef<Set<string>>(new Set());
  const refreshVideoRef = useRef<(() => Promise<void>) | null>(null);
  // Consecutive job-poll failures — after 3 (or an immediate 404) we synthesize
  // a terminal "failed" state so the poll interval tears down and the UI
  // unwedges instead of spinning forever against a dead/restarted engine.
  const pollFailuresRef = useRef(0);
  // One-shot image generation run id.  cancelImageGeneration bumps it to
  // force-resolve the local awaiting state; the original await then sees a
  // stale id and must NOT touch state (a newer run may already be in flight).
  const imageRunRef = useRef(0);
  // True while a one-shot image request is awaited — read inside the stable
  // generate callbacks (state would be a stale closure there).  Together with
  // imageJobsRef this drives the queue-first decision: Generate while ANY
  // image generation is queued/running/in flight becomes an enqueue-as-next,
  // never a concurrent one-shot against the engine.
  const imageOneShotInFlightRef = useRef(false);
  const imageJobsRef = useRef<ImageGenJob[]>([]);
  // A slow polling response must never overwrite newer state from an enqueue,
  // cancellation, or a later poll.  The server supplies the authoritative
  // ordering; this guards client delivery order.
  const imageJobsRefreshRef = useRef(0);
  /** Prevent a slow older queue poll from overwriting a newer mutation refresh. */
  const imageQueueRefreshRef = useRef(0);
  const imageHistoryLimitRef = useRef(50);
  const imageModelsRef = useRef<ImageGenModelInfo[]>([]);
  const imagePresetsRef = useRef<ImageGenWorkflowPreset[]>([]);
  const loraListRef = useRef<ImageGenLoraList | null>(null);
  // Video enqueue run id — guards the brief "Starting…" phase so a cancel
  // issued before the job id exists doesn't get resurrected by a late 202.
  const videoRunRef = useRef(0);
  const mediaRuntimeRef = useRef<MediaRuntimeStatus | null>(null);
  const runtimeExpectedAttemptRef = useRef<string | null>(null);
  const runtimeRequestRef = useRef(0);
  const runtimeStreamCleanupRef = useRef<(() => void) | null>(null);
  const runtimePollRef = useRef<number | null>(null);
  useEffect(() => {
    activeJobRef.current = activeJob;
  }, [activeJob]);
  useEffect(() => {
    videoResultsRef.current = videoResults;
  }, [videoResults]);
  useEffect(() => {
    imageJobThumbsRef.current = imageJobThumbs;
  }, [imageJobThumbs]);
  useEffect(() => {
    imageJobsRef.current = imageJobs;
  }, [imageJobs]);
  useEffect(() => {
    imagePresetsRef.current = imagePresets;
  }, [imagePresets]);
  useEffect(() => {
    mediaRuntimeRef.current = mediaRuntime;
  }, [mediaRuntime]);

  // ── Shared actions ────────────────────────────────────────────────────────
  const fetchParams = useCallback(
    async (
      modelId: string,
      kind: "image" | "video",
    ): Promise<MediaGenParams> => {
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected(`fetch ${kind} generation params`);
        throw new Error(ENGINE_NOT_CONNECTED_ACTION);
      }
      try {
        return kind === "image"
          ? await apiGetImageGenParams(base, modelId)
          : await apiGetVideoGenParams(base, modelId);
      } catch (e) {
        // LOUD: log for diagnostics AND rethrow so the caller must render the
        // failure. Falling back to a silent default schema is forbidden — the
        // user must know they are not seeing this model's real parameter set.
        emitClientLog(
          "error",
          `[media-gen] params fetch failed for ${kind} model ${modelId}: ${String(e)}`,
          "engine",
        );
        throw e instanceof Error ? e : new Error(String(e));
      }
    },
    [],
  );

  // ── Image actions ─────────────────────────────────────────────────────────
  const refreshImageJobs = useCallback(async () => {
    const base = engine.engineUrl;
    if (!base) return;
    const requestId = ++imageJobsRefreshRef.current;
    try {
      const list = await apiListImageGenJobs(base, imageHistoryLimitRef.current);
      if (requestId !== imageJobsRefreshRef.current) return;
      setImageJobs(list);
      setImageJobsError(null);
    } catch (e) {
      if (requestId !== imageJobsRefreshRef.current) return;
      setImageJobsError(
        e instanceof Error ? e.message : "Failed to load the image job queue",
      );
    }
  }, []);

  const loadMoreImageHistory = useCallback(async (): Promise<void> => {
    const next = Math.min(1000, imageHistoryLimitRef.current + 50);
    if (next === imageHistoryLimitRef.current) return;
    imageHistoryLimitRef.current = next;
    setImageHistoryLimit(next);
    await refreshImageJobs();
  }, [refreshImageJobs]);

  const refreshImage = useCallback(async () => {
    const base = engine.engineUrl;
    if (!base) {
      setImageStatusError(ENGINE_NOT_CONNECTED);
      setImageStatusLoading(false);
      return;
    }
    setImageStatusLoading(true);
    try {
      const [status, models, presets] = await Promise.all([
        getImageGenStatus(base),
        listImageGenModels(base),
        listImageGenPresets(base),
      ]);
      setImageStatus(status);
      imageModelsRef.current = models; // sync now — remix reads it post-await
      setImageModels(models);
      setImagePresets(presets);
      setImageStatusError(null);
    } catch (e) {
      // fetch TypeError = network-level failure (engine process gone), not an
      // API error. Normalize to the sentinel so the reconnect retry effect
      // arms — the raw "Failed to fetch" string never matched it, so a
      // mid-session engine death left the page stuck on the error card with
      // no auto-recovery (part of MXL-D-038). The raw exception is ALWAYS
      // logged first — the sentinel rebrand must never hide a real bug
      // (a TypeError from response-parsing code would otherwise vanish).
      emitClientLog(
        "error",
        `[media-gen] image status refresh failed: ${String(e)}`,
        "engine",
      );
      setImageStatusError(
        e instanceof TypeError
          ? ENGINE_NOT_CONNECTED
          : e instanceof Error
            ? e.message
            : "Failed to load image generation",
      );
    } finally {
      setImageStatusLoading(false);
    }
    // Queue refresh rides along but never blocks/aborts the status load —
    // it reports its own error via imageJobsError.
    void refreshImageJobs();
  }, [refreshImageJobs]);

  /** The latest fetched image model catalog, readable synchronously after an
   * awaited refreshImage() (React state + its sync effect would still be stale
   * at that point). */
  const getImageModels = useCallback(() => imageModelsRef.current, []);

  const setImageForm = useCallback((patch: Partial<ImageFormState>) => {
    setImageFormState((prev) => ({ ...prev, ...patch }));
  }, []);

  const disableIncompatibleLorasForModel = useCallback(
    (model: ImageGenModelInfo | null | undefined) => {
      if (!model) return;
      setImageFormState((prev) => {
        const loras = disableIncompatibleLoraSelections(
          prev.loras,
          loraListRef.current?.installed ?? [],
          model,
        );
        return loras === prev.loras ? prev : { ...prev, loras };
      });
    },
    [],
  );

  const prepareImageGenerate = useCallback(
    async (model: ImageGenModelInfo) => {
      setSelectedImageModelIdState(model.model_id);
      disableIncompatibleLorasForModel(model);
      setImageFormState((prev) => ({
        ...prev,
        view: "generate",
        paramsLoading: true,
        paramsError: null,
      }));
      let defaults: ImageFormDefaults;
      let paramsError: string | null = null;
      try {
        const p = await fetchParams(model.model_id, "image");
        defaults = {
          modelId: model.model_id,
          steps: p.common.steps ?? model.recommended_steps,
          guidance: p.common.guidance ?? model.recommended_guidance,
          width: p.common.width ?? model.default_width,
          height: p.common.height ?? model.default_height,
          negativePrompt: p.common.negative_prompt ?? "",
          advanced: p.advanced ?? {},
          supportsNegativePrompt:
            p.supports_negative_prompt ?? model.supports_negative_prompt,
          supportsImg2Img: model.supports_img2img ?? false,
          strength: p.common.strength ?? null,
        };
      } catch (e) {
        paramsError = e instanceof Error ? e.message : String(e);
        defaults = {
          modelId: model.model_id,
          steps: model.recommended_steps,
          guidance: model.recommended_guidance,
          width: model.default_width,
          height: model.default_height,
          negativePrompt: "",
          advanced: {},
          supportsNegativePrompt: model.supports_negative_prompt,
          supportsImg2Img: model.supports_img2img ?? false,
          strength: null,
        };
      }
      setImageFormState((prev) => ({
        ...prev,
        view: "generate",
        paramsLoading: false,
        paramsError,
        defaults,
        // Apply the new model's defaults; prompt text, input image and all
        // LoRA selections are preserved. Confirmed cross-family selections
        // were disabled above, so they remain available when switching back.
        negativePrompt: defaults.negativePrompt,
        steps: defaults.steps,
        guidance: defaults.guidance,
        width: defaults.width,
        height: defaults.height,
        seedText: "",
        strength: defaults.strength ?? IMG2IMG_DEFAULT_STRENGTH,
        // Switching models returns to that model's explicit Standard option.
        // A params retry for the same model must not discard the user's choice.
        textEncoderId:
          prev.defaults?.modelId === model.model_id ? prev.textEncoderId : null,
        advancedText: advancedJsonOf(defaults.advanced),
      }));
    },
    [disableIncompatibleLorasForModel, fetchParams],
  );

  const remixImageForm = useCallback((record: ImageRemixRecord) => {
    setImageFormState((prev) => {
      const p = record.params;
      const steps = numParamOf(p, "num_inference_steps");
      const guidance = numParamOf(p, "guidance_scale");
      const strength = numParamOf(p, "strength");
      // Advanced = the model's advanced defaults, with every key the ORIGINAL
      // generation recorded overriding its default. Keys the form owns are
      // excluded — they are applied to their own controls above.
      const advancedDefaults = prev.defaults?.advanced ?? {};
      const advanced: Record<string, unknown> = { ...advancedDefaults };
      for (const [k, v] of Object.entries(p ?? {})) {
        if (FORM_OWNED_PARAM_KEYS.has(k)) continue;
        // Only keys the model actually accepts, and only values we can replay.
        if (k in advancedDefaults && !isUnreplayableValue(v)) advanced[k] = v;
      }
      return {
        ...prev,
        view: "generate",
        prompt: record.prompt ?? "",
        negativePrompt: record.negativePrompt ?? "",
        seedText:
          typeof record.seed === "number" ? String(record.seed) : prev.seedText,
        width: record.width ?? prev.width,
        height: record.height ?? prev.height,
        steps: steps ?? prev.steps,
        guidance: guidance ?? prev.guidance,
        strength:
          strength ?? prev.defaults?.strength ?? IMG2IMG_DEFAULT_STRENGTH,
        loras: recordedLoras(p),
        textEncoderId: recordedTextEncoder(p),
        advancedText: advancedJsonOf(advanced),
      };
    });
  }, []);

  const resetImageCommon = useCallback(() => {
    setImageFormState((prev) => {
      const d = prev.defaults;
      if (!d) return prev;
      return {
        ...prev,
        negativePrompt: d.negativePrompt,
        steps: d.steps,
        guidance: d.guidance,
        width: d.width,
        height: d.height,
        seedText: "",
        strength: d.strength ?? IMG2IMG_DEFAULT_STRENGTH,
      };
    });
  }, []);

  const resetImageAdvanced = useCallback(() => {
    setImageFormState((prev) =>
      prev.defaults
        ? { ...prev, advancedText: advancedJsonOf(prev.defaults.advanced) }
        : prev,
    );
  }, []);

  const resetImageAll = useCallback(() => {
    setImageFormState((prev) => {
      const d = prev.defaults;
      if (!d) return prev;
      return {
        ...prev,
        negativePrompt: d.negativePrompt,
        steps: d.steps,
        guidance: d.guidance,
        width: d.width,
        height: d.height,
        seedText: "",
        strength: d.strength ?? IMG2IMG_DEFAULT_STRENGTH,
        textEncoderId: null,
        advancedText: advancedJsonOf(d.advanced),
      };
    });
  }, []);

  const loadImageModel = useCallback(
    async (modelId: string): Promise<MediaLoadResult> => {
      if (!isRuntimeReady(mediaRuntimeRef.current)) {
        setImageGenError(MEDIA_RUNTIME_NOT_READY);
        return { success: false, error: MEDIA_RUNTIME_NOT_READY };
      }
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("load image model");
        setImageGenError(ENGINE_NOT_CONNECTED_ACTION);
        return { success: false, error: ENGINE_NOT_CONNECTED_ACTION };
      }
      setLoadingImageModelId(modelId);
      setImageGenError(null);
      try {
        const result = await apiLoadImageGenModel(base, modelId);
        if (result.success) {
          setSelectedImageModelIdState(modelId);
          disableIncompatibleLorasForModel(
            imageModelsRef.current.find((model) => model.model_id === modelId),
          );
          await refreshImage();
        }
        return result;
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Failed to load model";
        setImageGenError(msg);
        return { success: false, error: msg };
      } finally {
        setLoadingImageModelId(null);
      }
    },
    [disableIncompatibleLorasForModel, refreshImage],
  );

  const unloadImageModel = useCallback(async () => {
    const base = engine.engineUrl;
    if (!base) {
      logEngineNotConnected("unload image model");
      setImageGenError(ENGINE_NOT_CONNECTED_ACTION);
      return;
    }
    await apiUnloadImageGenModel(base).catch(() => null);
    setSelectedImageModelIdState(null);
    setImageResult(null);
    setImageFormState((prev) => ({ ...prev, view: "models" }));
    await refreshImage();
  }, [refreshImage]);

  const downloadImageModel = useCallback(
    async (modelId: string): Promise<boolean> => {
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("image download");
        setImageGenError(ENGINE_NOT_CONNECTED_ACTION);
        return false;
      }
      try {
        await apiDownloadImageGenModel(base, modelId);
        return true;
      } catch (e) {
        emitClientLog(
          "error",
          `[media-gen] image download failed for ${modelId}: ${String(e)}`,
          "engine",
        );
        setImageGenError(
          e instanceof Error ? e.message : "Failed to start download",
        );
        return false;
      }
    },
    [],
  );

  const downloadTextEncoder = useCallback(
    async (modelId: string, textEncoderId: string): Promise<boolean> => {
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("text encoder download");
        setImageGenError(ENGINE_NOT_CONNECTED_ACTION);
        return false;
      }
      try {
        const result = await apiDownloadImageGenTextEncoder(
          base,
          modelId,
          textEncoderId,
        );
        if (result.already_installed) await refreshImage();
        setImageGenError(null);
        return true;
      } catch (e) {
        const message =
          e instanceof Error ? e.message : "Failed to start text encoder download";
        setImageGenError(message);
        emitClientLog(
          "error",
          `[media-gen] text encoder download failed for ${modelId}/${textEncoderId}: ${message}`,
          "engine",
        );
        return false;
      }
    },
    [refreshImage],
  );

  /**
   * True when firing a one-shot right now would collide with an in-flight
   * generation (a queued/running job, or another one-shot still awaited).
   * Read from refs so the stable generate callbacks never see stale state.
   */
  const isImageEngineBusy = useCallback((): boolean => {
    return (
      imageOneShotInFlightRef.current ||
      imageJobsRef.current.some(
        (j) => j.status === "queued" || j.status === "running",
      )
    );
  }, []);

  /**
   * Queue-first redirect: instead of firing a one-shot against a busy
   * engine (which used to corrupt the running generation AND the queue),
   * enqueue the request with priority "next" so it runs right after the
   * current generation, before the rest of the queue.
   */
  const enqueueImageAsNext = useCallback(
    async (base: string, input: ImageGenerateInput): Promise<void> => {
      try {
        await apiEnqueueImageGenJob(base, { ...input, priority: "next" });
        setImageGenError(null);
        setImageQueueNotice(
          "A generation is already running — added to the queue as next.",
        );
        emitClientLog(
          "info",
          "[media-gen] Generate clicked while a generation is in flight — enqueued as next (priority queue) instead of a concurrent one-shot",
          "engine",
        );
        await refreshImageJobs();
      } catch (e) {
        setImageGenError(
          e instanceof Error ? e.message : "Failed to queue the generation",
        );
      }
    },
    [refreshImageJobs],
  );

  const generateImage = useCallback(
    async (input: ImageGenerateInput): Promise<boolean> => {
      if (!isRuntimeReady(mediaRuntimeRef.current)) {
        setImageGenError(MEDIA_RUNTIME_NOT_READY);
        return false;
      }
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("generate image");
        setImageGenError(ENGINE_NOT_CONNECTED_ACTION);
        return false;
      }
      if (isImageEngineBusy()) {
        // Never fire a one-shot while anything is generating — queue it as
        // up-next instead. No result is produced now, so resolve false.
        await enqueueImageAsNext(base, input);
        return false;
      }
      const runId = ++imageRunRef.current;
      imageOneShotInFlightRef.current = true;
      setImageQueueNotice(null);
      setImageGenerating(true);
      setImageCancelling(false);
      setImageGenStartedAt(Date.now());
      setImageGenError(null);
      setImageResult(null);
      try {
        const result = await apiGenerateImage(base, input);
        if (imageRunRef.current !== runId) return false; // resolved by cancel
        if (result.success && result.image_b64) {
          const { init_image_b64: initImageBytes, ...requestFields } = input;
          const request: GeneratedImageRequest = {
            ...requestFields,
            has_init_image: initImageBytes !== undefined,
          };
          const nextResult: GeneratedImageResult = {
            b64: result.image_b64,
            elapsed: result.elapsed_seconds,
            width: result.width,
            height: result.height,
            seed: result.seed ?? input.seed ?? null,
            itemId: result.item_id ?? null,
            filePath: result.file_path ?? null,
            request,
          };
          setImageResult(nextResult);
          // Applying a revision advances the branch in one atomic state update:
          // the fresh result becomes the next parent, while the root remains
          // fixed. A stale response cannot advance a newer branch.
          const revision = input.revision;
          const nextItemId = nextResult.itemId;
          if (revision && nextItemId) {
            setImageFormState((prev) => {
              if (
                prev.revision?.parentItemId !== revision.parent_item_id
              ) {
                return prev;
              }
              return {
                ...prev,
                initImage: {
                  name: `${nextItemId}.png`,
                  base64: nextResult.b64,
                  previewUrl: `data:image/png;base64,${nextResult.b64}`,
                },
                revision: {
                  parentItemId: nextItemId,
                  rootItemId:
                    revision.root_item_id ?? revision.parent_item_id,
                },
              };
            });
          }
          return true;
        }
        setImageGenError(
          result.cancelled
            ? "Generation cancelled."
            : (result.error ?? "Generation failed"),
        );
        return false;
      } catch (e) {
        if (imageRunRef.current !== runId) return false;
        setImageGenError(e instanceof Error ? e.message : "Generation failed");
        return false;
      } finally {
        // Guarded: after a cancel (or a newer run) this stale run must not
        // clobber the fresh state.
        if (imageRunRef.current === runId) {
          imageOneShotInFlightRef.current = false;
          setImageGenerating(false);
          setImageCancelling(false);
          setImageGenStartedAt(null);
          // /generate now creates a durable job on the server. Refresh after
          // its synchronous response settles so an initially idle UI sees the
          // completed record without waiting for navigation or another job.
          void refreshImageJobs();
        }
      }
    },
    [isImageEngineBusy, enqueueImageAsNext, refreshImageJobs],
  );

  const generateImageWorkflow = useCallback(
    async (input: ImageWorkflowInput): Promise<boolean> => {
      if (!isRuntimeReady(mediaRuntimeRef.current)) {
        setImageGenError(MEDIA_RUNTIME_NOT_READY);
        return false;
      }
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("generate image workflow");
        setImageGenError(ENGINE_NOT_CONNECTED_ACTION);
        return false;
      }
      if (isImageEngineBusy()) {
        // Queue-first: materialize the preset into a plain generate request
        // (exactly what the engine's /generate-workflow route does) and
        // enqueue it as up-next.  Presets are loaded with the section; if
        // this one is somehow unknown we fall through to the one-shot — the
        // engine's generation gate serializes it safely either way.
        const preset = imagePresetsRef.current.find(
          (p) => p.preset_id === input.preset_id,
        );
        if (preset) {
          await enqueueImageAsNext(base, {
            prompt: preset.prompt_template.replace("{subject}", input.subject),
            model_id: input.model_id ?? preset.suggested_model_id,
            ...(preset.negative_prompt
              ? { negative_prompt: preset.negative_prompt }
              : {}),
            steps: preset.steps,
            guidance: preset.guidance,
            width: preset.width,
            height: preset.height,
            ...(input.seed !== undefined ? { seed: input.seed } : {}),
          });
          return false;
        }
      }
      const runId = ++imageRunRef.current;
      imageOneShotInFlightRef.current = true;
      setImageQueueNotice(null);
      setImageGenerating(true);
      setImageCancelling(false);
      setImageGenStartedAt(Date.now());
      setImageGenError(null);
      setImageResult(null);
      try {
        const result = await apiGenerateImageWorkflow(base, input);
        if (imageRunRef.current !== runId) return false; // resolved by cancel
        if (result.success && result.image_b64) {
          const preset = imagePresetsRef.current.find(
            (p) => p.preset_id === input.preset_id,
          );
          const request: GeneratedImageRequest = {
            prompt: preset
              ? preset.prompt_template.replace("{subject}", input.subject)
              : input.subject,
            model_id:
              result.model_id ||
              input.model_id ||
              preset?.suggested_model_id ||
              "",
            has_init_image: false,
            ...(preset?.negative_prompt
              ? { negative_prompt: preset.negative_prompt }
              : {}),
            ...(preset ? { steps: preset.steps } : {}),
            ...(preset ? { guidance: preset.guidance } : {}),
            ...(preset ? { width: preset.width, height: preset.height } : {}),
            ...(input.seed !== undefined ? { seed: input.seed } : {}),
          };
          setImageResult({
            b64: result.image_b64,
            elapsed: result.elapsed_seconds,
            width: result.width,
            height: result.height,
            seed: result.seed ?? input.seed ?? null,
            itemId: result.item_id ?? null,
            filePath: result.file_path ?? null,
            request,
          });
          return true;
        }
        setImageGenError(
          result.cancelled
            ? "Generation cancelled."
            : (result.error ?? "Generation failed"),
        );
        return false;
      } catch (e) {
        if (imageRunRef.current !== runId) return false;
        setImageGenError(e instanceof Error ? e.message : "Generation failed");
        return false;
      } finally {
        if (imageRunRef.current === runId) {
          imageOneShotInFlightRef.current = false;
          setImageGenerating(false);
          setImageCancelling(false);
          setImageGenStartedAt(null);
          void refreshImageJobs();
        }
      }
    },
    [isImageEngineBusy, enqueueImageAsNext, refreshImageJobs],
  );

  /**
   * Cancel the in-flight one-shot image generation.  The never-stuck
   * guarantee lives HERE: whatever the cancel endpoint does (success, 404
   * because the backend hasn't landed, timeout, engine down), the local
   * awaiting state is force-resolved in the finally block.
   */
  const cancelImageGeneration = useCallback(async () => {
    const base = engine.engineUrl;
    setImageCancelling(true);
    try {
      if (base) {
        const res = await apiCancelImageGeneration(base);
        emitClientLog(
          "info",
          `[media-gen] image cancel → cancelled=${String(res.cancelled)}${res.was ? ` (${res.was})` : ""}${res.reason ? `: ${res.reason}` : ""}`,
          "engine",
        );
      } else {
        logEngineNotConnected("cancel image generation");
      }
      setImageGenError("Generation cancelled.");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      emitClientLog(
        "error",
        `[media-gen] image cancel request failed: ${msg}`,
        "engine",
      );
      setImageGenError(
        `Cancel request failed (${msg}). The waiting state was released, but the engine may still be generating in the background.`,
      );
    } finally {
      // Force-resolve the local awaiting state unconditionally.
      imageRunRef.current++;
      imageOneShotInFlightRef.current = false;
      setImageGenerating(false);
      setImageCancelling(false);
      setImageGenStartedAt(null);
      // Refresh so is_generating/cancel_requested reflect reality.
      void refreshImageJobs();
    }
  }, [refreshImageJobs]);

  const enqueueImageJob = useCallback(
    async (
      input: ImageGenerateInput,
      priority: "normal" | "next" = "normal",
    ): Promise<boolean> => {
      if (!isRuntimeReady(mediaRuntimeRef.current)) {
        setImageGenError(MEDIA_RUNTIME_NOT_READY);
        return false;
      }
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("enqueue image job");
        setImageGenError(ENGINE_NOT_CONNECTED_ACTION);
        return false;
      }
      try {
        await apiEnqueueImageGenJob(
          base,
          priority === "next" ? { ...input, priority } : input,
        );
        await refreshImageJobs();
        return true;
      } catch (e) {
        setImageGenError(
          e instanceof Error ? e.message : "Failed to queue the job",
        );
        return false;
      }
    },
    [refreshImageJobs],
  );

  const cancelImageJob = useCallback(
    async (jobId: string) => {
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("cancel image job");
        setImageJobsError(ENGINE_NOT_CONNECTED_ACTION);
        return;
      }
      // Optimistic "Cancelling…" for active jobs — a running diffusion step
      // can take tens of seconds to actually stop.
      setImageJobs((prev) =>
        prev.map((j) =>
          j.job_id === jobId &&
          (j.status === "queued" || j.status === "running")
            ? { ...j, cancel_requested: true }
            : j,
        ),
      );
      try {
        await apiCancelImageGenJob(base, jobId);
      } catch (e) {
        setImageJobsError(
          e instanceof Error ? e.message : "Failed to cancel the job",
        );
      }
      await refreshImageJobs();
    },
    [refreshImageJobs],
  );

  // ── Batches (prompt matrix) + queue control ───────────────────────────────

  const refreshImageQueue = useCallback(async () => {
    const base = engine.engineUrl;
    if (!base) return;
    const requestId = ++imageQueueRefreshRef.current;
    try {
      const [state, batches] = await Promise.all([
        apiGetImageGenQueueState(base),
        apiListImageGenBatches(base),
      ]);
      if (requestId !== imageQueueRefreshRef.current) return;
      setImageQueueState(state);
      setImageBatches(batches);
    } catch (e) {
      if (requestId !== imageQueueRefreshRef.current) return;
      const msg = e instanceof Error ? e.message : String(e);
      emitClientLog(
        "warn",
        `[media-gen] queue state fetch failed: ${msg}`,
        "engine",
      );
    }
  }, []);

  const enqueueImageBatch = useCallback(
    async (
      jobs: ImageGenBatchJobSpec[],
      label?: string,
    ): Promise<EnqueueBatchResult> => {
      if (!isRuntimeReady(mediaRuntimeRef.current)) {
        setImageGenError(MEDIA_RUNTIME_NOT_READY);
        return { ok: false, error: MEDIA_RUNTIME_NOT_READY };
      }
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("enqueue image batch");
        setImageGenError(ENGINE_NOT_CONNECTED_ACTION);
        return { ok: false, error: ENGINE_NOT_CONNECTED_ACTION };
      }
      try {
        const res = await apiEnqueueImageGenBatch(base, {
          jobs,
          ...(label !== undefined ? { label } : {}),
        });
        emitClientLog(
          "info",
          `[media-gen] queued batch ${res.batch_id} (${res.count} job(s))`,
          "engine",
        );
        await Promise.all([refreshImageJobs(), refreshImageQueue()]);
        return { ok: true, batchId: res.batch_id, count: res.count };
      } catch (e) {
        // LOUD: the whole batch was rejected (the engine validates every run
        // before queueing any), so the user must see WHY — never a silent
        // half-enqueued sweep.
        const error =
          e instanceof Error ? e.message : "Failed to queue the batch";
        setImageGenError(error);
        return { ok: false, error };
      }
    },
    [refreshImageJobs, refreshImageQueue],
  );

  const setImageQueuePaused = useCallback(
    async (paused: boolean) => {
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("pause image queue");
        setImageJobsError(ENGINE_NOT_CONNECTED_ACTION);
        return;
      }
      // Optimistic — the toggle must feel instant even mid-generation.
      setImageQueueState((prev) => (prev ? { ...prev, paused } : prev));
      try {
        setImageQueueState(await apiSetImageGenQueuePaused(base, paused));
      } catch (e) {
        setImageJobsError(
          e instanceof Error ? e.message : "Failed to change the queue state",
        );
        await refreshImageQueue();
      }
    },
    [refreshImageQueue],
  );

  const reorderImageQueue = useCallback(
    async (jobIds: string[]) => {
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("reorder image queue");
        setImageJobsError(ENGINE_NOT_CONNECTED_ACTION);
        return;
      }
      // Optimistic reorder: the dragged row must land where it was dropped and
      // STAY there. Reconciled against the engine's authoritative order below.
      setImageJobs((prev) => {
        const rank = new Map(jobIds.map((id, i) => [id, i]));
        const queued = prev
          .filter((j) => j.status === "queued")
          .sort((a, b) => {
            const ai = rank.get(a.job_id) ?? Number.MAX_SAFE_INTEGER;
            const bi = rank.get(b.job_id) ?? Number.MAX_SAFE_INTEGER;
            return ai - bi;
          });
        let q = 0;
        return prev.map((j) =>
          j.status === "queued" ? (queued[q++] as ImageGenJob) : j,
        );
      });
      try {
        await apiReorderImageGenQueue(base, jobIds);
      } catch (e) {
        setImageJobsError(
          e instanceof Error ? e.message : "Failed to reorder the queue",
        );
      }
      await refreshImageJobs();
    },
    [refreshImageJobs],
  );

  const retryImageJob = useCallback(
    async (jobId: string) => {
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("retry image job");
        setImageJobsError(ENGINE_NOT_CONNECTED_ACTION);
        return;
      }
      try {
        await apiRetryImageGenJob(base, jobId);
      } catch (e) {
        setImageJobsError(
          e instanceof Error ? e.message : "Failed to retry the job",
        );
      }
      await Promise.all([refreshImageJobs(), refreshImageQueue()]);
    },
    [refreshImageJobs, refreshImageQueue],
  );

  const cancelImageBatch = useCallback(
    async (batchId: string) => {
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("cancel image batch");
        setImageJobsError(ENGINE_NOT_CONNECTED_ACTION);
        return;
      }
      try {
        await apiCancelImageGenBatch(base, batchId);
      } catch (e) {
        setImageJobsError(
          e instanceof Error ? e.message : "Failed to cancel the batch",
        );
      }
      await Promise.all([refreshImageJobs(), refreshImageQueue()]);
    },
    [refreshImageJobs, refreshImageQueue],
  );

  const clearFinishedImageJobs = useCallback(async () => {
    const base = engine.engineUrl;
    if (!base) {
      logEngineNotConnected("clear finished image jobs");
      setImageJobsError(ENGINE_NOT_CONNECTED_ACTION);
      return;
    }
    try {
      await apiClearFinishedImageGenJobs(base);
    } catch (e) {
      setImageJobsError(
        e instanceof Error ? e.message : "Failed to clear finished jobs",
      );
    }
    await Promise.all([refreshImageJobs(), refreshImageQueue()]);
  }, [refreshImageJobs, refreshImageQueue]);

  // ── LoRA adapters ─────────────────────────────────────────────────────────
  const refreshLoras = useCallback(async () => {
    const base = engine.engineUrl;
    if (!base) {
      setLoraError(ENGINE_NOT_CONNECTED);
      return;
    }
    try {
      const list = await apiListImageGenLoras(base);
      loraListRef.current = list;
      setLoraList(list);
      setLoraError(null);
    } catch (e) {
      // LOUD: 404 (backend not landed) / engine down land in the UI banner.
      const msg = e instanceof Error ? e.message : String(e);
      setLoraError(msg);
      emitClientLog(
        "warn",
        `[media-gen] LoRA list fetch failed: ${msg}`,
        "engine",
      );
    }
  }, []);

  const downloadLora = useCallback(
    async (ref: string, weightName?: string): Promise<string | null> => {
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("LoRA download");
        setLoraError(ENGINE_NOT_CONNECTED_ACTION);
        return null;
      }
      // Always classify first — curated catalog rows may be Civitai
      // (``civitai:<model>@<ver>``) or HF.  weight_name is HF-only (Civitai
      // resolves the primary .safetensors from the version metadata).
      const classified = classifyLoraRef(ref);
      const wire =
        "civitai" in classified
          ? classified
          : weightName
            ? { repo_id: classified.repo_id, weight_name: weightName }
            : classified;
      setLoraNeedsCivitaiKey(false);
      try {
        const { download_id } = await apiDownloadImageGenLora(base, wire);
        setLoraDownloads((prev) => ({ ...prev, [ref]: download_id }));
        setLoraError(null);
        return download_id;
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        if (e instanceof MediaGenHttpError && e.status === 401) {
          // Engine says the upstream needs credentials (Civitai API key).
          setLoraNeedsCivitaiKey(true);
        }
        setLoraError(msg);
        emitClientLog(
          "error",
          `[media-gen] LoRA download failed for ${ref}: ${msg}`,
          "engine",
        );
        return null;
      }
    },
    [],
  );

  const deleteLora = useCallback(
    async (loraId: string) => {
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("LoRA delete");
        setLoraError(ENGINE_NOT_CONNECTED_ACTION);
        return;
      }
      try {
        await apiDeleteImageGenLora(base, loraId);
        // Drop it from the form selection too — never send an unknown id.
        setImageFormState((prev) => ({
          ...prev,
          loras: prev.loras.filter((l) => l.id !== loraId),
        }));
        await refreshLoras();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setLoraError(msg);
        emitClientLog(
          "error",
          `[media-gen] LoRA delete failed for ${loraId}: ${msg}`,
          "engine",
        );
      }
    },
    [refreshLoras],
  );

  const useImageAsInput = useCallback((image: PickedImage) => {
    setImageFormState((prev) => ({
      ...prev,
      initImage: image,
      revision: null,
    }));
  }, []);

  const beginImageRevision = useCallback(
    (image: PickedImage, parentItemId: string, rootItemId?: string) => {
      setImageFormState((prev) => ({
        ...prev,
        view: "generate",
        initImage: image,
        seedText: "",
        revision: {
          parentItemId,
          rootItemId: rootItemId ?? parentItemId,
        },
      }));
    },
    [],
  );

  const endImageRevision = useCallback(() => {
    setImageFormState((prev) => ({
      ...prev,
      initImage: null,
      revision: null,
    }));
  }, []);

  // ── Custom models (Hugging Face / Civitai checkpoints) ────────────────────
  const inspectCustomModel = useCallback(
    async (ref: string): Promise<CustomImageModelInspectResult> => {
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("custom model inspect");
        throw new Error(ENGINE_NOT_CONNECTED_ACTION);
      }
      try {
        return await apiInspectCustomImageModel(base, ref);
      } catch (e) {
        emitClientLog(
          "error",
          `[media-gen] custom-model inspect failed for "${ref}": ${String(e)}`,
          "engine",
        );
        throw customModelUnsupported(e);
      }
    },
    [],
  );

  const registerCustomModel = useCallback(
    async (entry: CustomImageModelEntry): Promise<void> => {
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("custom model register");
        throw new Error(ENGINE_NOT_CONNECTED_ACTION);
      }
      try {
        await apiRegisterCustomImageModel(base, entry);
      } catch (e) {
        emitClientLog(
          "error",
          `[media-gen] custom-model register failed for ${entry.model_id}: ${String(e)}`,
          "engine",
        );
        throw customModelUnsupported(e);
      }
      // The entry (and its queued weights download) must appear in the picker.
      await refreshImage();
    },
    [refreshImage],
  );

  const deleteCustomModel = useCallback(
    async (modelId: string): Promise<void> => {
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("custom model delete");
        throw new Error(ENGINE_NOT_CONNECTED_ACTION);
      }
      try {
        await apiDeleteCustomImageModel(base, modelId);
      } catch (e) {
        emitClientLog(
          "error",
          `[media-gen] custom-model delete failed for ${modelId}: ${String(e)}`,
          "engine",
        );
        throw customModelUnsupported(e);
      }
      // Never leave a deleted model selected.
      setSelectedImageModelIdState((prev) => (prev === modelId ? null : prev));
      await refreshImage();
    },
    [refreshImage],
  );

  // Poll the image job queue every 2s ONLY while something is queued/running.
  // Batch roll-ups and the paused flag ride along on the same tick — a second
  // interval would just be a second way to hammer the engine.
  const hasActiveImageJobs = imageJobs.some(
    (j) => j.status === "queued" || j.status === "running",
  );
  useEffect(() => {
    if (!hasActiveImageJobs) return;
    const id = window.setInterval(() => {
      void refreshImageJobs();
      void refreshImageQueue();
    }, 2000);
    return () => window.clearInterval(id);
  }, [hasActiveImageJobs, refreshImageJobs, refreshImageQueue]);

  // One init fetch, so the paused flag is known before anything is queued (a
  // queue left paused last session must not silently swallow new work).
  // In the hook, NOT the page — a page-level init effect re-runs every render.
  useEffect(() => {
    void refreshImageQueue();
  }, [refreshImageQueue]);

  // ── Model-load watchdog (image) ────────────────────────────────────────────
  // While the engine reports is_loading, poll status every 2s so (a) the
  // spinner is guaranteed to clear when the engine finishes/fails and (b) a
  // load_error resolves the spinner into a LOUD error, never a forever-spin.
  const imageIsLoading = !!imageStatus?.is_loading;
  useEffect(() => {
    if (!imageIsLoading) return;
    const id = window.setInterval(() => {
      const base = engine.engineUrl;
      if (!base) return;
      getImageGenStatus(base)
        .then((s) => {
          setImageStatus(s);
          if (!s.is_loading && s.load_error) {
            setImageGenError(`Model load failed: ${s.load_error}`);
          }
        })
        .catch((e) => {
          emitClientLog(
            "warn",
            `[media-gen] image status poll (load watchdog) failed: ${String(e)}`,
            "engine",
          );
        });
    }, 2000);
    return () => window.clearInterval(id);
  }, [imageIsLoading]);

  // Elapsed readout for image model loads (client-observed start time).
  const imageLoadInFlight = loadingImageModelId !== null || imageIsLoading;
  useEffect(() => {
    if (imageLoadInFlight) {
      setImageLoadStartedAt((prev) => prev ?? Date.now());
    } else {
      setImageLoadStartedAt(null);
    }
  }, [imageLoadInFlight]);

  // Fetch gallery thumbs for completed queue jobs (via /media-library/thumb —
  // self-healing JPEG cache, not full PNG). Gated on the joined "jobId:itemId"
  // list of completed jobs so it re-runs only when that set actually changes.
  const completedJobItems = useMemo(
    () =>
      imageJobs
        .filter((j) => j.status === "completed" && j.item_id)
        .map((j) => `${j.job_id}\t${j.item_id as string}`)
        .join("\n"),
    [imageJobs],
  );
  useEffect(() => {
    if (!completedJobItems) return;
    const base = engine.engineUrl;
    if (!base) return;
    let cancelled = false;
    for (const pair of completedJobItems.split("\n")) {
      const [jobId, itemId] = pair.split("\t");
      if (!jobId || !itemId || imageJobThumbsRef.current[jobId]) continue;
      // Never re-attempt an id we've already resolved as unfetchable. Without
      // this, every mount re-fired every dead id — one vaulted history was
      // enough to put 41 red 404s in the issue report, forever.
      if (imageJobThumbFailuresRef.current[jobId]) continue;
      imageJobThumbFailuresRef.current[jobId] = "pending";
      const wasLocked = imageJobThumbWasLockedRef.current.has(jobId);
      void apiFetchMediaLibraryThumb(base, itemId)
        .then((url) => {
          delete imageJobThumbFailuresRef.current[jobId];
          // Resolved only after an unlock → these bytes came out of the vault.
          if (wasLocked) vaultBackedJobsRef.current.add(jobId);
          if (cancelled) {
            URL.revokeObjectURL(url);
            return;
          }
          setImageJobThumbs((prev) => {
            if (prev[jobId]) {
              URL.revokeObjectURL(url);
              return prev;
            }
            return { ...prev, [jobId]: url };
          });
        })
        .catch((e: unknown) => {
          // 423 = the item is in the LOCKED vault. Not a failure and not a
          // missing item — it resolves on unlock, so remember it as "locked"
          // and let the unlock listener below clear it for a retry. Anything
          // else is permanent: record it and never ask again.
          const locked = e instanceof MediaFileError && e.isVaultLocked;
          imageJobThumbFailuresRef.current[jobId] = locked ? "locked" : "gone";
          if (locked) imageJobThumbWasLockedRef.current.add(jobId);
          if (!locked) {
            emitClientLog(
              "warn",
              `[media-gen] thumbnail unavailable for job ${jobId}: ${String(e)} — not retrying`,
              "engine",
            );
          }
        });
    }
    return () => {
      cancelled = true;
    };
  }, [completedJobItems, thumbRetryNonce]);

  // Vault unlocked → the "locked" thumbnails just became readable. Drop only
  // those markers (never the "gone" ones) and let the effect above refetch.
  useEffect(() => {
    const onUnlocked = () => {
      let changed = false;
      for (const [jobId, reason] of Object.entries(
        imageJobThumbFailuresRef.current,
      )) {
        if (reason === "locked") {
          delete imageJobThumbFailuresRef.current[jobId];
          changed = true;
        }
      }
      if (changed) setThumbRetryNonce((n) => n + 1);
    };
    window.addEventListener(VAULT_UNLOCKED_EVENT, onUnlocked);
    return () => window.removeEventListener(VAULT_UNLOCKED_EVENT, onUnlocked);
  }, []);

  // The vault LOCKED. Any job thumbnail whose bytes came out of the vault is a
  // decrypted image still on screen — revoking the URL does not blank an <img>
  // that already rendered, so the thumbnails must be dropped from state. They
  // are marked "locked", which the VAULT_UNLOCKED_EVENT listener above re-arms.
  //
  // We cannot tell from a job which items are vaulted, so we drop the thumbs of
  // every job whose item is NOT in the plaintext library any more — those are
  // exactly the ones that were served out of the vault.
  useEffect(
    () =>
      onVaultLocked(() => {
        const vaulted = imageJobsRef.current.filter(
          (j) => j.item_id && vaultBackedJobsRef.current.has(j.job_id),
        );
        if (vaulted.length === 0) return;
        for (const j of vaulted) {
          imageJobThumbFailuresRef.current[j.job_id] = "locked";
        }
        setImageJobThumbs((prev) => {
          let changed = false;
          const next = { ...prev };
          for (const j of vaulted) {
            const url = next[j.job_id];
            if (url) {
              URL.revokeObjectURL(url);
              delete next[j.job_id];
              changed = true;
            }
          }
          return changed ? next : prev;
        });
        vaultBackedJobsRef.current.clear();
      }),
    [],
  );

  // An item left the plaintext library (deleted, or moved into the vault).
  // Every job whose result WAS that item must drop its thumbnail in the same
  // tick — a stale blob URL here is exactly the partial state update that left
  // deleted images visible in the queue and a dead id in the lightbox.
  useEffect(
    () =>
      onMediaItemsRemoved(({ itemIds, reason }) => {
        const gone = new Set(itemIds);
        // The fresh one-shot result pane must not survive its own deletion.
        setImageResult((prev) =>
          prev && prev.itemId && gone.has(prev.itemId) ? null : prev,
        );
        const affected = imageJobsRef.current
          .filter((j) => j.item_id && gone.has(j.item_id))
          .map((j) => j.job_id);
        if (affected.length === 0) return;
        for (const jobId of affected) {
          // Vaulted items come back on unlock; deleted ones never do.
          imageJobThumbFailuresRef.current[jobId] =
            reason === "vaulted" ? "locked" : "gone";
          if (reason === "vaulted") {
            // The item now lives in the vault, so any FUTURE thumbnail of it
            // (refetched after an unlock) is decrypted vault bytes that must be
            // dropped when the vault locks. Mark it now — the 423-on-fetch path
            // is not the only way a thumb becomes vault-backed, and relying on
            // that alone let a vault image survive a lock (was: a decrypted
            // private image left on screen after locking).
            imageJobThumbWasLockedRef.current.add(jobId);
            vaultBackedJobsRef.current.add(jobId);
          }
        }
        setImageJobThumbs((prev) => {
          let changed = false;
          const next = { ...prev };
          for (const jobId of affected) {
            const url = next[jobId];
            if (url) {
              URL.revokeObjectURL(url);
              delete next[jobId];
              changed = true;
            }
          }
          return changed ? next : prev;
        });
      }),
    [],
  );

  const setSelectedImageModelId = useCallback((modelId: string | null) => {
    setSelectedImageModelIdState(modelId);
    if (modelId) {
      disableIncompatibleLorasForModel(
        imageModelsRef.current.find((model) => model.model_id === modelId),
      );
    }
  }, [disableIncompatibleLorasForModel]);
  const clearImageResult = useCallback(() => setImageResult(null), []);
  const clearImageGenError = useCallback(() => setImageGenError(null), []);
  const clearImageQueueNotice = useCallback(
    () => setImageQueueNotice(null),
    [],
  );

  // ── Video actions ─────────────────────────────────────────────────────────
  const refreshVideo = useCallback(async () => {
    const base = engine.engineUrl;
    if (!base) {
      setVideoStatusError(ENGINE_NOT_CONNECTED);
      setVideoStatusLoading(false);
      return;
    }
    setVideoStatusLoading(true);
    try {
      const status = await getVideoGenStatus(base);
      setVideoStatus(status);
      setVideoStatusError(null);
      const [models, jobList] = await Promise.all([
        listVideoGenModels(base).catch((e) => {
          emitClientLog(
            "warn",
            `[media-gen] video model list failed: ${String(e)}`,
            "engine",
          );
          return [] as VideoGenModelInfo[];
        }),
        listVideoGenJobs(base).catch((e) => {
          emitClientLog(
            "warn",
            `[media-gen] video job list failed: ${String(e)}`,
            "engine",
          );
          return [] as VideoGenJob[];
        }),
      ]);
      setVideoModels(models);
      setJobs(jobList);
      // Re-adopt a job that is still running server-side (e.g. after a page
      // reload) so its progress bar and polling resume automatically.
      if (
        status.active_job_id &&
        activeJobRef.current?.job_id !== status.active_job_id
      ) {
        const found =
          jobList.find((j) => j.job_id === status.active_job_id) ?? null;
        if (found) setActiveJob(found);
        else {
          const job = await apiGetVideoGenJob(base, status.active_job_id).catch(
            () => null,
          );
          if (job) setActiveJob(job);
        }
      }
    } catch (e) {
      // Same sentinel normalization as refreshImage — a dead engine must arm
      // the reconnect retry, not park on a "Failed to fetch" card forever.
      // Raw exception logged first so the rebrand never hides a real bug.
      emitClientLog(
        "error",
        `[media-gen] video status refresh failed: ${String(e)}`,
        "engine",
      );
      setVideoStatusError(
        e instanceof TypeError
          ? ENGINE_NOT_CONNECTED
          : e instanceof Error
            ? e.message
            : "Failed to load video generation",
      );
    } finally {
      setVideoStatusLoading(false);
    }
  }, []);
  useEffect(() => {
    refreshVideoRef.current = refreshVideo;
  }, [refreshVideo]);

  const setVideoForm = useCallback((patch: Partial<VideoFormState>) => {
    setVideoFormState((prev) => ({ ...prev, ...patch }));
  }, []);

  const prepareVideoGenerate = useCallback(
    async (model: VideoGenModelInfo) => {
      setVideoFormState((prev) => ({
        ...prev,
        view: "generate",
        paramsLoading: true,
        paramsError: null,
      }));
      let defaults: VideoFormDefaults;
      let paramsError: string | null = null;
      try {
        const p = await fetchParams(model.model_id, "video");
        defaults = {
          modelId: model.model_id,
          steps: p.common.steps ?? VIDEO_FALLBACK_STEPS,
          guidance: p.common.guidance ?? VIDEO_FALLBACK_GUIDANCE,
          width: p.common.width ?? model.default_width,
          height: p.common.height ?? model.default_height,
          numFrames: p.common.num_frames ?? model.default_num_frames,
          fps: p.common.fps ?? model.default_fps,
          negativePrompt: p.common.negative_prompt ?? "",
          advanced: p.advanced ?? {},
          supportsNegativePrompt:
            p.supports_negative_prompt ?? model.supports_negative_prompt,
        };
      } catch (e) {
        paramsError = e instanceof Error ? e.message : String(e);
        defaults = {
          modelId: model.model_id,
          steps: VIDEO_FALLBACK_STEPS,
          guidance: VIDEO_FALLBACK_GUIDANCE,
          width: model.default_width,
          height: model.default_height,
          numFrames: model.default_num_frames,
          fps: model.default_fps,
          negativePrompt: "",
          advanced: {},
          supportsNegativePrompt: model.supports_negative_prompt,
        };
      }
      setVideoFormState((prev) => ({
        ...prev,
        view: "generate",
        paramsLoading: false,
        paramsError,
        defaults,
        negativePrompt: defaults.negativePrompt,
        steps: defaults.steps,
        guidance: defaults.guidance,
        width: defaults.width,
        height: defaults.height,
        numFrames: defaults.numFrames,
        fps: defaults.fps,
        seedText: "",
        advancedText: advancedJsonOf(defaults.advanced),
      }));
    },
    [fetchParams],
  );

  const resetVideoCommon = useCallback(() => {
    setVideoFormState((prev) => {
      const d = prev.defaults;
      if (!d) return prev;
      return {
        ...prev,
        negativePrompt: d.negativePrompt,
        steps: d.steps,
        guidance: d.guidance,
        width: d.width,
        height: d.height,
        numFrames: d.numFrames,
        fps: d.fps,
        seedText: "",
      };
    });
  }, []);

  const resetVideoAdvanced = useCallback(() => {
    setVideoFormState((prev) =>
      prev.defaults
        ? { ...prev, advancedText: advancedJsonOf(prev.defaults.advanced) }
        : prev,
    );
  }, []);

  const resetVideoAll = useCallback(() => {
    setVideoFormState((prev) => {
      const d = prev.defaults;
      if (!d) return prev;
      return {
        ...prev,
        negativePrompt: d.negativePrompt,
        steps: d.steps,
        guidance: d.guidance,
        width: d.width,
        height: d.height,
        numFrames: d.numFrames,
        fps: d.fps,
        seedText: "",
        advancedText: advancedJsonOf(d.advanced),
      };
    });
  }, []);

  const fetchVideoResult = useCallback(
    async (jobId: string): Promise<string | null> => {
      const existing = videoResultsRef.current[jobId];
      if (existing) return existing;
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("fetch video result");
        setVideoGenError(ENGINE_NOT_CONNECTED_ACTION);
        return null;
      }
      try {
        const url = await apiFetchVideoGenResult(base, jobId);
        // Bound the blob-URL cache to the 5 most recent jobs; revoke the URLs
        // we evict so long sessions don't leak object URLs indefinitely.
        setVideoResults((prev) => {
          const next: Record<string, string> = { ...prev, [jobId]: url };
          const keys = Object.keys(next);
          if (keys.length > 5) {
            for (const staleKey of keys.slice(0, keys.length - 5)) {
              const staleUrl = next[staleKey];
              if (staleUrl) URL.revokeObjectURL(staleUrl);
              delete next[staleKey];
            }
          }
          return next;
        });
        return url;
      } catch (e) {
        emitClientLog(
          "error",
          `[media-gen] fetch video result failed for ${jobId}: ${String(e)}`,
          "engine",
        );
        return null;
      }
    },
    [],
  );

  const pollJob = useCallback(
    async (jobId: string) => {
      const base = engine.engineUrl;
      if (!base) return;
      try {
        const job = await apiGetVideoGenJob(base, jobId);
        pollFailuresRef.current = 0;
        setActiveJob(job);
        if (job.status === "completed") {
          await fetchVideoResult(jobId);
          void refreshVideoRef.current?.();
        } else if (job.status === "failed") {
          setVideoGenError(job.error ?? "Video generation failed");
          void refreshVideoRef.current?.();
        } else if (job.status === "cancelled") {
          void refreshVideoRef.current?.();
        }
      } catch (e) {
        const status = (e as { status?: number }).status;
        const gone = status === 404; // job store lost it — fail immediately
        pollFailuresRef.current += 1;
        emitClientLog(
          "warn",
          `[media-gen] job poll failed for ${jobId} (${pollFailuresRef.current}x${gone ? ", gone" : ""}): ${String(e)}`,
          "engine",
        );
        if (gone || pollFailuresRef.current >= 3) {
          const msg = gone
            ? "This video job no longer exists on the engine"
            : "Lost contact with the engine while tracking this job";
          setVideoGenError(msg);
          // Synthesize a terminal state so the poll effect tears down and the
          // dismiss control / Generate button become usable again.
          setActiveJob((prev) =>
            prev && prev.job_id === jobId
              ? { ...prev, status: "failed", error: msg }
              : prev,
          );
        }
      }
    },
    [fetchVideoResult],
  );

  // Poll the active job every 2s ONLY while it is queued/running.  Narrowly
  // gated on the job id + status so it never restarts on unrelated renders.
  useEffect(() => {
    const jobId = activeJob?.job_id;
    const running =
      activeJob?.status === "queued" || activeJob?.status === "running";
    if (!jobId || !running) return;
    const id = window.setInterval(() => {
      void pollJob(jobId);
    }, 2000);
    return () => window.clearInterval(id);
  }, [activeJob?.job_id, activeJob?.status, pollJob]);

  const loadVideoModel = useCallback(
    async (modelId: string): Promise<MediaLoadResult> => {
      const runtime = mediaRuntimeRef.current;
      if (
        runtime?.state !== "ready" ||
        !runtime.video_packages_available
      ) {
        setVideoGenError(MEDIA_RUNTIME_NOT_READY);
        return { success: false, error: MEDIA_RUNTIME_NOT_READY };
      }
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("load video model");
        setVideoGenError(ENGINE_NOT_CONNECTED_ACTION);
        return { success: false, error: ENGINE_NOT_CONNECTED_ACTION };
      }
      setLoadingVideoModelId(modelId);
      setVideoGenError(null);
      try {
        const result = await apiLoadVideoGenModel(base, modelId);
        if (result.success) await refreshVideo();
        return result;
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Failed to load model";
        setVideoGenError(msg);
        return { success: false, error: msg };
      } finally {
        setLoadingVideoModelId(null);
      }
    },
    [refreshVideo],
  );

  const unloadVideoModel = useCallback(async () => {
    const base = engine.engineUrl;
    if (!base) {
      logEngineNotConnected("unload video model");
      setVideoGenError(ENGINE_NOT_CONNECTED_ACTION);
      return;
    }
    await apiUnloadVideoGenModel(base).catch(() => null);
    setVideoFormState((prev) => ({ ...prev, view: "models" }));
    await refreshVideo();
  }, [refreshVideo]);

  const downloadVideoModel = useCallback(
    async (modelId: string): Promise<boolean> => {
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("video download");
        setVideoGenError(ENGINE_NOT_CONNECTED_ACTION);
        return false;
      }
      try {
        await apiDownloadVideoGenModel(base, modelId);
        return true;
      } catch (e) {
        emitClientLog(
          "error",
          `[media-gen] video download failed for ${modelId}: ${String(e)}`,
          "engine",
        );
        setVideoGenError(
          e instanceof Error ? e.message : "Failed to start download",
        );
        return false;
      }
    },
    [],
  );

  const generateVideo = useCallback(
    async (req: VideoGenRequest): Promise<{ ok: boolean; error?: string }> => {
      const runtime = mediaRuntimeRef.current;
      if (
        runtime?.state !== "ready" ||
        !runtime.video_packages_available
      ) {
        setVideoGenError(MEDIA_RUNTIME_NOT_READY);
        return { ok: false, error: MEDIA_RUNTIME_NOT_READY };
      }
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("generate video");
        setVideoGenError(ENGINE_NOT_CONNECTED_ACTION);
        return { ok: false, error: ENGINE_NOT_CONNECTED_ACTION };
      }
      const runId = ++videoRunRef.current;
      setVideoGenerating(true);
      setVideoGenError(null);
      try {
        const { job_id } = await apiGenerateVideo(base, req);
        if (videoRunRef.current !== runId) {
          // Cancelled while the enqueue was in flight — kill the orphan job
          // instead of silently letting it run.
          void apiCancelVideoGenJob(base, job_id).catch(() => null);
          return { ok: false, error: "Cancelled" };
        }
        pollFailuresRef.current = 0;
        // Seed a queued job so the poll effect starts immediately.
        setActiveJob({
          job_id,
          status: "queued",
          progress: 0,
          current_step: 0,
          total_steps: 0,
          elapsed_seconds: 0,
          error: null,
          prompt: req.prompt,
          model_id: req.model_id ?? "",
        });
        return { ok: true };
      } catch (e) {
        if (videoRunRef.current !== runId) {
          return { ok: false, error: "Cancelled" };
        }
        const msg =
          e instanceof Error ? e.message : "Failed to start video generation";
        setVideoGenError(msg);
        return { ok: false, error: msg };
      } finally {
        setVideoGenerating(false);
      }
    },
    [],
  );

  /**
   * Cancel a specific queued/running video job.  Optimistically flags
   * `cancel_requested` so the UI shows "Cancelling…" immediately; the 2s job
   * poll picks up the real terminal status (steps can take tens of seconds).
   */
  const cancelVideoJob = useCallback(async (jobId: string) => {
    const base = engine.engineUrl;
    if (!base) {
      logEngineNotConnected("cancel video job");
      setVideoGenError(ENGINE_NOT_CONNECTED_ACTION);
      return;
    }
    setVideoCancelling(true);
    setActiveJob((prev) =>
      prev && prev.job_id === jobId
        ? { ...prev, cancel_requested: true }
        : prev,
    );
    setJobs((prev) =>
      prev.map((j) =>
        j.job_id === jobId && (j.status === "queued" || j.status === "running")
          ? { ...j, cancel_requested: true }
          : j,
      ),
    );
    try {
      await apiCancelVideoGenJob(base, jobId);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      emitClientLog(
        "error",
        `[media-gen] video job cancel failed for ${jobId}: ${msg}`,
        "engine",
      );
      setVideoGenError(`Cancel request failed: ${msg}`);
      setVideoCancelling(false);
    }
  }, []);

  /** Cancel the active (watched) video generation. */
  const cancelVideoGeneration = useCallback(async () => {
    const job = activeJobRef.current;
    if (job && (job.status === "queued" || job.status === "running")) {
      await cancelVideoJob(job.job_id);
      return;
    }
    // No job id yet (the enqueue POST may still be in flight) — bump the run
    // id so a late 202 is treated as cancelled, and clear every busy flag.
    videoRunRef.current++;
    setVideoGenerating(false);
    setVideoCancelling(false);
  }, [cancelVideoJob]);

  // Clear the "Cancelling…" flag once the watched job reaches ANY terminal
  // state (cancelled, failed, completed) or is dismissed — the poll's 3-strike
  // failure synthesis guarantees this also happens when the engine dies.
  const activeJobSettled =
    !activeJob ||
    (activeJob.status !== "queued" && activeJob.status !== "running");
  useEffect(() => {
    if (videoCancelling && activeJobSettled) setVideoCancelling(false);
  }, [videoCancelling, activeJobSettled]);

  // ── Model-load watchdog (video) — mirror of the image one ────────────────
  const videoIsLoading = !!videoStatus?.is_loading;
  useEffect(() => {
    if (!videoIsLoading) return;
    const id = window.setInterval(() => {
      const base = engine.engineUrl;
      if (!base) return;
      getVideoGenStatus(base)
        .then((s) => {
          setVideoStatus(s);
          if (!s.is_loading && s.load_error) {
            setVideoGenError(`Model load failed: ${s.load_error}`);
          }
        })
        .catch((e) => {
          emitClientLog(
            "warn",
            `[media-gen] video status poll (load watchdog) failed: ${String(e)}`,
            "engine",
          );
        });
    }, 2000);
    return () => window.clearInterval(id);
  }, [videoIsLoading]);

  const videoLoadInFlight = loadingVideoModelId !== null || videoIsLoading;
  useEffect(() => {
    if (videoLoadInFlight) {
      setVideoLoadStartedAt((prev) => prev ?? Date.now());
    } else {
      setVideoLoadStartedAt(null);
    }
  }, [videoLoadInFlight]);

  const clearActiveJob = useCallback(() => setActiveJob(null), []);
  const clearVideoGenError = useCallback(() => setVideoGenError(null), []);

  // ── Shared managed-runtime controller ────────────────────────────────────
  // One owner for image + video and every layout. Components never open their
  // own stream or infer health from package versions.
  const stopRuntimeTransport = useCallback(() => {
    runtimeStreamCleanupRef.current?.();
    runtimeStreamCleanupRef.current = null;
    if (runtimePollRef.current !== null) {
      window.clearInterval(runtimePollRef.current);
      runtimePollRef.current = null;
    }
  }, []);

  const applyRuntimeSnapshot = useCallback(
    (status: MediaRuntimeStatus, enforceAttempt = true): boolean => {
      if (
        enforceAttempt &&
        !acceptsRuntimeSnapshot(
          status,
          runtimeExpectedAttemptRef.current,
          mediaRuntimeRef.current,
        )
      ) {
        emitClientLog(
          "warn",
          `[media-gen] ignored stale runtime snapshot for attempt ${status.attempt_id ?? "none"}`,
          "engine",
        );
        return false;
      }
      const wasReady = isRuntimeReady(mediaRuntimeRef.current);
      mediaRuntimeRef.current = status;
      setMediaRuntime(status);
      setMediaRuntimeError(null);
      setMediaRuntimeLoading(false);
      if (!isRuntimeActive(status)) stopRuntimeTransport();
      if (!wasReady && isRuntimeReady(status)) {
        // Runtime activation changes both service snapshots. Refresh them as one
        // shared transition so one surface can never remain on stale setup UI.
        void Promise.all([refreshImage(), refreshVideo()]);
      }
      return true;
    },
    [refreshImage, refreshVideo, stopRuntimeTransport],
  );

  const startRuntimePolling = useCallback(() => {
    if (runtimePollRef.current !== null) return;
    runtimePollRef.current = window.setInterval(() => {
      const base = engine.engineUrl;
      if (!base) return;
      void getMediaRuntimeStatus(base)
        .then((status) => {
          const accepted = applyRuntimeSnapshot(status);
          if (
            accepted &&
            !isRuntimeActive(status) &&
            runtimePollRef.current !== null
          ) {
            window.clearInterval(runtimePollRef.current);
            runtimePollRef.current = null;
          }
        })
        .catch((error) => {
          setMediaRuntimeError(
            error instanceof Error
              ? error.message
              : "Could not check the AI runtime",
          );
        });
    }, 1500);
  }, [applyRuntimeSnapshot]);

  const connectRuntimeStream = useCallback(
    async (base: string) => {
      runtimeStreamCleanupRef.current?.();
      const headers = await engine.getEngineAuthHeaders();
      const auth = (headers as Record<string, string>)["Authorization"];
      const token = auth ? auth.replace("Bearer ", "") : null;
      runtimeStreamCleanupRef.current = streamMediaRuntimeStatus(
        base,
        async () => token,
        (status) => void applyRuntimeSnapshot(status),
        startRuntimePolling,
      );
    },
    [applyRuntimeSnapshot, startRuntimePolling],
  );

  const refreshMediaRuntime = useCallback(async () => {
    const base = engine.engineUrl;
    if (!base) {
      setMediaRuntimeError(ENGINE_NOT_CONNECTED);
      setMediaRuntimeLoading(false);
      return;
    }
    const requestId = ++runtimeRequestRef.current;
    setMediaRuntimeLoading(true);
    try {
      const status = await getMediaRuntimeStatus(base);
      if (requestId !== runtimeRequestRef.current) return;
      // A direct status read is authoritative for the current engine instance.
      runtimeExpectedAttemptRef.current = status.attempt_id;
      applyRuntimeSnapshot(status, false);
      if (isRuntimeActive(status)) {
        await connectRuntimeStream(base).catch(() => startRuntimePolling());
      }
    } catch (error) {
      if (requestId !== runtimeRequestRef.current) return;
      setMediaRuntimeError(
        error instanceof Error ? error.message : "Could not check the AI runtime",
      );
      setMediaRuntimeLoading(false);
    }
  }, [applyRuntimeSnapshot, connectRuntimeStream, startRuntimePolling]);

  const runRuntimeOperation = useCallback(
    async (operation: "ensure" | "repair") => {
      const base = engine.engineUrl;
      if (!base) {
        setMediaRuntimeError(ENGINE_NOT_CONNECTED_ACTION);
        return;
      }
      stopRuntimeTransport();
      const requestId = ++runtimeRequestRef.current;
      setMediaRuntimeLoading(true);
      setMediaRuntimeError(null);
      try {
        const status =
          operation === "repair"
            ? await apiRepairMediaRuntime(base)
            : await apiEnsureMediaRuntime(base);
        if (requestId !== runtimeRequestRef.current) return;
        runtimeExpectedAttemptRef.current = status.attempt_id;
        applyRuntimeSnapshot(status, false);
        if (isRuntimeActive(status)) {
          await connectRuntimeStream(base).catch(() => startRuntimePolling());
        }
      } catch (error) {
        if (requestId !== runtimeRequestRef.current) return;
        setMediaRuntimeError(
          error instanceof Error
            ? error.message
            : `Could not ${operation} the AI runtime`,
        );
        setMediaRuntimeLoading(false);
      }
    },
    [
      applyRuntimeSnapshot,
      connectRuntimeStream,
      startRuntimePolling,
      stopRuntimeTransport,
    ],
  );

  const ensureMediaRuntime = useCallback(
    () => runRuntimeOperation("ensure"),
    [runRuntimeOperation],
  );
  const repairMediaRuntime = useCallback(
    () => runRuntimeOperation("repair"),
    [runRuntimeOperation],
  );

  const restartMediaRuntime = useCallback(async () => {
    if (!isTauri()) {
      setMediaRuntimeError(
        "Restart the desktop app to activate the validated AI runtime.",
      );
      return;
    }
    stopRuntimeTransport();
    setMediaRuntimeLoading(true);
    setMediaRuntimeError(null);
    runtimeExpectedAttemptRef.current = null;
    try {
      await restartSidecar();
      const deadline = Date.now() + 90_000;
      while (Date.now() < deadline) {
        await new Promise((resolve) => window.setTimeout(resolve, 750));
        const base = await engine.rediscover();
        if (!base) continue;
        try {
          const status = await getMediaRuntimeStatus(base);
          runtimeExpectedAttemptRef.current = status.attempt_id;
          applyRuntimeSnapshot(status, false);
          if (isRuntimeActive(status)) {
            await connectRuntimeStream(base).catch(() => startRuntimePolling());
          }
          return;
        } catch {
          // The owned sidecar has spawned but has not bound its API yet.
        }
      }
      throw new Error("The AI engine did not reconnect within 90 seconds.");
    } catch (error) {
      setMediaRuntimeError(
        error instanceof Error
          ? error.message
          : "Could not restart the AI engine",
      );
      setMediaRuntimeLoading(false);
    }
  }, [
    applyRuntimeSnapshot,
    connectRuntimeStream,
    startRuntimePolling,
    stopRuntimeTransport,
  ]);

  // ── Init fetches (in the hook, [] deps) ────────────────────────────────────
  useEffect(() => {
    void refreshMediaRuntime();
    void refreshImage();
    void refreshVideo();
    void refreshLoras();
  }, [refreshMediaRuntime, refreshImage, refreshVideo, refreshLoras]);

  // Reload whenever the engine (re)connects — recovery keyed on CONNECTIVITY,
  // not on an exact error string. Without this, a transient HTTP error during
  // engine warm-up (or any non-sentinel failure) permanently disarmed the
  // string-gated retry below and the page parked on a stale error card.
  useEffect(
    () =>
      engine.on("connected", () => {
        void refreshMediaRuntime();
        void refreshImage();
        void refreshVideo();
        void refreshLoras();
      }),
    [refreshMediaRuntime, refreshImage, refreshVideo, refreshLoras],
  );

  // While the engine URL is not yet available, retry — engine.engineUrl is set
  // outside React state and isn't reactive.  Gated on the specific error string
  // from EITHER surface (image or video), so a video-only reconnect also retries.
  useEffect(() => {
    if (
      imageStatusError !== ENGINE_NOT_CONNECTED &&
      videoStatusError !== ENGINE_NOT_CONNECTED
    )
      return;
    const id = window.setInterval(() => {
      if (engine.engineUrl) {
        void refreshImage();
        void refreshVideo();
        void refreshLoras();
      }
    }, 2500);
    return () => window.clearInterval(id);
  }, [
    imageStatusError,
    videoStatusError,
    refreshImage,
    refreshVideo,
    refreshLoras,
  ]);

  // Revoke any object URLs on final unmount to avoid leaks.
  useEffect(() => {
    return () => {
      stopRuntimeTransport();
      for (const url of Object.values(videoResultsRef.current)) {
        URL.revokeObjectURL(url);
      }
      for (const url of Object.values(imageJobThumbsRef.current)) {
        URL.revokeObjectURL(url);
      }
    };
  }, [stopRuntimeTransport]);

  const state: MediaGenState = {
    mediaRuntime,
    mediaRuntimeLoading,
    mediaRuntimeError,
    imageStatus,
    imageModels,
    imagePresets,
    imageStatusLoading,
    imageStatusError,
    imageModelLoading: loadingImageModelId !== null,
    loadingImageModelId,
    imageGenerating,
    imageCancelling,
    imageGenStartedAt,
    imageLoadStartedAt,
    imageGenError,
    imageQueueNotice,
    imageResult,
    selectedImageModelId,
    imageForm,
    imageJobs,
    imageJobsError,
    canLoadMoreImageHistory:
      imageHistoryLimit < 1000 &&
      imageJobs.filter(
        (job) =>
          job.status === "completed" ||
          job.status === "failed" ||
          job.status === "cancelled",
      ).length >= imageHistoryLimit,
    imageQueueState,
    imageBatches,
    imageJobThumbs,
    loraList,
    loraError,
    loraDownloads,
    loraNeedsCivitaiKey,
    videoStatus,
    videoModels,
    videoStatusLoading,
    videoStatusError,
    videoModelLoading: loadingVideoModelId !== null,
    loadingVideoModelId,
    videoGenerating,
    videoCancelling,
    videoLoadStartedAt,
    videoGenError,
    activeJob,
    jobs,
    videoResults,
    videoForm,
  };

  const actions = useMemo<MediaGenActions>(
    () => ({
      refreshMediaRuntime,
      ensureMediaRuntime,
      repairMediaRuntime,
      restartMediaRuntime,
      refreshImage,
      getImageModels,
      loadImageModel,
      unloadImageModel,
      downloadImageModel,
      downloadTextEncoder,
      generateImage,
      generateImageWorkflow,
      cancelImageGeneration,
      setSelectedImageModelId,
      clearImageResult,
      clearImageGenError,
      clearImageQueueNotice,
      setImageForm,
      prepareImageGenerate,
      remixImageForm,
      resetImageCommon,
      resetImageAdvanced,
      resetImageAll,
      refreshImageJobs,
      loadMoreImageHistory,
      enqueueImageJob,
      cancelImageJob,
      enqueueImageBatch,
      refreshImageQueue,
      setImageQueuePaused,
      reorderImageQueue,
      retryImageJob,
      cancelImageBatch,
      clearFinishedImageJobs,
      refreshLoras,
      downloadLora,
      deleteLora,
      inspectCustomModel,
      registerCustomModel,
      deleteCustomModel,
      useImageAsInput,
      beginImageRevision,
      endImageRevision,
      refreshVideo,
      loadVideoModel,
      unloadVideoModel,
      downloadVideoModel,
      generateVideo,
      cancelVideoGeneration,
      cancelVideoJob,
      fetchVideoResult,
      clearActiveJob,
      clearVideoGenError,
      setVideoForm,
      prepareVideoGenerate,
      resetVideoCommon,
      resetVideoAdvanced,
      resetVideoAll,
      fetchParams,
    }),
    [
      refreshMediaRuntime,
      ensureMediaRuntime,
      repairMediaRuntime,
      restartMediaRuntime,
      refreshImage,
      getImageModels,
      loadImageModel,
      unloadImageModel,
      downloadImageModel,
      downloadTextEncoder,
      generateImage,
      generateImageWorkflow,
      cancelImageGeneration,
      setSelectedImageModelId,
      clearImageResult,
      clearImageGenError,
      clearImageQueueNotice,
      setImageForm,
      prepareImageGenerate,
      remixImageForm,
      resetImageCommon,
      resetImageAdvanced,
      resetImageAll,
      refreshImageJobs,
      loadMoreImageHistory,
      enqueueImageJob,
      cancelImageJob,
      enqueueImageBatch,
      refreshImageQueue,
      setImageQueuePaused,
      reorderImageQueue,
      retryImageJob,
      cancelImageBatch,
      clearFinishedImageJobs,
      refreshLoras,
      downloadLora,
      deleteLora,
      inspectCustomModel,
      registerCustomModel,
      deleteCustomModel,
      useImageAsInput,
      beginImageRevision,
      endImageRevision,
      refreshVideo,
      loadVideoModel,
      unloadVideoModel,
      downloadVideoModel,
      generateVideo,
      cancelVideoGeneration,
      cancelVideoJob,
      fetchVideoResult,
      clearActiveJob,
      clearVideoGenError,
      setVideoForm,
      prepareVideoGenerate,
      resetVideoCommon,
      resetVideoAdvanced,
      resetVideoAll,
      fetchParams,
    ],
  );

  return [state, actions];
}
