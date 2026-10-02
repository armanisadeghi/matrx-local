import { describe, expect, it } from "vitest";
import { readLiveRunRejoin, readLiveStreamUnavailable } from "@ai-matrx/agents/matrx";
import { settleCloudRejoinWithoutJournal } from "./cloud-chat-rejoin";

/**
 * A cloud rejoin answering `409 live_stream_unavailable` used to fall through
 * to "AIDream request failed (409)" in the desktop chat — a failure the run
 * never had. Now the desktop follows the run to its end under the run's
 * organization and reloads the saved turn (the shared `@ai-matrx/agents`
 * path the web app and the extension take).
 *
 * Faked: only `fetch`. Real: the package's readers, `followUnavailableRejoin`
 * and `settleRunPickup`. Not covered here: the `use-cloud-chat` request loop
 * that calls this (covered by `pnpm typecheck` only).
 */

const CLOUD = "https://server.app.matrxserver.com";
const LIVE = "f8656746-232f-4d83-8d05-c9cc23e52db9";
const DECOY = "0b7a1c55-9e1d-4f7c-8a2e-3d4c5b6a7f80";
const RUN_ORG = "7d1e2f3a-4b5c-4d6e-8f70-819203a4b5c6";

/** What `aidream/api/errors.py` sends (with the envelope's own `request_id` decoy). */
const RUN_IN_PROGRESS = {
  error: "run_in_progress",
  code: "run_in_progress",
  message: "This run is still running; rejoin it.",
  user_message: "This run is still running; rejoin it.",
  details: null,
  request_id: DECOY,
  live_request_id: LIVE,
  rejoin_path: `/runtime/operations/${LIVE}/rejoin`,
};
const UNAVAILABLE = {
  error: "live_stream_unavailable",
  code: "live_stream_unavailable",
  message: "Live response replay is unavailable for this operation.",
  user_message: "Live response replay is unavailable for this operation.",
  details: null,
  request_id: DECOY,
};

function rejoinTarget() {
  const rejoin = readLiveRunRejoin({ status: 409, serverDetail: RUN_IN_PROGRESS });
  const unavailable = readLiveStreamUnavailable({ status: 409, serverDetail: UNAVAILABLE });
  if (!rejoin || !unavailable) throw new Error("fixtures must classify");
  return { rejoin, unavailable };
}

function operationView(status: string, isTerminal: boolean) {
  return {
    request_id: LIVE,
    operation_count: 1,
    operations: [
      {
        execution_id: "ex-41",
        request_id: LIVE,
        type: "agent_run",
        status,
        is_terminal: isTerminal,
        waiting_input: false,
        cost: 0,
        meters: {},
        link_kind: "conversation",
        link_id: "c-desktop-1",
        error: null,
        created_at: null,
        started_at: null,
        ended_at: null,
        last_event_seq: 3,
        events_path: "/runtime/executions/ex-41/events",
        stream_path: "/runtime/executions/ex-41/events/stream",
      },
    ],
  };
}

function scripted(responses: (Response | Error)[]) {
  const calls: { url: string; headers: Record<string, string> }[] = [];
  const queue = [...responses];
  const fetchImpl = (async (url: string, init: RequestInit = {}) => {
    calls.push({ url: String(url), headers: { ...(init.headers as Record<string, string>) } });
    const next = queue.shift();
    if (!next) throw new Error(`no scripted response for ${url}`);
    if (next instanceof Error) throw next;
    return next;
  }) as typeof fetch;
  return { calls, fetchImpl };
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const sseEnd = (status: string) =>
  new Response(
    `id: 4\nevent: execution_event\ndata: {"seq":4,"kind":"${status}","execution_id":"ex-41","root_execution_id":null,"detail":null,"created_at":null}\n\n` +
      `event: end\ndata: {"status":"${status}"}\n\n`,
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );

describe("settleCloudRejoinWithoutJournal", () => {
  it.each([
    ["completed", "completed"],
    ["failed", "failed"],
  ])("follows a %s run to its end under the run's org, then reloads the saved turn", async (runStatus, expected) => {
    const { calls, fetchImpl } = scripted([json(operationView("running", false)), sseEnd(runStatus)]);
    const order: string[] = [];
    const settlement = await settleCloudRejoinWithoutJournal({
      cloudServerUrl: CLOUD,
      accessToken: "desktop-session",
      ...rejoinTarget(),
      organizationId: RUN_ORG,
      reloadSavedTurn: async () => {
        order.push(`reload after ${calls.length} calls`);
      },
      onStillRunning: async () => {
        order.push("still-running");
      },
      fetchImpl,
    });
    expect(settlement).toEqual({ state: "settled", status: expected, reloaded: true });
    expect(calls.map((c) => c.url)).toEqual([
      `${CLOUD}/api/runtime/operations/${LIVE}`,
      `${CLOUD}/api/runtime/executions/ex-41/events/stream`,
    ]);
    expect(calls[1]?.headers["Last-Event-ID"]).toBe("3");
    expect(calls.every((c) => c.headers["X-Organization-Id"] === RUN_ORG)).toBe(true);
    expect(calls.every((c) => c.headers.Authorization === "Bearer desktop-session")).toBe(true);
    expect(order).toEqual(["reload after 2 calls"]);
  });

  it("a follow that cannot reach the server hands the turn to the live-turn follower — never a failure", async () => {
    const { fetchImpl } = scripted([new TypeError("Failed to fetch")]);
    const order: string[] = [];
    const notes: string[] = [];
    const settlement = await settleCloudRejoinWithoutJournal({
      cloudServerUrl: CLOUD,
      accessToken: "desktop-session",
      ...rejoinTarget(),
      organizationId: RUN_ORG,
      reloadSavedTurn: async () => {
        order.push("reload");
      },
      onStillRunning: async () => {
        order.push("still-running");
      },
      onDiagnostic: (line) => notes.push(line),
      fetchImpl,
    });
    expect(settlement).toEqual({ state: "still_running", reloaded: true });
    expect(order).toEqual(["still-running"]);
    expect(notes.some((n) => n.includes("Failed to fetch"))).toBe(true);
  });
});
