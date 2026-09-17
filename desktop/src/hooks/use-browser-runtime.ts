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
import { enqueueDurableClientError } from "@/lib/error-outbox";

export interface BrowserRuntimeState {
  status: BrowserRuntimeStatus | null;
  /** Unknown until the first probe answers — never gate UI on `false` alone. */
  loaded: boolean;
  /** The last status request failed; `status`, if present, is stale. */
  statusFetchFailed: boolean;
  installing: boolean;
  percent: number;
  message: string | null;
  error: string | null;
}

export interface BrowserRuntimeActions {
  /** `captureFailure` is only true after engine connection or a user retry. */
  refresh: (captureFailure?: boolean) => Promise<void>;
  install: () => Promise<void>;
}

export type UseBrowserRuntimeReturn = BrowserRuntimeState & {
  available: boolean;
  actions: BrowserRuntimeActions;
};

// While a background first-boot download is running the engine's own state
// changes without us doing anything, so poll — but only then.
const INSTALLING_POLL_MS = 4000;
const ACTIONABLE_TERMINAL_CODES = new Set([
  "browser_install_failed",
  "browser_launch_failed",
  "browser_build_mismatch",
]);

/** Capture once per terminal-state transition. If identity is not ready, keep
 * the previous marker so a later refresh can safely retry instead of silently
 * losing the incident or assigning it to the wrong person. */
export function captureBrowserRuntimeFailureTransition(
  previous: string | null,
  nextCode: string,
  capture: typeof enqueueDurableClientError = enqueueDurableClientError,
): string | null {
  const safeCode = ACTIONABLE_TERMINAL_CODES.has(nextCode) ? nextCode : null;
  if (!safeCode || previous === safeCode) return safeCode;
  const accepted = capture({
    level: "error",
    source: "browser-runtime",
    message: `Browser runtime needs attention (${safeCode}).`,
    requireIdentity: true,
    causalSignature: `browser-runtime:${safeCode}`,
  });
  return accepted ? safeCode : previous;
}

/** Capture one failed status request after connection, then re-arm on success. */
export function captureBrowserRuntimeStatusFetchFailure(
  previouslyCaptured: boolean,
  capture: typeof enqueueDurableClientError = enqueueDurableClientError,
): boolean {
  if (previouslyCaptured) return true;
  return capture({
    level: "error",
    source: "browser-runtime",
    message: "Could not refresh the built-in browser status.",
    requireIdentity: true,
    causalSignature: "browser-runtime:status-fetch",
  });
}

export function useBrowserRuntime(): UseBrowserRuntimeReturn {
  const [status, setStatus] = useState<BrowserRuntimeStatus | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [percent, setPercent] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusFetchFailed, setStatusFetchFailed] = useState(false);
  const installRunning = useRef(false);
  const priorTerminalCode = useRef<string | null>(null);
  const statusFetchFailureCaptured = useRef(false);
  const statusRequestId = useRef(0);
  const mounted = useRef(true);

  useEffect(() => {
    // React StrictMode runs setup → cleanup → setup. Re-arm this hook for the
    // replay and fence every request that belonged to the prior generation.
    mounted.current = true;
    return () => {
      mounted.current = false;
      statusRequestId.current += 1;
    };
  }, []);

  const refresh = useCallback(async (captureFailure = false) => {
    const requestId = ++statusRequestId.current;
    try {
      const next = await engine.getBrowserRuntimeStatus();
      if (!mounted.current || requestId !== statusRequestId.current) return;
      priorTerminalCode.current = captureBrowserRuntimeFailureTransition(
        priorTerminalCode.current,
        next.code,
      );
      setStatus(next);
      setLoaded(true);
      setStatusFetchFailed(false);
      statusFetchFailureCaptured.current = false;
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
      if (!mounted.current || requestId !== statusRequestId.current) return;
      // Mount-time probing can precede discovery. A connected retry is an
      // incident; in both cases retain the last truthful state.
      setStatusFetchFailed(captureFailure);
      if (captureFailure) {
        statusFetchFailureCaptured.current = captureBrowserRuntimeStatusFetchFailure(
          statusFetchFailureCaptured.current,
        );
      }
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
      await refresh(true);
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
    const id = setInterval(() => void refresh(true), INSTALLING_POLL_MS);
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
    statusFetchFailed,
    // Optimistic before the first probe answers: never disable a working
    // control because the status call has not returned yet.
    available: status ? status.available : true,
    actions,
  };
}
