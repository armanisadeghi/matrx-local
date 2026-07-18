import { describe, expect, it } from "vitest";
import type { FilesystemIndexStatus, FilesystemIndexingSettings } from "@/lib/api";
import { authoredPriorityRoots, indexStateLabel, setContentPolicy } from "./FilesystemIndexSettings";

const settings: FilesystemIndexingSettings = {
  priority_roots: [{ path: "/Users/ada", label: "Authored overlap" }],
  paused: false,
  content_enabled: true,
  semantic_enabled: false,
  embedding_model: "test-model",
  max_content_bytes: 1024,
  max_embedding_entries: 100,
};

describe("FilesystemIndexSettings contracts", () => {
  it("uses the authored settings list even when a root overlaps a discovered place", () => {
    expect(authoredPriorityRoots(settings)).toEqual([
      { path: "/Users/ada", label: "Authored overlap" },
    ]);
  });

  it("presents durable scan failures as attention-needed, never complete", () => {
    const status = {
      started: true,
      metadata_state: "partial",
      index_complete: false,
    } as FilesystemIndexStatus;
    expect(indexStateLabel(status)).toBe("Needs attention");
  });

  it("turning off content indexing also disables dependent semantic indexing", () => {
    const { priority_roots: _priorityRoots, paused: _paused, ...policy } = settings;
    expect(setContentPolicy({ ...policy, semantic_enabled: true }, false)).toMatchObject({
      content_enabled: false,
      semantic_enabled: false,
    });
  });
});
