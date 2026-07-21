import { describe, expect, it } from "vitest";

import {
  classifyLoraCompatibility,
  disableIncompatibleLoraSelections,
  loraMatchesSearch,
  loraVisibleForModel,
  normalizeLoraFamily,
} from "./lora-compatibility";

const klein = { pipeline_type: "flux2-klein", lora_family: "flux2" };
const zImage = { pipeline_type: "z-image", lora_family: "z-image" };

function lora(id: string, baseFamily: string) {
  return {
    id,
    repo_id: `civitai:${id}`,
    name: `Style ${id}`,
    description: `${baseFamily} anatomy adapter`,
    base_family: baseFamily,
    source: "civitai",
    license: "civitai (see model page)",
  };
}

describe("LoRA family compatibility", () => {
  it("normalizes pipeline aliases to the engine family contract", () => {
    expect(normalizeLoraFamily("Flux.2 Klein")).toBe("flux2");
    expect(normalizeLoraFamily("stable-diffusion-xl")).toBe("sdxl");
    expect(normalizeLoraFamily("z-image")).toBe("zimage");
    expect(classifyLoraCompatibility("flux2", klein)).toBe("compatible");
    expect(classifyLoraCompatibility("z-image", klein)).toBe("incompatible");
    expect(classifyLoraCompatibility("unknown", klein)).toBe("unknown");
  });

  it("matches SDXL turbo models to sdxl LoRAs", () => {
    const sdxlTurbo = {
      pipeline_type: "stable-diffusion-xl",
      lora_family: "sdxl",
    };
    expect(classifyLoraCompatibility("sdxl", sdxlTurbo)).toBe("compatible");
    expect(classifyLoraCompatibility("stable-diffusion-xl", sdxlTurbo)).toBe(
      "compatible",
    );
  });

  it("defaults to confirmed matches and reveals every family on override", () => {
    expect(loraVisibleForModel(lora("1", "z-image"), zImage, false)).toBe(true);
    expect(loraVisibleForModel(lora("1", "flux2"), zImage, false)).toBe(false);
    expect(loraVisibleForModel(lora("1", "unknown"), zImage, false)).toBe(
      false,
    );
    expect(loraVisibleForModel(lora("1", "flux2"), zImage, true)).toBe(true);
  });

  it("preserves selections and disables only confirmed cross-family adapters", () => {
    const selections = [
      { id: "klein", scale: 0.8, enabled: true },
      { id: "zimage", scale: 1.1, enabled: true },
      { id: "unknown", scale: 0.6, enabled: true },
      { id: "missing", scale: 0.5, enabled: true },
    ];
    const result = disableIncompatibleLoraSelections(
      selections,
      [
        lora("klein", "flux2"),
        lora("zimage", "z-image"),
        lora("unknown", "unknown"),
      ],
      zImage,
    );

    expect(result).toEqual([
      { id: "klein", scale: 0.8, enabled: false },
      { id: "zimage", scale: 1.1, enabled: true },
      { id: "unknown", scale: 0.6, enabled: true },
      { id: "missing", scale: 0.5, enabled: true },
    ]);
  });

  it("scales filtering and search across hundreds of entries", () => {
    const library = Array.from({ length: 500 }, (_, index) =>
      lora(String(index), index % 2 === 0 ? "flux2" : "z-image"),
    );
    expect(
      library.filter((entry) => loraVisibleForModel(entry, klein, false)),
    ).toHaveLength(250);
    expect(
      library.filter((entry) => loraMatchesSearch(entry, "style 42")),
    ).toHaveLength(11);
  });
});
