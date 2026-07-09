/**
 * useMediaGen — single source of truth for the media-generation experience
 * (image + video).  Lives behind `MediaGenProvider` (App.tsx) so that a
 * running video job and any generated results SURVIVE tab switches and page
 * navigation.  A 10-minute video job must never be orphaned by navigating away
 * from the Local Models page.
 *
 * React rules obeyed strictly (see repo CLAUDE.md → React Patterns):
 *  - `actions` is wrapped in useMemo and its callbacks are stable (useCallback).
 *  - Init fetches live here in the hook, on [] deps — never in a page effect.
 *  - The job poll interval is gated on the SPECIFIC booleans (job id + running
 *    state), always cleans up, and never restarts on unrelated re-renders.
 *  - No focus/visibility re-initialization.
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
  getVideoGenStatus,
  listVideoGenModels,
  listVideoGenJobs,
  loadVideoGenModel as apiLoadVideoGenModel,
  unloadVideoGenModel as apiUnloadVideoGenModel,
  downloadVideoGenModel as apiDownloadVideoGenModel,
  generateVideo as apiGenerateVideo,
  getVideoGenJob as apiGetVideoGenJob,
  fetchVideoGenResult as apiFetchVideoGenResult,
} from "@/lib/api";
import type {
  ImageGenStatus,
  ImageGenModelInfo,
  ImageGenWorkflowPreset,
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
}

export interface ImageWorkflowInput {
  preset_id: string;
  subject: string;
  model_id?: string;
  seed?: number;
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

  // Refs to read latest values inside stable callbacks without stale closures.
  const activeJobRef = useRef<VideoGenJob | null>(null);
  const videoResultsRef = useRef<Record<string, string>>({});
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

  // ── Image actions ─────────────────────────────────────────────────────────
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
      refreshVideo,
      loadVideoModel,
      unloadVideoModel,
      downloadVideoModel,
      generateVideo,
      fetchVideoResult,
      clearActiveJob,
      clearVideoGenError,
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
      refreshVideo,
      loadVideoModel,
      unloadVideoModel,
      downloadVideoModel,
      generateVideo,
      fetchVideoResult,
      clearActiveJob,
      clearVideoGenError,
    ],
  );

  return [state, actions];
}
