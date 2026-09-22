/**
 * The surface must never post `/resume` — or a new turn — while a tool it
 * delegated to this computer is still running.
 *
 * On 2026-09-22 the owner's run died on HTTP 409 `outstanding_delegated_calls`
 * (conversation 60b6f5e7…, `toolu_01KCH6aM36tYXNk6CBExGmEZ` still delegated).
 * The wait loop returned the moment the engine offered a continuation, and
 * the engine's continuation fact was stale: a tool-using turn keeps ONE
 * user_request_id, so the invitation from the previous suspend still looked
 * current while the next call was running. The refusal was then rendered as a
 * dead conversation.
 */
import { describe, expect, it, vi, afterEach } from "vitest";

import {
  isBenignResumeConflict,
  outstandingCalls,
  waitForDelegatedContinuation,
  type EngineDelegationState,
} from "./cloud-chat-delegation";

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubEngine(states: EngineDelegationState[]) {
  let i = 0;
  const seen: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      seen.push(String(url));
      const body = states[Math.min(i, states.length - 1)];
      i += 1;
      return new Response(JSON.stringify(body), { status: 200 });
    }),
  );
  return { seen };
}

const EXECUTING: EngineDelegationState = {
  claimed: true,
  calls: [{ call_id: "call_b", tool_name: "local_window", state: "executing" }],
  outstanding: [{ call_id: "call_b", tool_name: "local_window", state: "executing" }],
  // The stale invitation from the PREVIOUS suspend of the same turn.
  continuation: { user_request_id: "req_1", needed: true },
};

const SETTLED: EngineDelegationState = {
  claimed: true,
  calls: [{ call_id: "call_b", tool_name: "local_window", state: "delivered" }],
  outstanding: [],
  continuation: { user_request_id: "req_1", needed: true },
};

describe("outstandingCalls", () => {
  it("falls back to the call states when the engine is older than the field", () => {
    const state: EngineDelegationState = {
      calls: [
        { call_id: "a", tool_name: "local_window", state: "delivered" },
        { call_id: "b", tool_name: "local_shell", state: "executing" },
        { call_id: "c", tool_name: "google_email_send", state: "awaiting_user_review" },
      ],
    };
    expect(outstandingCalls(state).map((c) => c.call_id)).toEqual(["b", "c"]);
  });

  it("is empty for a state with nothing in it", () => {
    expect(outstandingCalls(null)).toEqual([]);
  });
});

describe("waitForDelegatedContinuation", () => {
  it("does not hand back a continuation while a call is still running", async () => {
    stubEngine([EXECUTING]);
    const controller = new AbortController();
    const statuses: string[] = [];
    const wait = waitForDelegatedContinuation(
      "http://127.0.0.1:22140",
      "conv_1",
      "token",
      controller.signal,
      (s) => statuses.push(s),
    );
    await new Promise((r) => setTimeout(r, 60));
    controller.abort();
    expect(await wait).toBeNull();
    expect(statuses.some((s) => s.includes("Running on this computer: local_window"))).toBe(
      true,
    );
  });

  it("hands it back once the call has landed", async () => {
    stubEngine([EXECUTING, SETTLED]);
    const controller = new AbortController();
    const result = await waitForDelegatedContinuation(
      "http://127.0.0.1:22140",
      "conv_1",
      "token",
      controller.signal,
      () => {},
    );
    expect(result).toBe("req_1");
  });
});

describe("isBenignResumeConflict", () => {
  it("recognises the refusal that ended the owner's run", () => {
    expect(
      isBenignResumeConflict(
        409,
        JSON.stringify({
          code: "outstanding_delegated_calls",
          message: "client-delegated tool calls are still awaiting a response.",
        }),
      ),
    ).toBe(true);
  });

  it("recognises a racing resume claim", () => {
    expect(isBenignResumeConflict(409, '{"code":"resume_conflict"}')).toBe(true);
  });

  it("never swallows a real failure", () => {
    expect(isBenignResumeConflict(500, "boom")).toBe(false);
    expect(isBenignResumeConflict(409, '{"code":"not_resumable"}')).toBe(false);
    expect(isBenignResumeConflict(401, "outstanding_delegated_calls")).toBe(false);
  });
});
