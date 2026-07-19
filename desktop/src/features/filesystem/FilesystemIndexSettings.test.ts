import { describe, expect, it } from "vitest";
import type { FilesystemIndexStatus, FilesystemIndexingSettings } from "@/lib/api";
import {
  authoredPriorityRoots,
  FilesystemIndexRequestFence,
  indexStateLabel,
  setContentPolicy,
} from "./FilesystemIndexSettings";

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

  it("rejects an old poll after a mutation starts and serializes mutations", () => {
    const fence = new FilesystemIndexRequestFence();
    const oldPoll = fence.beginRequest();
    const save = fence.beginMutation();

    expect(save).not.toBeNull();
    expect(fence.isCurrent(oldPoll)).toBe(false);
    expect(fence.isCurrent(save!)).toBe(true);
    expect(fence.beginMutation()).toBeNull();

    fence.finishMutation(save!);
    expect(fence.beginMutation()).not.toBeNull();
  });

  it("serializes loads with mutations and always releases the owning load", () => {
    const fence = new FilesystemIndexRequestFence();
    const load = fence.beginLoad();

    expect(load).not.toBeNull();
    expect(fence.beginLoad()).toBeNull();
    expect(fence.beginMutation()).toBeNull();
    expect(fence.finishLoad(load!)).toBe(true);

    const save = fence.beginMutation();
    expect(save).not.toBeNull();
    expect(fence.beginLoad()).toBeNull();
  });

  it("does not let an invalidated load clear a later loading owner", () => {
    const fence = new FilesystemIndexRequestFence();
    const disconnectedLoad = fence.beginLoad();
    expect(disconnectedLoad).not.toBeNull();

    fence.invalidate();
    const reconnectedLoad = fence.beginLoad();
    expect(reconnectedLoad).not.toBeNull();
    expect(fence.finishLoad(disconnectedLoad!)).toBe(false);
    expect(fence.finishLoad(reconnectedLoad!)).toBe(true);
  });
});
