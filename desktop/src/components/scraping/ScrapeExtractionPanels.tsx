/**
 * The panels that show what a scrape actually extracted.
 *
 * Every scrape already produces an outline, tables, media, code blocks, link
 * buckets and head metadata (`app/tools/tools/network.py`); these are the
 * surfaces for it. Two rules shape all of them:
 *
 *  - **No dead ends.** Every URL is openable in the user's browser; every
 *    image goes through the canonical media stack (MediaThumb → lightbox →
 *    info → "Open original URL"), never a hand-rolled `<img>`; every detected
 *    problem (a firewall, a `noindex`) says what it means, not just its name.
 *  - **No empty scaffolding.** A panel only exists when it has content. The
 *    caller decides which tabs to show from the extraction's own lengths, so a
 *    PDF scrape shows the text view alone.
 */

import { useCallback, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AlertTriangle,
  Check,
  ChevronRight,
  Copy,
  ExternalLink,
  EyeOff,
  Search,
  ShieldAlert,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MediaThumb } from "@/components/media/MediaThumb";
import {
  descriptorFromWebImage,
  descriptorFromWebVideo,
  type MediaDescriptor,
} from "@/components/media/types";
import { openExternal } from "@/lib/open-external";
import { cn } from "@/lib/utils";
import {
  orderedImages,
  type ScrapeExtraction,
  type ScrapeLinkBucket,
} from "@/lib/scrape-extraction";
import { ScrapeDataTable } from "./ScrapeDataTable";

// ── Shared bits ──────────────────────────────────────────────────────────────

export function SectionNote({ children }: { children: React.ReactNode }) {
  return (
    <p className="px-1 text-[10px] leading-relaxed text-muted-foreground">
      {children}
    </p>
  );
}

function CopyText({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(() => {
    navigator.clipboard
      .writeText(value)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
      })
      .catch(() => undefined);
  }, [value]);
  return (
    <Button
      variant="ghost"
      size="icon"
      className={cn("h-6 w-6 shrink-0", copied && "text-emerald-500")}
      onClick={copy}
      title={label}
      aria-label={label}
    >
      {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
    </Button>
  );
}

/** A URL the user can always reach — never a bare, unclickable string. */
function LinkRow({ url, label }: { url: string; label?: string }) {
  return (
    <div className="group flex min-w-0 items-center gap-1 rounded px-1.5 py-1 hover:bg-accent/40">
      <button
        type="button"
        onClick={() => void openExternal(url)}
        className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
        title={`Open ${url}`}
      >
        <ExternalLink className="h-3 w-3 shrink-0 opacity-40 group-hover:opacity-90" />
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-foreground/85 group-hover:text-foreground">
          {label ?? url}
        </span>
      </button>
      <CopyText value={url} label="Copy URL" />
    </div>
  );
}

function MetaRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[8rem_minmax(0,1fr)] gap-3 border-b border-border/40 px-1 py-1.5 last:border-b-0">
      <span className="text-[11px] font-medium text-muted-foreground">{label}</span>
      <div className="min-w-0 text-[11px] break-words">{children}</div>
    </div>
  );
}

// ── Outline ──────────────────────────────────────────────────────────────────

/**
 * Jump-to-section navigator for a long page.
 *
 * Clicking a heading scrolls the markdown/text view to it. The scroll target
 * is resolved by heading TEXT rather than by index: the markdown view renders
 * its own headings, and matching on text keeps the two in step even when the
 * extraction and the rendered markdown disagree about a heading.
 */
