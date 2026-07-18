import { describe, expect, it } from "vitest";
import type { ChatMessage, ToolCallResult } from "@/hooks/use-chat";
import {
  extractToolParts,
  isFilesystemTool,
  normalizeFilesystemResult,
  reduceLiveToolEvent,
  safeToolOutput,
  stitchHydratedToolMessages,
} from "./tool-results";
import { placesFromEnginePaths } from "./types";

describe("filesystem tool results", () => {
  it("normalizes the engine directory page contract", () => {
    const result: ToolCallResult = {
      tool_call_id: "call-1",
      type: "success",
      output: JSON.stringify({
        kind: "filesystem.directory-page",
        namespace: "host",
        path: "C:\\Users\\Ada\\Code",
        next_cursor: "100",
        total: 120,
        entries: [
          { name: "matrx", path: "C:\\Users\\Ada\\Code\\matrx", kind: "dir", size: 0 },
          {
            name: "README.md",
            path: "C:\\Users\\Ada\\Code\\README.md",
            kind: "file",
            size: 42,
            modified_at: 1_721_234_567.25,
          },
        ],
      }),
    };

    expect(normalizeFilesystemResult(result)).toEqual({
      kind: "filesystem.directory-page",
      namespace: "host",
      path: "C:\\Users\\Ada\\Code",
      nextCursor: "100",
      total: 120,
      entries: [
        { name: "matrx", path: "C:\\Users\\Ada\\Code\\matrx", kind: "directory", size: 0 },
        {
          name: "README.md",
          path: "C:\\Users\\Ada\\Code\\README.md",
          kind: "file",
          size: 42,
          modifiedAt: 1_721_234_567.25,
        },
      ],
    });
  });

  it("keeps search pages distinct and preserves paging state", () => {
    const result: ToolCallResult = {
      tool_call_id: "search-1",
      type: "success",
      output: "Two likely matches.",
      metadata: {
        kind: "filesystem.search-page",
        namespace: "host",
        query: "quarterly report",
        source: "disk",
        index_complete: false,
        next_cursor: "100",
        entries: [
          {
            path: "/Users/ada/Documents/report.md",
            name: "report.md",
            kind: "file",
            modified_at: 0,
          },
          { path: "", name: "invalid", kind: "file" },
        ],
      },
    };

    expect(normalizeFilesystemResult(result)).toEqual({
      kind: "filesystem.search-page",
      namespace: "host",
      query: "quarterly report",
      source: "disk",
      indexComplete: false,
      nextCursor: "100",
      entries: [
        {
          path: "/Users/ada/Documents/report.md",
          name: "report.md",
          kind: "file",
          modifiedAt: 0,
        },
      ],
    });
  });

  it("normalizes content matches without inventing entries", () => {
    const result: ToolCallResult = {
      tool_call_id: "content-1",
      type: "success",
      output: JSON.stringify({
        kind: "filesystem.content-search",
        namespace: "host",
        query: "lease recovery",
        results: [
          { path: "/repo/design.md", snippet: "Crash-safe [lease recovery]" },
          { path: "/repo/empty.md", snippet: "" },
          { path: "/repo/missing-snippet.md" },
          "not-a-match",
        ],
      }),
    };

    expect(normalizeFilesystemResult(result)).toEqual({
      kind: "filesystem.content-search",
      namespace: "host",
      query: "lease recovery",
      results: [
        { path: "/repo/design.md", snippet: "Crash-safe [lease recovery]" },
        { path: "/repo/empty.md", snippet: "" },
      ],
    });
  });

  it("normalizes semantic matches with finite scores and entry metadata", () => {
    const result: ToolCallResult = {
      tool_call_id: "semantic-1",
      type: "success",
      output: "Semantic matches found.",
      metadata: {
        kind: "filesystem.semantic-search",
        namespace: "host",
        query: "filesystem design",
        model: "BAAI/bge-small-en-v1.5",
        results: [
          {
            score: 0.912,
            entry: {
              path: "/repo/filesystem.md",
              name: "filesystem.md",
              kind: "file",
              size: 81,
              modified_at: 1_721_234_567,
              hidden: false,
            },
          },
          { score: Number.NaN, entry: { path: "/repo/bad.md", name: "bad.md", kind: "file" } },
          { score: 0.5, entry: { name: "missing-path.md", kind: "file" } },
        ],
      },
    };

    expect(normalizeFilesystemResult(result)).toEqual({
      kind: "filesystem.semantic-search",
      namespace: "host",
      query: "filesystem design",
      model: "BAAI/bge-small-en-v1.5",
      results: [
        {
          score: 0.912,
          entry: {
            path: "/repo/filesystem.md",
            name: "filesystem.md",
            kind: "file",
            size: 81,
            modifiedAt: 1_721_234_567,
            hidden: false,
          },
        },
      ],
    });
  });

  it("preserves place policy metadata, including false and zero values", () => {
    const result: ToolCallResult = {
      tool_call_id: "places-1",
      type: "success",
      output: "Places on this computer.",
      metadata: {
        kind: "filesystem.places",
        namespace: "host",
        places: [
          {
            id: "volume-1",
            label: "Archive",
            path: "/Volumes/Archive",
            alias: "@archive",
            category: "volume",
            priority: 0,
            available: false,
            configured: false,
          },
          { id: "invalid", label: "Missing", category: "volume" },
        ],
      },
    };

    expect(normalizeFilesystemResult(result)).toEqual({
      kind: "filesystem.places",
      namespace: "host",
      places: [
        {
          id: "volume-1",
          label: "Archive",
          path: "/Volumes/Archive",
          alias: "@archive",
          category: "volume",
          priority: 0,
          available: false,
          configured: false,
        },
      ],
    });
  });

  it("prefers canonical metadata over JSON-looking display output", () => {
    const result: ToolCallResult = {
      tool_call_id: "metadata-first",
      type: "success",
      output: JSON.stringify({
        kind: "filesystem.directory-page",
        path: "/wrong",
        entries: [],
      }),
      metadata: {
        kind: "filesystem.search-page",
        namespace: "host",
        query: "right",
        source: "index",
        index_complete: true,
        entries: [],
      },
    };

    expect(normalizeFilesystemResult(result)).toEqual({
      kind: "filesystem.search-page",
      namespace: "host",
      query: "right",
      source: "index",
      indexComplete: true,
      entries: [],
    });
  });

  it.each([
    { kind: "filesystem.search-page", query: "missing entries" },
    { kind: "filesystem.search-page", entries: [] },
    { kind: "filesystem.content-search", query: "missing results" },
    { kind: "filesystem.semantic-search", query: "q", model: "m" },
    { kind: "filesystem.semantic-search", query: "q", results: [] },
  ])("rejects malformed explicit structured result %#", (metadata) => {
    expect(normalizeFilesystemResult({
      tool_call_id: "malformed",
      type: "success",
      output: "Malformed result",
      metadata,
    })).toBeNull();
  });

  it("maps unsupported namespaces and search sources without widening the contract", () => {
    const normalized = normalizeFilesystemResult({
      tool_call_id: "unknown-policy",
      type: "success",
      output: "Result",
      metadata: {
        kind: "filesystem.search-page",
        namespace: "remote-mystery",
        query: "q",
        source: "network",
        entries: [],
      },
    });
    expect(normalized).toEqual({
      kind: "filesystem.search-page",
      namespace: "unknown",
      query: "q",
      entries: [],
    });
  });

  it("reads structured filesystem metadata without depending on display text", () => {
    const result: ToolCallResult = {
      tool_call_id: "call-2",
      type: "success",
      output: "3 entries in /home/ada/projects",
      metadata: {
        kind: "filesystem.directory-page",
        path: "/home/ada/projects",
        entries: [{ path: "/home/ada/projects/app", name: "app", is_dir: true }],
      },
    };
    expect(normalizeFilesystemResult(result)?.kind).toBe("filesystem.directory-page");
  });

  it("recovers metadata nested inside a persisted bridge output envelope", () => {
    const extracted = extractToolParts([{
      type: "tool_result",
      call_id: "persisted-search",
      name: "local_file",
      content: JSON.stringify({
        output: "Found one.",
        metadata: {
          kind: "filesystem.search-page",
          namespace: "host",
          query: "roadmap",
          source: "index",
          index_complete: true,
          entries: [{ path: "/repo/roadmap.md", name: "roadmap.md", kind: "file" }],
        },
      }),
      is_error: false,
    }]);

    expect(normalizeFilesystemResult(extracted.results[0]!, "local_file")).toEqual({
      kind: "filesystem.search-page",
      namespace: "host",
      query: "roadmap",
      source: "index",
      indexComplete: true,
      entries: [{ path: "/repo/roadmap.md", name: "roadmap.md", kind: "file" }],
    });
  });

  it("gates legacy shape inference to recognized filesystem tools", () => {
    const generic: ToolCallResult = {
      tool_call_id: "generic",
      type: "success",
      output: JSON.stringify({
        path: "/repo",
        entries: [{ path: "/repo/readme.md", name: "readme.md", kind: "file" }],
      }),
    };

    expect(normalizeFilesystemResult(generic, "database_query")).toBeNull();
    expect(normalizeFilesystemResult(generic, "local_file")?.kind).toBe("filesystem.directory-page");
  });

  it("rejects directory pages without a path and preserves the source", () => {
    expect(normalizeFilesystemResult({
      tool_call_id: "missing-path",
      type: "success",
      output: "Invalid directory page",
      metadata: { kind: "filesystem.directory-page", entries: [] },
    })).toBeNull();

    expect(normalizeFilesystemResult({
      tool_call_id: "disk-page",
      type: "success",
      output: "Directory",
      metadata: {
        kind: "filesystem.directory-page",
        path: "/repo",
        source: "disk",
        entries: [],
      },
    })).toMatchObject({ source: "disk" });
  });

  it.each([
    "ListDirectory",
    "FindPaths",
    "SemanticFindPaths",
    "FilesystemPlaces",
    "local_filesystem_places",
    "local_semantic_find_paths",
  ])("recognizes the canonical filesystem tool name %s", (toolName) => {
    expect(isFilesystemTool(toolName)).toBe(true);
  });

  it("does not retain legacy inline screenshot bytes in UI results", () => {
    const output = safeToolOutput({ image: { base64_data: "A".repeat(5000) } });
    expect(output).toContain("inline binary omitted");
    expect(output).not.toContain("A".repeat(100));
  });
});

