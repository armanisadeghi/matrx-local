import { describe, expect, it } from "vitest";
import type { ImageFormDefaults } from "@/hooks/use-media-gen";
import {
  buildCustomQueueInput,
  buildQuickQueueInput,
  customQueueSettingsFromDefaults,
} from "./quick-queue";

const defaults: ImageFormDefaults = {
  modelId: "flux-dev",
  steps: 28,
  guidance: 3.5,
  width: 1024,
  height: 1024,
  negativePrompt: "blur",
  advanced: {},
  supportsNegativePrompt: true,
  supportsImg2Img: false,
  strength: null,
};

describe("quick queue", () => {
  it("builds quick input from model defaults without a seed", () => {
    expect(
      buildQuickQueueInput(defaults, {
        prompt: "  a cat  ",
        negativePrompt: "noise",
      }),
    ).toEqual({
      prompt: "a cat",
      model_id: "flux-dev",
      steps: 28,
      guidance: 3.5,
      width: 1024,
      height: 1024,
      negative_prompt: "noise",
    });
    expect(
      buildQuickQueueInput(defaults, { prompt: "x" })?.seed,
    ).toBeUndefined();
  });

  it("builds custom input with optional seed", () => {
    const settings = {
      ...customQueueSettingsFromDefaults(defaults),
      seedText: "42",
      steps: 20,
    };
    expect(
      buildCustomQueueInput(defaults, { prompt: "dog" }, settings),
    ).toEqual({
      prompt: "dog",
      model_id: "flux-dev",
      steps: 20,
      guidance: 3.5,
      width: 1024,
      height: 1024,
      seed: 42,
      negative_prompt: "blur",
    });
    expect(
      buildCustomQueueInput(defaults, { prompt: "dog" }, settings)?.seed,
    ).toBe(42);
    expect(
      buildCustomQueueInput(
        defaults,
        { prompt: "dog" },
        {
          ...settings,
          seedText: "",
        },
      )?.seed,
    ).toBeUndefined();
  });
});
