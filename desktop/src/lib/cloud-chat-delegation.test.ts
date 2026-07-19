import { afterEach, describe, expect, it, vi } from "vitest";
import {
  claimDelegationUi,
  releaseDelegationUi,
  waitForDelegatedContinuation,
} from "./cloud-chat-delegation";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Cloud Chat delegation UI requests", () => {
  it("authenticates UI claims with the current Supabase bearer", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          claimed: true,
          calls: [],
          continuation: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const state = await claimDelegationUi(
      "http://127.0.0.1:22140",
      "conversation-1",
      "supabase-access-token",
    );

    expect(state?.claimed).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:22140/chat/delegation/ui-claim",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          Authorization: "Bearer supabase-access-token",
        }),
      }),
    );
  });

  it("keeps the bearer on continuation polling and release", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            claimed: true,
            calls: [],
            continuation: { needed: true, user_request_id: "request-1" },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const requestId = await waitForDelegatedContinuation(
      "http://127.0.0.1:22140",
      "conversation-1",
      "supabase-access-token",
      new AbortController().signal,
      vi.fn(),
    );
    await releaseDelegationUi(
      "http://127.0.0.1:22140",
      "conversation-1",
      "supabase-access-token",
    );

    expect(requestId).toBe("request-1");
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://127.0.0.1:22140/chat/delegation/ui-claim",
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer supabase-access-token",
        }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:22140/chat/delegation/ui-release",
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer supabase-access-token",
        }),
      }),
    );
  });

  it("re-reads the bearer while a delegated tool is still running", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            claimed: true,
            calls: [{ call_id: "call-1", tool_name: "shell", state: "executing" }],
            continuation: null,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            claimed: true,
            calls: [],
            continuation: { needed: true, user_request_id: "request-rotated" },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const tokenProvider = vi
      .fn()
      .mockResolvedValueOnce("token-before-refresh")
      .mockResolvedValueOnce("token-after-refresh");

    const waiting = waitForDelegatedContinuation(
      "http://127.0.0.1:22140",
      "conversation-1",
      tokenProvider,
      new AbortController().signal,
      vi.fn(),
    );
    await vi.advanceTimersByTimeAsync(1000);

    await expect(waiting).resolves.toBe("request-rotated");
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      expect.any(String),
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer token-before-refresh",
        }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      expect.any(String),
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer token-after-refresh",
        }),
      }),
    );
    vi.useRealTimers();
  });
});