export function ScrapeOutlinePanel({
  extraction,
  onJump,
}: {
  extraction: ScrapeExtraction;
  onJump?: (headingText: string) => void;
}) {
  const [filter, setFilter] = useState("");
  const headers = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return extraction.outline;
    return extraction.outline.filter((h) => h.text.toLowerCase().includes(needle));
  }, [extraction.outline, filter]);

  // The parser's levels start wherever the page's do; indent relative to the
  // shallowest heading so a page whose top heading is <h2> isn't all indented.
  const baseLevel = useMemo(
    () => extraction.outline.reduce((min, h) => Math.min(min, h.level), 6),
    [extraction.outline],
  );

  return (
    <div className="flex min-h-0 flex-col gap-2 p-3">
      <div className="flex items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter sections"
            className="w-full rounded-md border bg-background py-1.5 pl-7 pr-2 text-xs outline-none focus:ring-1 focus:ring-primary"
          />
        </div>
        <Badge variant="secondary" className="shrink-0 text-[10px]">
          {extraction.outline.length} headings
        </Badge>
      </div>

      {headers.length === 0 ? (
        <SectionNote>No heading matches “{filter}”.</SectionNote>
      ) : (
        <div className="min-w-0">
          {headers.map((h, i) => (
            <button
              key={`${h.text}-${i}`}
              type="button"
              onClick={() => onJump?.(h.text)}
              disabled={!onJump}
              className={cn(
                "group flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left",
                onJump ? "hover:bg-accent/50" : "cursor-default",
              )}
              style={{ paddingLeft: `${0.375 + (h.level - baseLevel) * 0.85}rem` }}
              title={onJump ? `Jump to “${h.text}”` : h.text}
            >
              <ChevronRight
                className={cn(
                  "h-3 w-3 shrink-0 opacity-0",
                  onJump && "group-hover:opacity-60",
                )}
              />
              <span
                className={cn(
                  "min-w-0 flex-1 truncate",
                  h.level - baseLevel === 0
                    ? "text-xs font-semibold"
                    : "text-[11px] text-foreground/80",
                )}
              >
                {h.text}
              </span>
              <span className="shrink-0 font-mono text-[9px] text-muted-foreground/60">
                h{h.level}
              </span>
            </button>
          ))}
        </div>
      )}

      {extraction.truncated["document_outline"] && (
        <SectionNote>
          The heading list was capped by the engine — this page has more
          sections than are listed here.
        </SectionNote>
      )}
    </div>
  );
}

// ── Tables ───────────────────────────────────────────────────────────────────

export function ScrapeTablesPanel({ extraction }: { extraction: ScrapeExtraction }) {
  return (
    <div className="flex flex-col gap-3 p-3">
      {extraction.tables.map((table, i) => (
        <ScrapeDataTable key={i} table={table} index={i} />
      ))}
      {extraction.truncated["tables"] && (
        <SectionNote>
          Only the first {extraction.tables.length} tables are shown — the
          engine caps how many travel with a result.
        </SectionNote>
      )}
    </div>
  );
}

// ── Media ────────────────────────────────────────────────────────────────────

