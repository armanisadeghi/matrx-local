import { useState, useEffect, useCallback } from "react";
import {
  Globe,
  Play,
  StopCircle,
  Trash2,
  History,
  Plus,
  Construction,
  X,
} from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrapeResultViewer } from "@/components/scraping/ScrapeResultViewer";
import { ScrapeUrlList } from "@/components/scraping/ScrapeUrlList";
import { MethodSelector } from "@/components/scraping/MethodSelector";
import {
  useScrapeOne,
  useScrapeMany,
  loadScrapeHistory,
  normalizeUrl,
  type ScrapeMethod,
  type ScrapeHistoryEntry,
} from "@/hooks/use-scrape";
import type { EngineStatus } from "@/hooks/use-engine";
import { cn } from "@/lib/utils";

interface ScrapingProps {
  engineStatus: EngineStatus;
  engineUrl: string | null;
}

// ── Single Tab ─────────────────────────────────────────────────────────────

function SingleTab({ engineStatus }: { engineStatus: EngineStatus }) {
  const [urlInput, setUrlInput] = useState("");
  const [method, setMethod] = useState<ScrapeMethod>("engine");
  const [useCache, setUseCache] = useState(true);
  const { scrape, loading, result, error, reset } = useScrapeOne();

  const handleScrape = useCallback(async () => {
    const normalized = normalizeUrl(urlInput);
    if (!normalized) return;
    await scrape(normalized, method, useCache);
  }, [urlInput, method, useCache, scrape]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter" && !loading) handleScrape();
    },
    [handleScrape, loading],
  );

  const isConnected = engineStatus === "connected";

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Controls */}
      <div className="shrink-0 space-y-3 border-b p-4">
        <div className="flex items-center gap-2">
          <input
            type="url"
            placeholder="https://example.com"
            value={urlInput}
            onChange={(e) => {
              setUrlInput(e.target.value);
              if (result || error) reset();
            }}
            onKeyDown={handleKeyDown}
            disabled={loading}
            className="min-w-0 flex-1 rounded-md border bg-background px-3 py-2 text-sm font-mono outline-none placeholder:text-muted-foreground/60 focus:ring-1 focus:ring-primary disabled:opacity-50"
            autoFocus
          />
          <Button
            onClick={handleScrape}
            disabled={loading || !urlInput.trim() || !isConnected}
            size="sm"
            className="shrink-0 gap-1.5"
          >
            <Play className="h-3.5 w-3.5" />
            Scrape
          </Button>
        </div>

        <div className="flex items-center gap-3 flex-wrap">
          <MethodSelector value={method} onChange={setMethod} />
          <div className="flex items-center gap-2 ml-auto">
            <Switch
              id="single-cache"
              checked={useCache}
              onCheckedChange={setUseCache}
            />
            <Label htmlFor="single-cache" className="text-xs cursor-pointer">
              Cache
            </Label>
          </div>
        </div>

        {!isConnected && (
          <p className="text-xs text-amber-400">
            Engine not connected — start the Python engine to scrape
          </p>
        )}

        {error && (
          <p className="text-xs text-red-400" role="alert">
            Scrape failed: {error}
          </p>
        )}
      </div>

      {/* Result */}
      <ScrapeResultViewer
        url={urlInput ? normalizeUrl(urlInput) : undefined}
        result={result}
        loading={loading}
        className="flex-1"
      />
    </div>
  );
}

// ── History Drawer ─────────────────────────────────────────────────────────

