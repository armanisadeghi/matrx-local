import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { FilesystemResultView } from "./FilesystemResultView";
import type { FilesystemResult } from "./types";

describe("FilesystemResultView", () => {
  it.each<{ result: FilesystemResult; expected: string }>([
    {
      result: {
        kind: "filesystem.places",
        namespace: "host",
        places: [{ id: "home", label: "Home", path: "/Users/ada", available: true }],
      },
      expected: "Home",
    },
    {
      result: {
        kind: "filesystem.directory-page",
        namespace: "host",
        path: "/repo",
        source: "disk",
        entries: [{ name: "src", path: "/repo/src", kind: "directory" }],
      },
      expected: "src",
    },
    {
      result: {
        kind: "filesystem.search-page",
        namespace: "host",
        query: "roadmap",
        source: "index",
        indexComplete: false,
        entries: [{ name: "roadmap.md", path: "/repo/roadmap.md", kind: "file" }],
      },
      expected: "index still improving",
    },
    {
      result: {
        kind: "filesystem.content-search",
        namespace: "host",
        query: "leases",
        results: [{ path: "/repo/design.md", snippet: "Crash-safe lease recovery" }],
      },
      expected: "Crash-safe lease recovery",
    },
    {
      result: {
        kind: "filesystem.semantic-search",
        namespace: "host",
        query: "filesystem design",
        model: "test-model",
        results: [{
          score: 0.9,
          entry: { name: "design.md", path: "/repo/design.md", kind: "file" },
        }],
      },
      expected: "0.900",
    },
  ])("renders $result.kind without falling through", ({ result, expected }) => {
    expect(renderToStaticMarkup(<FilesystemResultView result={result} />)).toContain(expected);
  });
});
