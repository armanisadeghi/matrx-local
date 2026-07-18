import { describe, expect, it } from "vitest";
import type { ChatMessage, ToolCallResult } from "@/hooks/use-chat";
import {
  extractToolParts,
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
          { name: "README.md", path: "C:\\Users\\Ada\\Code\\README.md", kind: "file", size: 42 },
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
        { name: "README.md", path: "C:\\Users\\Ada\\Code\\README.md", kind: "file", size: 42 },
      ],
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
