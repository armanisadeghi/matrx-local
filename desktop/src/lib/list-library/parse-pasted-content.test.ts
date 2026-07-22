import { describe, expect, it } from "vitest";
import {
  formatLabelForPaste,
  parsePastedListContent,
  splitCommaSeparated,
} from "./parse-pasted-content";

describe("splitCommaSeparated", () => {
  it("splits simple comma lists", () => {
    expect(splitCommaSeparated("a, b, c")).toEqual(["a", "b", "c"]);
  });

  it("respects quoted commas", () => {
    expect(splitCommaSeparated('"hello, world", foo')).toEqual([
      "hello, world",
      "foo",
    ]);
  });
});

describe("parsePastedListContent", () => {
  it("parses line-separated text", () => {
    const result = parsePastedListContent("Red\nGreen\nBlue");
    expect(result.kind).toBe("options");
    if (result.kind === "options") {
      expect(result.format).toBe("lines");
      expect(result.options).toEqual(["Red", "Green", "Blue"]);
    }
  });

  it("strips bullet and numbered markers", () => {
    const result = parsePastedListContent("- Alpha\n2. Beta\n* Gamma");
    expect(result.kind).toBe("options");
    if (result.kind === "options") {
      expect(result.options).toEqual(["Alpha", "Beta", "Gamma"]);
    }
  });

  it("parses comma-separated single line", () => {
    const result = parsePastedListContent("Red, Green, Blue");
    expect(result.kind).toBe("options");
    if (result.kind === "options") {
      expect(result.format).toBe("comma-separated");
      expect(result.options).toEqual(["Red", "Green", "Blue"]);
    }
  });

  it("parses semicolon-separated single line", () => {
    const result = parsePastedListContent("Red; Green; Blue");
    expect(result.kind).toBe("options");
    if (result.kind === "options") {
      expect(result.format).toBe("semicolon-separated");
      expect(result.options).toEqual(["Red", "Green", "Blue"]);
    }
  });

  it("parses JSON string arrays", () => {
    const result = parsePastedListContent('["Red", "Green", "Blue"]');
    expect(result.kind).toBe("options");
    if (result.kind === "options") {
      expect(result.format).toBe("json-array");
      expect(result.options).toEqual(["Red", "Green", "Blue"]);
    }
  });

  it("parses JSON list objects", () => {
    const result = parsePastedListContent(
      '{"name":"Colors","options":["Red","Blue"]}',
    );
    expect(result.kind).toBe("single-list");
    if (result.kind === "single-list") {
      expect(result.list.name).toBe("Colors");
      expect(result.list.options).toEqual(["Red", "Blue"]);
    }
  });

  it("parses JSON bundles with lists key", () => {
    const result = parsePastedListContent(
      '{"lists":[{"name":"A","options":["1"]},{"name":"B","options":["2"]}]}',
    );
    expect(result.kind).toBe("multi-list");
    if (result.kind === "multi-list") {
      expect(result.lists).toHaveLength(2);
      expect(result.lists[0]?.name).toBe("A");
    }
  });

  it("deduplicates options case-insensitively", () => {
    const result = parsePastedListContent("Red\nred\nRED");
    expect(result.kind).toBe("options");
    if (result.kind === "options") {
      expect(result.options).toEqual(["Red"]);
    }
  });

  it("labels formats for UI", () => {
    expect(formatLabelForPaste("comma-separated")).toBe("comma-separated");
    expect(formatLabelForPaste("json-array")).toBe("JSON array");
  });
});
