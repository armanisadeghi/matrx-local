/**
 * Continue a coding session — natively, on this Mac, from the row itself.
 *
 * The desktop half of lane XT-04. "Continue" used to copy a command and stop
 * there; this app has always been able to run the turn. The dialog asks the
 * three verdicts (`@/lib/coding-sessions/continue-door`), states what it can
 * and cannot do in those verdicts' own words, starts the run through the
 * loopback runtime route, then polls the `status` rpc and offers `cancel` —
 * the two rpcs the runtime has always implemented and nobody called.
 *
 * Whatever the verdicts say, the resume command is still here to copy: a
 * refusal hands over the fallback instead of being a dead end.
 *
 * FOUR PROVIDERS, ONE CONTROL. The three verdicts are Claude Code's runtime
 * door and only a Claude Code row enters it (`continuationRoute`): this Mac
 * runs no Codex or Cursor turns. A Codex row gets the engine's own command to
 * copy and none of the Claude reads are made; a Cursor or VS Code row gets the
 * engine's sentence saying no command reopens one chat, plus the mirrored
 * conversation in AI Matrx when there is one. No provider ever sees a
 * `claude --resume` this app invented for it.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Copy, ExternalLink, Loader2, Play, Square } from "lucide-react";

import { Button } from "@ai-matrx/design-system";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { engine } from "@/lib/api";
import type { CodingSessionRow, LocalRuntimeCapabilities, LocalRuntimeRun } from "@/lib/api";
import { fetchBridgeCapabilities, type BridgeCapabilityReport } from "@/lib/aidream-client";
import { getAuthedSession } from "@/lib/custodian";
import {
  isOrganizationNotSelectedError,
  requireActiveOrganizationId,
} from "@/lib/org/active-org";
import {
  continuationRoute,
  continueDoorView,
  runStatusSentence,
  type SessionResumeVerdict,
} from "@/lib/coding-sessions/continue-door";
import { providerLabel } from "@/lib/coding-sessions/providers";

const POLL_MS = 1500;

function errorMessage(cause: unknown): string {
  if (isOrganizationNotSelectedError(cause)) {
    return "Choose an organization before continuing a session.";
  }
  return cause instanceof Error ? cause.message : String(cause);
}

export function ContinueSessionDialog({
  row,
  supportsResume,
  onOpenConversation,
  onClose,
}: {
  row: CodingSessionRow | null;
  /** The provider block's own `supports_resume`, never inferred from the row. */
  supportsResume: boolean;
  /** Opens the mirrored conversation; absent when this surface cannot. */
  onOpenConversation?: () => void;
  onClose: () => void;
}) {
  const [platform, setPlatform] = useState<{
    report: BridgeCapabilityReport | null;
    error: string | null;
  }>({ report: null, error: null });
  const [machine, setMachine] = useState<{
    capabilities: LocalRuntimeCapabilities | null;
    error: string | null;
  }>({ capabilities: null, error: null });
  const [session, setSession] = useState<{
    verdict: SessionResumeVerdict | null;
    error: string | null;
  }>({ verdict: null, error: null });
  const [prompt, setPrompt] = useState("");
  const [run, setRun] = useState<LocalRuntimeRun | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [copied, setCopied] = useState(false);
  const runIdRef = useRef<string | null>(null);

  const sessionId = row?.session_id ?? null;
  const provider = row?.provider ?? "claude_code";
  const label = providerLabel(provider);
  const route = continuationRoute({
    provider,
    label,
    continuation: row?.continuation ?? null,
    supportsResume,
    hasMirroredConversation:
      Boolean(row?.cloud?.conversation_id) && onOpenConversation !== undefined,
  });
  const native = route.kind === "native_runtime";
  const copyCommand = route.copyCommand ?? "";

  // Every read is re-run per opened row, and each one reports its own failure
  // rather than collapsing into a single "unavailable".
  useEffect(() => {
    // Only Claude Code's runtime door asks these three, and two of them are
    // Claude-Code-only engine routes: asking them for a Cursor row would
    // report a Claude answer about a chat Claude never wrote.
    if (!sessionId || !native) return;
    let cancelled = false;
    setPlatform({ report: null, error: null });
    setMachine({ capabilities: null, error: null });
    setSession({ verdict: null, error: null });
    setRun(null);
    setStartError(null);
    setPrompt("");
    runIdRef.current = null;

    void (async () => {
      try {
        const authed = await getAuthedSession();
        if (!authed?.access_token) {
          throw new Error("Sign in to AI Matrx in the desktop app.");
        }
        const organizationId = await requireActiveOrganizationId();
        const report = await fetchBridgeCapabilities(
          provider,
          "matrx_local",
          authed.access_token,
          organizationId,
        );
        if (!cancelled) setPlatform({ report, error: null });
      } catch (cause: unknown) {
        if (!cancelled) setPlatform({ report: null, error: errorMessage(cause) });
      }
    })();

    void (async () => {
      try {
        const capabilities = await engine.getRuntimeCapabilities();
        if (!cancelled) setMachine({ capabilities, error: null });
      } catch (cause: unknown) {
        if (!cancelled) setMachine({ capabilities: null, error: errorMessage(cause) });
      }
    })();

    void (async () => {
      try {
        const verdict = await engine.getRuntimeResumable(sessionId);
        if (!cancelled) setSession({ verdict, error: null });
      } catch (cause: unknown) {
        if (!cancelled) setSession({ verdict: null, error: errorMessage(cause) });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [native, provider, sessionId]);

  const door = continueDoorView({ copyCommand, label, platform, machine, session });
  const active = run !== null && (run.status === "starting" || run.status === "running");

  // The `status` rpc, finally wired: the run's live state, polled only while
  // it is actually active, with the poll cleared on close (React rule 14).
  useEffect(() => {
    if (!active || runIdRef.current === null) return;
    let stopped = false;
    const timer = window.setInterval(() => {
      const runtimeId = runIdRef.current;
      if (runtimeId === null) return;
      void engine
        .getRuntimeRun(runtimeId)
        .then((next) => {
          if (!stopped) setRun(next);
        })
        .catch((cause: unknown) => {
          if (!stopped) setStartError(errorMessage(cause));
        });
    }, POLL_MS);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [active]);

  const start = useCallback(async () => {
    if (!door.canStart || door.workspace === null || door.resumeSessionId === null) return;
    setStarting(true);
    setStartError(null);
    try {
      const started = await engine.startRuntimeSession({
        workspace: door.workspace,
        prompt: prompt.trim(),
        resume_session_id: door.resumeSessionId,
      });
      runIdRef.current = started.runtime_id;
      setRun(started);
    } catch (cause: unknown) {
      setStartError(errorMessage(cause));
    } finally {
      setStarting(false);
    }
  }, [door.canStart, door.resumeSessionId, door.workspace, prompt]);

  const cancel = useCallback(async () => {
    const runtimeId = runIdRef.current;
    if (runtimeId === null) return;
    setCancelling(true);
    try {
      await engine.cancelRuntimeSession(runtimeId);
      setRun(await engine.getRuntimeRun(runtimeId));
    } catch (cause: unknown) {
      setStartError(errorMessage(cause));
    } finally {
      setCancelling(false);
    }
  }, []);

  const copy = useCallback(() => {
    void navigator.clipboard
      .writeText(copyCommand)
      .then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      })
      .catch((cause: unknown) => setStartError(errorMessage(cause)));
  }, [copyCommand]);

  return (
    <Dialog open={row !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Continue this session</DialogTitle>
          <DialogDescription>
            {row?.title ?? "A coding session"}
            {row?.project ? ` · ${row.project}` : ""}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 text-sm">
          {!native && (
            <p className="text-muted-foreground" data-testid="continuation-route-sentence">
              {route.sentence}
            </p>
          )}

          {native && door.status === "checking" && (
            <p
              className="flex items-center gap-2 text-muted-foreground"
              data-testid="continue-door-checking"
            >
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Asking AI Matrx and this Mac whether this session can be continued here…
            </p>
          )}

          {native && door.reasons.map((reason) => (
            <p
              key={reason}
              className={
                door.status === "refused"
                  ? "flex items-start gap-2 text-amber-600 dark:text-amber-400"
                  : "text-muted-foreground"
              }
              data-testid={door.status === "refused" ? "continue-door-refusal" : "continue-door-offer"}
            >
              {door.status === "refused" && (
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              )}
              <span>{reason}</span>
            </p>
          ))}

          {native && door.canStart && run === null && (
            <div className="space-y-2">
              <label className="block text-xs font-medium text-muted-foreground" htmlFor="continue-prompt">
                What should it do next?
              </label>
              <textarea
                id="continue-prompt"
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                rows={4}
                className="w-full resize-none rounded-md border border-border/60 bg-background px-3 py-2 text-sm focus:outline-none"
                placeholder="Pick up where this session left off and…"
              />
            </div>
          )}

          {run !== null && (
            <div className="space-y-1 rounded-md border bg-muted/30 p-3" data-testid="continue-run-status">
              <p className="flex items-center gap-2">
                {active && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {runStatusSentence(run)}
              </p>
              <p className="font-mono text-[11px] text-muted-foreground">
                run {run.runtime_id}
                {run.provider_session_id ? ` · session ${run.provider_session_id}` : ""}
              </p>
            </div>
          )}

          {startError && (
            <p className="flex items-start gap-2 text-destructive" role="alert">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{startError}</span>
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2 pt-1">
            {native && door.canStart && run === null && (
              <Button type="button" size="sm" disabled={starting || prompt.trim().length === 0} onClick={() => void start()}>
                {starting ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Play className="mr-1.5 h-3.5 w-3.5" />
                )}
                Continue on this Mac
              </Button>
            )}
            {active && (
              <Button
                type="button"
                size="sm"
                variant="destructive"
                disabled={cancelling}
                onClick={() => void cancel()}
              >
                {cancelling ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Square className="mr-1.5 h-3.5 w-3.5" />
                )}
                Cancel this run
              </Button>
            )}
            {route.copyCommand !== null && (
              <Button type="button" size="sm" variant="outline" onClick={copy}>
                <Copy className="mr-1.5 h-3.5 w-3.5" />
                {copied ? "Copied" : `Copy the ${label} resume command`}
              </Button>
            )}
            {route.offerMirrored && onOpenConversation !== undefined && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => onOpenConversation()}
              >
                <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                Open the conversation in AI Matrx
              </Button>
            )}
          </div>
          {route.copyCommand !== null && (
            <p className="font-mono text-[11px] text-muted-foreground">{route.copyCommand}</p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
