import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ImageGenLoraInfo } from "@/lib/api";
import type { ImageGenController } from "./imageController";

let installed: ImageGenLoraInfo[] = [];
let selections: Array<{ id: string; scale: number; enabled: boolean }> = [];

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => vi.fn() };
});

vi.mock("@/contexts/DownloadManagerContext", () => ({
  useDownloadManager: () => ({ downloads: [] }),
}));

vi.mock("@/contexts/MediaGenContext", () => ({
  useMediaGenApp: () => [
    {
      loraList: { installed, catalog: [] },
      loraError: null,
      loraDownloads: {},
      loraNeedsCivitaiKey: false,
    },
    {
      setImageForm: vi.fn(),
      deleteLora: vi.fn(),
      downloadLora: vi.fn(),
      refreshLoras: vi.fn(),
    },
  ],
}));

import { LoraStylesSection } from "./LoraManager";

function makeLora(index: number): ImageGenLoraInfo {
  return {
    id: `style-${index}`,
    repo_id: `civitai:${index}@${index}`,
    name: `Library style ${index}`,
    description: "Test adapter",
    weight_name: `style-${index}.safetensors`,
    base_family: "flux2",
    size_bytes: 1024,
    added_at: null,
    source: "civitai",
    installed: true,
  };
}

function controller(): ImageGenController {
  return {
    form: { loras: selections },
    model: {
      model_id: "flux-klein-4b",
      name: "FLUX.2 Klein 4B",
      pipeline_type: "flux2-klein",
      lora_family: "flux2",
    },
  } as unknown as ImageGenController;
}

describe("compact LoRA page summary", () => {
  beforeEach(() => {
    installed = Array.from({ length: 300 }, (_, index) => makeLora(index));
    selections = [];
  });

  it("does not render a large installed library inline", () => {
    const html = renderToStaticMarkup(<LoraStylesSection ctl={controller()} />);
    expect(html).toContain("No active adapters");
    expect(html).toContain("Add / Manage");
    expect(html).not.toContain("Library style 299");
  });

  it("renders only the current active adapters with strength and removal", () => {
    selections = [{ id: "style-42", scale: 0.75, enabled: true }];
    const html = renderToStaticMarkup(<LoraStylesSection ctl={controller()} />);
    expect(html).toContain("1 active");
    expect(html).toContain("Library style 42");
    expect(html).toContain("0.75");
    expect(html).not.toContain("Library style 43");
  });
});
