/**
 * Central scraping hook — single source of truth for all scrape operations.
 *
 * Provides:
 *   scrapeOne()   – single URL, immediate result, no queue
 *   scrapeMany()  – bulk, manages ScrapeEntry queue, supports abort
 *
 * Shared by: Scraping page (Single tab, Bulk tab) and QuickScrapeModal.
 * Logging: single-line INFO on success; full structured dump on any error.
 */

import { useState, useCallback, useRef, useMemo } from "react";
import { engine, type ScrapeResultData } from "@/lib/api";
import { parseScrapeExtraction } from "@/lib/scrape-extraction";

// ── Types ──────────────────────────────────────────────────────────────────

export type ScrapeMethod = "engine" | "local-browser" | "remote";

export interface ScrapeEntry {
  id: string;          // stable key (url + timestamp)
  url: string;
  status: "pending" | "running" | "success" | "error";
  result: ScrapeResultData | null;
  startedAt: Date;
  completedAt?: Date;
}

export interface ScrapeHistoryEntry {
  url: string;
  success: boolean;
  title: string;
  elapsed_ms: number;
  savedAt: string;
  content?: string;
  status_code?: number;
  method?: ScrapeMethod;
}

// ── URL normalization ──────────────────────────────────────────────────────

export function normalizeUrl(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return "";
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  return `https://${trimmed}`;
}

export function parseUrlList(text: string): string[] {
  return text
    .split(/[\n,]+/)
    .map((u) => normalizeUrl(u))
    .filter(Boolean);
}

// ── History persistence ────────────────────────────────────────────────────

const HISTORY_KEY = "matrx:scrape-history";
const MAX_HISTORY = 200;

export function loadScrapeHistory(): ScrapeHistoryEntry[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    return raw ? (JSON.parse(raw) as ScrapeHistoryEntry[]) : [];
  } catch {
    return [];
  }
}

function persistHistory(entries: ScrapeHistoryEntry[]): void {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(entries));
  } catch {
    // quota exceeded — silently ignore
  }
}

function addToHistory(entry: ScrapeHistoryEntry): void {
  const existing = loadScrapeHistory();
  const merged = [entry, ...existing].slice(0, MAX_HISTORY);
  persistHistory(merged);
}

// ── Logging helpers ────────────────────────────────────────────────────────

function logSuccess(url: string, result: ScrapeResultData, method: ScrapeMethod): void {
  console.info(
    `[scrape] ✓ ${method} | ${url} | HTTP ${result.status_code} | ${result.elapsed_ms}ms | "${result.title}"`,
  );
}

function logFailure(
  url: string,
  method: ScrapeMethod,
  useCache: boolean,
  error: unknown,
  result?: Partial<ScrapeResultData>,
): void {
  const err = error instanceof Error ? error : new Error(String(error));
  console.error("[scrape] FAILED", {
    url,
    method,
    useCache,
    error: err.message,
    stack: err.stack,
    status_code: result?.status_code ?? null,
    content_type: result?.content_type ?? null,
    title: result?.title ?? null,
    response_body_preview: result?.content ? result.content.slice(0, 500) : null,
    error_field: result?.error ?? null,
    timestamp: new Date().toISOString(),
  });
}

// ── Result normalisation ───────────────────────────────────────────────────

function makeFallbackResult(url: string, error: string): ScrapeResultData {
  return {
    url,
    success: false,
    status_code: 0,
    content: "",
    title: "",
    content_type: "",
    response_url: url,
    error,
    elapsed_ms: 0,
    extraction: null,
  };
}

