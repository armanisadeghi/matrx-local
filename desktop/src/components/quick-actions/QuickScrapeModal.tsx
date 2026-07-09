import { useState, useCallback } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Loader2, FileText, Check, Copy } from "lucide-react";
import { engine } from "@/lib/api";
import type { ScrapeResultData } from "@/lib/api";
import { cn } from "@/lib/utils";

type ScrapeMethod = "engine" | "browser";

const HISTORY_KEY = "matrx:scrape-history";
const MAX_HISTORY = 100;

interface ScrapeHistoryEntry {
  url: string;
  success: boolean;
  title: string;
  elapsed_ms: number;
  savedAt: string;
  content?: string;
}

function saveToHistory(entry: ScrapeHistoryEntry) {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    const history: ScrapeHistoryEntry[] = raw ? JSON.parse(raw) : [];
    history.unshift(entry);
    if (history.length > MAX_HISTORY) history.length = MAX_HISTORY;
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
  } catch {
    /* best-effort */
  }
}

interface QuickScrapeModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  userId?: string | null;
}

export function QuickScrapeModal({ open, onOpenChange, userId }: QuickScrapeModalProps) {
  const [url, setUrl] = useState("");
  const [method, setMethod] = useState<ScrapeMethod>("engine");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ScrapeResultData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [savedNote, setSavedNote] = useState(false);
  const [savingNote, setSavingNote] = useState(false);

  const handleScrape = useCallback(async () => {
    const trimmed = url.trim();
    if (!trimmed) return;
    setLoading(true);
    setResult(null);
    setError(null);
    setCopied(false);
    setSavedNote(false);
    try {
      const toolName = method === "browser" ? "FetchWithBrowser" : "Scrape";
      const args =
        method === "browser"
          ? { url: trimmed, extract_text: true }
          : { urls: [trimmed] };
      const raw = await engine.invokeTool(toolName, args);

      // invokeTool returns a ToolResult envelope { type, output, metadata } —
      // the old Array/"content" checks never matched it, so every scrape
      // (including failures) rendered the raw envelope JSON as a "Success".
      if (raw.type === "error") {
        throw new Error(raw.output || "Scrape failed");
      }
      let payload: unknown = raw.output;
      try {
        payload = JSON.parse(raw.output);
      } catch {
        // Plain text/markdown output — use as-is.
      }

      let parsed: ScrapeResultData;
      if (Array.isArray(payload) && payload.length > 0) {
        parsed = payload[0] as ScrapeResultData;
      } else if (
        typeof payload === "object" &&
        payload !== null &&
        "content" in (payload as Record<string, unknown>)
      ) {
        parsed = payload as ScrapeResultData;
      } else {
        parsed = {
          url: trimmed,
          success: true,
          status_code: 200,
          content:
            typeof payload === "string" ? payload : JSON.stringify(payload, null, 2),
          title: "",
          content_type: "text/plain",
          response_url: trimmed,
          error: null,
          elapsed_ms: 0,
        };
      }
      setResult(parsed);

      saveToHistory({
        url: parsed.response_url || trimmed,
        success: parsed.success,
        title: parsed.title || "",
        elapsed_ms: parsed.elapsed_ms,
        savedAt: new Date().toISOString(),
        content: parsed.content?.slice(0, 2000),
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Scrape failed");
    } finally {
      setLoading(false);
    }
  }, [url, method]);

  const handleCopy = useCallback(() => {
    if (!result?.content) return;
    navigator.clipboard.writeText(result.content).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [result]);

  const handleSaveToNote = useCallback(async () => {
    if (!result?.content) return;
    setError(null);
    if (!engine.engineUrl) {
      setError("Engine not connected — cannot save the note. Try again in a moment.");
      return;
    }
    setSavingNote(true);
    try {
      const label = result.title || new URL(result.url || url).hostname;
      await engine.createNote(userId ?? "local", {
        label: `Scraped: ${label}`,
        content: result.content,
        folder_name: "Scraped Pages",
      });
      setSavedNote(true);
      setTimeout(() => setSavedNote(false), 3000);
    } catch (e) {
      setError(
        `Failed to save note: ${e instanceof Error ? e.message : String(e)}`,
      );
    } finally {
      setSavingNote(false);
    }
  }, [result, url, userId]);

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) {
          setUrl("");
          setResult(null);
          setError(null);
          setCopied(false);
          setSavedNote(false);
        }
        onOpenChange(v);
      }}
    >
      <DialogContent className="flex max-h-[70vh] max-w-2xl flex-col gap-0 p-0">
        <DialogHeader className="shrink-0 px-6 pt-6 pb-3">
          <DialogTitle>Quick Scrape</DialogTitle>
        </DialogHeader>
        <div className="flex shrink-0 items-center gap-2 border-b px-6 pb-3">
          <Input
            type="url"
            placeholder="https://example.com"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleScrape()}
            autoFocus
            className="min-w-0 flex-1"
          />
          <select
            value={method}
            onChange={(e) => setMethod(e.target.value as ScrapeMethod)}
            className="flex h-9 rounded-md border border-input bg-transparent px-2 py-2 text-xs shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            <option value="engine">Engine</option>
            <option value="browser">Browser</option>
          </select>
          <Button onClick={handleScrape} disabled={loading || !url.trim()} size="sm">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Go"}
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-6 py-3">
          {error && (
            <p className="text-sm text-destructive">{error}</p>
          )}
          {result && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className={result.success ? "text-emerald-500" : "text-destructive"}>
                  {result.success ? "Success" : "Failed"}
                </span>
                {result.status_code > 0 && (
                  <span>· {result.status_code}</span>
                )}
                {result.elapsed_ms > 0 && (
                  <span>· {result.elapsed_ms}ms</span>
                )}
                {result.title && (
                  <span className="truncate">· {result.title}</span>
                )}
              </div>

              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  className={cn("gap-1.5 text-xs", copied && "text-emerald-500")}
                  onClick={handleCopy}
                  disabled={!result.content}
                >
                  {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                  {copied ? "Copied!" : "Copy"}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className={cn("gap-1.5 text-xs", savedNote && "text-emerald-500")}
                  onClick={handleSaveToNote}
                  disabled={savingNote || !result.content}
                >
                  {savingNote ? <Loader2 className="h-3 w-3 animate-spin" /> : savedNote ? <Check className="h-3 w-3" /> : <FileText className="h-3 w-3" />}
                  {savedNote ? "Saved!" : "Save as Note"}
                </Button>
              </div>

              {result.content && (
                <pre className="max-h-[40vh] whitespace-pre-wrap rounded-lg border bg-muted/30 p-3 text-xs text-foreground overflow-auto">
                  {result.content.slice(0, 8000)}
                  {result.content.length > 8000 && "\n\n… (truncated)"}
                </pre>
              )}
              {result.error && (
                <p className="text-xs text-destructive">{result.error}</p>
              )}
            </div>
          )}
          {!result && !error && !loading && (
            <p className="text-sm text-muted-foreground">
              Enter a URL and click Go to scrape.
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
