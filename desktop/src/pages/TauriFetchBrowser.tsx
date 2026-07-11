/**
 * Tauri Invoke-Fetch Browser
 *
 * Uses `window.__TAURI__.core.invoke("proxy_fetch", { url })` to fetch pages
 * from Rust (reqwest).  The response bytes come back as base64, we decode them
 * to a Blob, create an object URL, and set it as the iframe src.
 *
 * Why this is the most powerful approach:
 * - Rust's reqwest has ZERO browser security restrictions
 * - X-Frame-Options and CSP frame-ancestors are irrelevant — they're browser headers
 * - Works even for sites that block proxies by checking the Referer/Origin
 * - Supports cookie sessions via Rust's cookie jar (future enhancement)
 * - The resulting blob URL is served from memory, so same-origin rules apply
 *   inside the iframe (JS may be blocked depending on CSP for blob:)
 *
 * Scraping: The raw bytes returned by proxy_fetch can be parsed directly —
 * no network round-trip, no rate limit, instant.
 */
import { useState, useRef, useCallback, type KeyboardEvent, useEffect } from "react";
import {
    ChevronLeft,
    ChevronRight,
    RefreshCw,
    ExternalLink,
    FileText,
    Copy,
    Check,
    AlertCircle,
    Loader2,
    Zap,
} from "lucide-react";
import { isTauri } from "@/lib/sidecar";

const DEFAULT_URL = "https://www.aimatrx.com";

interface FetchResponse {
    status: number;
    content_type: string;
    body_b64: string;
    final_url: string;
}

interface PageState {
    url: string;         // original requested URL
    finalUrl: string;    // after redirects
    blobUrl: string;     // object URL for the iframe
    contentType: string;
    byteCount: number;
    status: number;
}

function b64ToBlob(b64: string, contentType: string): Blob {
    const byteChars = atob(b64);
    const byteNums = new Array(byteChars.length);
    for (let i = 0; i < byteChars.length; i++) {
        byteNums[i] = byteChars.charCodeAt(i);
    }
    return new Blob([new Uint8Array(byteNums)], { type: contentType });
}

/** Rewrite relative links in HTML so clicks tunnel through our fetch. */
function rewriteHtml(html: string, baseUrl: string): string {
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");
    const base = doc.createElement("base");
    base.href = baseUrl;
    doc.head.prepend(base);
    // Remove CSP metas
    doc.querySelectorAll('meta[http-equiv="Content-Security-Policy"]').forEach((el) => el.remove());
    return "<!DOCTYPE html>" + doc.documentElement.outerHTML;
}

