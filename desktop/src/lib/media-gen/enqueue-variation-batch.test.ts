import { describe, expect, it } from "vitest";
import { selectVariationItemsForQueue } from "./enqueue-variation-batch";
import type {
  VariationBatch,
  VariationItem,
} from "@/lib/variation-batches/types";

function item(id: string, prompt: string): VariationItem {
  return {
    id,
    prompt,
    negativePrompt: "",
    status: "done",
    error: "",
    updatedAt: 1,
  };
}

function batch(items: VariationItem[]): VariationBatch {
  return {
    id: "b1",
    name: "Test",
    sourcePromptId: null,
    templatePrompt: "",
    templateNegative: "",
    variableListByName: {},
    items,
    createdAt: 1,
    updatedAt: 1,
  };
}

describe("selectVariationItemsForQueue", () => {
  const rows = batch([
    item("1", "a"),
    item("2", "b"),
    item("3", "c"),
    item("4", "d"),
    item("5", "e"),
  ]);

  it("takes from start by default", () => {
    const picked = selectVariationItemsForQueue(rows, {
      count: 2,
      order: "start",
    });
    expect(picked.map((row) => row.id)).toEqual(["1", "2"]);
  });

  it("takes from end", () => {
    const picked = selectVariationItemsForQueue(rows, {
      count: 3,
      order: "end",
    });
    expect(picked.map((row) => row.id)).toEqual(["3", "4", "5"]);
  });

  it("clamps count to ready total", () => {
    const picked = selectVariationItemsForQueue(rows, {
      count: 99,
      order: "start",
    });
    expect(picked).toHaveLength(5);
  });

  it("random returns the requested count", () => {
    const picked = selectVariationItemsForQueue(rows, {
      count: 4,
      order: "random",
    });
    expect(picked).toHaveLength(4);
    const ids = new Set(picked.map((row) => row.id));
    expect(ids.size).toBe(4);
  });

  it("ignores non-ready items", () => {
    const mixed = batch([
      item("1", "a"),
      { ...item("2", ""), status: "done" },
      { ...item("3", "c"), status: "pending" },
    ]);
    const picked = selectVariationItemsForQueue(mixed, {
      count: 2,
      order: "start",
    });
    expect(picked.map((row) => row.id)).toEqual(["1"]);
  });
});
