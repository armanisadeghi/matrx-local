import { describe, expect, it } from "vitest";
import {
  buildCloudChatRequest,
  type CloudChatRunControls,
} from "@/hooks/use-cloud-chat";
import {
  buildEngineChatRequest,
  type Conversation,
} from "@/hooks/use-chat";

const RUN_CONTROLS: CloudChatRunControls = {
  modelOverride: null,
  temperature: null,
  maxTokens: null,
  excludedTools: [],
};

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

describe("Cloud Chat request contract", () => {
  it("sends the required triple on a bare AIDream chat start", () => {
    const request = buildCloudChatRequest(
      conversation(),
      "hi",
      "test-model",
      "cloud",
      null,
      "http://127.0.0.1:22240",
      "https://api.example.test",
      undefined,
      [],
      null,
      RUN_CONTROLS,
      [],
    );

    expect(request.url).toBe("https://api.example.test/api/ai/chat");
    expectRequiredStartFields(request.body);
  });

  it("sends the required triple on an AIDream agent start", () => {
    const request = buildCloudChatRequest(
      conversation(),
      "hi",
      "test-model",
      "cloud",
      null,
      "http://127.0.0.1:22240",
      "https://api.example.test",
      { agentId: "agent-1" },
      [],
      null,
      RUN_CONTROLS,
      [],
    );

    expect(request.url).toBe("https://api.example.test/api/ai/agents/agent-1");
    expectRequiredStartFields(request.body);
  });

  it("uses the continue route without start-only fields for an existing conversation", () => {
    const request = buildCloudChatRequest(
      conversation({ cloudConversationId: "existing-conversation" }),
      "again",
      "test-model",
      "cloud",
      null,
      "http://127.0.0.1:22240",
      "https://api.example.test",
      undefined,
      [],
      null,
      RUN_CONTROLS,
      [],
    );

    expect(request.url).toBe(
      "https://api.example.test/api/ai/conversations/existing-conversation",
    );
    expect(request.body).not.toHaveProperty("conversation_id");
    expect(request.body).not.toHaveProperty("is_new");
  });

  it("uses the same required start contract for local execution", () => {
    const request = buildCloudChatRequest(
      conversation(),
      "hi",
      "test-model",
      "local",
      "local-model",
      "http://127.0.0.1:22240",
      "https://api.example.test",
      undefined,
      [],
      null,
      RUN_CONTROLS,
      [],
    );

    expect(request.url).toBe("http://127.0.0.1:22240/ai/chat");
    expectRequiredStartFields(request.body);
  });
});

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
