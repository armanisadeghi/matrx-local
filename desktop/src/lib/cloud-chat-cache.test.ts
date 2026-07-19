import { beforeEach, describe, expect, it } from "vitest";
import {
  CloudChatAuthHydrationGuard,
  cloudChatStorageKey,
  discardLegacyCloudChatCache,
  loadCloudChatCache,
  saveCloudChatCache,
} from "./cloud-chat-cache";
import type { Conversation } from "@/hooks/use-chat";

const memory = new Map<string, string>();

Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: {
    getItem: (key: string) => memory.get(key) ?? null,
    setItem: (key: string, value: string) => memory.set(key, value),
    removeItem: (key: string) => memory.delete(key),
    clear: () => memory.clear(),
  },
});

function conversation(id: string, content: string): Conversation {
  return {
    id,
    title: content,
    mode: "chat",
    model: "test-model",
    messages: [
      {
        id: `${id}-message`,
        role: "user",
        content,
        timestamp: new Date(0).toISOString(),
      },
    ],
    created_at: new Date(0).toISOString(),
    updated_at: new Date(0).toISOString(),
  };
}

describe("cloud chat cache isolation", () => {
  beforeEach(() => memory.clear());

  it("never exposes account A conversations to account B", () => {
    saveCloudChatCache("account-a", [conversation("a", "private A")], 100);

    expect(loadCloudChatCache("account-b")).toEqual([]);
    expect(loadCloudChatCache("account-a")[0]?.messages[0]?.content).toBe(
      "private A",
    );
  });

  it("discards the unscoped legacy cache instead of assigning it to a user", () => {
    memory.set(
      "matrx-cloud-chat-conversations",
      JSON.stringify([conversation("legacy", "unknown owner")]),
    );

    discardLegacyCloudChatCache();

    expect(memory.has("matrx-cloud-chat-conversations")).toBe(false);
    expect(loadCloudChatCache("account-a")).toEqual([]);
  });

  it("keeps same-user offline history and enforces the size limit", () => {
    saveCloudChatCache(
      "account-a",
      [conversation("newest", "one"), conversation("older", "two")],
      1,
    );

    expect(loadCloudChatCache("account-a").map((item) => item.id)).toEqual([
      "newest",
    ]);
    expect(memory.has(cloudChatStorageKey("account-a"))).toBe(true);
  });

  it("rejects stale initial-session hydration after a newer auth event", () => {
    const guard = new CloudChatAuthHydrationGuard();
    const initialRequest = guard.captureInitialRequest();

    guard.noteAuthEvent();

    expect(guard.acceptsInitialResult(initialRequest)).toBe(false);
  });
});
