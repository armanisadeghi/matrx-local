import type { ChatMessage } from "@/hooks/use-chat";

const OPTIMISTIC_MATCH_WINDOW_MS = 10 * 60 * 1000;

function normalized(value: string): string {
  return value.trim().replace(/\s+/g, " ");
}

function isUnsettledAssistant(message: ChatMessage): boolean {
  return (
    message.role === "assistant" &&
    (message.isStreaming === true ||
      /background|waiting for local|local tools finished/i.test(
        message.streamStatus ?? "",
      ))
  );
}

function isDurableReplacement(
  optimistic: ChatMessage,
  durable: ChatMessage,
): boolean {
  if (optimistic.role !== durable.role) return false;
  const timeDelta = Math.abs(
    new Date(optimistic.timestamp).getTime() -
      new Date(durable.timestamp).getTime(),
  );
  if (!Number.isFinite(timeDelta) || timeDelta > OPTIMISTIC_MATCH_WINDOW_MS) {
    return false;
  }
  if (optimistic.role === "user") {
    return normalized(optimistic.content) === normalized(durable.content);
  }
  return isUnsettledAssistant(optimistic);
}

export function reconcileHydratedChatMessages(
  existing: ChatMessage[],
  hydrated: ChatMessage[],
): ChatMessage[] {
  const hydratedIds = new Set(hydrated.map((message) => message.id));
  const optimistic = existing.filter(
    (message) =>
      !hydratedIds.has(message.id) &&
      !hydrated.some((durable) => isDurableReplacement(message, durable)),
  );
  return [...hydrated, ...optimistic].sort(
    (a, b) =>
      new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
  );
}

export function needsBackgroundChatReconciliation(
  messages: ChatMessage[],
): boolean {
  return messages.some(isUnsettledAssistant);
}