export function TauriFetchBrowser() {
    const [inputUrl, setInputUrl] = useState(DEFAULT_URL);
    const [page, setPage] = useState<PageState | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [iframeKey, setIframeKey] = useState(0);

    const historyRef = useRef<PageState[]>([]);
    const historyIdxRef = useRef(-1);
    const currentBlobUrl = useRef<string | null>(null);

    // Extract panel
    const [showExtract, setShowExtract] = useState(false);
    const [extractedText, setExtractedText] = useState<string | null>(null);
    const [copied, setCopied] = useState(false);

    const isInTauri = isTauri();

    // Blob URL lifetime is owned by HISTORY, not by "current": revoking the
    // current URL on every navigation/back/forward invalidated entries still
    // referenced by historyRef, so traversal loaded dead blob: URLs (blank
    // iframes). URLs are revoked only when their entries leave history
    // (forward-truncation on navigate) or on unmount.
    const revokeAllHistory = () => {
        for (const entry of historyRef.current) {
            if (entry.blobUrl) URL.revokeObjectURL(entry.blobUrl);
        }
        historyRef.current = [];
        currentBlobUrl.current = null;
    };

    const navigate = useCallback(async (target: string, addToHistory = true) => {
        let finalTarget = target.trim();
        if (finalTarget && !/^https?:\/\//i.test(finalTarget)) {
            finalTarget = "https://" + finalTarget;
        }

        setLoading(true);
        setError(null);
        setExtractedText(null);

        try {
            if (!isInTauri) {
                throw new Error("Tauri runtime not available — run this in the desktop app");
            }

            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const tauri = (window as any).__TAURI__;
            const result: FetchResponse = await tauri.core.invoke("proxy_fetch", { url: finalTarget });

            const ct = result.content_type || "text/html";
            let blob: Blob;

            if (ct.includes("html")) {
                // Decode, rewrite links, re-encode
                const raw = atob(result.body_b64);
                const decoder = new TextDecoder("utf-8");
                const rawHtml = decoder.decode(
                    new Uint8Array(raw.split("").map((c) => c.charCodeAt(0)))
                );
                const rewritten = rewriteHtml(rawHtml, result.final_url || finalTarget);
                blob = new Blob([rewritten], { type: "text/html;charset=utf-8" });

                // Extract plain text for the scrape panel
                const tempDoc = new DOMParser().parseFromString(rawHtml, "text/html");
                const text = (tempDoc.body?.innerText || tempDoc.body?.textContent || "").trim();
                setExtractedText(text);
            } else {
                blob = b64ToBlob(result.body_b64, ct);
            }

            // Revoke only the entry NOT in history: a reload (addToHistory
            // false) replaces the current state, whose URL equals the history
            // entry's — handled below by truncation semantics.
            const blobUrl = URL.createObjectURL(blob);
            currentBlobUrl.current = blobUrl;

            const state: PageState = {
                url: finalTarget,
                finalUrl: result.final_url || finalTarget,
                blobUrl,
                contentType: ct,
                byteCount: Math.round((result.body_b64.length * 3) / 4),
                status: result.status,
            };

            if (addToHistory) {
                // Truncate forward entries and revoke THEIR blob URLs — they
                // are no longer reachable.
                const removed = historyRef.current.slice(historyIdxRef.current + 1);
                for (const entry of removed) {
                    if (entry.blobUrl) URL.revokeObjectURL(entry.blobUrl);
                }
                historyRef.current = historyRef.current.slice(0, historyIdxRef.current + 1);
                historyRef.current.push(state);
                historyIdxRef.current = historyRef.current.length - 1;
            } else {
                // Reload: replace the current history entry; revoke the old blob.
                const idx = historyIdxRef.current;
                const old = historyRef.current[idx];
                if (old && old.blobUrl && old.blobUrl !== blobUrl) {
                    URL.revokeObjectURL(old.blobUrl);
                }
                if (idx >= 0 && historyRef.current[idx]) {
                    historyRef.current[idx] = state;
                }
            }

            setPage(state);
            setInputUrl(finalTarget);
            setIframeKey((k) => k + 1);
        } catch (e) {
            setError(String(e));
        } finally {
            setLoading(false);
        }
    }, [isInTauri]);

    // Load default on mount
    useEffect(() => {
        if (isInTauri) navigate(DEFAULT_URL);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Cleanup blob URLs on unmount
    useEffect(() => () => revokeAllHistory(), []);

    const goBack = () => {
        if (historyIdxRef.current <= 0) return;
        historyIdxRef.current -= 1;
        const prev = historyRef.current[historyIdxRef.current];
        if (!prev) return;
        currentBlobUrl.current = prev.blobUrl;
        setPage(prev);
        setInputUrl(prev.url);
        setIframeKey((k) => k + 1);
    };

    const goForward = () => {
        if (historyIdxRef.current >= historyRef.current.length - 1) return;
        historyIdxRef.current += 1;
        const next = historyRef.current[historyIdxRef.current];
        if (!next) return;
        currentBlobUrl.current = next.blobUrl;
        setPage(next);
        setInputUrl(next.url);
        setIframeKey((k) => k + 1);
    };

    const reload = () => {
        if (page) navigate(page.url, false);
    };

    const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
        if (e.key === "Enter") navigate(inputUrl);
    };

    const copyText = () => {
        if (!extractedText) return;
        navigator.clipboard.writeText(extractedText).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        });
    };

    const canBack = historyIdxRef.current > 0;
    const canForward = historyIdxRef.current < historyRef.current.length - 1;

    return (
        <div className="flex h-full flex-col">
            {/* Toolbar */}
            <div className="flex items-center gap-1.5 border-b bg-background/80 backdrop-blur px-3 py-2 shrink-0">
                <button onClick={goBack} disabled={!canBack || loading} title="Back"
                    className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground transition-colors disabled:opacity-30 disabled:cursor-not-allowed">
                    <ChevronLeft className="h-4 w-4" />
                </button>
                <button onClick={goForward} disabled={!canForward || loading} title="Forward"
                    className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground transition-colors disabled:opacity-30 disabled:cursor-not-allowed">
                    <ChevronRight className="h-4 w-4" />
                </button>
                <button onClick={reload} disabled={loading || !page} title="Reload"
                    className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground transition-colors disabled:opacity-50">
                    {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                </button>

                {/* Badge */}
                <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-violet-500/15 text-violet-600 dark:text-violet-400 border border-violet-500/20 shrink-0 flex items-center gap-1">
                    <Zap className="h-2.5 w-2.5" />Tauri Fetch
                </span>

                <input
                    type="text"
                    value={inputUrl}
                    onChange={(e) => setInputUrl(e.target.value)}
                    onKeyDown={handleKeyDown}
                    onFocus={(e) => e.currentTarget.select()}
                    placeholder="Enter URL and press Enter…"
                    className="flex-1 h-7 rounded-md border border-input bg-muted/50 px-3 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />

                {page && (
                    <span className="text-[10px] text-muted-foreground shrink-0">
                        HTTP {page.status} · {(page.byteCount / 1024).toFixed(1)} KB
                    </span>
                )}

                <button
                    onClick={() => setShowExtract(!showExtract)}
                    title="Show extracted text"
                    className={`flex h-7 items-center gap-1.5 px-2 rounded text-xs font-medium transition-colors ${showExtract ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground"
                        }`}
                >
                    <FileText className="h-3.5 w-3.5" />
                    {showExtract ? "Hide" : "Extract"}
                </button>

                {page && (
                    <a href={page.url} target="_blank" rel="noopener noreferrer" title="Open in system browser"
                        className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground transition-colors">
                        <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                )}
            </div>

            {/* Main area */}
            <div className="flex flex-1 overflow-hidden">
                {/* iframe / states */}
                <div className={`flex flex-col ${showExtract ? "flex-1" : "w-full"} overflow-hidden`}>
                    {!isInTauri && (
                        <div className="flex flex-1 items-center justify-center">
                            <div className="text-center space-y-2">
                                <AlertCircle className="h-8 w-8 text-amber-500 mx-auto" />
                                <p className="text-sm font-medium">Desktop only</p>
                                <p className="text-xs text-muted-foreground">This approach requires the Tauri runtime</p>
                            </div>
                        </div>
                    )}
                    {isInTauri && error && (
                        <div className="flex flex-1 items-center justify-center p-6">
                            <div className="text-center space-y-3 max-w-md">
                                <AlertCircle className="h-8 w-8 text-destructive mx-auto" />
                                <p className="text-sm font-medium text-destructive">Fetch failed</p>
                                <p className="text-xs text-muted-foreground break-all">{error}</p>
                                <button onClick={() => navigate(inputUrl)}
                                    className="text-xs px-3 py-1.5 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">
                                    Retry
                                </button>
                            </div>
                        </div>
                    )}
                    {isInTauri && !error && loading && (
                        <div className="flex flex-1 items-center justify-center gap-2 text-muted-foreground">
                            <Loader2 className="h-5 w-5 animate-spin" />
                            <span className="text-sm">Fetching via Rust…</span>
                        </div>
                    )}
                    {isInTauri && !error && !loading && page && (
                        <iframe
                            key={iframeKey}
                            src={page.blobUrl}
                            title="Tauri Fetch Browser"
                            className="flex-1 w-full border-0"
                            sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
                        />
                    )}
                </div>

                {/* Extract panel */}
                {showExtract && (
                    <div className="w-96 flex flex-col border-l bg-muted/30 overflow-hidden shrink-0">
                        <div className="flex items-center justify-between px-3 py-2 border-b shrink-0">
                            <span className="text-xs font-semibold text-foreground">Extracted Text</span>
                            {extractedText && (
                                <button onClick={copyText}
                                    className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
                                    title="Copy text">
                                    {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
                                </button>
                            )}
                        </div>
                        <div className="flex-1 overflow-y-auto p-3 text-xs">
                            {!extractedText && (
                                <p className="text-muted-foreground">Navigate to a page to see extracted text.</p>
                            )}
                            {extractedText && (
                                <pre className="whitespace-pre-wrap font-sans text-foreground/80 leading-relaxed break-words">
                                    {extractedText}
                                </pre>
                            )}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
