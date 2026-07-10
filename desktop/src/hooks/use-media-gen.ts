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
  generateImage as apiGenerateImage,
  generateImageFromWorkflow as apiGenerateImageWorkflow,
  enqueueImageGenJob as apiEnqueueImageGenJob,
  listImageGenJobs as apiListImageGenJobs,
  cancelImageGenJob as apiCancelImageGenJob,
  fetchMediaLibraryFile as apiFetchMediaLibraryFile,
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
} from "@/lib/api";
import type {
  ImageGenStatus,
  ImageGenModelInfo,
  ImageGenWorkflowPreset,
  ImageGenJob,
  MediaGenParams,
  MediaLoadResult,
  VideoGenStatus,
  VideoGenModelInfo,
  VideoGenJob,
  VideoGenRequest,
} from "@/lib/api";
import { emitClientLog } from "@/hooks/use-unified-log";

const ENGINE_NOT_CONNECTED = "Engine not connected";
// User-facing message for ACTION paths (download/load/generate/…) that cannot
// run because the engine URL is null. Distinct from ENGINE_NOT_CONNECTED, which
// is the status-error sentinel the reconnect effect matches on — do NOT reuse
// that here or you would trip the retry loop for a one-shot action failure.
const ENGINE_NOT_CONNECTED_ACTION =
  "Engine not connected — check the engine status and try again";

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
  /**
   * Extra diffusers pipeline kwargs (advanced settings) merged into the call.
   * Only CHANGED keys should be sent — the UI diffs against the defaults from
   * the params endpoint before building this object.
   */
  extra_params?: Record<string, unknown>;
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
}

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
}

export interface VideoFormDefaults extends ImageFormDefaults {
  numFrames: number;
  fps: number;
}

export interface VideoFormState extends Omit<ImageFormState, "defaults"> {
  numFrames: number;
  fps: number;
  sourceImage: { name: string; base64: string; previewUrl: string } | null;
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

export interface MediaGenState {
  // ── Image ──────────────────────────────────────────────────────────────
  imageStatus: ImageGenStatus | null;
  imageModels: ImageGenModelInfo[];
  imagePresets: ImageGenWorkflowPreset[];
  imageStatusLoading: boolean;
  imageStatusError: string | null;
  imageModelLoading: boolean;
  imageGenerating: boolean;
  imageGenError: string | null;
  imageResult: GeneratedImageResult | null;
  /** The model the user is currently working with (survives tab switches). */
  selectedImageModelId: string | null;
  /** Persistent generate-form state (survives navigation). */
  imageForm: ImageFormState;
  /** Image job queue, newest first (engine order). */
  imageJobs: ImageGenJob[];
  imageJobsError: string | null;
  /** jobId → object URL of a completed job's image (thumbnails). */
  imageJobThumbs: Record<string, string>;

