import { describe, expect, it } from "vitest";
import { appendFilesystemPage, pageIdentity } from "./FilesystemResultController";
import type { FilesystemDirectoryPage, FilesystemSearchPage } from "./types";

describe("appendFilesystemPage", () => {
  it("keys selection and request state by directory or scoped-search identity", () => {
    expect(pageIdentity({
      kind: "filesystem.directory-page",
      namespace: "host",
      path: "/repo",
      entries: [],
    })).toBe("filesystem.directory-page:host:/repo");
    expect(pageIdentity({
      kind: "filesystem.search-page",
      namespace: "host",
      root: "/repo",
      query: "report",
      entries: [],
    })).toBe("filesystem.search-page:host:/repo:report");
  });

  it("appends cursor pages without duplicating paths", () => {
    const current: FilesystemDirectoryPage = {
      kind: "filesystem.directory-page",
      namespace: "host",
      path: "/repo",
      entries: [{ path: "/repo/a", name: "a", kind: "file" }],
      nextCursor: "1",
    };
    const next: FilesystemDirectoryPage = {
      ...current,
      entries: [
        { path: "/repo/a", name: "a updated", kind: "file" },
        { path: "/repo/b", name: "b", kind: "file" },
      ],
      nextCursor: null,
    };

    expect(appendFilesystemPage(current, next)).toMatchObject({
      entries: [
        { path: "/repo/a", name: "a updated" },
        { path: "/repo/b", name: "b" },
      ],
      nextCursor: null,
    });
  });

  it("refuses a page from another scoped search", () => {
    const current: FilesystemSearchPage = {
      kind: "filesystem.search-page",
      namespace: "host",
      query: "report",
      root: "/private/a",
      entries: [],
    };
    const next: FilesystemSearchPage = {
      ...current,
      root: "/private/b",
      entries: [{ path: "/private/b/report", name: "report", kind: "file" }],
    };

    expect(appendFilesystemPage(current, next)).toBe(current);
  });
});