function HistoryDrawer({
  open,
  onClose,
  onRestore,
}: {
  open: boolean;
  onClose: () => void;
  onRestore: (entry: ScrapeHistoryEntry) => void;
}) {
  const [history, setHistory] = useState<ScrapeHistoryEntry[]>([]);

  useEffect(() => {
    if (open) setHistory(loadScrapeHistory());
  }, [open]);

  const clearHistory = useCallback(() => {
    localStorage.removeItem("matrx:scrape-history");
    setHistory([]);
  }, []);

  if (!open) return null;

  return (
    <div className="absolute inset-y-0 right-0 z-20 flex w-80 flex-col border-l bg-background shadow-xl">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <span className="text-sm font-semibold">
          History ({history.length})
        </span>
        <div className="flex items-center gap-1">
          {history.length > 0 && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              onClick={clearHistory}
              title="Clear all history"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={onClose}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
      <ScrollArea className="flex-1">
        {history.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-12 text-muted-foreground">
            <History className="h-8 w-8 opacity-20" />
            <p className="text-xs">No scrape history</p>
          </div>
        ) : (
          <div className="divide-y divide-border/40">
            {history.map((h, i) => (
              <button
                key={`${h.url}-${h.savedAt}-${i}`}
                onClick={() => {
                  onRestore(h);
                  onClose();
                }}
                className="group flex w-full flex-col gap-0.5 px-4 py-2.5 text-left transition-colors hover:bg-accent/50"
              >
                <div className="flex items-center gap-2">
                  <span
                    className={cn(
                      "h-2 w-2 shrink-0 rounded-full",
                      h.success ? "bg-emerald-500" : "bg-red-400",
                    )}
                  />
                  <span
                    className="flex-1 truncate font-mono text-xs"
                    title={h.url}
                  >
                    {h.url.replace(/^https?:\/\//, "").replace(/\/$/, "")}
                  </span>
                  {h.status_code && h.status_code > 0 && (
                    <Badge
                      variant="secondary"
                      className="text-[10px] font-mono shrink-0"
                    >
                      {h.status_code}
                    </Badge>
                  )}
                </div>
                {h.title && (
                  <p className="truncate pl-4 text-[10px] text-muted-foreground">
                    {h.title}
                  </p>
                )}
                <div className="flex items-center gap-2 pl-4 text-[10px] text-muted-foreground/60">
                  <span>{new Date(h.savedAt).toLocaleDateString()}</span>
                  {h.method && <span>· {h.method}</span>}
                  {h.elapsed_ms > 0 && (
                    <span>
                      ·{" "}
                      {h.elapsed_ms < 1000
                        ? `${h.elapsed_ms}ms`
                        : `${(h.elapsed_ms / 1000).toFixed(1)}s`}
                    </span>
                  )}
                </div>
              </button>
            ))}
          </div>
        )}
      </ScrollArea>
    </div>
  );
}

// ── Bulk Tab ───────────────────────────────────────────────────────────────

function BulkTab({ engineStatus }: { engineStatus: EngineStatus }) {
  const [urlDraft, setUrlDraft] = useState("");
  const [method, setMethod] = useState<ScrapeMethod>("engine");
  const [useCache, setUseCache] = useState(true);
  const [selectedUrl, setSelectedUrl] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);

  const {
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
  } = useScrapeMany();

  const selectedEntry = entries.find((e) => e.url === selectedUrl) ?? null;

  const handleAdd = useCallback(() => {
    addUrls(urlDraft);
    setUrlDraft("");
  }, [urlDraft, addUrls]);

  const handleRemove = useCallback(
    (url: string) => {
      removeEntry(url);
      if (selectedUrl === url) setSelectedUrl(null);
    },
    [removeEntry, selectedUrl],
  );

  const handleClearAll = useCallback(() => {
    clearAll();
    setSelectedUrl(null);
  }, [clearAll]);

  const handleRestore = useCallback(
    (h: ScrapeHistoryEntry) => {
      // Pre-fill from history — addUrls created a "pending" row that dropped
      // the saved result and rendered "No result yet".
      restoreEntry(h);
      setSelectedUrl(h.url);
    },
    [restoreEntry],
  );

  // Auto-select first completed entry
  const handleSelect = useCallback((url: string) => {
    setSelectedUrl((prev) => (prev === url ? null : url));
  }, []);

  // When a new entry becomes success/error, auto-select if nothing selected
  useEffect(() => {
    if (!selectedUrl) {
      const done = entries.find(
        (e) => e.status === "success" || e.status === "error",
      );
      if (done) setSelectedUrl(done.url);
    }
  }, [entries, selectedUrl]);

  const isConnected = engineStatus === "connected";

  // Count new URLs that would be added
  // Use the same normalization as addUrls — the old startsWith("http") check
  // diverged for hosts like httpbin.org (counted as new, then deduped away).
  const newUrlCount = urlDraft
    .split(/[\n,]+/)
    .map((u) => normalizeUrl(u.trim()))
    .filter((u) => u && !entries.find((e) => e.url === u)).length;

  return (
    <div className="relative flex h-full overflow-hidden">
      {/* Left panel */}
      <div className="flex w-72 shrink-0 flex-col border-r">
        {/* URL input */}
        <div className="shrink-0 space-y-2 p-3">
          <textarea
            placeholder={
              "Paste URLs — one per line or comma-separated\n\nexample.com\nhttps://site.org"
            }
            value={urlDraft}
            onChange={(e) => setUrlDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                handleAdd();
              }
            }}
            className="h-28 w-full resize-none rounded-md border bg-background px-3 py-2 font-mono text-xs outline-none focus:ring-1 focus:ring-primary placeholder:text-muted-foreground/50"
          />
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              className="gap-1.5 text-xs"
              onClick={handleAdd}
              disabled={newUrlCount === 0}
            >
              <Plus className="h-3.5 w-3.5" />
              Add{newUrlCount > 0 ? ` ${newUrlCount}` : ""}
            </Button>
            <MethodSelector
              value={method}
              onChange={setMethod}
              className="ml-auto"
            />
          </div>
          <div className="flex items-center gap-2">
            <Switch
              id="bulk-cache"
              checked={useCache}
              onCheckedChange={setUseCache}
            />
            <Label htmlFor="bulk-cache" className="text-xs cursor-pointer">
              Cache
            </Label>
            <div className="ml-auto flex items-center gap-1.5">
              {running ? (
                <Button
                  size="sm"
                  variant="destructive"
                  className="gap-1.5 text-xs h-7"
                  onClick={stop}
                >
                  <StopCircle className="h-3.5 w-3.5" />
                  Stop
                </Button>
              ) : (
                <Button
                  size="sm"
                  className="gap-1.5 text-xs h-7"
                  onClick={() => startScrape(method, useCache)}
                  disabled={pendingCount === 0 || !isConnected}
                >
                  <Play className="h-3.5 w-3.5" />
                  Scrape{pendingCount > 0 ? ` ${pendingCount}` : ""}
                </Button>
              )}
            </div>
          </div>

          {running && <Progress value={progress} className="h-1" />}
        </div>

        <Separator />

        {/* Queue header */}
        <div className="flex shrink-0 items-center justify-between px-3 py-1.5">
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-foreground">
              Queue
            </span>
            <Badge variant="secondary" className="text-[10px] h-4 px-1.5">
              {entries.length}
            </Badge>
            {doneCount > 0 && (
              <span className="text-[10px] text-muted-foreground">
                ({doneCount} done)
              </span>
            )}
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setHistoryOpen(true)}
              className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
              title="View scrape history"
            >
              <History className="h-3 w-3" />
              History
            </button>
            {entries.length > 0 && (
              <>
                <span className="text-muted-foreground/30 text-[10px] mx-0.5">
                  |
                </span>
                <button
                  onClick={handleClearAll}
                  className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-destructive transition-colors"
                >
                  <Trash2 className="h-3 w-3" />
                  Clear
                </button>
              </>
            )}
          </div>
        </div>

        {/* URL list */}
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <ScrapeUrlList
            entries={entries}
            selectedUrl={selectedUrl}
            onSelect={handleSelect}
            onRemove={handleRemove}
          />
        </div>
      </div>

      {/* Right panel — result viewer */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {selectedEntry ? (
          <ScrapeResultViewer
            url={selectedEntry.url}
            result={selectedEntry.result}
            loading={selectedEntry.status === "running"}
            className="flex-1"
          />
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 text-muted-foreground">
            <div className="rounded-full border border-dashed border-muted-foreground/30 p-6">
              <Globe className="h-8 w-8 opacity-20" />
            </div>
            <div className="text-center">
              <p className="text-sm font-medium">
                {entries.length === 0
                  ? "Add URLs to get started"
                  : "Select a URL to view result"}
              </p>
              <p className="mt-1 text-xs opacity-60">
                {entries.length === 0
                  ? "Paste URLs in the left panel, then click Scrape"
                  : "Click any row in the queue to view its content"}
              </p>
            </div>
          </div>
        )}
      </div>

      {/* History drawer */}
      <HistoryDrawer
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onRestore={handleRestore}
      />
    </div>
  );
}

