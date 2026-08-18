/**
 * GmailReviewCard — the Gmail consent surface on the desktop.
 *
 * THIS CARD IS THE AUTHORIZATION. The agent proposed a message; nothing has
 * been sent, and nothing can be until the user presses Send here. Everything
 * that will leave their mailbox is on screen and editable: sender, recipient,
 * CC, subject, body. The send posts exactly what the fields hold at that
 * moment — never the agent's original arguments once the user has changed them.
 *
 * Deliberately absent: any "always send" affordance, any pre-checked consent,
 * and any path that sends without a click. Approval here covers ONE message.
 *
 * Behavioural twin of matrx-frontend's
 * `features/google-workspace/agent/GmailReviewCard.tsx`.
 */

import { useEffect, useState } from "react";
import { ExternalLink, Loader2, Send, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  googleWorkspaceSettingsUrl,
  resolveGmailSendConnection,
  sendReviewedGmail,
  type GoogleConnectionRef,
} from "@/lib/google-workspace";
import { openExternal } from "@/lib/open-external";
import type {
  PendingEmailReview,
  ReviewDecision,
} from "@/hooks/use-email-reviews";

interface GmailReviewCardProps {
  review: PendingEmailReview;
  onResolve: (callId: string, decision: ReviewDecision) => Promise<void>;
}

function parseAddressList(raw: string): string[] {
  return raw
    .split(/[,;]/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

const NO_CONNECTION_MESSAGE =
  "No Google account with sending access is connected. Connect one at " +
  "Settings → Integrations → Google Workspace, then ask again.";

export function GmailReviewCard({ review, onResolve }: GmailReviewCardProps) {
  const [to, setTo] = useState(review.to);
  const [cc, setCc] = useState(review.cc.join(", "));
  const [subject, setSubject] = useState(review.subject);
  const [body, setBody] = useState(review.body);
  const [mailbox, setMailbox] = useState<GoogleConnectionRef | null>(null);
  const [resolvingMailbox, setResolvingMailbox] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Which mailbox would this send from? Answered before any Send is possible.
  // No connected account is a refusal the agent can act on — never a send,
  // and never a silent hang.
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    void (async () => {
      try {
        const connection = await resolveGmailSendConnection(controller.signal);
        if (cancelled) return;
        setMailbox(connection);
        setResolvingMailbox(false);
        if (!connection) {
          await onResolve(review.callId, {
            outcome: "error",
            error: NO_CONNECTION_MESSAGE,
          });
        }
      } catch (cause) {
        if (cancelled) return;
        setResolvingMailbox(false);
        setError(
          cause instanceof Error
            ? cause.message
            : "Could not check your connected Google account.",
        );
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [review.callId, onResolve]);

  const edited =
    to !== review.to ||
    cc !== review.cc.join(", ") ||
    subject !== review.subject ||
    body !== review.body;
  const canSend =
    Boolean(mailbox) && to.trim().includes("@") && Boolean(subject.trim()) && Boolean(body.trim());

  async function send() {
    if (!mailbox || sending || !canSend) return;
    setSending(true);
    setError(null);
    const ccList = parseAddressList(cc);
    try {
      // The exact bytes on screen — not the agent's arguments.
      const messageId = await sendReviewedGmail({
        connectionId: mailbox.connectionId,
        to: to.trim(),
        cc: ccList,
        subject,
        body,
      });
      await onResolve(review.callId, {
        outcome: "sent",
        messageId,
        to: to.trim(),
        cc: ccList,
        subject,
        fromEmail: mailbox.accountEmail,
        edited,
      });
    } catch (cause) {
      // The card stays open and says nothing was sent. Never a success.
      setError(
        cause instanceof Error ? cause.message : "The message was not sent.",
      );
      setSending(false);
    }
  }

  function decline() {
    void onResolve(review.callId, { outcome: "declined" }).catch(
      (cause: unknown) =>
        setError(
          cause instanceof Error ? cause.message : "Could not record your choice.",
        ),
    );
  }

  function dismiss() {
    void onResolve(review.callId, { outcome: "cancelled" }).catch(
      (cause: unknown) =>
        setError(
          cause instanceof Error ? cause.message : "Could not record your choice.",
        ),
    );
  }

  if (resolvingMailbox) {
    return (
      <div className="flex items-center gap-2 rounded-2xl border border-border/60 bg-card px-4 py-3 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        Checking which Google account this would send from…
      </div>
    );
  }
  if (!mailbox) return null;

  return (
    <div className="rounded-2xl border border-border/60 bg-card shadow-sm">
      <div className="flex items-start gap-3 border-b border-border/60 px-4 py-3">
        <div className="mt-0.5 rounded-lg bg-sky-500/10 p-1.5 text-sky-500">
          <Send className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            Review before sending
          </div>
          <div className="truncate text-sm font-medium">
            {subject.trim() || "No subject"}
          </div>
          <div className="truncate text-xs text-muted-foreground">
            {mailbox.accountEmail
              ? `From ${mailbox.accountEmail} — your connected Google account`
              : "From your connected Google account"}
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 shrink-0"
          onClick={dismiss}
          disabled={sending}
          aria-label="Dismiss without sending"
          title="Dismiss without sending"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="flex flex-col gap-3 px-4 py-3">
        <div className="grid gap-1.5">
          <Label htmlFor={`gmail-to-${review.callId}`}>To</Label>
          <Input
            id={`gmail-to-${review.callId}`}
            value={to}
            onChange={(event) => setTo(event.target.value)}
            disabled={sending}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor={`gmail-cc-${review.callId}`}>
            Cc <span className="text-muted-foreground">(optional)</span>
          </Label>
          <Input
            id={`gmail-cc-${review.callId}`}
            value={cc}
            onChange={(event) => setCc(event.target.value)}
            placeholder="Separate addresses with commas"
            disabled={sending}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor={`gmail-subject-${review.callId}`}>Subject</Label>
          <Input
            id={`gmail-subject-${review.callId}`}
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
            disabled={sending}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor={`gmail-body-${review.callId}`}>Message</Label>
          <Textarea
            id={`gmail-body-${review.callId}`}
            value={body}
            onChange={(event) => setBody(event.target.value)}
            rows={8}
            disabled={sending}
          />
        </div>
        {error ? (
          <p className="text-sm text-red-600 dark:text-red-400">
            {error} Nothing was sent.
          </p>
        ) : null}
        <button
          type="button"
          onClick={() => void googleWorkspaceSettingsUrl().then(openExternal)}
          className="inline-flex w-fit items-center gap-1 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
        >
          Manage or disconnect this Google account
          <ExternalLink className="h-3 w-3" />
        </button>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/60 px-4 py-2.5">
        <span className="text-xs text-muted-foreground">
          Nothing sends until you press Send.
        </span>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={decline} disabled={sending}>
            Don&apos;t send
          </Button>
          <Button size="sm" onClick={() => void send()} disabled={sending || !canSend}>
            {sending ? (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
            ) : (
              <Send className="mr-1.5 h-4 w-4" />
            )}
            {sending ? "Sending…" : "Send"}
          </Button>
        </div>
      </div>
    </div>
  );
}