describe("tool call persistence", () => {
  it("extracts durable call and result blocks and stitches them by call id", () => {
    const callParts = extractToolParts([{ type: "tool_call", call_id: "abc", name: "local_file", arguments: { action: "list" } }]);
    const resultParts = extractToolParts([{ type: "tool_result", call_id: "abc", name: "local_file", content: "done", is_error: false }]);
    const messages: ChatMessage[] = [
      { id: "assistant", role: "assistant", content: "Looking.", timestamp: "2026-01-01", tool_calls: callParts.calls },
      { id: "tool", role: "assistant", content: "", timestamp: "2026-01-02", tool_results: resultParts.results },
    ];

    const stitched = stitchHydratedToolMessages(messages);
    expect(stitched).toHaveLength(1);
    expect(stitched[0]?.tool_results?.[0]?.output).toBe("done");
  });

  it("updates one live execution instead of duplicating cards", () => {
    const started = reduceLiveToolEvent(
      { calls: [], results: [] },
      { event: "tool_started", call_id: "abc", tool_name: "local_file", data: { arguments: { action: "list" } } },
    );
    const completed = reduceLiveToolEvent(started, {
      event: "tool_completed",
      call_id: "abc",
      tool_name: "local_file",
      data: { result: { output: "ok", metadata: { count: 1 } } },
    });
    expect(completed.calls).toHaveLength(1);
    expect(completed.results).toHaveLength(1);
    expect(completed.results[0]?.metadata).toEqual({ count: 1 });
  });
});

