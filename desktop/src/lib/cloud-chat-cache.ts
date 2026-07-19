import type { Conversation } from "@/hooks/use-chat";

const LEGACY_STORAGE_KEY = "matrx-cloud-chat-conversations";
const STORAGE_KEY_PREFIX = `${LEGACY_STORAGE_KEY}:user:`;

export function cloudChatStorageKey(userId: string): string {
  return `${STORAGE_KEY_PREFIX}${userId}`;
}

/**
 * Remove the pre-user-scoping cache. It cannot be attributed safely to the
 * current account, so migrating it would risk exposing another user's chat.
 */
export function discardLegacyCloudChatCache(): void {
  try {
    localStorage.removeItem(LEGACY_STORAGE_KEY);
  } catch {
    // Storage can be unavailable in hardened browser contexts.
  }
}

export function loadCloudChatCache(userId: string): Conversation[] {
  try {
    const raw = localStorage.getItem(cloudChatStorageKey(userId));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Conversation[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function saveCloudChatCache(
  userId: string,
  conversations: Conversation[],
  maxConversations: number,
): void {
  try {
    localStorage.setItem(
      cloudChatStorageKey(userId),
      JSON.stringify(conversations.slice(0, maxConversations)),
    );
  } catch {
    // Storage full: keep the in-memory chat usable.
  }
}
