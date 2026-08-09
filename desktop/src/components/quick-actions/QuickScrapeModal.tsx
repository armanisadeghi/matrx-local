/**
 * Quick Scrape — the scraper in a dialog, from anywhere in the app.
 *
 * It runs the SAME scrape as the Scraping page (`useScrapeOne`) and renders
 * the SAME result surface (`ScrapeResultViewer`), so an outline, tables,
 * images and page metadata are here too. It only adds what is specific to the
 * quick flow: copy the text, and save it as a note.
 *
 * Until 2026-08-09 this modal held a private copy of the scrape call, its own
 * result normalisation (which JSON.parsed a text blob and mislabelled failures
 * as successes) and its own history writer into the same localStorage key with
 * a different row shape. All three are gone: one scrape path, one history, one
 * viewer.
 */

import { useCallback, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Check, Copy, FileText, Loader2 } from "lucide-react";
import { engine } from "@/lib/api";
import { MethodSelector } from "@/components/scraping/MethodSelector";
import { ScrapeResultViewer } from "@/components/scraping/ScrapeResultViewer";
import { useScrapeOne, normalizeUrl, type ScrapeMethod } from "@/hooks/use-scrape";
import { cn } from "@/lib/utils";

interface QuickScrapeModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  userId?: string | null;
}

export function QuickScrapeModal({
  open,
  onOpenChange,
  userId,
}: QuickScrapeModalProps) {
  const [url, setUrl] = useState("");
  const [method, setMethod] = useState<ScrapeMethod>("engine");
  const [copied, setCopied] = useState(false);
  const [savedNote, setSavedNote] = useState(false);
  const [savingNote, setSavingNote] = useState(false);
  const [noteError, setNoteError] = useState<string | null>(null);

  const { scrape, loading, result, error, reset } = useScrapeOne();

  const handleScrape = useCallback(() => {
    const normalized = normalizeUrl(url);
    if (!normalized) return;
    setCopied(false);
    setSavedNote(false);
    setNoteError(null);
    void scrape(normalized, method, true);
  }, [url, method, scrape]);

  const handleCopy = useCallback(() => {
    if (!result?.text_data) return;
    navigator.clipboard
      .writeText(result.text_data)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      })
      .catch(() => undefined);
  }, [result]);

  const handleSaveToNote = useCallback(async () => {
    if (!result?.text_data) return;
    setNoteError(null);
    if (!engine.engineUrl) {
      setNoteError("Engine not connected — cannot save the note. Try again in a moment.");
      return;
    }
    setSavingNote(true);
    try {
      let label = result.title;
      if (!label) {
        try {
          label = new URL(result.url || normalizeUrl(url)).hostname;
        } catch {
          label = result.url || url;
        }
      }
      await engine.createNote(userId ?? "local", {
        label: `Scraped: ${label}`,
        // The markdown extraction keeps headings, tables and lists intact —
        // a note made from the flat text blob loses all of that structure.
        content: result.extraction?.markdown ?? result.text_data,
        folder_name: "Scraped Pages",
      });
      setSavedNote(true);
      setTimeout(() => setSavedNote(false), 3000);
    } catch (e) {
      setNoteError(
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
          setCopied(false);
          setSavedNote(false);
          setNoteError(null);
        }
        onOpenChange(v);
      }}
    >
      <DialogContent className="flex h-[80vh] max-h-[80vh] max-w-4xl flex-col gap-0 p-0">
        <DialogHeader className="shrink-0 px-6 pb-3 pt-6">
          <DialogTitle>Quick Scrape</DialogTitle>
        </DialogHeader>

        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b px-6 pb-3">
          <Input
            type="url"
            placeholder="https://example.com"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !loading) handleScrape();
            }}
            autoFocus
            className="min-w-0 flex-1"
          />
          <MethodSelector value={method} onChange={setMethod} />
          <Button onClick={handleScrape} disabled={loading || !url.trim()} size="sm">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Go"}
          </Button>
        </div>

        {(error || noteError) && (
          <div className="shrink-0 px-6 py-2">
            <p className="text-xs text-destructive" role="alert">
              {noteError ?? `Scrape failed: ${error}`}
            </p>
          </div>
        )}

        {result && (
          <div className="flex shrink-0 items-center gap-2 border-b px-6 py-2">
            <Button
              variant="outline"
              size="sm"
              className={cn("gap-1.5 text-xs", copied && "text-emerald-500")}
              onClick={handleCopy}
              disabled={!result.text_data}
            >
              {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
              {copied ? "Copied!" : "Copy text"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              className={cn("gap-1.5 text-xs", savedNote && "text-emerald-500")}
              onClick={() => void handleSaveToNote()}
              disabled={savingNote || !result.text_data}
            >
              {savingNote ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : savedNote ? (
                <Check className="h-3 w-3" />
              ) : (
                <FileText className="h-3 w-3" />
              )}
              {savedNote ? "Saved!" : "Save as Note"}
            </Button>
          </div>
        )}

        <ScrapeResultViewer
          {...(url ? { url: normalizeUrl(url) } : {})}
          result={result}
          loading={loading}
          className="min-h-0 flex-1"
        />
      </DialogContent>
    </Dialog>
  );
}
