import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { filesystemBreadcrumbParts, filesystemBreadcrumbs, FilesystemResultView, mergeFilesystemEntries } from "./FilesystemResultView";
import type { FilesystemResult } from "./types";

describe("FilesystemResultView", () => {
  it("preserves Windows drive and UNC share roots in breadcrumbs", () => {
    expect(filesystemBreadcrumbParts("C:\\Users\\ada")).toEqual(["C:\\", "Users", "ada"]);
    expect(filesystemBreadcrumbParts("\\\\server\\share\\folder")).toEqual(["\\\\server\\share\\", "folder"]);
  });

  it("builds navigable breadcrumb paths across supported path styles", () => {
    expect(filesystemBreadcrumbs("/")).toEqual([{ label: "/", path: "/" }]);
    expect(filesystemBreadcrumbs("/Users/ada/project")).toEqual([
      { label: "Users", path: "/Users" },
      { label: "ada", path: "/Users/ada" },
      { label: "project", path: "/Users/ada/project" },
    ]);
    expect(filesystemBreadcrumbs("C:\\Users\\ada")).toEqual([
      { label: "C:\\", path: "C:\\" },
      { label: "Users", path: "C:\\Users" },
      { label: "ada", path: "C:\\Users\\ada" },
    ]);
    expect(filesystemBreadcrumbs("\\\\server\\share\\folder")).toEqual([
      { label: "\\\\server\\share\\", path: "\\\\server\\share\\" },
      { label: "folder", path: "\\\\server\\share\\folder" },
    ]);
  });

  it("merges lazy child pages without losing or duplicating entries", () => {
    expect(mergeFilesystemEntries(
      [{ name: "a", path: "/repo/a", kind: "file" }],
      [
        { name: "a updated", path: "/repo/a", kind: "file" },
        { name: "b", path: "/repo/b", kind: "file" },
      ],
    )).toEqual([
      { name: "a updated", path: "/repo/a", kind: "file" },
      { name: "b", path: "/repo/b", kind: "file" },
    ]);
  });

  it("uses search-specific empty copy and a page-sized results host", () => {
    const html = renderToStaticMarkup(<FilesystemResultView
      layout="page"
      result={{
        kind: "filesystem.search-page",
        namespace: "host",
        query: "missing",
        entries: [],
      }}
    />);
    expect(html).toContain("No matching files or folders.");
    expect(html).toContain("min-h-[24rem]");
    expect(html).not.toContain("This directory is empty.");
  });

  it("renders an empty cursor page as snapshot progress, not an empty directory", () => {
    const html = renderToStaticMarkup(<FilesystemResultView
      result={{
        kind: "filesystem.directory-page",
        namespace: "host",
        path: "/large",
        entries: [],
        nextCursor: "opaque-progress-token",
      }}
      onLoadMore={() => undefined}
    />);

    expect(html).toContain("Scanning this large directory");
    expect(html).toContain("Continue scanning");
    expect(html).not.toContain("This directory is empty.");
  });

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
      expected: "index incomplete",
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
