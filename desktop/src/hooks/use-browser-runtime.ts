/**
 * use-browser-runtime — is browser-rendered scraping available, and the
 * one-click install that makes it available.
 *
 * The Playwright Chromium build is NOT bundled in the sidecar (it breaks macOS
 * codesign), so a fresh install downloads it in the background at first boot.
 * Until that finishes — or forever, if it failed — the "Browser" scrape method
 * and every JS-heavy page silently fail. This hook is the state behind telling
 * the user that in plain language and fixing it in one click.
 *
 * Mount ONCE via BrowserRuntimeProvider (CLAUDE.md § React Patterns: persistent
 * state belongs in Context, init fetch lives in the hook, polling is gated on
 * the specific boolean).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { engine, type BrowserRuntimeStatus } from "@/lib/api";

export interface BrowserRuntimeState {
  status: BrowserRuntimeStatus | null;
  /** Unknown until the first probe answers — never gate UI on `false` alone. */
  loaded: boolean;
  installing: boolean;
  percent: number;
  message: string | null;
  error: string | null;
}

export interface BrowserRuntimeActions {
  refresh: () => Promise<void>;
  install: () => Promise<void>;
}

export type UseBrowserRuntimeReturn = BrowserRuntimeState & {
  available: boolean;
  actions: BrowserRuntimeActions;
};

// While a background first-boot download is running the engine's own state
// changes without us doing anything, so poll — but only then.
const INSTALLING_POLL_MS = 4000;

export function useBrowserRuntime(): UseBrowserRuntimeReturn {
  const [status, setStatus] = useState<BrowserRuntimeStatus | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [percent, setPercent] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const installRunning = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const next = await engine.getBrowserRuntimeStatus();
      setStatus(next);
      setLoaded(true);
      // Adopt an install started elsewhere (first-boot background download, or
      // another window) so this surface shows progress it did not start.
      if (!installRunning.current) {
        setInstalling(next.installing);
        if (next.installing) {
          setPercent(next.install_percent ?? 0);
          setMessage(next.install_message);
        }
      }
    } catch (err) {
      // Engine not up yet is not an error worth showing; keep the last state.
      console.warn("[use-browser-runtime] status probe failed", err);
    }
  }, []);

  const install = useCallback(async () => {
    if (installRunning.current) return;
    installRunning.current = true;
    setInstalling(true);
    setError(null);
    setPercent(0);
    setMessage("Starting download…");
    try {
      await engine.installBrowserRuntime({
        onProgress: (event) => {
          if (typeof event.percent === "number") setPercent(event.percent);
          if (event.message) setMessage(event.message);
          if (event.status === "error") setError(event.message);
        },
        onComplete: () => {
          setMessage(null);
        },
        onError: (err) => setError(err),
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      installRunning.current = false;
      setInstalling(false);
      setPercent(0);
      await refresh();
    }
  }, [refresh]);

  // Init fetch lives in the hook, not the page — a page-level effect on
  // `actions` re-runs every render and loops.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Narrowly gated: only while an install is actually running, with cleanup.
  useEffect(() => {
    if (!installing) return;
    const id = setInterval(() => void refresh(), INSTALLING_POLL_MS);
    return () => clearInterval(id);
  }, [installing, refresh]);

  const actions = useMemo<BrowserRuntimeActions>(
    () => ({ refresh, install }),
    [refresh, install],
  );

  return {
    status,
    loaded,
    installing,
    percent,
    message,
    error,
    // Optimistic before the first probe answers: never disable a working
    // control because the status call has not returned yet.
    available: status ? status.available : true,
    actions,
  };
}
