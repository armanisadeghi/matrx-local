import { describe, expect, it } from "vitest";
import {
  findMediaIndexById,
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
});
