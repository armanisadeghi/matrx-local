/**
 * A cloud rejoin with no journal to replay (`409 live_stream_unavailable`).
 *
 * The desktop's request loop owns its own wire, so it takes the package's ONE
 * fallback directly — `followUnavailableRejoin` (follow the run to its end
 * under the run's organization) then `settleRunPickup` (reload the saved turn,
 * or hand a still-running turn to this surface's live-turn follower) — exactly
 * as the web app and the extension do. Never a failed request for a run that
 * did not fail.
 */

import {
  followUnavailableRejoin,
  settleRunPickup,
  type MatrxLiveRunRejoin,
  type MatrxLiveStreamUnavailable,
  type MatrxRunPickupSettlement,
  type MatrxTransport,
} from "@ai-matrx/agents/matrx";

export interface SettleCloudRejoinArgs {
  /** The cloud server root; requests go to `${cloudServerUrl}/api${path}`. */
  cloudServerUrl: string;
  /** A fresh bearer for the follow. */
  accessToken: string;
  rejoin: MatrxLiveRunRejoin;
  unavailable: MatrxLiveStreamUnavailable;
  /** THE RUN's organization (its conversation's), when known. */
  organizationId: string | null;
  signal?: AbortSignal;
  /** Re-read the conversation's saved messages into the screen. */
  reloadSavedTurn: () => Promise<void>;
  /** The follow gave up while the turn may still be running. */
  onStillRunning: () => Promise<void>;
  /** A one-line note for the run's diagnostics. */
  onDiagnostic?: (line: string) => void;
  fetchImpl?: typeof fetch;
}

export async function settleCloudRejoinWithoutJournal(
  args: SettleCloudRejoinArgs,
): Promise<MatrxRunPickupSettlement> {
  const doFetch = args.fetchImpl ?? fetch;
  const transport: MatrxTransport = {
    fetch: (path, init) =>
      doFetch(`${args.cloudServerUrl}/api${path}`, {
        method: init.method,
        headers: { ...init.headers, Authorization: `Bearer ${args.accessToken}` },
        ...(init.body !== undefined ? { body: init.body } : {}),
        ...(init.signal ? { signal: init.signal } : {}),
      }),
  };
  const followed = await followUnavailableRejoin(transport, args.rejoin, args.unavailable, {
    ...(args.signal ? { signal: args.signal } : {}),
    ...(args.organizationId ? { organizationId: args.organizationId } : {}),
  }).catch((error: unknown) => {
    // The follow itself failed: the saved record is still the best truth.
    args.onDiagnostic?.(
      `Following the run failed (${error instanceof Error ? error.message : String(error)}) — reloading the saved turn.`,
    );
    return null;
  });
  const settlement = await settleRunPickup(
    followed ?? { kind: "followed", executionId: "unknown", ended: false, status: null },
    { reloadSavedTurn: args.reloadSavedTurn, onStillRunning: args.onStillRunning },
  );
  args.onDiagnostic?.(`Rejoin without a live journal settled: ${settlement.state}.`);
  return settlement;
}
