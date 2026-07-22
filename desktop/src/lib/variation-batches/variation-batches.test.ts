import { describe, expect, it } from "vitest";
import { countPromptVariations, expandPromptVariations } from "./expand";
import {
  cloneVariationBatchTemplate,
  emptyVariationItem,
  sanitizeVariationBatches,
} from "./types";

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
    expect(expanded.total).toBe(25);
    expect(expanded.truncated).toBe(false);
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

  it("counts cartesian totals without materializing", () => {
    expect(
      countPromptVariations("test {{variables}} and {{one}}", "", [
        { name: "variables", options: ["a", "b", "c", "d", "e"] },
        { name: "one", options: ["1", "2", "3", "4", "5"] },
      ]),
    ).toBe(25);
    expect(countPromptVariations("plain prompt", "", [])).toBe(1);
    expect(
      countPromptVariations("{{missing}}", "", [
        { name: "missing", options: [] },
      ]),
    ).toBeNull();
  });

  it("samples down to maxCount when the cartesian total is larger", () => {
    const expanded = expandPromptVariations(
      "test {{variables}} now and {{one}} more",
      "",
      [
        { name: "variables", options: ["a", "b", "c", "d", "e"] },
        { name: "one", options: ["1", "2", "3", "4", "5"] },
      ],
      { maxCount: 10, order: "random" },
    );
    expect(expanded.errors).toEqual([]);
    expect(expanded.total).toBe(25);
    expect(expanded.truncated).toBe(true);
    expect(expanded.variations).toHaveLength(10);
  });

  it("random order draws distinct combinations without cycling", () => {
    const variables = [
      { name: "variables", options: ["a", "b", "c", "d", "e"] },
      { name: "one", options: ["1", "2", "3", "4", "5"] },
    ];
    const expanded = expandPromptVariations(
      "test {{variables}} now and {{one}} more",
      "",
      variables,
      { maxCount: 5, order: "random" },
    );
    expect(expanded.errors).toEqual([]);
    expect(expanded.variations).toHaveLength(5);
    expect(new Set(expanded.variations.map((row) => row.prompt)).size).toBe(5);
  });

  it("sequence order is stable and walks cartesian order", () => {
    const variables = [
      { name: "variables", options: ["a", "b", "c"] },
      { name: "one", options: ["1", "2"] },
    ];
    const first = expandPromptVariations(
      "test {{variables}} and {{one}}",
      "",
      variables,
      { maxCount: 3, order: "sequence" },
    );
    const second = expandPromptVariations(
      "test {{variables}} and {{one}}",
      "",
      variables,
      { maxCount: 3, order: "sequence" },
    );
    expect(first.errors).toEqual([]);
    expect(first.variations.map((row) => row.prompt)).toEqual([
      "test a and 1",
      "test a and 2",
      "test b and 1",
    ]);
    expect(second.variations.map((row) => row.prompt)).toEqual(
      first.variations.map((row) => row.prompt),
    );
  });

  it("clones batch template without variation rows", () => {
    const source = {
      id: "b1",
      name: "Portrait run",
      sourcePromptId: "prompt-1",
      templatePrompt: "test {{style}}",
      templateNegative: "bad",
      variableListByName: { style: "list-1" },
      items: [emptyVariationItem("one", "neg")],
      createdAt: 1,
      updatedAt: 2,
    };
    const copy = cloneVariationBatchTemplate(source);
    expect(copy.id).not.toBe(source.id);
    expect(copy.name).toBe("Portrait run (copy)");
    expect(copy.templatePrompt).toBe(source.templatePrompt);
    expect(copy.variableListByName).toEqual(source.variableListByName);
    expect(copy.items).toEqual([]);
  });
});