  // ── Video ──────────────────────────────────────────────────────────────
  videoStatus: VideoGenStatus | null;
  videoModels: VideoGenModelInfo[];
  videoStatusLoading: boolean;
  videoStatusError: string | null;
  videoModelLoading: boolean;
  videoGenerating: boolean;
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
  // Image
  refreshImage: () => Promise<void>;
  loadImageModel: (modelId: string) => Promise<MediaLoadResult>;
  unloadImageModel: () => Promise<void>;
  downloadImageModel: (modelId: string) => Promise<boolean>;
  /** Resolves true only when a result was produced; false on any failure. */
  generateImage: (input: ImageGenerateInput) => Promise<boolean>;
  /** Resolves true only when a result was produced; false on any failure. */
  generateImageWorkflow: (input: ImageWorkflowInput) => Promise<boolean>;
  setSelectedImageModelId: (modelId: string | null) => void;
  clearImageResult: () => void;
  clearImageGenError: () => void;
  /** Patch the persistent image generate-form state. */
  setImageForm: (patch: Partial<ImageFormState>) => void;
  /**
   * Enter the generate view for a model: selects it, fetches its FULL
   * parameter schema and resets the form to the model's defaults (the prompt
   * text is preserved).  On params failure the catalog fallbacks are applied
   * and `imageForm.paramsError` is set — loud, never silent.
   */
  prepareImageGenerate: (model: ImageGenModelInfo) => Promise<void>;
  /** Reset the common controls to the current model's defaults. */
  resetImageCommon: () => void;
  /** Reset the advanced JSON to the current model's defaults. */
  resetImageAdvanced: () => void;
  /** Master reset: common + advanced (prompt text preserved). */
  resetImageAll: () => void;
  // Image queue
  refreshImageJobs: () => Promise<void>;
  /** Enqueue a job (same body as generate). True when accepted. */
  enqueueImageJob: (input: ImageGenerateInput) => Promise<boolean>;
  /** Cancel a queued job / remove a finished one. */
  cancelImageJob: (jobId: string) => Promise<void>;
  // Video
  refreshVideo: () => Promise<void>;
  loadVideoModel: (modelId: string) => Promise<MediaLoadResult>;
  unloadVideoModel: () => Promise<void>;
  downloadVideoModel: (modelId: string) => Promise<boolean>;
  generateVideo: (
    req: VideoGenRequest,
  ) => Promise<{ ok: boolean; error?: string }>;
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
  // ── Image state ──────────────────────────────────────────────────────────
  const [imageStatus, setImageStatus] = useState<ImageGenStatus | null>(null);
  const [imageModels, setImageModels] = useState<ImageGenModelInfo[]>([]);
  const [imagePresets, setImagePresets] = useState<ImageGenWorkflowPreset[]>(
    [],
  );
  const [imageStatusLoading, setImageStatusLoading] = useState(true);
  const [imageStatusError, setImageStatusError] = useState<string | null>(null);
  const [imageModelLoading, setImageModelLoading] = useState(false);
  const [imageGenerating, setImageGenerating] = useState(false);
  const [imageGenError, setImageGenError] = useState<string | null>(null);
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
  const [imageJobThumbs, setImageJobThumbs] = useState<Record<string, string>>(
    {},
  );

