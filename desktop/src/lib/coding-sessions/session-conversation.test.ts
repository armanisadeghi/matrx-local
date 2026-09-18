import { describe, expect, it } from "vitest";

import {
  conversationChatHref,
  conversationWebUrl,
  resolveSessionConversation,
} from "@/lib/coding-sessions/session-conversation";
import type { CodingSessionRow, CodingSessionState } from "@/lib/api";

function row(
  state: CodingSessionState,
  conversationId: string | null,
): CodingSessionRow {
  return {
    session_id: "abc",
    title: "A session",
    title_source: null,
    project: null,
    last_activity_at: 0,
    bytes: 0,
    on_disk: true,
    state,
    pinned: false,
    pinned_rank: null,
    category: null,
    archived: false,
    in_claude_sidebar: true,
    cloud: conversationId
      ? { conversation_id: conversationId, fidelity: null, last_seen_at: null }
      : null,
    delivery: { pending: 0, quarantined: 0 },
  } as unknown as CodingSessionRow;
}

describe("resolveSessionConversation", () => {
  it("opens the conversation the server already bound to the session", () => {
    expect(resolveSessionConversation(row("in_cloud", "conv-1"))).toEqual({
      kind: "conversation",
      conversationId: "conv-1",
    });
  });

  it("explains a queued session in the words of its own state", () => {
    const target = resolveSessionConversation(row("queued", null));
    expect(target.kind).toBe("unavailable");
    if (target.kind !== "unavailable") throw new Error("expected unavailable");
    expect(target.reason).toContain("waiting in this Mac's delivery queue");
  });

  it("never returns a click with nothing to say", () => {
    const states: CodingSessionState[] = [
      "in_cloud",
      "changed",
      "queued",
      "failed",
      "not_in_cloud",
      "unknown",
    ];
    for (const state of states) {
      const target = resolveSessionConversation(row(state, null));
      if (target.kind !== "unavailable") throw new Error("expected unavailable");
      expect(target.reason.length).toBeGreaterThan(20);
    }
  });
});

describe("conversation links", () => {
  it("carries the way back to the list it was opened from", () => {
    expect(conversationChatHref("conv-1")).toBe(
      "/cloud-chat?conversation=conv-1&from=coding-sessions",
    );
  });

  it("points at the web app's own conversation route", () => {
    expect(conversationWebUrl("https://aimatrx.com/", "conv-1")).toBe(
      "https://aimatrx.com/chat/conv-1",
    );
  });
});
