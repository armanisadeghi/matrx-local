import type { ChatMessage } from "@/hooks/use-chat";

const OPTIMISTIC_MATCH_WINDOW_MS = 10 * 60 * 1000;

function normalized(value: string): string {
  return value.trim().replace(/\s+/g, " ");
}

function textRepresentsSameTurn(optimistic: string, durable: string): boolean {
  const local = normalized(optimistic);
  const remote = normalized(durable);
  if (local === remote) return true;

  // The legacy /chat persistence path can collapse the client-managed
  // transcript into one durable user row.  Treat that row as authoritative
  // when it contains the optimistic prompt verbatim, but avoid matching tiny
  // replies such as "yes" against unrelated prose.
  return local.length >= 12 && remote.includes(local);
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
    return textRepresentsSameTurn(optimistic.content, durable.content);
  }
  return (
    isUnsettledAssistant(optimistic) ||
    textRepresentsSameTurn(optimistic.content, durable.content)
  );
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
