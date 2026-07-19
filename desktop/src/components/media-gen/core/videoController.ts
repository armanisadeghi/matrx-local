/**
 * useVideoGenController — the video twin of useImageGenController: the ONE
 * canonical implementation of model resolution, auto-prepare, validation,
 * request building, generate/cancel, source-image picking, playback state and
 * the completed-download → catalog-refresh effect that every layout variant
 * previously re-implemented.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import type { VideoGenModelInfo, VideoGenRequest } from "@/lib/api";
import type { VideoFormDefaults, VideoFormState } from "@/hooks/use-media-gen";
import {
  computeAdvancedOverrides,
  dimensionError,
  parseSeedText,
  randomSeed,
} from "@/components/media-gen/shared";
import type {
  AdvancedOverrides,
  SizePreset,
} from "@/components/media-gen/shared";
import { readPickedImage } from "./imageController";

export interface VideoGenController {
  /** The model the form works with (form defaults > loaded model). */
  model: VideoGenModelInfo | null;
  defaults: VideoFormDefaults | null;
  form: VideoFormState;
  advanced: AdvancedOverrides;
  dimError: string | null;
  formInvalid: boolean;
  sizePresets: SizePreset[];
  maxFrames: number;
  approxSeconds: number;
  /** True while the watched job is queued/running. */
  jobIsActive: boolean;
  genError: string | null;
  dismissGenError: () => void;
  setLocalError: (message: string | null) => void;
  buildRequest: () => VideoGenRequest | null;
  handleGenerate: () => Promise<void>;
  reuseSeed: (seed: number) => void;
  handleLoadModel: (model: VideoGenModelInfo) => Promise<boolean>;
  handleDownloadModel: (model: VideoGenModelInfo) => void;
  handleOpenGenerate: (model: VideoGenModelInfo) => void;
  handleUnload: () => Promise<void>;
  handlePickSourceImage: (file: File) => void;
  clearSourceImage: () => void;
  /** Playback: the selected/completed job's blob URL (null while fetching). */
  playbackUrl: string | null;
  /** Non-null while a Play click is fetching bytes. */
  playbackJobId: string | null;
  handlePlay: (jobId: string) => void;
}

