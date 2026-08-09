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
import { useScrapeOne, type ScrapeMethod } from "@/hooks/use-scrape";
import { cn } from "@/lib/utils";

interface QuickScrapeModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  userId?: string | null;
}

export function QuickScrapeModal({ open, onOpenChange, userId }: QuickScrapeModalProps) {
  const [url, setUrl] = useState("");
  const [method, setMethod] = useState<ScrapeMethod>("engine");
  // The shared hook owns invocation, result reading and history for every
  // method — this modal used to parse tool output itself and only knew two
  // methods, so it disagreed with the Scraping page about the same scrape.
  const { scrape, loading, result, error: scrapeError, reset } = useScrapeOne();
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [savedNote, setSavedNote] = useState(false);
  const [savingNote, setSavingNote] = useState(false);

  const handleScrape = useCallback(async () => {
    const trimmed = url.trim();
    if (!trimmed) return;
    setError(null);
    setCopied(false);
    setSavedNote(false);
    await scrape(trimmed, method, true);
  }, [url, method, scrape]);

  const handleCopy = useCallback(() => {
    if (!result?.text_data) return;
    navigator.clipboard.writeText(result.text_data).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [result]);

  const handleSaveToNote = useCallback(async () => {
    if (!result?.text_data) return;
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
        content: result.text_data,
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
          reset();
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
            <option value="local-browser">Browser</option>
            <option value="remote">Remote</option>
          </select>
          <Button onClick={handleScrape} disabled={loading || !url.trim()} size="sm">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Go"}
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-6 py-3">
          {(error || scrapeError) && (
            <p className="text-sm text-destructive">{error ?? scrapeError}</p>
          )}
          {result && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className={result.success ? "text-emerald-500" : "text-destructive"}>
                  {result.success ? "Success" : "Failed"}
                </span>
                {result.status_code !== null && result.status_code > 0 && (
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
                  disabled={!result.text_data}
                >
                  {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                  {copied ? "Copied!" : "Copy"}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className={cn("gap-1.5 text-xs", savedNote && "text-emerald-500")}
                  onClick={handleSaveToNote}
                  disabled={savingNote || !result.text_data}
                >
                  {savingNote ? <Loader2 className="h-3 w-3 animate-spin" /> : savedNote ? <Check className="h-3 w-3" /> : <FileText className="h-3 w-3" />}
                  {savedNote ? "Saved!" : "Save as Note"}
                </Button>
              </div>

              {result.text_data && (
                <pre className="max-h-[40vh] whitespace-pre-wrap rounded-lg border bg-muted/30 p-3 text-xs text-foreground overflow-auto">
                  {result.text_data.slice(0, 8000)}
                  {result.text_data.length > 8000 && "\n\n… (truncated)"}
                </pre>
              )}
              {result.failure_reason && (
                <p className="text-xs text-destructive">{result.failure_reason}</p>
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
