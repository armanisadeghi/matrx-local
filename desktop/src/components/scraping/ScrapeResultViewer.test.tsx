/**
 * What the Scraping page actually shows.
 *
 * Server-rendered (the unit suite runs in Node), which is enough for the two
 * claims that matter: a content-rich page surfaces its outline, tables, media,
 * links and metadata as reachable views — and a plain page or a PDF shows the
 * text view alone, with no empty scaffolding.
 *
 * The metadata fixtures below are trimmed from a live
 * `Scrape(get_extraction=true)` call, so the shapes are the engine's, not
 * invented ones.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ScrapeResultViewData as ScrapeResultData } from "@/lib/scrape-result";
import { parseScrapeExtraction } from "@/lib/scrape-extraction";
import { toScrapeResult } from "@/lib/scrape-result";
import {
  capabilitiesOf,
  descriptorFromWebImage,
} from "@/components/media/types";
import { ScrapeResultViewer } from "./ScrapeResultViewer";
import {
  ScrapeLinksPanel,
  ScrapeMetadataPanel,
  ScrapeOutlinePanel,
  ScrapeTablesPanel,
} from "./ScrapeExtractionPanels";

const RICH_META = {
  status: "success",
  url: "https://en.wikipedia.org/wiki/Python_(programming_language)",
  response_url: "https://en.wikipedia.org/wiki/Python_(programming_language)",
  status_code: 200,
  content_type: "html",
  title: "Python (programming language)",
  cms: "mediawiki",
  firewall: "none",
  scraped_at: "2026-08-09T21:06:00+00:00",
  main_image: "https://upload.wikimedia.org/python-logo.png",
  document_outline: [
    { type: "header", level: 0, content: "unassociated" },
    { type: "header", level: 1, content: "Python (programming language)" },
    { type: "header", level: 2, content: "History" },
    { type: "header", level: 3, content: "Indentation" },
  ],
  tables: [
    {
      type: "table",
      rows: [
        { Python: "Paradigm", col2: "Multi-paradigm" },
        { Python: "Designed by", col2: "Guido van Rossum" },
      ],
      rows_total: 2,
    },
  ],
  images: [
    {
      type: "image",
      src: "https://upload.wikimedia.org/guido.jpg",
      alt: "",
      caption: "Guido van Rossum at PyCon US 2024",
      title: "",
      width: "208",
      height: "311",
    },
  ],
  code_blocks: [{ type: "code", content: "print('Hello, World!')" }],
  links: {
    internal: ["https://en.wikipedia.org/wiki/C_(programming_language)"],
    external: ["https://www.python.org"],
  },
  link_counts: { internal: 876, external: 512 },
  markdown_renderable: "# Python (programming language)\n\nA language.",
  page_metadata: {
    canonical_url: "https://en.wikipedia.org/wiki/Python_(programming_language)",
    meta_tags: { robots: "max-image-preview:standard", generator: "MediaWiki" },
    structured_data: ["{}"],
  },
  redirect_chain: [
    { status: 301, url: "http://en.wikipedia.org/wiki/Python" },
    { status: 200, url: "https://en.wikipedia.org/wiki/Python_(programming_language)" },
  ],
  hashes: { simhash: "abc123" },
  truncated: { links: true },
};

/** A PDF scrape: extraction lives in `raw_text`, nothing structured exists. */
const PDF_META = {
  status: "success",
  url: "https://arxiv.org/pdf/1706.03762",
  response_url: "https://arxiv.org/pdf/1706.03762",
  status_code: 200,
  content_type: "pdf",
  scraped_at: "2026-08-09T21:08:03+00:00",
  firewall: "none",
};

function makeResult(meta: unknown, overrides: Partial<ScrapeResultData> = {}): ScrapeResultData {
  const record = meta as Record<string, unknown>;
  return {
    ...toScrapeResult({
      ...record,
      success: true,
      text_data: "Some extracted page text.",
      elapsed_ms: 1200,
    }),
    ...overrides,
  };
}