  // ── Video state ──────────────────────────────────────────────────────────
  const [videoStatus, setVideoStatus] = useState<VideoGenStatus | null>(null);
  const [videoModels, setVideoModels] = useState<VideoGenModelInfo[]>([]);
  const [videoStatusLoading, setVideoStatusLoading] = useState(true);
  const [videoStatusError, setVideoStatusError] = useState<string | null>(null);
  const [videoModelLoading, setVideoModelLoading] = useState(false);
  const [videoGenerating, setVideoGenerating] = useState(false);
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
  const refreshVideoRef = useRef<(() => Promise<void>) | null>(null);
  // Consecutive job-poll failures — after 3 (or an immediate 404) we synthesize
  // a terminal "failed" state so the poll interval tears down and the UI
  // unwedges instead of spinning forever against a dead/restarted engine.
  const pollFailuresRef = useRef(0);
  useEffect(() => {
    activeJobRef.current = activeJob;
  }, [activeJob]);
  useEffect(() => {
    videoResultsRef.current = videoResults;
  }, [videoResults]);
  useEffect(() => {
    imageJobThumbsRef.current = imageJobThumbs;
  }, [imageJobThumbs]);

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
    try {
      const list = await apiListImageGenJobs(base);
      setImageJobs(list);
      setImageJobsError(null);
    } catch (e) {
      setImageJobsError(
        e instanceof Error ? e.message : "Failed to load the image job queue",
      );
    }
  }, []);

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
      setImageModels(models);
      setImagePresets(presets);
      setImageStatusError(null);
    } catch (e) {
      setImageStatusError(
        e instanceof Error ? e.message : "Failed to load image generation",
      );
    } finally {
      setImageStatusLoading(false);
    }
    // Queue refresh rides along but never blocks/aborts the status load —
    // it reports its own error via imageJobsError.
    void refreshImageJobs();
  }, [refreshImageJobs]);

  const setImageForm = useCallback((patch: Partial<ImageFormState>) => {
    setImageFormState((prev) => ({ ...prev, ...patch }));
  }, []);

  const prepareImageGenerate = useCallback(
    async (model: ImageGenModelInfo) => {
      setSelectedImageModelIdState(model.model_id);
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
        };
      }
      setImageFormState((prev) => ({
        ...prev,
        view: "generate",
        paramsLoading: false,
        paramsError,
        defaults,
        // Apply the new model's defaults; the prompt text is preserved.
        negativePrompt: defaults.negativePrompt,
        steps: defaults.steps,
        guidance: defaults.guidance,
        width: defaults.width,
        height: defaults.height,
        seedText: "",
        advancedText: advancedJsonOf(defaults.advanced),
      }));
    },
    [fetchParams],
  );

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
        advancedText: advancedJsonOf(d.advanced),
      };
    });
  }, []);

  const loadImageModel = useCallback(
    async (modelId: string): Promise<MediaLoadResult> => {
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("load image model");
        setImageGenError(ENGINE_NOT_CONNECTED_ACTION);
        return { success: false, error: ENGINE_NOT_CONNECTED_ACTION };
      }
      setImageModelLoading(true);
      setImageGenError(null);
      try {
        const result = await apiLoadImageGenModel(base, modelId);
        if (result.success) {
          setSelectedImageModelIdState(modelId);
          await refreshImage();
        }
        return result;
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Failed to load model";
        setImageGenError(msg);
        return { success: false, error: msg };
      } finally {
        setImageModelLoading(false);
      }
    },
    [refreshImage],
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

  const generateImage = useCallback(
    async (input: ImageGenerateInput): Promise<boolean> => {
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("generate image");
        setImageGenError(ENGINE_NOT_CONNECTED_ACTION);
        return false;
      }
      setImageGenerating(true);
      setImageGenError(null);
      setImageResult(null);
      try {
        const result = await apiGenerateImage(base, input);
        if (result.success && result.image_b64) {
          setImageResult({
            b64: result.image_b64,
            elapsed: result.elapsed_seconds,
            width: result.width,
            height: result.height,
            seed: result.seed ?? input.seed ?? null,
            itemId: result.item_id ?? null,
            filePath: result.file_path ?? null,
          });
          return true;
        }
        setImageGenError(result.error ?? "Generation failed");
        return false;
      } catch (e) {
        setImageGenError(e instanceof Error ? e.message : "Generation failed");
        return false;
      } finally {
        setImageGenerating(false);
      }
    },
    [],
  );

  const generateImageWorkflow = useCallback(
    async (input: ImageWorkflowInput): Promise<boolean> => {
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("generate image workflow");
        setImageGenError(ENGINE_NOT_CONNECTED_ACTION);
        return false;
      }
      setImageGenerating(true);
      setImageGenError(null);
      setImageResult(null);
      try {
        const result = await apiGenerateImageWorkflow(base, input);
        if (result.success && result.image_b64) {
          setImageResult({
            b64: result.image_b64,
            elapsed: result.elapsed_seconds,
            width: result.width,
            height: result.height,
            seed: result.seed ?? input.seed ?? null,
            itemId: result.item_id ?? null,
            filePath: result.file_path ?? null,
          });
          return true;
        }
        setImageGenError(result.error ?? "Generation failed");
        return false;
      } catch (e) {
        setImageGenError(e instanceof Error ? e.message : "Generation failed");
        return false;
      } finally {
        setImageGenerating(false);
      }
    },
    [],
  );

  const enqueueImageJob = useCallback(
    async (input: ImageGenerateInput): Promise<boolean> => {
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("enqueue image job");
        setImageGenError(ENGINE_NOT_CONNECTED_ACTION);
        return false;
      }
      try {
        await apiEnqueueImageGenJob(base, input);
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

  // Poll the image job queue every 2s ONLY while something is queued/running.
  const hasActiveImageJobs = imageJobs.some(
    (j) => j.status === "queued" || j.status === "running",
  );
  useEffect(() => {
    if (!hasActiveImageJobs) return;
    const id = window.setInterval(() => {
      void refreshImageJobs();
    }, 2000);
    return () => window.clearInterval(id);
  }, [hasActiveImageJobs, refreshImageJobs]);

  // Fetch thumbnails for completed queue jobs (via the media-library file
  // endpoint).  Gated on the joined "jobId:itemId" list of completed jobs so
  // it re-runs only when that set actually changes.
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
      void apiFetchMediaLibraryFile(base, itemId)
        .then((url) => {
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
        .catch((e) => {
          emitClientLog(
            "warn",
            `[media-gen] thumbnail fetch failed for job ${jobId}: ${String(e)}`,
            "engine",
          );
        });
    }
    return () => {
      cancelled = true;
    };
  }, [completedJobItems]);

  const setSelectedImageModelId = useCallback((modelId: string | null) => {
    setSelectedImageModelIdState(modelId);
  }, []);
  const clearImageResult = useCallback(() => setImageResult(null), []);
  const clearImageGenError = useCallback(() => setImageGenError(null), []);

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
        listVideoGenModels(base).catch(() => [] as VideoGenModelInfo[]),
        listVideoGenJobs(base).catch(() => [] as VideoGenJob[]),
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
          const job = await apiGetVideoGenJob(
            base,
            status.active_job_id,
          ).catch(() => null);
          if (job) setActiveJob(job);
        }
      }
    } catch (e) {
      setVideoStatusError(
        e instanceof Error ? e.message : "Failed to load video generation",
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
              URL.revokeObjectURL(next[staleKey]);
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
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("load video model");
        setVideoGenError(ENGINE_NOT_CONNECTED_ACTION);
        return { success: false, error: ENGINE_NOT_CONNECTED_ACTION };
      }
      setVideoModelLoading(true);
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
        setVideoModelLoading(false);
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
      const base = engine.engineUrl;
      if (!base) {
        logEngineNotConnected("generate video");
        setVideoGenError(ENGINE_NOT_CONNECTED_ACTION);
        return { ok: false, error: ENGINE_NOT_CONNECTED_ACTION };
      }
      setVideoGenerating(true);
      setVideoGenError(null);
      try {
        const { job_id } = await apiGenerateVideo(base, req);
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

  const clearActiveJob = useCallback(() => setActiveJob(null), []);
  const clearVideoGenError = useCallback(() => setVideoGenError(null), []);

  // ── Init fetches (in the hook, [] deps) ────────────────────────────────────
  useEffect(() => {
    void refreshImage();
    void refreshVideo();
  }, [refreshImage, refreshVideo]);

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
      }
    }, 2500);
    return () => window.clearInterval(id);
  }, [imageStatusError, videoStatusError, refreshImage, refreshVideo]);

  // Revoke any object URLs on final unmount to avoid leaks.
  useEffect(() => {
    return () => {
      for (const url of Object.values(videoResultsRef.current)) {
        URL.revokeObjectURL(url);
      }
      for (const url of Object.values(imageJobThumbsRef.current)) {
        URL.revokeObjectURL(url);
      }
    };
  }, []);

  const state: MediaGenState = {
    imageStatus,
    imageModels,
    imagePresets,
    imageStatusLoading,
    imageStatusError,
    imageModelLoading,
    imageGenerating,
    imageGenError,
    imageResult,
    selectedImageModelId,
    imageForm,
    imageJobs,
    imageJobsError,
    imageJobThumbs,
    videoStatus,
    videoModels,
    videoStatusLoading,
    videoStatusError,
    videoModelLoading,
    videoGenerating,
    videoGenError,
    activeJob,
    jobs,
    videoResults,
    videoForm,
  };

  const actions = useMemo<MediaGenActions>(
    () => ({
      refreshImage,
      loadImageModel,
      unloadImageModel,
      downloadImageModel,
      generateImage,
      generateImageWorkflow,
      setSelectedImageModelId,
      clearImageResult,
      clearImageGenError,
      setImageForm,
      prepareImageGenerate,
      resetImageCommon,
      resetImageAdvanced,
      resetImageAll,
      refreshImageJobs,
      enqueueImageJob,
      cancelImageJob,
      refreshVideo,
      loadVideoModel,
      unloadVideoModel,
      downloadVideoModel,
      generateVideo,
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
      refreshImage,
      loadImageModel,
      unloadImageModel,
      downloadImageModel,
      generateImage,
      generateImageWorkflow,
      setSelectedImageModelId,
      clearImageResult,
      clearImageGenError,
      setImageForm,
      prepareImageGenerate,
      resetImageCommon,
      resetImageAdvanced,
      resetImageAll,
      refreshImageJobs,
      enqueueImageJob,
      cancelImageJob,
      refreshVideo,
      loadVideoModel,
      unloadVideoModel,
      downloadVideoModel,
      generateVideo,
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
