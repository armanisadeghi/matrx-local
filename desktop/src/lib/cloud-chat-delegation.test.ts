import { afterEach, describe, expect, it, vi } from "vitest";
import {
  claimDelegationUi,
  releaseDelegationUi,
  waitForDelegatedContinuation,
} from "./cloud-chat-delegation";

afterEach(() => {
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
});
