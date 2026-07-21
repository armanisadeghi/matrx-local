import { describe, expect, it } from "vitest";
import { expandPromptVariations } from "./expand";
import { emptyVariationItem, sanitizeVariationBatches } from "./types";

describe("variation batches", () => {
  it("expands and sanitizes cartesian variations", () => {
    const expanded = expandPromptVariations(
      "test {{variables}} now and {{one}} more",
      "",
      [
        { name: "variables", options: ["a", "b", "c", "d", "e"] },
        { name: "one", options: ["1", "2", "3", "4", "5"] },
      ],
    );
    expect(expanded.errors).toEqual([]);
    expect(expanded.variations).toHaveLength(25);

    const items = expanded.variations.map((row) =>
      emptyVariationItem(row.prompt, row.negativePrompt),
    );
    const batch = {
      id: "b1",
      name: "Test",
      sourcePromptId: null,
      templatePrompt: "test {{variables}} now and {{one}} more",
      templateNegative: "",
      variableListByName: { variables: "list-a", one: "list-b" },
      items,
      createdAt: 1,
      updatedAt: 2,
    };
    const sanitized = sanitizeVariationBatches([batch]);
    expect(sanitized).toHaveLength(1);
    expect(sanitized[0]?.items).toHaveLength(25);
    expect(sanitized[0]?.variableListByName).toEqual({
      variables: "list-a",
      one: "list-b",
    });
  });

  it("defaults missing variableListByName for legacy rows", () => {
    const batch = {
      id: "b1",
      name: "Legacy",
      sourcePromptId: null,
      templatePrompt: "",
      templateNegative: "",
      items: [],
      createdAt: 1,
      updatedAt: 2,
    };
    const sanitized = sanitizeVariationBatches([batch]);
    expect(sanitized[0]?.variableListByName).toEqual({});
  });
});
