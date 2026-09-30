/**
 * A tool card this desktop answered must finish — the server never says so.
 *
 * aidream hard-suspends and ends the stream when it delegates a call, and the
 * `/resume` segment does not replay the answered call. The live card was only
 * ever driven by stream events, so it spun "running on this computer…" after
 * the agent had moved on (2026-09-30; same class matrx-frontend fixed in
 * `settle-client-tool-call.ts`). The engine's delivery funnel now records the
 * answer on its snapshot; `settleAnsweredDelegatedCalls` folds it in.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { StreamBlockBuilder, type ChatToolBlock } from "./chat-blocks";
import {
  settleAnsweredDelegatedCalls,
  waitForDelegatedContinuation,
  type DelegationCallResult,
  type EngineDelegationState,
} from "./cloud-chat-delegation";
import { reduceLiveToolEvent, type ExtractedToolParts } from "@/features/filesystem/tool-results";
import type { ToolEventPayload } from "@/types/python-generated/stream-events";

afterEach(() => {
  vi.unstubAllGlobals();
});

const delegated: ToolEventPayload = {
  event: "tool_delegated",
  call_id: "call_1",
  tool_name: "local_file",
  data: { arguments: { action: "list", path: "." } },
};

/** The exact event sequence a delegated turn produces on the wire. */
function delegatedTurn(): { builder: StreamBlockBuilder; parts: ExtractedToolParts } {
  const builder = new StreamBlockBuilder();
  builder.applyToolEvent(delegated);
  const parts = reduceLiveToolEvent({ calls: [], results: [] }, delegated);
  // …the stream ends, the engine answers, /resume streams the reply. No
  // tool_completed for call_1 ever arrives.
  builder.addText("There are 3 files.");
  return { builder, parts };
}

function toolBlock(builder: StreamBlockBuilder): ChatToolBlock {
  return builder.snapshot().find((b): b is ChatToolBlock => b.type === "tool_call")!;
}

const answered = (result: DelegationCallResult): EngineDelegationState => ({
  calls: [{ call_id: "call_1", tool_name: "local_file", state: "delivered", result }],
});

describe("settleAnsweredDelegatedCalls", () => {
  it("the stream alone leaves an answered call spinning (the defect)", () => {
    const { builder, parts } = delegatedTurn();
    expect(toolBlock(builder).phase).toBe("delegated");
    expect(parts.results).toEqual([]);
  });

  it("settles the card and the result list from the engine's answer", () => {
    const { builder, parts } = delegatedTurn();
    const next = settleAnsweredDelegatedCalls(
      answered({ is_error: false, output: { output: "3 files" } }),
      builder,
      parts,
    );
    expect(toolBlock(builder).phase).toBe("complete");
    expect(toolBlock(builder).output).toEqual({ output: "3 files" });
    expect(next?.results).toHaveLength(1);
    expect(next?.results[0]).toMatchObject({ tool_call_id: "call_1", type: "success" });
  });

  it("settles an error answer as an error", () => {
    const { builder, parts } = delegatedTurn();
    const next = settleAnsweredDelegatedCalls(
      answered({ is_error: true, error_message: "Disabled by the user." }),
      builder,
      parts,
    );
    expect(toolBlock(builder).phase).toBe("error");
    expect(toolBlock(builder).errorMessage).toBe("Disabled by the user.");
    expect(next?.results[0]).toMatchObject({ type: "error", output: "Disabled by the user." });
  });

  it("is idempotent and never downgrades a stream-completed call", () => {
    const builder = new StreamBlockBuilder();
    builder.applyToolEvent(delegated);
    const completed: ToolEventPayload = {
      event: "tool_completed",
      call_id: "call_1",
      tool_name: "local_file",
      data: { result: "from the stream" },
    };
    builder.applyToolEvent(completed);
    const parts = reduceLiveToolEvent({ calls: [], results: [] }, completed);

    const state = answered({ is_error: true, error_message: "late local copy" });
    expect(settleAnsweredDelegatedCalls(state, builder, parts)).toBeNull();
    expect(toolBlock(builder).phase).toBe("complete");
    expect(toolBlock(builder).output).toBe("from the stream");
  });

  it("ignores calls that have no answer yet and calls not in this message", () => {
    const { builder, parts } = delegatedTurn();
    const state: EngineDelegationState = {
      calls: [
        { call_id: "call_1", tool_name: "local_file", state: "executing" },
        {
          call_id: "other",
          tool_name: "local_file",
          state: "delivered",
          result: { is_error: false, output: "x" },
        },
      ],
    };
    expect(settleAnsweredDelegatedCalls(state, builder, parts)).toBeNull();
    expect(toolBlock(builder).phase).toBe("delegated");
  });
});

describe("waitForDelegatedContinuation", () => {
  it("hands every engine snapshot to the caller before deciding", async () => {
    const snapshot: EngineDelegationState = {
      calls: [
        {
          call_id: "call_1",
          tool_name: "local_file",
          state: "delivered",
          result: { is_error: false, output: "ok" },
        },
      ],
      outstanding: [],
      continuation: { user_request_id: "req_1", needed: true },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(snapshot), { status: 200 })),
    );
    const seen: EngineDelegationState[] = [];
    const id = await waitForDelegatedContinuation(
      "http://engine.test",
      "conv_1",
      "jwt",
      new AbortController().signal,
      () => undefined,
      (state) => seen.push(state),
    );
    expect(id).toBe("req_1");
    expect(seen).toEqual([snapshot]);
  });
});
