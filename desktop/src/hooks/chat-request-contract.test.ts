import { describe, expect, it } from "vitest";
import {
  buildEngineChatRequest,
  type Conversation,
} from "@/hooks/use-chat";

function conversation(patch: Partial<Conversation> = {}): Conversation {
  return {
    id: "desktop-only-id",
    title: "New conversation",
    mode: "chat",
    model: "test-model",
    messages: [],
    created_at: "2026-08-12T00:00:00.000Z",
    updated_at: "2026-08-12T00:00:00.000Z",
    ...patch,
  };
}

function expectRequiredStartFields(body: Record<string, unknown>): void {
  expect(body.conversation_id).toEqual(expect.any(String));
  expect(body.conversation_id).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
  );
  expect(body.is_new).toBe(true);
  expect(body.store).toBe(true);
}

describe("legacy Chat request contract", () => {
  it("targets the mounted engine route and sends the required start triple", () => {
    const request = buildEngineChatRequest(
      "http://127.0.0.1:22240",
      conversation(),
      "hi",
      "test-model",
    );

    expect(request.url).toBe("http://127.0.0.1:22240/chat/ai/chat");
    expectRequiredStartFields(request.body);
  });

  it("targets the mounted continuation route without start-only fields", () => {
    const request = buildEngineChatRequest(
      "http://127.0.0.1:22240",
      conversation({ serverConversationId: "existing-conversation" }),
      "again",
      "test-model",
    );

    expect(request.url).toBe(
      "http://127.0.0.1:22240/chat/ai/conversations/existing-conversation",
    );
    expect(request.body).not.toHaveProperty("conversation_id");
    expect(request.body).not.toHaveProperty("is_new");
  });
});

/** Provenance: every engine chat body carries `initiation` (aidream derives origin_class from it). */
describe("legacy Chat provenance attestation", () => {
  it("legacy engine builder stamps initiation on all three shapes", () => {
    const start = buildEngineChatRequest("http://127.0.0.1:22240", conversation(), "hi", "m");
    const agent = buildEngineChatRequest("http://127.0.0.1:22240", conversation(), "hi", "m", {
      agentId: "agent-1",
    });
    const cont = buildEngineChatRequest(
      "http://127.0.0.1:22240",
      conversation({ serverConversationId: "existing" }),
      "hi",
      "m",
    );
    expect(start.body.initiation).toBe("user");
    expect(agent.body.initiation).toBe("user");
    expect(cont.body.initiation).toBe("user");
    expect(
      buildEngineChatRequest("http://127.0.0.1:22240", conversation(), "hi", "m", {
        initiation: "auto",
      }).body.initiation,
    ).toBe("auto");
  });
});
