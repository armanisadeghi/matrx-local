/**
 * useImageGenController — THE single canonical implementation of the image
 * generate-flow logic that every layout (Classic section, Studio, Workspace,
 * Gallery, Focus) previously re-implemented: model resolution, auto-prepare,
 * validation, request building (incl. img2img + LoRA), generate/enqueue,
 * seed reuse, size presets, error merging, and the completed-download →
 * catalog-refresh effect.
 *
 * Layout components call this ONCE per mounted variant and pass the returned
 * controller to the core form components. All durable state stays in
 * MediaGenContext — the controller only adds stable handlers plus a local,
 * presentation-only error line.
 *
 * React rules (repo CLAUDE.md → React Patterns): every handler is
 * useCallback'd, the controller object is useMemo'd, and every effect is
 * gated on the specific primitives being watched.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import type { ImageGenModelInfo } from "@/lib/api";
import type {
  ImageFormDefaults,
  ImageFormState,
  ImageGenerateInput,
  PickedImage,
} from "@/hooks/use-media-gen";
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
import { readPickedImage } from "./pickedImage";

export {
  MAX_INPUT_IMAGE_BYTES,
  pickedImageFromUrl,
  readPickedImage,
} from "./pickedImage";

export interface ImageGenController {
  /** The model the generate form works with (form defaults > selection > loaded). */
  model: ImageGenModelInfo | null;
  defaults: ImageFormDefaults | null;
  form: ImageFormState;
  /** Parsed advanced-JSON diff against the model defaults. */
  advanced: AdvancedOverrides;
  dimError: string | null;
  /** True when Generate/Queue must be disabled. */
  formInvalid: boolean;
  sizePresets: SizePreset[];
  /** Context genError merged with the controller-local one. */
  genError: string | null;
  dismissGenError: () => void;
  setLocalError: (message: string | null) => void;
  /** Number of queued/running image jobs. */
  activeJobCount: number;
  /** True while Apply creates children in a durable revision branch. */
  isRevision: boolean;
  /** Build the request body (null when the form is not submittable). */
  buildInput: () => ImageGenerateInput | null;
  handleGenerate: () => Promise<void>;
  handleEnqueue: () => Promise<void>;
  reuseSeed: (seed: number) => void;
  /** Load a model then prepare its form. Resolves true on success. */
  handleLoadModel: (model: ImageGenModelInfo) => Promise<boolean>;
  /** Start the weights download (progress via the DownloadManager). */
  handleDownloadModel: (model: ImageGenModelInfo) => void;
  /** Open the generate view for a model, keeping tweaks when it's the same one. */
  handleOpenGenerate: (model: ImageGenModelInfo) => void;
  handleUnload: () => Promise<void>;
  /** File-input change handler for the img2img input image (20 MB guard). */
  handlePickInitImage: (file: File) => void;
  clearInitImage: () => void;
}

