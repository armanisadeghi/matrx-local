import { describe, expect, it } from "vitest";
import {
  findMediaIndexById,
  descriptorFromResult,
  descriptorSupportsRevision,
  mediaFocusId,
  mediaMatchesId,
  type MediaDescriptor,
} from "./types";

function descriptor(
  id: string,
  itemId: string | null,
  source: MediaDescriptor["source"] = "library",
): MediaDescriptor {
  return {
    id,
    itemId,
    source,
    kind: "image",
    url: `blob:${id}`,
  };
}

describe("media identity", () => {
  it("anchors persisted media on the engine file id", () => {
    const jobDescriptor = descriptor("job-123", "file-abc", "job");

    expect(mediaFocusId(jobDescriptor)).toBe("file-abc");
    expect(mediaMatchesId(jobDescriptor, "file-abc")).toBe(true);
    expect(mediaMatchesId(jobDescriptor, "job-123")).toBe(true);
  });

  it("finds the exact file when descriptor display ids differ", () => {
    const items = [
      descriptor("file-before", "file-before"),
      descriptor("job-clicked", "file-clicked", "job"),
      descriptor("file-after", "file-after"),
    ];

    expect(findMediaIndexById(items, "file-clicked")).toBe(1);
    expect(findMediaIndexById(items, "job-clicked")).toBe(1);
  });

  it("falls back to descriptor id for unpersisted results", () => {
    const result = descriptor("generated-result", null, "result");

    expect(mediaFocusId(result)).toBe("generated-result");
    expect(findMediaIndexById([result], "generated-result")).toBe(0);
  });

  it("offers Iterate only for persisted Z-Image and FLUX images", () => {
    const flux = {
      ...descriptor("flux-item", "flux-item"),
      modelId: "black-forest-labs/FLUX.2-klein-4B",
    };
    const sdxl = {
      ...descriptor("sdxl-item", "sdxl-item"),
      modelId: "stabilityai/sdxl-turbo",
    };

    expect(descriptorSupportsRevision(flux)).toBe(true);
    expect(descriptorSupportsRevision(sdxl)).toBe(false);
  });

  it("builds fresh-result metadata from its immutable request snapshot", () => {
    const result = descriptorFromResult({
      b64: "png",
      elapsed: 1,
      width: 1024,
      height: 1024,
      seed: 7,
      itemId: "child-1",
      filePath: "/tmp/child-1.png",
      request: {
        prompt: "make the jacket red",
        model_id: "black-forest-labs/FLUX.1-schnell",
        has_init_image: true,
        steps: 4,
        revision: {
          parent_item_id: "parent-1",
          root_item_id: "root-1",
        },
      },
    });

    expect(result.prompt).toBe("make the jacket red");
    expect(result.modelId).toBe("black-forest-labs/FLUX.1-schnell");
    expect(result.params?.["revision_parent_item_id"]).toBe("parent-1");
    expect(result.params?.["revision_root_item_id"]).toBe("root-1");
    expect(result.hasInitImage).toBe(true);
  });
});
