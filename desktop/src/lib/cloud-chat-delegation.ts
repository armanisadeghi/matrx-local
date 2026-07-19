const DELEGATION_POLL_MS = 1000;
const DELEGATION_CLAIM_TTL_SECONDS = 20;
// Longest mega-tool execution timeout is Shell at 900s; add headroom.
const DELEGATION_WAIT_CAP_MS = 16 * 60 * 1000;

export interface EngineDelegationState {
  claimed?: boolean;
  calls?: Array<{ call_id: string; tool_name: string; state: string }>;
  continuation?: { user_request_id?: string | null; needed?: boolean } | null;
}

export type DelegationAccessToken = string | (() => Promise<string>);

async function resolveAccessToken(source: DelegationAccessToken): Promise<string> {
  return typeof source === "function" ? source() : source;
}

function authenticatedHeaders(accessToken: string): HeadersInit {
  return {
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  };
}

export async function claimDelegationUi(
  engineUrl: string,
  conversationId: string,
  accessToken: string,
): Promise<EngineDelegationState | null> {
  try {
    const response = await fetch(`${engineUrl}/chat/delegation/ui-claim`, {
      method: "POST",
      headers: authenticatedHeaders(accessToken),
      body: JSON.stringify({
        conversation_id: conversationId,
        ttl_seconds: DELEGATION_CLAIM_TTL_SECONDS,
      }),
      signal: AbortSignal.timeout(4000),
    });
    if (!response.ok) return null;
    return (await response.json()) as EngineDelegationState;
  } catch {
    return null;
  }
}

export async function releaseDelegationUi(
  engineUrl: string,
  conversationId: string,
  accessToken: string,
): Promise<void> {
  try {
    await fetch(`${engineUrl}/chat/delegation/ui-release`, {
      method: "POST",
      headers: authenticatedHeaders(accessToken),
      body: JSON.stringify({ conversation_id: conversationId }),
      signal: AbortSignal.timeout(4000),
    });
  } catch {
    // Claim TTL expiry makes the engine self-heal; release is best-effort.
  }
}

/**
 * Poll the local engine until the delegated calls resolve and a continuation
 * (`user_request_id`) is available, re-claiming UI ownership on every poll.
 * Returns null when the wait is abandoned — the engine's headless resume
 * then finishes the conversation once the claim expires.
 */
export async function waitForDelegatedContinuation(
  engineUrl: string,
  conversationId: string,
  accessToken: DelegationAccessToken,
  signal: AbortSignal,
  onStatus: (status: string) => void,
): Promise<string | null> {
  const startedAt = Date.now();
  while (!signal.aborted && Date.now() - startedAt < DELEGATION_WAIT_CAP_MS) {
    const state = await claimDelegationUi(
      engineUrl,
      conversationId,
      await resolveAccessToken(accessToken),
    );
    if (state) {
      const continuation = state.continuation;
      if (continuation?.needed && continuation.user_request_id) {
        return continuation.user_request_id;
      }
      const executing = state.calls?.filter((call) => call.state === "executing") ?? [];
      if (executing.length > 0) {
        onStatus(
          `Running on this computer: ${executing.map((call) => call.tool_name).join(", ")}...`,
        );
      } else {
        onStatus("Waiting for local tool results...");
      }
    } else {
      onStatus("Waiting for the local engine...");
    }
    await new Promise((resolve) => setTimeout(resolve, DELEGATION_POLL_MS));
  }
  return null;
}
