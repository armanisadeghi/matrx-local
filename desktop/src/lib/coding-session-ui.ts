export type SingleFlightRef<T> = { current: Promise<T> | null };

/** Coalesce concurrent refresh triggers onto the request already in flight. */
export function runSingleFlight<T>(
  flight: SingleFlightRef<T>,
  operation: () => Promise<T>,
): Promise<T> {
  if (flight.current) return flight.current;

  const request = operation();
  flight.current = request;
  const clear = () => {
    if (flight.current === request) flight.current = null;
  };
  void request.then(clear, clear);
  return request;
}

export function formatRetryDuration(seconds: number): string {
  const rounded = Math.max(1, Math.ceil(seconds));
  if (rounded < 60) return `${rounded}s`;
  const minutes = Math.floor(rounded / 60);
  const remainder = rounded % 60;
  return remainder === 0 ? `${minutes}m` : `${minutes}m ${remainder}s`;
}

const ACTION_LABELS: Record<string, string> = {
  observe_hook: "Live event delivery",
  append_native: "Conversation update delivery",
  load_native: "Conversation load",
  list_native: "Conversation list",
  delete: "Conversation deletion",
  health: "Bridge health check",
};

const SOURCE_LABELS: Record<string, string> = {
  claude_local_jsonl: "Claude history import",
  independent_hook: "provider command hook",
  matrx_local: "Matrx Local",
  matrx_sandbox: "Matrx sandbox",
};

export function codingSessionActionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action.replace(/_/g, " ");
}

export function codingSessionSourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source.replace(/_/g, " ");
}