async function executeScrape(
  url: string,
  method: ScrapeMethod,
  useCache: boolean,
  signal?: AbortSignal,
): Promise<ScrapeResultData> {
  const t0 = Date.now();
  console.info(`[use-scrape/executeScrape] START`, { url, method, useCache, timestamp: new Date().toISOString() });

  if (signal?.aborted) {
    console.warn(`[use-scrape/executeScrape] ABORTED before start`, { url, method });
    throw new DOMException("Aborted", "AbortError");
  }

  if (method === "local-browser") {
    console.info(`[use-scrape/executeScrape] → FetchWithBrowser`, { url });
    let toolResult;
    try {
      toolResult = await engine.invokeTool("FetchWithBrowser", {
        url,
        extract_text: true,
      });
    } catch (err) {
      console.error(`[use-scrape/executeScrape] FetchWithBrowser THREW`, {
        url, method, error: (err as Error).message, stack: (err as Error).stack,
        elapsed_ms: Date.now() - t0, timestamp: new Date().toISOString(),
      });
      throw err;
    }
    console.info(`[use-scrape/executeScrape] FetchWithBrowser returned`, {
      url, type: toolResult.type, outputLength: toolResult.output?.length, elapsed_ms: Date.now() - t0,
    });
    const meta = toolResult.metadata ?? {};
    return {
      url,
      success: toolResult.type === "success",
      status_code: (meta.status_code as number) ?? 0,
      content: toolResult.output,
      title: (meta.title as string) ?? "",
      content_type: "text/html",
      response_url: (meta.url as string) ?? url,
      error: toolResult.type === "error" ? toolResult.output : null,
      elapsed_ms: (meta.elapsed_ms as number) ?? 0,
      // FetchWithBrowser returns page text, not a parse — there is nothing
      // structured to show, and empty panels would be a lie.
      extraction: null,
    };
  }

  if (method === "engine") {
    console.info(`[use-scrape/executeScrape] → Scrape tool`, { url, useCache });
    let toolResult;
    try {
      toolResult = await engine.invokeTool("Scrape", {
        urls: [url],
        use_cache: useCache,
        // The parse produces all of this either way; these flags only decide
        // whether it comes back. A UI is exactly the consumer they exist for —
        // see `_scrape_result_to_metadata` in app/tools/tools/network.py.
        get_extraction: true,
        get_links: true,
        get_overview: true,
      });
    } catch (err) {
      console.error(`[use-scrape/executeScrape] Scrape tool THREW`, {
        url, method, useCache,
        error: (err as Error).message, stack: (err as Error).stack,
        elapsed_ms: Date.now() - t0, timestamp: new Date().toISOString(),
      });
      throw err;
    }
    console.info(`[use-scrape/executeScrape] Scrape tool returned`, {
      url, type: toolResult.type, outputLength: toolResult.output?.length,
      metadataKeys: toolResult.metadata ? Object.keys(toolResult.metadata) : [],
      elapsed_ms: Date.now() - t0,
    });
    const meta = toolResult.metadata ?? {};
    const results = meta.results as Record<string, unknown>[] | undefined;
    const r = results?.[0];
    if (results && results.length > 0) {
      console.info(`[use-scrape/executeScrape] first result metadata`, {
        url, status: r?.status, status_code: r?.status_code, has_error: !!r?.error,
      });
    }
    return {
      url,
      success: r ? r.status === "success" : toolResult.type === "success",
      status_code: (r?.status_code as number) ?? (meta.status_code as number) ?? 0,
      content: toolResult.output,
      title: (r?.title as string) ?? "",
      content_type: (r?.content_type as string) ?? "text/html",
      response_url: (r?.url as string) ?? url,
      error: r?.error ? String(r.error) : toolResult.type === "error" ? toolResult.output : null,
      elapsed_ms: (r?.elapsed_ms as number) ?? (meta.elapsed_ms as number) ?? 0,
      // Null for a PDF/image/JSON scrape: those carry `raw_text` and no HTML
      // extraction, so the viewer shows the text view alone.
      extraction: parseScrapeExtraction(r),
    };
  }

  // remote — caller handles SSE streaming; this path is never called directly
  console.error(`[use-scrape/executeScrape] BUG: executeScrape called with method="remote"`, { url });
  throw new Error("Remote streaming must be handled via scrapeMany()");
}