export function useVideoGenController(options?: {
  onAfterSelect?: () => void;
}): VideoGenController {
  const onAfterSelect = options?.onAfterSelect;
  const [state, actions] = useMediaGenApp();
  const {
    mediaRuntime,
    videoStatus,
    videoModels,
    videoGenError,
    activeJob,
    videoResults,
    videoForm,
  } = state;
  const {
    refreshVideo,
    loadVideoModel,
    unloadVideoModel,
    downloadVideoModel,
    generateVideo,
    fetchVideoResult,
    clearVideoGenError,
    setVideoForm,
    prepareVideoGenerate,
  } = actions;

  const [localError, setLocalError] = useState<string | null>(null);
  const [playbackJobId, setPlaybackJobId] = useState<string | null>(null);

  // Refresh the catalog when a video_gen weights download completes.
  const { downloads } = useDownloadManager();
  const completedVideoDownloads = useMemo(
    () =>
      downloads.filter(
        (d) => d.category === "video_gen" && d.status === "completed",
      ).length,
    [downloads],
  );
  useEffect(() => {
    if (completedVideoDownloads === 0) return;
    void refreshVideo();
  }, [completedVideoDownloads, refreshVideo]);

  const loadedModel = useMemo(
    () =>
      videoModels.find((m) => m.model_id === videoStatus?.loaded_model_id) ??
      null,
    [videoModels, videoStatus?.loaded_model_id],
  );
  const model = useMemo(
    () =>
      videoModels.find((m) => m.model_id === videoForm.defaults?.modelId) ??
      loadedModel,
    [videoModels, videoForm.defaults?.modelId, loadedModel],
  );

  // Auto-prepare: fetch the full parameter schema when the form defaults
  // belong to a different model (or none). Runs once per model change.
  useEffect(() => {
    if (videoForm.paramsLoading) return;
    if (!model) return;
    if (videoForm.defaults?.modelId === model.model_id) return;
    void prepareVideoGenerate(model);
  }, [
    videoForm.paramsLoading,
    videoForm.defaults?.modelId,
    model,
    prepareVideoGenerate,
  ]);

  // ── Validation + request building ──────────────────────────────────────
  const defaults = videoForm.defaults;
  const advanced = useMemo(
    () =>
      computeAdvancedOverrides(
        videoForm.advancedText,
        defaults?.advanced ?? {},
      ),
    [videoForm.advancedText, defaults?.advanced],
  );
  const dimError = dimensionError(videoForm.width, videoForm.height);
  const formInvalid =
    mediaRuntime?.state !== "ready" ||
    mediaRuntime.video_packages_available !== true ||
    !videoForm.prompt.trim() ||
    !defaults ||
    !advanced.ok ||
    dimError !== null;
  const jobIsActive =
    activeJob?.status === "queued" || activeJob?.status === "running";

  const buildRequest = useCallback((): VideoGenRequest | null => {
    if (
      mediaRuntime?.state !== "ready" ||
      !mediaRuntime.video_packages_available
    ) {
      return null;
    }
    const d = videoForm.defaults;
    if (!d || !videoForm.prompt.trim()) return null;
    const adv = computeAdvancedOverrides(videoForm.advancedText, d.advanced);
    if (!adv.ok) return null;
    const seed = parseSeedText(videoForm.seedText) ?? randomSeed();
    const negativePrompt = d.supportsNegativePrompt
      ? videoForm.negativePrompt.trim() || undefined
      : undefined;
    const imageBase64 = videoForm.sourceImage?.base64;
    const extraParams = adv.count > 0 ? adv.overrides : undefined;
    return {
      prompt: videoForm.prompt.trim(),
      model_id: d.modelId,
      width: videoForm.width,
      height: videoForm.height,
      num_frames: videoForm.numFrames,
      fps: videoForm.fps,
      steps: videoForm.steps,
      guidance: videoForm.guidance,
      seed,
      ...(negativePrompt !== undefined ? { negative_prompt: negativePrompt } : {}),
      ...(imageBase64 !== undefined ? { image_base64: imageBase64 } : {}),
      ...(extraParams !== undefined ? { extra_params: extraParams } : {}),
    };
  }, [videoForm, mediaRuntime]);

  const handleGenerate = useCallback(async () => {
    const req = buildRequest();
    if (!req) return;
    setLocalError(null);
    const result = await generateVideo(req);
    if (result.ok) setPlaybackJobId(null);
  }, [buildRequest, generateVideo]);

  const reuseSeed = useCallback(
    (seed: number) => setVideoForm({ seedText: String(seed) }),
    [setVideoForm],
  );

  const handleLoadModel = useCallback(
    async (m: VideoGenModelInfo): Promise<boolean> => {
      setLocalError(null);
      const result = await loadVideoModel(m.model_id);
      if (result.success) {
        await prepareVideoGenerate(m);
        onAfterSelect?.();
        return true;
      }
      if (result.needs_download) {
        setLocalError(
          `${m.name} is not downloaded yet. Use the Download button first.`,
        );
      } else if (result.error) {
        setLocalError(result.error);
      }
      return false;
    },
    [loadVideoModel, prepareVideoGenerate, onAfterSelect],
  );

  const handleOpenGenerate = useCallback(
    (m: VideoGenModelInfo) => {
      if (videoForm.defaults?.modelId === m.model_id) {
        setVideoForm({ view: "generate" });
      } else {
        void prepareVideoGenerate(m);
      }
      onAfterSelect?.();
    },
    [
      videoForm.defaults?.modelId,
      setVideoForm,
      prepareVideoGenerate,
      onAfterSelect,
    ],
  );

  const handleDownloadModel = useCallback(
    (m: VideoGenModelInfo) => {
      setLocalError(null);
      void downloadVideoModel(m.model_id);
    },
    [downloadVideoModel],
  );

  const handleUnload = useCallback(async () => {
    await unloadVideoModel();
  }, [unloadVideoModel]);

  const handlePickSourceImage = useCallback(
    (file: File) => {
      readPickedImage(
        file,
        (sourceImage) => setVideoForm({ sourceImage }),
        (msg) => setLocalError(msg),
      );
    },
    [setVideoForm],
  );
  const clearSourceImage = useCallback(
    () => setVideoForm({ sourceImage: null }),
    [setVideoForm],
  );

  const handlePlay = useCallback(
    (jobId: string) => {
      setPlaybackJobId(jobId);
      void fetchVideoResult(jobId);
    },
    [fetchVideoResult],
  );

  const playbackUrl = playbackJobId
    ? (videoResults[playbackJobId] ?? null)
    : activeJob?.status === "completed"
      ? (videoResults[activeJob.job_id] ?? null)
      : null;

  const genError = videoGenError ?? localError;
  const dismissGenError = useCallback(() => {
    setLocalError(null);
    clearVideoGenError();
  }, [clearVideoGenError]);

  const sizePresets = useMemo<SizePreset[]>(() => {
    const base: SizePreset[] = defaults
      ? [
          {
            label: `Default ${defaults.width}×${defaults.height}`,
            width: defaults.width,
            height: defaults.height,
          },
        ]
      : [];
    const fixed: SizePreset[] = [
      { label: "Landscape 832×480", width: 832, height: 480 },
      { label: "Portrait 480×832", width: 480, height: 832 },
      { label: "Wide 1280×704", width: 1280, height: 704 },
    ];
    return [
      ...base,
      ...fixed.filter(
        (p) =>
          !defaults ||
          p.width !== defaults.width ||
          p.height !== defaults.height,
      ),
    ];
  }, [defaults]);

  const maxFrames = model && model.max_num_frames > 0 ? model.max_num_frames : 200;
  const approxSeconds =
    videoForm.fps > 0 ? videoForm.numFrames / videoForm.fps : 0;

  return useMemo<VideoGenController>(
    () => ({
      model,
      defaults,
      form: videoForm,
      advanced,
      dimError,
      formInvalid,
      sizePresets,
      maxFrames,
      approxSeconds,
      jobIsActive,
      genError,
      dismissGenError,
      setLocalError,
      buildRequest,
      handleGenerate,
      reuseSeed,
      handleLoadModel,
      handleDownloadModel,
      handleOpenGenerate,
      handleUnload,
      handlePickSourceImage,
      clearSourceImage,
      playbackUrl,
      playbackJobId,
      handlePlay,
    }),
    [
      model,
      defaults,
      videoForm,
      advanced,
      dimError,
      formInvalid,
      sizePresets,
      maxFrames,
      approxSeconds,
      jobIsActive,
      genError,
      dismissGenError,
      buildRequest,
      handleGenerate,
      reuseSeed,
      handleLoadModel,
      handleDownloadModel,
      handleOpenGenerate,
      handleUnload,
      handlePickSourceImage,
      clearSourceImage,
      playbackUrl,
      playbackJobId,
      handlePlay,
    ],
  );
}
