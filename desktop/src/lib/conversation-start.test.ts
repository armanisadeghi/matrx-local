import { describe, expect, it } from "vitest";
import { buildConversationStartRequest } from "@/lib/conversation-start";

const CONVERSATION_ID = "56fd1e9e-b25c-41f3-9228-fd739cb98f5b";

describe("conversation-start wire contract", () => {
  it("adds all required fields to persistent chat and agent starts", () => {
    const request = buildConversationStartRequest(
      "https://api.example.test/api/ai/chat",
      { stream: true, user_input: "hi" },
      { conversationId: CONVERSATION_ID },
    );

    expect(request).toEqual({
      url: "https://api.example.test/api/ai/chat",
      startedConversationId: CONVERSATION_ID,
      body: {
        stream: true,
        user_input: "hi",
        conversation_id: CONVERSATION_ID,
        is_new: true,
        store: true,
      },
    });
  });

  it("uses store=false as the explicit ephemeral signal", () => {
    const request = buildConversationStartRequest(
      "https://api.example.test/api/ai/agents/agent-1",
      { stream: true },
      { conversationId: CONVERSATION_ID, store: false },
    );

    expect(request.body).toMatchObject({
      conversation_id: CONVERSATION_ID,
      is_new: true,
      store: false,
    });
  });
});
