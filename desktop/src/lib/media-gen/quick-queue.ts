/**
 * Canonical quick-queue path — one prompt → image job with explicit placement.
 *
 * Quick (top/bottom): current model, model defaults, no seed, no form overrides.
 * Custom: same model + user overrides from the popover; seed only when set.
 */

import type {
  ImageFormDefaults,
  ImageGenerateInput,
} from "@/hooks/use-media-gen";
import { parseSeedText } from "@/components/media-gen/shared";

export type QueuePlacement = "top" | "bottom";

export interface QuickQueuePrompt {
  prompt: string;
  negativePrompt?: string;
}

export interface CustomQueueSettings {
  steps: number;
  guidance: number;
  width: number;
  height: number;
  /** Empty string = omit seed (engine picks). */
  seedText: string;
  negativePrompt: string;
}

export function customQueueSettingsFromDefaults(
  defaults: ImageFormDefaults,
): CustomQueueSettings {
  return {
    steps: defaults.steps,
    guidance: defaults.guidance,
    width: defaults.width,
    height: defaults.height,
    seedText: "",
    negativePrompt: defaults.negativePrompt,
  };
}

function resolveNegativePrompt(
  defaults: ImageFormDefaults,
  negativePrompt: string | undefined,
  override?: string,
): string | undefined {
  if (!defaults.supportsNegativePrompt) return undefined;
  const text = (override ?? negativePrompt ?? defaults.negativePrompt).trim();
  return text.length > 0 ? text : undefined;
}

/** Quick queue: model defaults only, never a seed. */
export function buildQuickQueueInput(
  defaults: ImageFormDefaults,
  { prompt, negativePrompt }: QuickQueuePrompt,
): ImageGenerateInput | null {
  const trimmed = prompt.trim();
  if (!trimmed) return null;
  const negative = resolveNegativePrompt(defaults, negativePrompt);
  return {
    prompt: trimmed,
    model_id: defaults.modelId,
    steps: defaults.steps,
    guidance: defaults.guidance,
    width: defaults.width,
    height: defaults.height,
    ...(negative !== undefined ? { negative_prompt: negative } : {}),
  };
}

/** Custom queue: explicit overrides; seed only when seedText is non-empty. */
export function buildCustomQueueInput(
  defaults: ImageFormDefaults,
  { prompt, negativePrompt }: QuickQueuePrompt,
  settings: CustomQueueSettings,
): ImageGenerateInput | null {
  const trimmed = prompt.trim();
  if (!trimmed) return null;
  const negative = resolveNegativePrompt(
    defaults,
    negativePrompt,
    settings.negativePrompt,
  );
  const seed = parseSeedText(settings.seedText.trim());
  return {
    prompt: trimmed,
    model_id: defaults.modelId,
    steps: settings.steps,
    guidance: settings.guidance,
    width: settings.width,
    height: settings.height,
    ...(typeof seed === "number" ? { seed } : {}),
    ...(negative !== undefined ? { negative_prompt: negative } : {}),
  };
}

export async function enqueueQuickQueueJob(
  enqueueImageJob: (
    input: ImageGenerateInput,
    priority?: "normal" | "next",
  ) => Promise<boolean>,
  input: ImageGenerateInput,
  placement: QueuePlacement,
): Promise<boolean> {
  return enqueueImageJob(input, placement === "top" ? "next" : "normal");
}

export function quickQueueBlockedReason(
  defaults: ImageFormDefaults | null,
  imageAvailable: boolean,
  prompt: string,
): string | null {
  if (!imageAvailable) return "Image generation is not available.";
  if (!defaults) return "Load a model on Images first.";
  if (!prompt.trim()) return "Prompt is empty.";
  return null;
}
