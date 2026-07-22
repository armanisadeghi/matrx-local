import { describe, expect, it } from "vitest";
import type { NamedList } from "@/lib/list-library/types";
import {
  insertListVariableToken,
  listsMatchingVariableName,
  resolveListForVariableName,
  sampleListValues,
  variableNameForList,
  variableTokenForList,
} from "./list-variables";

function list(
  id: string,
  name: string,
  values: Array<{ value: string; enabled?: boolean }>,
): NamedList {
  return {
    id,
    name,
    description: "",
    options: values.map((option, index) => ({
      id: `${id}-${index}`,
      value: option.value,
      enabled: option.enabled !== false,
    })),
    createdAt: 1,
    updatedAt: 1,
  };
}

describe("list variables", () => {
  it("produces readable prompt-matrix tokens from saved list names", () => {
    expect(variableNameForList("  People / celebrities  ")).toBe(
      "People celebrities",
    );
    expect(variableTokenForList("Camera_angles-01")).toBe(
      "{{Camera_angles-01}}",
    );
    expect(variableTokenForList("✨")).toBeNull();
  });

  it("inserts at the caret without changing surrounding whitespace", () => {
    expect(insertListVariableToken("one  two", "Colors", 5, 5)).toEqual({
      text: "one  {{Colors}}two",
      cursor: 15,
      token: "{{Colors}}",
      variableName: "Colors",
    });
  });

  it("matches generated variable identities case-insensitively", () => {
    const lists = [
      list("one", "Camera / angles", [{ value: "wide" }]),
      list("two", "CAMERA ANGLES", [{ value: "close" }]),
      list("three", "Lighting", [{ value: "soft" }]),
    ];
    expect(
      listsMatchingVariableName(lists, "camera angles").map((item) => item.id),
    ).toEqual(["one", "two"]);
  });

  it("resolves a unique exact or near list match for auto-mapping", () => {
    const lists = [
      list("camera", "Camera angles", [{ value: "wide" }]),
      list("people", "People / celebrities", [{ value: "actor" }]),
      list("light", "Lighting setup", [{ value: "soft" }]),
      list("style-a", "Style A", [{ value: "a" }]),
      list("style-b", "Style B", [{ value: "b" }]),
    ];

    expect(resolveListForVariableName(lists, "camera")?.id).toBe("camera");
    expect(resolveListForVariableName(lists, "people")?.id).toBe("people");
    expect(resolveListForVariableName(lists, "light")).toBeNull();
    expect(resolveListForVariableName(lists, "style")).toBeNull();
    expect(resolveListForVariableName(lists, "camera_angles")?.id).toBe(
      "camera",
    );
  });

  it("replaces only the selected range and clamps stale selections", () => {
    expect(insertListVariableToken("one two", "Styles", 4, 7)?.text).toBe(
      "one {{Styles}}",
    );
    expect(insertListVariableToken("abc", "Styles", 99, 120)?.text).toBe(
      "abc{{Styles}}",
    );
  });

  it("samples enabled values and avoids repeating on reroll", () => {
    const lists = [
      list("colors", "Colors", [
        { value: "red" },
        { value: "disabled", enabled: false },
        { value: "blue" },
        { value: "   " },
      ]),
    ];
    const first = sampleListValues(lists, null, () => 0);
    expect(first.get("colors")?.value).toBe("red");

    const next = sampleListValues(lists, first, () => 0);
    expect(next.get("colors")?.value).toBe("blue");
  });
});
