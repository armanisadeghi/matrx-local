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

const CLAUDE_ACCOUNT_REASON_MESSAGES: Record<string, string> = {
  claude_not_installed:
    "Claude Code is not installed or could not be found on this computer.",
  claude_not_signed_in:
    "Open Claude Code and sign in with the Claude account you want to use.",
  claude_status_timeout:
    "Claude Code was found, but its account check timed out. Close any blocked Claude process and try again.",
  claude_status_execution_failed:
    "Claude Code was found, but it could not report its account status. Open Claude Code, confirm it starts normally, and try again.",
  claude_account_identity_unavailable:
    "Claude is signed in, but this login did not report a stable email or organization identity. Sync stays paused so accounts cannot be mixed.",
  "Claude Code is not installed on this machine (no `claude` binary found)":
    "Claude Code is not installed or could not be found on this computer.",
  "Claude login unavailable":
    "Open Claude Code and sign in with the Claude account you want to use.",
  "Sign in to AI Matrx in the desktop app":
    "Sign in to AI Matrx in the desktop app, then refresh this check.",
};

/** Turn local provider probe codes into guidance; never expose an internal key. */
export function claudeAccountReasonMessage(reason: string): string {
  const known = CLAUDE_ACCOUNT_REASON_MESSAGES[reason];
  if (known) return known;
  if (reason.startsWith("claude-agent-sdk unavailable:")) {
    return "The Claude runtime component is unavailable. Update and restart AI Matrx Local, then try again.";
  }
  return "Claude account access could not be verified. Open Claude Code, confirm it is signed in, then refresh this check.";
}
