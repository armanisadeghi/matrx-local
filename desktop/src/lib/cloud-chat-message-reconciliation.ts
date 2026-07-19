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

function durableReplacementKind(
  optimistic: ChatMessage,
  durable: ChatMessage,
): "exact" | "combined" | "unsettled" | null {
  if (optimistic.role !== durable.role) return null;
  const timeDelta = Math.abs(
    new Date(optimistic.timestamp).getTime() -
      new Date(durable.timestamp).getTime(),
  );
  if (!Number.isFinite(timeDelta) || timeDelta > OPTIMISTIC_MATCH_WINDOW_MS) {
    return null;
  }
  const local = normalized(optimistic.content);
  const remote = normalized(durable.content);
  if (optimistic.role === "user") {
    if (local === remote) return "exact";
    return textRepresentsSameTurn(optimistic.content, durable.content)
      ? "combined"
      : null;
  }
  if (local === remote) return "exact";
  if (isUnsettledAssistant(optimistic)) return "unsettled";
  return textRepresentsSameTurn(optimistic.content, durable.content)
    ? "combined"
    : null;
}

export function reconcileHydratedChatMessages(
  existing: ChatMessage[],
  hydrated: ChatMessage[],
): ChatMessage[] {
  const hydratedIds = new Set(hydrated.map((message) => message.id));
  const consumedDurable = new Set<number>();
  const optimistic = existing.filter((message) => {
    if (hydratedIds.has(message.id)) return false;
    for (let index = 0; index < hydrated.length; index += 1) {
      const kind = durableReplacementKind(message, hydrated[index]!);
      if (!kind) continue;
      // A legacy combined user row legitimately replaces multiple cached
      // prompts. Every other durable message represents exactly one turn and
      // must not consume two repeated local messages.
      const canRepresentMultiplePrompts =
        kind === "combined" && message.role === "user";
      if (!canRepresentMultiplePrompts && consumedDurable.has(index)) continue;
      if (!canRepresentMultiplePrompts) consumedDurable.add(index);
      return false;
    }
    return true;
  });
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