describe("ScrapeResultViewer", () => {
  it("offers a view for everything a content-rich page extracted", () => {
    const html = renderToStaticMarkup(
      <ScrapeResultViewer result={makeResult(RICH_META)} />,
    );
    for (const label of ["Text", "Markdown", "Tables", "Media", "Links", "Code", "Page info"]) {
      expect(html).toContain(label);
    }
    // The outline rail renders beside the text view — sections are navigable
    // without switching tabs.
    expect(html).toContain("History");
    expect(html).toContain("Indentation");
    // Counts come from the payload's TRUE totals, not the carried slice.
    expect(html).toContain("1388"); // 876 internal + 512 external
  });

  it("shows a PDF the text view alone — no empty panels", () => {
    const html = renderToStaticMarkup(
      <ScrapeResultViewer result={makeResult(PDF_META)} />,
    );
    expect(html).toContain("Some extracted page text.");
    for (const label of ["Tables", "Media", "Links", "Markdown", "Code"]) {
      expect(html).not.toContain(`>${label}`);
    }
  });

  it("shows a plain HTML page with no tables or media only what it has", () => {
    const html = renderToStaticMarkup(
      <ScrapeResultViewer
        result={makeResult({
          ...PDF_META,
          content_type: "html",
          url: "https://example.com",
          response_url: "https://example.com",
          markdown_renderable: "Just a sentence.",
        })}
      />,
    );
    expect(html).toContain("Markdown");
    expect(html).not.toContain(">Tables");
    expect(html).not.toContain(">Media");
  });

  it("names the firewall and the fix when a scrape is blocked", () => {
    const html = renderToStaticMarkup(
      <ScrapeResultViewer
        result={makeResult(
          { ...PDF_META, firewall: "cloudflare", content_type: "html" },
          { success: false, failure_reason: "bad_status", status_code: 403 },
        )}
      />,
    );
    expect(html).toContain("Scrape failed");
    expect(html).toContain("cloudflare");
    expect(html).toContain("Local browser");
  });
});

describe("extraction panels", () => {
  const extraction = parseScrapeExtraction(RICH_META);

  it("renders a scraped table as a real table", () => {
    expect(extraction).not.toBeNull();
    if (!extraction) return;
    const html = renderToStaticMarkup(<ScrapeTablesPanel extraction={extraction} />);
    expect(html).toContain("<table");
    expect(html).toContain("Python");
    expect(html).toContain("Guido van Rossum");
    // Copy-out affordances, so a table is never a dead end.
    expect(html).toContain("TSV");
    expect(html).toContain("Markdown");
  });

  it("states how many links were carried vs how many exist", () => {
    expect(extraction).not.toBeNull();
    if (!extraction) return;
    const html = renderToStaticMarkup(<ScrapeLinksPanel extraction={extraction} />);
    expect(html).toContain("internal");
    expect(html).toContain("876");
    expect(html).toContain("512");
    expect(html).toContain("1 of 876 carried");
  });

  it("turns the outline into a heading list with its own levels", () => {
    expect(extraction).not.toBeNull();
    if (!extraction) return;
    const html = renderToStaticMarkup(<ScrapeOutlinePanel extraction={extraction} />);
    expect(html).toContain("3 headings");
    expect(html).toContain("History");
    expect(html).not.toContain("unassociated");
  });

  it("surfaces page facts and the redirect chain", () => {
    expect(extraction).not.toBeNull();
    if (!extraction) return;
    const html = renderToStaticMarkup(
      <ScrapeMetadataPanel extraction={extraction} requestUrl={RICH_META.url} />,
    );
    expect(html).toContain("Canonical");
    expect(html).toContain("mediawiki");
    expect(html).toContain("Redirect chain (2 hops)");
    expect(html).toContain("301");
  });
});

describe("scraped images use the canonical media contract", () => {
  it("builds a web descriptor that can be opened but not mistaken for ours", () => {
    const descriptor = descriptorFromWebImage(
      {
        src: "https://upload.wikimedia.org/guido.jpg",
        caption: "Guido van Rossum",
        width: 208,
        height: 311,
      },
      { pageUrl: "https://en.wikipedia.org/wiki/Python" },
    );
    expect(descriptor.source).toBe("web");
    expect(descriptor.itemId).toBeNull();
    expect(descriptor.sourceUrl).toBe("https://upload.wikimedia.org/guido.jpg");
    expect(descriptor.sourcePageUrl).toBe("https://en.wikipedia.org/wiki/Python");

    const caps = capabilitiesOf(descriptor);
    // Reachable: full size, metadata, its own URL.
    expect(caps.canOpenSource).toBe(true);
    expect(caps.canCopyPrompt).toBe(true);
    // Never offered, because the bytes are someone else's server's:
    expect(caps.canDownload).toBe(false);
    expect(caps.canCopyImage).toBe(false);
    expect(caps.canUseAsInput).toBe(false);
    expect(caps.canDelete).toBe(false);
    expect(caps.canVault).toBe(false);
    expect(caps.canRemix).toBe(false);
    expect(caps.canShowInFolder).toBe(false);
  });

  it("leaves generated media's capabilities untouched", () => {
    const generated = {
      id: "x",
      kind: "image" as const,
      url: "blob:x",
      itemId: "item-1",
      source: "library" as const,
      modelId: "z-image",
      prompt: "a cat",
      filePath: "/tmp/a.png",
    };
    const caps = capabilitiesOf(generated);
    expect(caps.canDownload).toBe(true);
    expect(caps.canCopyImage).toBe(true);
    expect(caps.canUseAsInput).toBe(true);
    expect(caps.canDelete).toBe(true);
    expect(caps.canShowInFolder).toBe(true);
    expect(caps.canOpenSource).toBe(false);
  });
});
