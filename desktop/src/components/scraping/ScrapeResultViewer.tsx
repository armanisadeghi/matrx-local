/**
 * ScrapeResultViewer — the shared result display (Single tab, Bulk tab, Quick
 * Scrape modal).
 *
 * A scrape produces far more than text: a heading outline, tables as rows,
 * images, code blocks, link buckets and the page's own metadata. This viewer
 * shows what was actually extracted, and shows ONLY what was extracted — a
 * tab exists when it has content, so a PDF, an image or a JSON scrape (which
 * carry text and nothing else) renders exactly one view instead of a row of
 * empty panels.
 *
 * Nothing here hand-rolls media: scraped images go through the canonical
 * MediaThumb/lightbox/info stack (`components/media/FEATURE.md`) so a
 * thumbnail is never a dead end.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  Clock,
  Code2,
  ExternalLink,
  FileText,
  Image as ImageIcon,
  Info,
  Link2,
  List,
  Loader2,
  Table2,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { openExternal } from "@/lib/open-external";
import { cn } from "@/lib/utils";
import type { ScrapeResultData } from "@/lib/api";
import { hasPageFacts, orderedImages } from "@/lib/scrape-extraction";
import {
  headingSlug,
  ScrapeCodePanel,
  ScrapeLinksPanel,
  ScrapeMarkdownPanel,
  ScrapeMediaPanel,
  ScrapeMetadataPanel,
  ScrapeOutlinePanel,
  ScrapeTablesPanel,
} from "./ScrapeExtractionPanels";

interface ScrapeResultViewerProps {
  url?: string;
  result?: ScrapeResultData | null;
  loading?: boolean;
  className?: string;
}

type ViewId =
  | "text"
  | "markdown"
  | "tables"
  | "media"
  | "links"
  | "code"
  | "page";

interface ViewTab {
  id: ViewId;
  label: string;
  icon: React.ReactNode;
  count?: number;
}

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function statusCodeColor(code: number): string {
  if (code >= 200 && code < 300) return "text-emerald-500";
  if (code >= 300 && code < 400) return "text-blue-700 dark:text-blue-400";
  if (code >= 400 && code < 500) return "text-amber-700 dark:text-amber-400";
  if (code >= 500) return "text-red-700 dark:text-red-400";
  return "text-muted-foreground";
}

const TAB_ICON = "h-3.5 w-3.5";

export function ScrapeResultViewer({
  url,
  result,
  loading,
  className,
}: ScrapeResultViewerProps) {
  const [view, setView] = useState<ViewId>("text");
  const markdownRef = useRef<HTMLDivElement | null>(null);
  /**
   * The section the outline last asked for. `seq` is what makes a SECOND click
   * on the same heading (or any click while already on the markdown view)
   * scroll again — keying the scroll off the view alone silently did nothing
   * once the user was already there.
   */
  const [jump, setJump] = useState<{ text: string; seq: number } | null>(null);

  const extraction = result?.extraction ?? null;

  const tabs = useMemo<ViewTab[]>(() => {
    const list: ViewTab[] = [
      { id: "text", label: "Text", icon: <FileText className={TAB_ICON} /> },
    ];
    if (!extraction) return list;
    if (extraction.markdown) {
      list.push({
        id: "markdown",
        label: "Markdown",
        icon: <FileText className={TAB_ICON} />,
      });
    }
    if (extraction.tables.length > 0) {
      list.push({
        id: "tables",
        label: "Tables",
        icon: <Table2 className={TAB_ICON} />,
        count: extraction.tables.length,
      });
    }
    const mediaCount =
      orderedImages(extraction).length +
      extraction.videos.length +
      extraction.audios.length;
    if (mediaCount > 0) {
      list.push({
        id: "media",
        label: "Media",
        icon: <ImageIcon className={TAB_ICON} />,
        count: mediaCount,
      });
    }
    if (extraction.links.length > 0) {
      list.push({
        id: "links",
        label: "Links",
        icon: <Link2 className={TAB_ICON} />,
        count: extraction.linkTotal,
      });
    }
    if (extraction.codeBlocks.length > 0) {
      list.push({
        id: "code",
        label: "Code",
        icon: <Code2 className={TAB_ICON} />,
        count: extraction.codeBlocks.length,
      });
    }
    if (hasPageFacts(extraction)) {
      list.push({ id: "page", label: "Page info", icon: <Info className={TAB_ICON} /> });
    }
    return list;
  }, [extraction]);

  const availableViews = useMemo(() => tabs.map((t) => t.id), [tabs]);

  // A new result (or a switch to a plainer one) must never leave the viewer on
  // a tab that no longer exists — that rendered a blank pane.
  useEffect(() => {
    if (!availableViews.includes(view)) setView("text");
  }, [availableViews, view]);

  const outlineVisible = !!extraction && extraction.outline.length > 0;
  const hasMarkdown = !!extraction?.markdown;

  const jumpToSection = useCallback(
    (headingText: string) => {
      if (!hasMarkdown) return;
      setJump((prev) => ({ text: headingText, seq: (prev?.seq ?? 0) + 1 }));
      setView("markdown");
    },
    [hasMarkdown],
  );

  // Scroll once the markdown view has mounted the heading we want. It is
  // mounted in the same commit, but the heading is only measurable after a
  // paint — and a long page can take a couple of frames to lay out, so this
  // retries briefly rather than firing once into an unlaid-out tree.
  useEffect(() => {
    if (view !== "markdown" || !jump) return;
    const selector = `#${CSS.escape(headingSlug(jump.text))}`;
    let frame = 0;
    let handle = 0;
    const tick = () => {
      const node = markdownRef.current?.querySelector(selector);
      if (node) {
        node.scrollIntoView({ block: "start", behavior: "smooth" });
        return;
      }
      if (frame++ < 20) handle = window.requestAnimationFrame(tick);
    };
    handle = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(handle);
  }, [view, jump]);

  if (loading) {
    return (
      <div
        className={cn(
          "flex flex-1 items-center justify-center gap-3 text-muted-foreground",
          className,
        )}
      >
        <Loader2 className="h-5 w-5 animate-spin text-primary" />
        <span className="text-sm">Scraping {url ?? "…"}…</span>
      </div>
    );
  }

  if (!result) {
    return (
      <div
        className={cn(
          "flex flex-1 flex-col items-center justify-center gap-3 text-muted-foreground",
          className,
        )}
      >
        <div className="rounded-full border border-dashed border-muted-foreground/30 p-6">
          <ExternalLink className="h-8 w-8 opacity-20" />
        </div>
        <div className="text-center">
          <p className="text-sm font-medium">No result yet</p>
          <p className="mt-1 text-xs opacity-60">
            Enter a URL and scrape to see content here
          </p>
        </div>
      </div>
    );
  }

  const displayUrl = result.response_url || result.url || url || "";
  const failed = !!result.error && !result.success;

  return (
    <div className={cn("flex flex-col overflow-hidden", className)}>
      {/* Header bar */}
      <div className="flex shrink-0 items-center gap-2 border-b bg-muted/30 px-4 py-2">
        {result.success ? (
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
        ) : (
          <XCircle className="h-4 w-4 shrink-0 text-red-700 dark:text-red-400" />
        )}

        <span
          className="min-w-0 flex-1 truncate font-mono text-xs text-foreground/80"
          title={displayUrl}
        >
          {displayUrl}
        </span>

        <div className="flex shrink-0 items-center gap-2">
          {result.status_code > 0 && (
            <Badge
              variant="secondary"
              className={cn(
                "text-[10px] font-mono tabular-nums",
                statusCodeColor(result.status_code),
              )}
            >
              {result.status_code}
            </Badge>
          )}
          {result.content_type && (
            <Badge variant="outline" className="hidden text-[10px] font-mono sm:inline-flex">
              {result.content_type.split(";")[0]}
            </Badge>
          )}
          {result.elapsed_ms > 0 && (
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
              <Clock className="h-3 w-3" />
              {formatElapsed(result.elapsed_ms)}
            </span>
          )}
          {result.title && (
            <span
              className="hidden max-w-[160px] truncate text-[10px] text-muted-foreground md:block"
              title={result.title}
            >
              {result.title}
            </span>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0"
            onClick={() => void openExternal(displayUrl || result.url)}
            title="Open in browser"
          >
            <ExternalLink className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* View switcher — only rendered when this result has more than text. */}
      {tabs.length > 1 && (
        <div className="flex shrink-0 items-center gap-0.5 overflow-x-auto border-b px-2 py-1">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setView(tab.id)}
              className={cn(
                "flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                view === tab.id
                  ? "bg-accent text-foreground"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
              )}
            >
              {tab.icon}
              {tab.label}
              {tab.count !== undefined && (
                <span className="font-mono text-[10px] tabular-nums opacity-60">
                  {tab.count}
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      {/* Body: optional outline rail + the active view */}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {outlineVisible && (view === "text" || view === "markdown") && (
          <div className="hidden w-60 shrink-0 overflow-auto border-r lg:block">
            <ScrapeOutlinePanel
              extraction={extraction}
              {...(hasMarkdown ? { onJump: jumpToSection } : {})}
            />
          </div>
        )}

        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {failed ? (
            <div className="min-h-0 flex-1 overflow-auto p-4">
              <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-4">
                <p className="mb-2 text-sm font-semibold text-red-700 dark:text-red-400">
                  Scrape failed
                </p>
                <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-red-700 dark:text-red-300">
                  {result.error}
                </pre>
                {extraction?.firewall && extraction.firewall !== "none" && (
                  <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
                    <strong className="text-foreground">
                      {extraction.firewall}
                    </strong>{" "}
                    is protecting this site. Re-run with the{" "}
                    <strong className="text-foreground">Local browser</strong>{" "}
                    method — it renders the page in a real browser from this
                    machine.
                  </p>
                )}
              </div>

              {/* Still show any partial content */}
              {result.content && (
                <div className="mt-4">
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Partial content
                  </p>
                  <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-foreground">
                    {result.content}
                  </pre>
                </div>
              )}
            </div>
          ) : view === "markdown" && extraction ? (
            <ScrapeMarkdownPanel extraction={extraction} containerRef={markdownRef} />
          ) : view === "tables" && extraction ? (
            <div className="min-h-0 flex-1 overflow-auto">
              <ScrapeTablesPanel extraction={extraction} />
            </div>
          ) : view === "media" && extraction ? (
            <div className="min-h-0 flex-1 overflow-auto">
              <ScrapeMediaPanel
                extraction={extraction}
                pageUrl={displayUrl || result.url}
              />
            </div>
          ) : view === "links" && extraction ? (
            <div className="min-h-0 flex-1 overflow-auto">
              <ScrapeLinksPanel extraction={extraction} />
            </div>
          ) : view === "code" && extraction ? (
            <div className="min-h-0 flex-1 overflow-auto">
              <ScrapeCodePanel extraction={extraction} />
            </div>
          ) : view === "page" && extraction ? (
            <div className="min-h-0 flex-1 overflow-auto">
              <ScrapeMetadataPanel
                extraction={extraction}
                requestUrl={result.url || url || ""}
              />
            </div>
          ) : (
            <div className="min-h-0 flex-1 overflow-auto">
              <pre className="whitespace-pre-wrap break-words p-4 font-mono text-xs leading-relaxed text-foreground">
                {result.content || "(no content returned)"}
              </pre>
            </div>
          )}

          {/* The outline rail is hidden below lg — the sections must still be
              reachable there, so it collapses into a details block. */}
          {outlineVisible && (view === "text" || view === "markdown") && (
            <details className="shrink-0 border-t lg:hidden">
              <summary className="cursor-pointer px-4 py-2 text-xs font-medium text-muted-foreground">
                <List className="mr-1.5 inline h-3.5 w-3.5" />
                {extraction.outline.length} sections
              </summary>
              <div className="max-h-64 overflow-auto border-t">
                <ScrapeOutlinePanel
                  extraction={extraction}
                  {...(hasMarkdown ? { onJump: jumpToSection } : {})}
                />
              </div>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}
