import { describe, expect, it } from "vitest";
import {
  needsBackgroundChatReconciliation,
  reconcileHydratedChatMessages,
} from "./cloud-chat-message-reconciliation";
import type { ChatMessage } from "@/hooks/use-chat";

const at = "2026-07-18T12:00:00.000Z";

function message(
  id: string,
  role: "user" | "assistant",
  content: string,
  extra: Partial<ChatMessage> = {},
): ChatMessage {
  return { id, role, content, timestamp: at, ...extra };
}

describe("Cloud Chat durable message reconciliation", () => {
  it("replaces optimistic user and background assistant messages", () => {
    const existing = [
      message("local-user", "user", "run the tool"),
      message("local-assistant", "assistant", "Starting…", {
        streamStatus: "Local tools finished in the background.",
      }),
    ];
    const hydrated = [
      message("server-user", "user", "run the tool"),
      message("server-assistant", "assistant", "Finished with durable output"),
    ];

    expect(reconcileHydratedChatMessages(existing, hydrated)).toEqual(hydrated);
    expect(needsBackgroundChatReconciliation(existing)).toBe(true);
  });

  it("preserves unrelated local drafts and messages", () => {
    const existing = [message("local-user", "user", "different message")];
    const hydrated = [message("server-user", "user", "server message")];

    expect(
      reconcileHydratedChatMessages(existing, hydrated).map((item) => item.id),
    ).toEqual(["server-user", "local-user"]);
  });

  it("replaces a cached turn when legacy persistence combines its prompts", () => {
    const existing = [
      message("local-user-1", "user", "Please inspect the project handoff."),
      message("local-assistant", "assistant", "I inspected the handoff."),
      message("local-user-2", "user", "Now implement the first phase."),
    ];
    const hydrated = [
      message(
        "server-user",
        "user",
        "Please inspect the project handoff.\n\nNow implement the first phase.",
      ),
      message("server-assistant", "assistant", "I inspected the handoff."),
    ];

    expect(reconcileHydratedChatMessages(existing, hydrated)).toEqual(hydrated);
  });

  it("does not match short replies merely because durable prose contains them", () => {
    const existing = [message("local-user", "user", "yes")];
    const hydrated = [
      message("server-user", "user", "Yesterday the answer was yes, but not today."),
    ];

    expect(
      reconcileHydratedChatMessages(existing, hydrated).map((item) => item.id),
    ).toEqual(["server-user", "local-user"]);
  });
});
