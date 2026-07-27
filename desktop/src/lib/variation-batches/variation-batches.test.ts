import { describe, expect, it } from "vitest";
import {
  countPromptVariations,
  expandPromptVariations,
  extractTemplateVariableNames,
} from "./expand";
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

  it("declares a numbered list once and expands independent slots with replacement", () => {
    expect(
      extractTemplateVariableNames(
        "{{color#1}} jacket, {{color#1}} shoes, {{color#2}} hat",
        "",
      ),
    ).toEqual(["color"]);
    expect(
      countPromptVariations("{{color#1}} / {{color#2}}", "", [
        { name: "color", options: ["red", "blue"] },
      ]),
    ).toBe(4);

    const expanded = expandPromptVariations(
      "{{color#1}} jacket, {{color#1}} shoes, {{color#2}} hat",
      "",
      [{ name: "color", options: ["red", "blue"] }],
      { order: "sequence" },
    );
    expect(expanded.errors).toEqual([]);
    expect(expanded.variations.map((row) => row.prompt)).toEqual([
      "red jacket, red shoes, red hat",
      "red jacket, red shoes, blue hat",
      "blue jacket, blue shoes, red hat",
      "blue jacket, blue shoes, blue hat",
    ]);
  });

  it("counts five numbered uses of a 50-value list as 50^5 assignments", () => {
    const colors = Array.from({ length: 50 }, (_, index) => `color-${index}`);
    expect(
      countPromptVariations(
        "{{color#1}} {{color#2}} {{color#3}} {{color#4}} {{color#5}}",
        "",
        [{ name: "color", options: colors }],
      ),
    ).toBe(312_500_000);
  });

  it("uses the injected uniform random source for numbered-slot selection", () => {
    const maxima: number[] = [];
    const expanded = expandPromptVariations(
      "{{color#1}} / {{color#2}}",
      "",
      [{ name: "color", options: ["red", "blue"] }],
      {
        maxCount: 1,
        order: "random",
        random: {
          int: (maxExclusive) => {
            maxima.push(maxExclusive);
            return 1;
          },
          seed: () => 7,
        },
      },
    );
    expect(maxima[0]).toBe(4);
    expect(expanded.variations[0]?.prompt).toBe("red / blue");
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