// ── Site Tab ───────────────────────────────────────────────────────────────

function SiteTab() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-6 text-muted-foreground">
      <div className="rounded-full border border-dashed border-muted-foreground/30 p-8">
        <Construction className="h-10 w-10 opacity-30" />
      </div>
      <div className="text-center max-w-sm">
        <p className="text-base font-semibold text-foreground">Coming Soon</p>
        <p className="mt-2 text-sm opacity-70 leading-relaxed">
          Site Scrape will crawl and scrape an entire website — following
          internal links, respecting robots.txt, and extracting structured
          content from every page.
        </p>
        <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
          {[
            "Full site crawl",
            "Sitemap discovery",
            "Link graph",
            "Depth control",
            "Rate limiting",
          ].map((f) => (
            <Badge key={f} variant="outline" className="text-xs opacity-60">
              {f}
            </Badge>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────

export function Scraping({ engineStatus }: ScrapingProps) {
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader
        title="Scraping"
        description="Extract content from websites using multiple strategies"
      />

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden px-0">
        <Tabs
          defaultValue="single"
          className="flex min-h-0 flex-1 flex-col overflow-hidden"
        >
          <div className="shrink-0 min-w-0 overflow-x-auto border-b px-4">
            <TabsList className="h-9 w-max justify-start rounded-none bg-transparent p-0 gap-0">
              <TabsTrigger
                value="single"
                className="shrink-0 rounded-none border-b-2 border-transparent px-4 py-2 text-sm font-medium data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
              >
                Single
              </TabsTrigger>
              <TabsTrigger
                value="bulk"
                className="shrink-0 rounded-none border-b-2 border-transparent px-4 py-2 text-sm font-medium data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
              >
                Bulk Scrape
              </TabsTrigger>
              <TabsTrigger
                value="site"
                className="shrink-0 rounded-none border-b-2 border-transparent px-4 py-2 text-sm font-medium data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
              >
                Site Scrape
                <Badge
                  variant="secondary"
                  className="ml-1.5 text-[10px] py-0 px-1"
                >
                  Soon
                </Badge>
              </TabsTrigger>
            </TabsList>
          </div>

          <TabsContent
            value="single"
            className="mt-0 flex min-h-0 flex-1 flex-col overflow-hidden data-[state=inactive]:hidden"
          >
            <SingleTab engineStatus={engineStatus} />
          </TabsContent>

          <TabsContent
            value="bulk"
            className="mt-0 flex min-h-0 flex-1 flex-col overflow-hidden data-[state=inactive]:hidden"
          >
            <BulkTab engineStatus={engineStatus} />
          </TabsContent>

          <TabsContent
            value="site"
            className="mt-0 flex min-h-0 flex-1 flex-col overflow-hidden data-[state=inactive]:hidden"
          >
            <SiteTab />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