export function useImageGenController(options?: {
  /** Called after a model was successfully loaded/opened for generation. */
  onAfterSelect?: () => void;
}): ImageGenController {
  const onAfterSelect = options?.onAfterSelect;
  const [state, actions] = useMediaGenApp();
  const {
    imageStatus,
    imageModels,
    imageGenError,
    selectedImageModelId,
    imageForm,
    imageJobs,
  } = state;
  const {
    refreshImage,
    loadImageModel,
    unloadImageModel,
    downloadImageModel,
    generateImage,
    clearImageGenError,
    setImageForm,
    prepareImageGenerate,
    enqueueImageJob,
  } = actions;

  const [localError, setLocalError] = useState<string | null>(null);

  // When an image_gen weights download completes, refresh the catalog so
  // `is_downloaded` flips without a manual reload. Gated narrowly on the
  // COUNT of completed entries in this category — never the downloads array.
  const { downloads } = useDownloadManager();
  const completedImageDownloads = useMemo(
    () =>
      downloads.filter(
        (d) => d.category === "image_gen" && d.status === "completed",
      ).length,
    [downloads],
  );
  useEffect(() => {
    if (completedImageDownloads === 0) return;
    void refreshImage();
  }, [completedImageDownloads, refreshImage]);

  // The model the generate form works with — canonical resolution order.
  const generateModelId =
    imageForm.defaults?.modelId ??
    selectedImageModelId ??
    imageStatus?.loaded_model_id ??
    null;
  const model = useMemo(
    () => imageModels.find((m) => m.model_id === generateModelId) ?? null,
    [imageModels, generateModelId],
  );

  // If the form defaults belong to a different model (or none — e.g. a model
  // was already loaded when the app started), fetch that model's full
  // parameter schema. Guarded so it runs once per model change, never loops.
  useEffect(() => {
    if (imageForm.paramsLoading) return;
    if (!model) return;
    if (imageForm.defaults?.modelId === model.model_id) return;
    void prepareImageGenerate(model);
  }, [
    imageForm.paramsLoading,
    imageForm.defaults?.modelId,
    model,
    prepareImageGenerate,
  ]);

  // ── Validation + request building ──────────────────────────────────────
  const defaults = imageForm.defaults;
  const advanced = useMemo(
    () =>
      computeAdvancedOverrides(
        imageForm.advancedText,
        defaults?.advanced ?? {},
      ),
    [imageForm.advancedText, defaults?.advanced],
  );
  const dimError = dimensionError(imageForm.width, imageForm.height);
  const formInvalid =
    !imageForm.prompt.trim() || !defaults || !advanced.ok || dimError !== null;
  const isRevision = imageForm.revision !== null;

  const buildInput = useCallback((): ImageGenerateInput | null => {
    const d = imageForm.defaults;
    if (!d) return null;
    const adv = computeAdvancedOverrides(imageForm.advancedText, d.advanced);
    if (!adv.ok) return null;
    // Resolve a concrete seed even for "random" so every result is
    // reproducible — the used seed is shown on the result and in the queue.
    const seed = parseSeedText(imageForm.seedText) ?? randomSeed();
    const useInitImage = d.supportsImg2Img && imageForm.initImage !== null;
    const enabledLoras = imageForm.loras
      .filter((l) => l.enabled)
      .map(({ id, scale }) => ({ id, scale }));
    const negativePrompt = d.supportsNegativePrompt
      ? imageForm.negativePrompt.trim() || undefined
      : undefined;
    const initImageB64 = useInitImage
      ? (imageForm.initImage as PickedImage).base64
      : undefined;
    // d.strength is null when the params endpoint omits it — that model
    // (e.g. flux2-klein) edits from the reference image directly and the
    // engine 400s on an explicit strength. Never send one there.
    const strength =
      useInitImage && d.strength !== null ? imageForm.strength : undefined;
    const loras = enabledLoras.length > 0 ? enabledLoras : undefined;
    const extraParams = adv.count > 0 ? adv.overrides : undefined;
    return {
      prompt: imageForm.prompt.trim(),
      model_id: d.modelId,
      steps: imageForm.steps,
      guidance: imageForm.guidance,
      width: imageForm.width,
      height: imageForm.height,
      seed,
      ...(negativePrompt !== undefined ? { negative_prompt: negativePrompt } : {}),
      ...(initImageB64 !== undefined ? { init_image_b64: initImageB64 } : {}),
      ...(strength !== undefined ? { strength } : {}),
      ...(imageForm.revision !== null
        ? {
            revision: {
              parent_item_id: imageForm.revision.parentItemId,
              root_item_id: imageForm.revision.rootItemId,
            },
          }
        : {}),
      ...(loras !== undefined ? { loras } : {}),
      ...(extraParams !== undefined ? { extra_params: extraParams } : {}),
    };
  }, [imageForm]);

  const handleGenerate = useCallback(async () => {
    const input = buildInput();
    if (!input) return;
    await generateImage(input);
  }, [buildInput, generateImage]);

  const handleEnqueue = useCallback(async () => {
    const input = buildInput();
    if (!input) return;
    // Queue and clear NOTHING — the prompt stays editable so the user can
    // immediately write the next one.
    await enqueueImageJob(input);
  }, [buildInput, enqueueImageJob]);

  const reuseSeed = useCallback(
    (seed: number) => setImageForm({ seedText: String(seed) }),
    [setImageForm],
  );

  // ── Model selection handlers ─────────────────────────────────────────────
  const handleLoadModel = useCallback(
    async (m: ImageGenModelInfo): Promise<boolean> => {
      setLocalError(null);
      if (
        imageForm.revision &&
        imageForm.defaults?.modelId !== m.model_id
      ) {
        setImageForm({ revision: null, initImage: null });
      }
      const result = await loadImageModel(m.model_id);
      if (result.success) {
        await prepareImageGenerate(m);
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
    [
      imageForm.revision,
      imageForm.defaults?.modelId,
      setImageForm,
      loadImageModel,
      prepareImageGenerate,
      onAfterSelect,
    ],
  );

  const handleOpenGenerate = useCallback(
    (m: ImageGenModelInfo) => {
      if (imageForm.defaults?.modelId === m.model_id) {
        // Same model — keep the user's tweaked settings, just switch views.
        setImageForm({ view: "generate" });
      } else {
        if (imageForm.revision) {
          setImageForm({ revision: null, initImage: null });
        }
        void prepareImageGenerate(m);
      }
      onAfterSelect?.();
    },
    [
      imageForm.defaults?.modelId,
      imageForm.revision,
      setImageForm,
      prepareImageGenerate,
      onAfterSelect,
    ],
  );

  const handleDownloadModel = useCallback(
    (m: ImageGenModelInfo) => {
      setLocalError(null);
      void downloadImageModel(m.model_id);
    },
    [downloadImageModel],
  );

  const handleUnload = useCallback(async () => {
    await unloadImageModel();
  }, [unloadImageModel]);

  // ── img2img input image ──────────────────────────────────────────────────
  const handlePickInitImage = useCallback(
    (file: File) => {
      readPickedImage(
        file,
        (initImage) => setImageForm({ initImage }),
        (msg) => setLocalError(msg),
      );
    },
    [setImageForm],
  );
  const clearInitImage = useCallback(
    () => setImageForm({ initImage: null, revision: null }),
    [setImageForm],
  );

  const genError = imageGenError ?? localError;
  const dismissGenError = useCallback(() => {
    setLocalError(null);
    clearImageGenError();
  }, [clearImageGenError]);

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
      { label: "512", width: 512, height: 512 },
      { label: "768", width: 768, height: 768 },
      { label: "1024", width: 1024, height: 1024 },
      { label: "1536", width: 1536, height: 1536 },
      { label: "Portrait 832×1216", width: 832, height: 1216 },
      { label: "Landscape 1216×832", width: 1216, height: 832 },
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

  const activeJobCount = useMemo(
    () =>
      imageJobs.filter((j) => j.status === "queued" || j.status === "running")
        .length,
    [imageJobs],
  );

  return useMemo<ImageGenController>(
    () => ({
      model,
      defaults,
      form: imageForm,
      advanced,
      dimError,
      formInvalid,
      sizePresets,
      genError,
      dismissGenError,
      setLocalError,
      activeJobCount,
      isRevision,
      buildInput,
      handleGenerate,
      handleEnqueue,
      reuseSeed,
      handleLoadModel,
      handleDownloadModel,
      handleOpenGenerate,
      handleUnload,
      handlePickInitImage,
      clearInitImage,
    }),
    [
      model,
      defaults,
      imageForm,
      advanced,
      dimError,
      formInvalid,
      sizePresets,
      genError,
      dismissGenError,
      activeJobCount,
      isRevision,
      buildInput,
      handleGenerate,
      handleEnqueue,
      reuseSeed,
      handleLoadModel,
      handleDownloadModel,
      handleOpenGenerate,
      handleUnload,
      handlePickInitImage,
      clearInitImage,
    ],
  );
}