// ── Hook: useScrapeOne ─────────────────────────────────────────────────────
// Lightweight hook for single-URL scraping (QuickScrapeModal, Single tab).

export function useScrapeOne() {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ScrapeResultData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const scrape = useCallback(
    async (url: string, method: ScrapeMethod, useCache: boolean) => {
      const normalized = normalizeUrl(url);
      if (!normalized) {
        console.warn(`[use-scrape/useScrapeOne] scrape() called with empty URL — ignoring`, { url });
        return;
      }

      console.info(`[use-scrape/useScrapeOne] scrape() called`, {
        url: normalized, method, useCache, timestamp: new Date().toISOString(),
      });

      setLoading(true);
      setResult(null);
      setError(null);

      try {
        if (method === "remote") {
          console.info(`[use-scrape/useScrapeOne] using remote scrapeRemotely()`, { url: normalized });
          // Remote single: use non-streaming endpoint
          let resp;
          try {
            resp = await engine.scrapeRemotely([normalized], { use_cache: useCache });
          } catch (remoteErr) {
            console.error(`[use-scrape/useScrapeOne] scrapeRemotely() THREW`, {
              url: normalized, method, useCache,
              error: (remoteErr as Error).message, stack: (remoteErr as Error).stack,
              timestamp: new Date().toISOString(),
            });
            throw remoteErr;
          }
          console.info(`[use-scrape/useScrapeOne] scrapeRemotely() returned`, {
            url: normalized, resultCount: resp.results.length,
            status: resp.status, execution_time_ms: resp.execution_time_ms,
          });
          const r = resp.results[0];
          if (!r) throw new Error("No result returned from remote scraper");

          const mapped: ScrapeResultData = {
            url: r.url,
            success: r.status === "success",
            status_code: r.status_code ?? 0,
            content: r.text_data ?? "",
            title: "",
            content_type: r.content_type ?? "",
            response_url: r.url,
            error: r.error,
            elapsed_ms: resp.execution_time_ms,
            // The remote scraper's REST shape carries text only.
            extraction: null,
          };

          if (mapped.success) {
            logSuccess(normalized, mapped, method);
          } else {
            logFailure(normalized, method, useCache, new Error(mapped.error ?? "Unknown error"), mapped);
          }

          addToHistory({
            url: normalized,
            success: mapped.success,
            title: mapped.title,
            elapsed_ms: mapped.elapsed_ms,
            savedAt: new Date().toISOString(),
            content: mapped.content.slice(0, 2000),
            status_code: mapped.status_code,
            method,
          });

          setResult(mapped);
          return mapped;
        }

        console.info(`[use-scrape/useScrapeOne] calling executeScrape()`, { url: normalized, method, useCache });
        const res = await executeScrape(normalized, method, useCache);
        console.info(`[use-scrape/useScrapeOne] executeScrape() returned`, {
          url: normalized, success: res.success, status_code: res.status_code, elapsed_ms: res.elapsed_ms,
        });

        if (res.success) {
          logSuccess(normalized, res, method);
        } else {
          logFailure(normalized, method, useCache, new Error(res.error ?? "Scrape failed"), res);
        }

        addToHistory({
          url: normalized,
          success: res.success,
          title: res.title,
          elapsed_ms: res.elapsed_ms,
          savedAt: new Date().toISOString(),
          content: res.content.slice(0, 2000),
          status_code: res.status_code,
          method,
        });

        setResult(res);
        return res;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        console.error(`[use-scrape/useScrapeOne] CAUGHT in scrape()`, {
          url: normalized, method, useCache,
          error: message, stack: (err as Error).stack, timestamp: new Date().toISOString(),
        });
        logFailure(normalized, method, useCache, err);
        setError(message);
        return null;
      } finally {
        console.info(`[use-scrape/useScrapeOne] finally — setLoading(false)`, { url: normalized });
        setLoading(false);
      }
    },
    [],
  );

  const reset = useCallback(() => {
    setResult(null);
    setError(null);
  }, []);

  return { scrape, loading, result, error, reset };
}

