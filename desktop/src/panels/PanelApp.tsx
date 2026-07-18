/**
 * PanelApp — root component for lightweight panel windows (`panel-<page>`).
 *
 * Mounts ONLY the providers the panel's page needs (see manifest.tsx), plus a
 * per-window engine connection. As a non-leader window it never runs the
 * background-task orchestrator, cloud heartbeat, or auto-update polling
 * (leader gating lives in the hooks themselves).
 *
 * Auth is shared through localStorage (supabase-js is multi-tab aware): if
 * the user isn't signed in, the panel shows a pointer to the main window
 * rather than duplicating the login/OAuth flow.
 */

import { useEffect, type ReactNode } from "react";
import { HashRouter } from "react-router-dom";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { AppActionBanner } from "@/components/layout/AppActionBanner";
import {
  ActionNeededNavigationBridge,
  ActionNeededSources,
  actionNeededStore,
} from "@/features/action-needed";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useAuth } from "@/hooks/use-auth";
import { useEngine } from "@/hooks/use-engine";
import { useTheme } from "@/hooks/use-theme";
import { PANEL_MANIFEST } from "@/panels/manifest";
import { PanelLayout } from "@/panels/PanelLayout";
import type { PanelPage } from "@/lib/window-role";

export function PanelApp({ page }: { page: PanelPage }) {
  const entry = PANEL_MANIFEST[page];

  if (!entry) {
    // Unknown label → loud, actionable failure (never a blank window).
    return (
      <div className="flex h-screen items-center justify-center bg-background p-8 text-center text-sm text-muted-foreground">
        Unknown panel page “{page}”. This window label has no entry in
        panels/manifest.tsx — add one or fix the PANEL_PAGES table in
        windows.rs.
      </div>
    );
  }

  return (
    <ErrorBoundary>
      <TooltipProvider delayDuration={150}>
        <PanelInner page={page} />
      </TooltipProvider>
    </ErrorBoundary>
  );
}

function PanelInner({ page }: { page: PanelPage }) {
  const entry = PANEL_MANIFEST[page];
  const auth = useAuth();
  useTheme(); // applies the persisted light/dark class to this window
  const { status, url, tools } = useEngine(auth.isAuthenticated);

  useEffect(() => {
    if (status !== "connected") actionNeededStore.reset();
  }, [status]);

  let body: ReactNode;
  if (auth.loading) {
    body = (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Loading…
      </div>
    );
  } else if (!auth.isAuthenticated) {
    body = (
      <div className="flex h-full items-center justify-center p-8 text-center text-sm text-muted-foreground">
        Sign in from the main AI Matrx window to use this panel.
      </div>
    );
  } else {
    const ctx = { status, url, tools, user: auth.user };
    const surface = (
      <>
        <ActionNeededSources />
        {entry.render(ctx)}
      </>
    );
    // Compose the manifest's providers around the page, outermost first.
    body = entry.providers.reduceRight<ReactNode>(
      (children, provider) => provider.wrap(children, ctx),
      surface,
    );
  }

  return (
    <HashRouter>
      <ActionNeededNavigationBridge />
      <PanelLayout title={entry.title} status={status}>
        <AppActionBanner engineStatus={status} />
        {body}
      </PanelLayout>
    </HashRouter>
  );
}
