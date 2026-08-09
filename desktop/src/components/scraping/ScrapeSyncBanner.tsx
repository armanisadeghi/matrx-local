/**
 * ScrapeSyncBanner — makes the scrape cloud-sync backlog visible, with the
 * one click that clears it.
 *
 * Every scrape is a dual write: local SQLite first, then a push to the server
 * so the web app and every other device sees it. The second half used to fail
 * silently — the only way to learn that 6 of your 8 scrapes never left the
 * machine was to call GET /scrapes/sync-status by hand.
 *
 * Per the repo's states-not-errors doctrine, this renders the STATE the engine
 * reports, never `cloud_sync_error`:
 *   signed_out — the engine's stored JWT went stale. "Sign in to sync" pushes
 *                the live Supabase token back to the engine, which drains the
 *                whole backlog (POST /auth/token → sync_after_sign_in).
 *   offline    — informational; it fixes itself.
 *   rejected   — the only true failure, and retry is still offered.
 *   queued     — a quiet note that upload is in flight.
 * `synced` renders nothing at all.
 */

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, CloudOff, LogIn, Loader2, RefreshCw, UploadCloud } from "lucide-react";
import { Button } from "@/components/ui/button";
import { engine, type ScrapeSyncState, type ScrapeSyncStatus } from "@/lib/api";
import supabase from "@/lib/supabase";

const POLL_MS = 30_000;

const STYLES: Record<Exclude<ScrapeSyncState, "synced">, { container: string; icon: string }> = {
  signed_out: {
    container:
      "border-amber-300/60 bg-amber-50/95 text-amber-950 dark:border-amber-800/50 dark:bg-amber-950/35 dark:text-amber-100",
    icon: "text-amber-600 dark:text-amber-400",
  },
  offline: {
    container:
      "border-blue-300/60 bg-blue-50/95 text-blue-950 dark:border-blue-800/50 dark:bg-blue-950/35 dark:text-blue-100",
    icon: "text-blue-600 dark:text-blue-400",
  },
  rejected: {
    container: "border-destructive/30 bg-destructive/10 text-foreground",
    icon: "text-destructive",
  },
  queued: {
    container: "border-border bg-muted/50 text-foreground",
    icon: "text-muted-foreground",
  },
};

const ICONS: Record<Exclude<ScrapeSyncState, "synced">, typeof CloudOff> = {
  signed_out: LogIn,
  offline: CloudOff,
  rejected: CloudOff,
  queued: UploadCloud,
};

export function ScrapeSyncBanner() {
  const [status, setStatus] = useState<ScrapeSyncStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [justSynced, setJustSynced] = useState(0);

  const refresh = useCallback(async () => {
    try {
      setStatus(await engine.getScrapeSyncStatus());
    } catch {
      // The engine being down is already surfaced by EngineDownBanner —
      // don't stack a second complaint about the same thing.
      setStatus(null);
    }
  }, []);

  // `refresh` is useCallback([]) — stable forever — so this effect runs once
  // and the interval is never torn down and recreated by a re-render. That is
  // the shape CLAUDE.md § React Patterns requires; an unstable dep here is the
  // bug class that flooded the production engine.
  useEffect(() => {
    void refresh();
    const id = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  /** Re-establish the engine's JWT from the live Supabase session. The engine
   *  drains the scrape backlog on receipt, so this is the whole fix. */
  const handleSignIn = useCallback(async () => {
    setBusy(true);
    try {
      const { data } = await supabase.auth.getSession();
      const session = data.session;
      if (session) {
        await engine.syncTokenToPython(
          session.access_token,
          session.user.id,
          session.refresh_token,
          session.expires_in,
        );
      }
      const result = await engine.triggerScrapeSync();
      setJustSynced(result.pushed);
      setStatus(result.status);
    } catch {
      await refresh();
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  const handleRetry = useCallback(async () => {
    setBusy(true);
    try {
      const result = await engine.triggerScrapeSync();
      setJustSynced(result.pushed);
      setStatus(result.status);
    } catch {
      await refresh();
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  // A success note, but only right after the user pressed something — an
  // idle "all synced" strip is noise.
  useEffect(() => {
    if (!justSynced) return;
    const id = setTimeout(() => setJustSynced(0), 6000);
    return () => clearTimeout(id);
  }, [justSynced]);

  return (
    <ScrapeSyncStrip
      status={status}
      busy={busy}
      justSynced={justSynced}
      onAction={() => void (status?.action === "sign_in" ? handleSignIn() : handleRetry())}
    />
  );
}

/**
 * The presentation half — pure, so it can be rendered and asserted without a
 * DOM or a live engine. `ScrapeSyncBanner` owns the fetching; this owns what
 * the user actually reads.
 */
export function ScrapeSyncStrip({
  status,
  busy = false,
  justSynced = 0,
  onAction,
}: {
  status: ScrapeSyncStatus | null;
  busy?: boolean;
  justSynced?: number;
  onAction?: () => void;
}) {
  if (!status) return null;

  if (status.state === "synced") {
    if (!justSynced) return null;
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex shrink-0 items-center gap-3 border-b border-emerald-300/60 bg-emerald-50/95 px-4 py-2 text-sm text-emerald-950 dark:border-emerald-800/50 dark:bg-emerald-950/35 dark:text-emerald-100"
      >
        <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
        <span className="min-w-0 flex-1">
          {justSynced} scrape{justSynced === 1 ? "" : "s"} uploaded to the cloud.
        </span>
      </div>
    );
  }

  const style = STYLES[status.state];
  const Icon = ICONS[status.state];
  const showSignIn = status.action === "sign_in";
  const showRetry = status.action === "retry";

  return (
    <div
      role="status"
      aria-live="polite"
      className={`flex shrink-0 items-start gap-3 border-b px-4 py-2 text-sm ${style.container}`}
    >
      <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${style.icon}`} />
      <div className="min-w-0 flex-1">
        <span className="font-medium">
          {status.unsynced} scrape{status.unsynced === 1 ? "" : "s"} not yet in the cloud.
        </span>{" "}
        <span className="opacity-80">{status.message}</span>
      </div>
      {(showSignIn || showRetry) && (
        <Button
          size="sm"
          variant="outline"
          className="h-7 shrink-0 bg-background/70 px-2.5 text-xs"
          disabled={busy}
          onClick={onAction}
        >
          {busy ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : showSignIn ? (
            <LogIn className="mr-1.5 h-3.5 w-3.5" />
          ) : (
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          )}
          {showSignIn ? "Sign in to sync" : "Retry upload"}
        </Button>
      )}
    </div>
  );
}