describe("engine paths", () => {
  it("turns engine-resolved paths into places without constructing OS paths", () => {
    const places = placesFromEnginePaths({
      aliases: {
        "@home": "D:\\Users\\Ada",
        "@user": "D:\\Users\\Ada\\Matrx",
        "@files": "D:\\Users\\Ada\\Matrx\\Files",
        "@code": "E:\\Source",
        "@workspaces": "D:\\Users\\Ada\\Matrx\\Workspaces",
        "@notes": "D:\\Users\\Ada\\Matrx\\Notes",
        "@matrx": "D:\\Users\\Ada\\.matrx",
        "@agentdata": "D:\\Users\\Ada\\.matrx\\data",
        "@temp": "D:\\Temp",
        "@data": "D:\\Users\\Ada\\.matrx\\data",
        "@logs": "D:\\Users\\Ada\\.matrx\\logs",
        "@docs": "D:\\Users\\Ada\\Matrx\\Notes",
      },
      resolved: {
        discovery: "", settings: "", instance: "", agent_data: "", workspaces: "",
        user_root: "", notes: "", files: "", code: "", temp: "", screenshots: "D:\\Shots",
        data: "", logs: "", config: "",
      },
    });
    expect(places.find((place) => place.id === "code")?.path).toBe("E:\\Source");
    expect(places.find((place) => place.id === "screenshots")?.path).toBe("D:\\Shots");
  });
});