// ── Hook: useScrapeMany ────────────────────────────────────────────────────
// Full bulk-scraping hook with queue management, progress, and abort support.

export function useScrapeMany() {
  const [entries, setEntries] = useState<ScrapeEntry[]>([]);
  const [running, setRunning] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const addUrls = useCallback((rawText: string) => {
    const urls = parseUrlList(rawText);
    if (urls.length === 0) return;
    setEntries((prev) => {
      const existingUrls = new Set(prev.map((e) => e.url));
      const newEntries: ScrapeEntry[] = urls
        .filter((u) => !existingUrls.has(u))
        .map((url) => ({
          id: `${url}-${Date.now()}`,
          url,
          status: "pending",
          result: null,
          startedAt: new Date(),
        }));
      return [...prev, ...newEntries];
    });
  }, []);

  const removeEntry = useCallback((url: string) => {
    setEntries((prev) => prev.filter((e) => e.url !== url));
  }, []);

  /** Restore a history item as a completed entry (used by the History panel —
   * adding it as "pending" dropped the saved result and showed "No result yet"). */
  const restoreEntry = useCallback((h: ScrapeHistoryEntry) => {
    setEntries((prev) => {
      if (prev.some((e) => e.url === h.url)) return prev;
      const entry: ScrapeEntry = {
        id: `${h.url}-${Date.now()}`,
        url: h.url,
        status: h.success ? "success" : "error",
        result: {
          url: h.url,
          success: h.success,
          status_code: h.status_code ?? (h.success ? 200 : 0),
          content: h.content ?? "",
          title: h.title,
          content_type: "text/plain",
          response_url: h.url,
          error: h.success ? null : "Restored from history (original error not saved)",
          elapsed_ms: h.elapsed_ms,
          // History stores a text preview; the extraction is not persisted.
          extraction: null,
        },
        startedAt: new Date(h.savedAt),
        completedAt: new Date(h.savedAt),
      };
      return [...prev, entry];
    });
  }, []);

  const clearAll = useCallback(() => {
    abortRef.current?.abort();
    setEntries([]);
    setRunning(false);
  }, []);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setRunning(false);
    setEntries((prev) =>
      prev.map((e) =>
        e.status === "running" || e.status === "pending"
          ? {
              ...e,
              status: "error",
              result: makeFallbackResult(e.url, "Stopped by user"),
              completedAt: new Date(),
            }
          : e,
      ),
    );
  }, []);

  const startScrape = useCallback(
    async (method: ScrapeMethod, useCache: boolean) => {
      const toScrape = entries.filter((e) => e.status === "pending");
      if (toScrape.length === 0) {
        console.warn(`[use-scrape/useScrapeMany] startScrape() called with no pending entries`);
        return;
      }

      console.info(`[use-scrape/useScrapeMany] startScrape() starting`, {
        method, useCache, urlCount: toScrape.length,
        urls: toScrape.map((e) => e.url),
        timestamp: new Date().toISOString(),
      });

      setRunning(true);
      const controller = new AbortController();
      abortRef.current = controller;

      const markRunning = (url: string) =>
        setEntries((prev) =>
          prev.map((e) => (e.url === url ? { ...e, status: "running" } : e)),
        );

      const markDone = (url: string, result: ScrapeResultData) =>
        setEntries((prev) =>
          prev.map((e) =>
            e.url === url
              ? {
                  ...e,
                  status: result.success ? "success" : "error",
                  result,
                  completedAt: new Date(),
                }
              : e,
          ),
        );

      if (method === "remote") {
        const urls = toScrape.map((e) => e.url);
        urls.forEach((url) => markRunning(url));

        // True once stop() has run for this batch: it aborts the controller
        // currently in abortRef and then nulls the ref. Late stream events
        // must not overwrite the "Stopped by user" entries or add history.
        const isStopped = () =>
          controller.signal.aborted || abortRef.current === null;

        const streamController = await engine.scrapeRemotelyStream(
          urls,
          { use_cache: useCache },
          (event, data) => {
            if (isStopped()) return;
            const d = data as Record<string, unknown>;
            if (event === "page_result") {
              const url = String(d.url ?? "");
              const result: ScrapeResultData = {
                url,
                success: d.status === "success",
                status_code: (d.status_code as number) ?? 0,
                content: String(d.text_data ?? d.content ?? ""),
                title: String(d.title ?? ""),
                content_type: String(d.content_type ?? ""),
                response_url: url,
                error: d.error ? String(d.error) : null,
                elapsed_ms: (d.elapsed_ms as number) ?? 0,
                extraction: null,
              };

              if (result.success) {
                logSuccess(url, result, method);
              } else {
                logFailure(url, method, useCache, new Error(result.error ?? "Remote error"), result);
              }

              addToHistory({
                url,
                success: result.success,
                title: result.title,
                elapsed_ms: result.elapsed_ms,
                savedAt: new Date().toISOString(),
                content: result.content.slice(0, 2000),
                status_code: result.status_code,
                method,
              });

              markDone(url, result);
            } else if (event === "error") {
              const url = String(d.url ?? "");
              const errMsg = String(d.error ?? d.message ?? "Remote stream error");
              console.error("[scrape] STREAM ERROR", {
                url,
                method,
                event,
                data: d,
                timestamp: new Date().toISOString(),
              });
              markDone(url, makeFallbackResult(url, errMsg));
            }
          },
          () => {
            abortRef.current = null;
            setRunning(false);
          },
          (err) => {
            console.error("[scrape] STREAM FAILED", {
              method,
              urls,
              error: err.message,
              stack: err.stack,
              timestamp: new Date().toISOString(),
            });
            abortRef.current = null;
            setRunning(false);
          },
        );

        abortRef.current = streamController;
        return;
      }

      // Sequential for engine / local-browser
      for (const entry of toScrape) {
        if (controller.signal.aborted) break;
        markRunning(entry.url);

        try {
          const result = await executeScrape(entry.url, method, useCache, controller.signal);

          if (result.success) {
            logSuccess(entry.url, result, method);
          } else {
            logFailure(entry.url, method, useCache, new Error(result.error ?? "Scrape failed"), result);
          }

          addToHistory({
            url: entry.url,
            success: result.success,
            title: result.title,
            elapsed_ms: result.elapsed_ms,
            savedAt: new Date().toISOString(),
            content: result.content.slice(0, 2000),
            status_code: result.status_code,
            method,
          });

          markDone(entry.url, result);
        } catch (err) {
          if ((err as Error).name === "AbortError") break;
          const message = err instanceof Error ? err.message : String(err);
          logFailure(entry.url, method, useCache, err);
          markDone(entry.url, makeFallbackResult(entry.url, message));
        }
      }

      console.info(`[use-scrape/useScrapeMany] startScrape() complete — setRunning(false)`, { method, useCache });
      setRunning(false);
      abortRef.current = null;
    },
    [entries],
  );

  const pendingCount = entries.filter((e) => e.status === "pending").length;
  const doneCount = entries.filter(
    (e) => e.status === "success" || e.status === "error",
  ).length;
  const progress =
    entries.length > 0 ? Math.round((doneCount / entries.length) * 100) : 0;

  return useMemo(
    () => ({
      entries,
      running,
      pendingCount,
      doneCount,
      progress,
      addUrls,
      restoreEntry,
      removeEntry,
      clearAll,
      stop,
      startScrape,
    }),
    [entries, running, pendingCount, doneCount, progress, addUrls, restoreEntry, removeEntry, clearAll, stop, startScrape],
  );
}
