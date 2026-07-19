/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { engine } from "@/lib/api";
import { ToolExecutionCard } from "./ToolExecutionCard";
import type { ToolCall, ToolCallResult } from "@/hooks/use-chat";

const toolCall: ToolCall = {
  id: "call-files",
  name: "ListDirectory",
  input: { path: "/repo" },
};

const result: ToolCallResult = {
  tool_call_id: toolCall.id,
  type: "success",
  output: JSON.stringify({
    kind: "filesystem.directory-page",
    namespace: "host",
    path: "/repo",
    entries: [{ path: "/repo/a.txt", name: "a.txt", kind: "file" }],
    nextCursor: "page-2",
  }),
};

describe("ToolExecutionCard filesystem paging", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
      .IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  it("preserves an appended page when the card rerenders with the same tool result", async () => {
    vi.spyOn(engine, "listFilesystem").mockResolvedValue({
      kind: "filesystem.directory-page",
      namespace: "host",
      path: "/repo",
      entries: [{
        path: "/repo/b.txt",
        name: "b.txt",
        kind: "file",
        size: 1,
        modified_at: null,
        hidden: false,
        extension: ".txt",
        indexed: true,
      }],
      next_cursor: null,
    });

    await act(async () => {
      root.render(<ToolExecutionCard toolCall={toolCall} result={result} elapsedMs={10} />);
    });
    const loadMore = [...container.querySelectorAll("button")].find((button) =>
      button.textContent?.includes("Load more"),
    );
    expect(loadMore).toBeDefined();

    await act(async () => {
      loadMore!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[title="/repo/b.txt"]')).not.toBeNull();

    await act(async () => {
      root.render(
        <ToolExecutionCard
          toolCall={toolCall}
          result={{ ...result }}
          elapsedMs={20}
        />,
      );
    });

    expect(container.querySelector('[title="/repo/a.txt"]')).not.toBeNull();
    expect(container.querySelector('[title="/repo/b.txt"]')).not.toBeNull();
    expect(engine.listFilesystem).toHaveBeenCalledTimes(1);
  });
});