export function ScrapeMediaPanel({
  extraction,
  pageUrl,
}: {
  extraction: ScrapeExtraction;
  pageUrl: string;
}) {
  const images = useMemo(() => orderedImages(extraction), [extraction]);
  const mainSrc = extraction.mainImage;

  // One viewing set so the lightbox pages through every image on the page.
  const descriptors: MediaDescriptor[] = useMemo(
    () => images.map((image) => descriptorFromWebImage(image, { pageUrl })),
    [images, pageUrl],
  );

  const videoDescriptors: MediaDescriptor[] = useMemo(
    () => extraction.videos.map((v) => descriptorFromWebVideo(v, { pageUrl })),
    [extraction.videos, pageUrl],
  );

  return (
    <div className="flex flex-col gap-4 p-3">
      {descriptors.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-wide">
              Images
            </span>
            <Badge variant="secondary" className="text-[10px]">
              {images.length}
            </Badge>
          </div>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(8rem,1fr))] gap-2">
            {descriptors.map((descriptor, i) => {
              const image = images[i];
              const isMain = !!mainSrc && image?.src === mainSrc;
              return (
                <figure key={descriptor.id} className="min-w-0 space-y-1">
                  <div className="relative overflow-hidden rounded-lg border bg-muted/30">
                    <MediaThumb
                      item={descriptor}
                      variant="card"
                      viewingSet={descriptors}
                      className="aspect-square"
                    >
                      {isMain && (
                        <Badge className="absolute left-1 top-1 text-[9px] shadow">
                          Main
                        </Badge>
                      )}
                    </MediaThumb>
                  </div>
                  {(image?.caption || image?.alt) && (
                    <figcaption className="line-clamp-2 text-[10px] leading-snug text-muted-foreground">
                      {image.caption || image.alt}
                    </figcaption>
                  )}
                </figure>
              );
            })}
          </div>
          {extraction.truncated["images"] && (
            <SectionNote>
              Only the first {images.length} images are shown — the engine caps
              how many travel with a result.
            </SectionNote>
          )}
        </div>
      )}

      {videoDescriptors.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-wide">
              Videos
            </span>
            <Badge variant="secondary" className="text-[10px]">
              {videoDescriptors.length}
            </Badge>
          </div>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] gap-2">
            {videoDescriptors.map((descriptor) => (
              <div
                key={descriptor.id}
                className="overflow-hidden rounded-lg border bg-muted/30"
              >
                <MediaThumb
                  item={descriptor}
                  variant="card"
                  viewingSet={videoDescriptors}
                  className="aspect-video"
                />
              </div>
            ))}
          </div>
        </div>
      )}

      {extraction.audios.length > 0 && (
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-wide">
              Audio
            </span>
            <Badge variant="secondary" className="text-[10px]">
              {extraction.audios.length}
            </Badge>
          </div>
          {extraction.audios.map((audio) => (
            <LinkRow
              key={audio.src}
              url={audio.src}
              {...(audio.title ? { label: audio.title } : {})}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Links ────────────────────────────────────────────────────────────────────

function LinkBucketBlock({ bucket }: { bucket: ScrapeLinkBucket }) {
  const [expanded, setExpanded] = useState(false);
  const PREVIEW = 25;
  const shown = expanded ? bucket.urls : bucket.urls.slice(0, PREVIEW);
  const capped = bucket.total > bucket.urls.length;

  return (
    <div className="min-w-0 rounded-lg border">
      <div className="flex items-center gap-2 border-b bg-muted/30 px-3 py-1.5">
        <span className="text-[11px] font-semibold capitalize">{bucket.name}</span>
        <Badge variant="secondary" className="text-[10px] tabular-nums">
          {bucket.total}
        </Badge>
        <div className="ml-auto flex items-center gap-1">
          <CopyText value={bucket.urls.join("\n")} label="Copy these URLs" />
        </div>
      </div>
      <div className="p-1">
        {shown.map((url) => (
          <LinkRow key={url} url={url} />
        ))}
      </div>
      {(bucket.urls.length > PREVIEW || capped) && (
        <div className="flex items-center gap-2 border-t px-3 py-1.5">
          {bucket.urls.length > PREVIEW && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="text-[10px] font-medium text-primary hover:underline"
            >
              {expanded
                ? "Show fewer"
                : `Show all ${bucket.urls.length} loaded`}
            </button>
          )}
          {capped && (
            <span className="text-[10px] text-muted-foreground">
              {bucket.urls.length} of {bucket.total} carried — the engine caps
              URLs per bucket.
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export function ScrapeLinksPanel({ extraction }: { extraction: ScrapeExtraction }) {
  return (
    <div className="flex flex-col gap-3 p-3">
      {extraction.links.map((bucket) => (
        <LinkBucketBlock key={bucket.name} bucket={bucket} />
      ))}
    </div>
  );
}

// ── Page facts ───────────────────────────────────────────────────────────────

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function ScrapeMetadataPanel({
  extraction,
  requestUrl,
}: {
  extraction: ScrapeExtraction;
  requestUrl: string;
}) {
  const page = extraction.page;
  const redirected = extraction.redirectChain.length > 1;
  const canonicalDiffers =
    !!page?.canonicalUrl &&
    !!extraction.responseUrl &&
    page.canonicalUrl !== extraction.responseUrl;
  const robots = page?.robots?.toLowerCase() ?? "";
  const noindex = robots.includes("noindex");
  const nofollow = robots.includes("nofollow");
  const firewalled = !!extraction.firewall && extraction.firewall !== "none";

  return (
    <div className="flex flex-col gap-3 p-3">
      {/* Findings first: a detected problem states what it MEANS and ships
          with the one thing the user can do about it. */}
      {(firewalled || noindex || canonicalDiffers) && (
        <div className="space-y-2">
          {firewalled && (
            <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 p-2.5">
              <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
              <div className="min-w-0 flex-1 space-y-1">
                <p className="text-[11px] font-semibold text-amber-700 dark:text-amber-400">
                  {extraction.firewall} protects this site
                </p>
                <p className="text-[10px] leading-relaxed text-muted-foreground">
                  The plain fetch can be challenged or served a stub. Re-run
                  with the <strong>Local browser</strong> method — it renders
                  the page in a real browser from this machine.
                </p>
              </div>
            </div>
          )}
          {noindex && (
            <div className="flex items-start gap-2 rounded-lg border border-border bg-muted/30 p-2.5">
              <EyeOff className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <p className="text-[11px] font-semibold">
                  This page asks not to be indexed
                </p>
                <p className="text-[10px] leading-relaxed text-muted-foreground">
                  <code className="font-mono">robots: {page?.robots}</code>
                  {nofollow ? " — links here are marked nofollow too." : ""}
                </p>
              </div>
            </div>
          )}
          {canonicalDiffers && (
            <div className="flex items-start gap-2 rounded-lg border border-border bg-muted/30 p-2.5">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1 space-y-1">
                <p className="text-[11px] font-semibold">
                  The page names a different canonical URL
                </p>
                <p className="text-[10px] text-muted-foreground">
                  Scrape the canonical instead to get the version the site
                  considers authoritative.
                </p>
                {page?.canonicalUrl && <LinkRow url={page.canonicalUrl} />}
              </div>
            </div>
          )}
        </div>
      )}

      <div className="rounded-lg border p-2">
        <MetaRow label="Requested">
          <LinkRow url={requestUrl} />
        </MetaRow>
        {extraction.responseUrl && extraction.responseUrl !== requestUrl && (
          <MetaRow label="Resolved to">
            <LinkRow url={extraction.responseUrl} />
          </MetaRow>
        )}
        {page?.canonicalUrl && (
          <MetaRow label="Canonical">
            <LinkRow url={page.canonicalUrl} />
          </MetaRow>
        )}
        {page?.description && (
          <MetaRow label="Description">{page.description}</MetaRow>
        )}
        {page?.siteName && <MetaRow label="Site name">{page.siteName}</MetaRow>}
        {page?.ogType && <MetaRow label="OG type">{page.ogType}</MetaRow>}
        {page?.robots && (
          <MetaRow label="Robots">
            <code className="font-mono text-[10px]">{page.robots}</code>
          </MetaRow>
        )}
        {extraction.cms && <MetaRow label="CMS">{extraction.cms}</MetaRow>}
        {firewalled && <MetaRow label="Firewall">{extraction.firewall}</MetaRow>}
        {extraction.publishedAt && (
          <MetaRow label="Published">{formatDate(extraction.publishedAt)}</MetaRow>
        )}
        {extraction.modifiedAt && (
          <MetaRow label="Modified">{formatDate(extraction.modifiedAt)}</MetaRow>
        )}
        {extraction.scrapedAt && (
          <MetaRow label="Scraped">{formatDate(extraction.scrapedAt)}</MetaRow>
        )}
        {page && page.structuredDataCount > 0 && (
          <MetaRow label="Structured data">
            {page.structuredDataCount} JSON-LD block
            {page.structuredDataCount === 1 ? "" : "s"}
          </MetaRow>
        )}
      </div>

      {redirected && (
        <div className="min-w-0 rounded-lg border">
          <div className="border-b bg-muted/30 px-3 py-1.5 text-[11px] font-semibold">
            Redirect chain ({extraction.redirectChain.length} hops)
          </div>
          <div className="p-1">
            {extraction.redirectChain.map((hop, i) => (
              <div key={`${hop.url}-${i}`} className="flex items-center gap-1.5">
                <Badge
                  variant="secondary"
                  className="shrink-0 font-mono text-[10px] tabular-nums"
                >
                  {hop.status ?? "—"}
                </Badge>
                <div className="min-w-0 flex-1">
                  <LinkRow url={hop.url} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {page && Object.keys(page.metaTags).length > 0 && (
        <details className="min-w-0 rounded-lg border">
          <summary className="cursor-pointer bg-muted/30 px-3 py-1.5 text-[11px] font-semibold">
            All meta tags ({Object.keys(page.metaTags).length})
          </summary>
          <div className="p-2">
            {Object.entries(page.metaTags).map(([key, value]) => (
              <MetaRow key={key} label={key}>
                <span className="font-mono text-[10px]">{value}</span>
              </MetaRow>
            ))}
          </div>
        </details>
      )}

      {Object.keys(extraction.hashes).length > 0 && (
        <details className="min-w-0 rounded-lg border">
          <summary className="cursor-pointer bg-muted/30 px-3 py-1.5 text-[11px] font-semibold">
            Content hashes
          </summary>
          <div className="p-2">
            {Object.entries(extraction.hashes).map(([key, value]) => (
              <MetaRow key={key} label={key}>
                <span className="break-all font-mono text-[10px]">{value}</span>
              </MetaRow>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

// ── Code blocks ──────────────────────────────────────────────────────────────

export function ScrapeCodePanel({ extraction }: { extraction: ScrapeExtraction }) {
  return (
    <div className="flex flex-col gap-2 p-3">
      {extraction.codeBlocks.map((block, i) => (
        <div key={i} className="min-w-0 rounded-lg border">
          <div className="flex items-center gap-2 border-b bg-muted/30 px-3 py-1">
            <span className="text-[10px] font-semibold">Block {i + 1}</span>
            <div className="ml-auto">
              <CopyText value={block.content} label="Copy this code block" />
            </div>
          </div>
          <pre className="max-h-80 overflow-auto p-3 font-mono text-[11px] leading-relaxed">
            {block.content}
          </pre>
          {block.truncated && (
            <p className="border-t px-3 py-1 text-[10px] text-muted-foreground">
              Truncated by the engine — this block is longer on the page.
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Markdown ─────────────────────────────────────────────────────────────────

/** Slug used to anchor a heading so the outline can scroll to it. */
export function headingSlug(text: string): string {
  return `scrape-h-${text.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")}`;
}

export function ScrapeMarkdownPanel({
  extraction,
  containerRef,
}: {
  extraction: ScrapeExtraction;
  containerRef?: React.RefObject<HTMLDivElement | null>;
}) {
  const markdown = extraction.markdown ?? "";
  const heading = useCallback(
    (level: 1 | 2 | 3 | 4 | 5 | 6) =>
      ({ children }: { children?: React.ReactNode }) => {
        const text =
          typeof children === "string"
            ? children
            : Array.isArray(children)
              ? children.filter((c) => typeof c === "string").join("")
              : "";
        const Tag = `h${level}` as const;
        const size =
          level <= 1
            ? "text-lg"
            : level === 2
              ? "text-base"
              : level === 3
                ? "text-sm"
                : "text-xs";
        return (
          <Tag
            id={text ? headingSlug(text) : undefined}
            className={cn("mt-4 scroll-mt-4 font-semibold first:mt-0", size)}
          >
            {children}
          </Tag>
        );
      },
    [],
  );

  return (
    <div ref={containerRef} className="min-h-0 flex-1 overflow-auto">
      <div className="prose-none max-w-none space-y-2 p-4 text-[13px] leading-relaxed [&_a]:text-primary [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground [&_li]:my-0.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-2 [&_ul]:list-disc [&_ul]:pl-5">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            h1: heading(1),
            h2: heading(2),
            h3: heading(3),
            h4: heading(4),
            h5: heading(5),
            h6: heading(6),
            a: ({ href, children }) => (
              <button
                type="button"
                onClick={() => href && void openExternal(href)}
                className="text-primary underline-offset-2 hover:underline"
                title={href}
              >
                {children}
              </button>
            ),
            img: ({ src, alt }) =>
              typeof src === "string" && /^https?:\/\//i.test(src) ? (
                <span className="my-2 inline-block max-w-xs overflow-hidden rounded-lg border align-top">
                  <MediaThumb
                    item={descriptorFromWebImage({
                      src,
                      ...(alt ? { alt } : {}),
                    })}
                    variant="gallery"
                  />
                </span>
              ) : null,
            table: ({ children }) => (
              <div className="my-3 overflow-x-auto rounded-lg border">
                <table className="w-full border-collapse text-xs">{children}</table>
              </div>
            ),
            th: ({ children }) => (
              <th className="border-b bg-muted/40 px-2.5 py-1.5 text-left font-semibold">
                {children}
              </th>
            ),
            td: ({ children }) => (
              <td className="border-b border-border/40 px-2.5 py-1.5 align-top">
                {children}
              </td>
            ),
            pre: ({ children }) => (
              <pre className="my-2 overflow-x-auto rounded-md bg-muted p-3 text-[11px]">
                {children}
              </pre>
            ),
            code: ({ className, children, ...props }) =>
              className ? (
                <code className={className} {...props}>
                  {children}
                </code>
              ) : (
                <code
                  className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]"
                  {...props}
                >
                  {children}
                </code>
              ),
          }}
        >
          {markdown}
        </ReactMarkdown>
        {extraction.truncated["markdown_renderable"] && (
          <SectionNote>
            The markdown was truncated by the engine — switch to the Text view
            for the full extraction.
          </SectionNote>
        )}
      </div>
    </div>
  );
}
