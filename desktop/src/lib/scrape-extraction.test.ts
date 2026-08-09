import { describe, expect, it } from "vitest";
import {
  hasPageFacts,
  orderedImages,
  parseScrapeExtraction,
} from "./scrape-extraction";

/** A realistic Scrape-tool metadata entry (shapes taken from a live scrape of
 * a Wikipedia article through `_scrape_result_to_metadata`). */
function richMeta(overrides: Record<string, unknown> = {}) {
  return {
    status: "success",
    url: "https://en.wikipedia.org/wiki/Python",
    response_url: "https://en.wikipedia.org/wiki/Python",
    status_code: 200,
    content_type: "html",
    title: "Python",
    cms: "mediawiki",
    firewall: "none",
    scraped_at: "2026-08-09T12:00:00+00:00",
    main_image: "https://upload.wikimedia.org/logo.png",
    document_outline: [
      { type: "header", level: 0, content: "unassociated" },
      { type: "header", level: 1, content: "Python" },
      { type: "header", level: 2, content: "History" },
    ],
    tables: [
      {
        type: "table",
        rows: [
          { Feature: "Typing", Value: "Dynamic" },
          { Feature: "License", Value: "PSF" },
        ],
        rows_total: 2,
      },
    ],
    images: [
      {
        type: "image",
        src: "https://upload.wikimedia.org/guido.jpg",
        alt: "",
        caption: "Guido van Rossum",
        title: "",
        width: "208",
        height: "311",
      },
    ],
    code_blocks: [{ type: "code", content: "print('hi')" }],
    links: {
      internal: ["https://en.wikipedia.org/wiki/C"],
      external: ["https://python.org"],
    },
    link_counts: { internal: 940, external: 12 },
    markdown_renderable: "# Python\n\nA language.",
    page_metadata: {
      canonical_url: "https://en.wikipedia.org/wiki/Python",
      meta_tags: { robots: "noindex", generator: "MediaWiki", description: "A language" },
      structured_data: ["{}"],
    },
    redirect_chain: [{ status: 200, url: "https://en.wikipedia.org/wiki/Python" }],
    hashes: { simhash: "abc123" },
    ...overrides,
  };
}

describe("parseScrapeExtraction", () => {
  it("reads a content-rich HTML page", () => {
    const e = parseScrapeExtraction(richMeta());
    expect(e).not.toBeNull();
    if (!e) return;

    // Level-0 "unassociated" is the parser's pre-heading bucket, never a section.
    expect(e.outline.map((h) => h.text)).toEqual(["Python", "History"]);

    expect(e.tables).toHaveLength(1);
    expect(e.tables[0]?.columns).toEqual(["Feature", "Value"]);
    expect(e.tables[0]?.rows).toEqual([
      ["Typing", "Dynamic"],
      ["License", "PSF"],
    ]);

    expect(e.images[0]?.src).toBe("https://upload.wikimedia.org/guido.jpg");
    expect(e.images[0]?.width).toBe(208); // numeric strings coerce
    expect(e.codeBlocks[0]?.content).toBe("print('hi')");
    expect(e.markdown).toBe("# Python\n\nA language.");
    expect(e.page?.canonicalUrl).toBe("https://en.wikipedia.org/wiki/Python");
    expect(e.page?.robots).toBe("noindex");
    expect(e.page?.description).toBe("A language");
    expect(e.hashes["simhash"]).toBe("abc123");
    expect(hasPageFacts(e)).toBe(true);
  });

  it("keeps the TRUE link totals when the engine capped a bucket", () => {
    const e = parseScrapeExtraction(richMeta());
    const internal = e?.links.find((b) => b.name === "internal");
    expect(internal?.urls).toHaveLength(1); // what was carried
    expect(internal?.total).toBe(940); // what the page actually has
    expect(e?.linkTotal).toBe(952);
  });

  it("orders buckets internal-first regardless of key order", () => {
    const e = parseScrapeExtraction(
      richMeta({
        links: {
          others: ["https://o.example"],
          external: ["https://python.org"],
          internal: ["https://en.wikipedia.org/wiki/C"],
        },
        link_counts: {},
      }),
    );
    expect(e?.links.map((b) => b.name)).toEqual(["internal", "external", "others"]);
  });

  it("returns null for a result with no extraction (PDF, browser fetch, history)", () => {
    expect(parseScrapeExtraction(null)).toBeNull();
    expect(parseScrapeExtraction("some text")).toBeNull();
    expect(
      parseScrapeExtraction({
        status: "success",
        url: "https://example.com/a.pdf",
        status_code: 200,
        content_type: "pdf",
      }),
    ).toBeNull();
  });

  it("treats a single-hop redirect chain as 'no redirect'", () => {
    const e = parseScrapeExtraction({
      status: "success",
      url: "https://example.com",
      redirect_chain: [{ status: 200, url: "https://example.com" }],
    });
    // One hop alone is not worth a panel — nothing else here, so: no extraction.
    expect(e).toBeNull();
  });

  it("survives table cells that arrive nested instead of as strings", () => {
    const e = parseScrapeExtraction(
      richMeta({
        tables: [
          {
            type: "table",
            rows: [
              {
                A: [{ type: "text", content: "nested" }, { content: "value" }],
                B: { content: ["deep", "text"] },
                C: 42,
              },
            ],
          },
        ],
      }),
    );
    expect(e?.tables[0]?.rows[0]).toEqual(["nested value", "deep text", "42"]);
  });

  it("drops images the user could not open anyway", () => {
    const e = parseScrapeExtraction(
      richMeta({
        images: [
          { type: "image", src: "data:image/png;base64,AAAA" },
          { type: "image", src: "/relative/path.png" },
          { type: "image", src: "https://cdn.example/a.png" },
          { type: "image", src: "https://cdn.example/a.png" }, // duplicate
        ],
      }),
    );
    expect(e?.images.map((i) => i.src)).toEqual(["https://cdn.example/a.png"]);
  });

  it("carries the engine's truncation flags through", () => {
    const e = parseScrapeExtraction(
      richMeta({ truncated: { tables: true, links: true, images: false } }),
    );
    expect(e?.truncated).toEqual({ tables: true, links: true });
  });

  it("never throws on a malformed payload", () => {
    expect(() =>
      parseScrapeExtraction({
        document_outline: "not a list",
        tables: [null, 7, { rows: "nope" }],
        images: [{ src: 12 }],
        links: [1, 2, 3],
        page_metadata: [],
        redirect_chain: { url: "x" },
        hashes: "abc",
      }),
    ).not.toThrow();
  });
});

describe("orderedImages", () => {
  it("puts the main image first and does not duplicate it", () => {
    const e = parseScrapeExtraction(
      richMeta({
        main_image: "https://cdn.example/b.png",
        images: [
          { type: "image", src: "https://cdn.example/a.png" },
          { type: "image", src: "https://cdn.example/b.png" },
        ],
      }),
    );
    expect(e).not.toBeNull();
    if (!e) return;
    expect(orderedImages(e).map((i) => i.src)).toEqual([
      "https://cdn.example/b.png",
      "https://cdn.example/a.png",
    ]);
  });

  it("adds the main image when it is not among the body images (og:image)", () => {
    const e = parseScrapeExtraction(
      richMeta({
        main_image: "https://cdn.example/og.png",
        images: [{ type: "image", src: "https://cdn.example/a.png" }],
      }),
    );
    expect(e).not.toBeNull();
    if (!e) return;
    expect(orderedImages(e).map((i) => i.src)).toEqual([
      "https://cdn.example/og.png",
      "https://cdn.example/a.png",
    ]);
  });
});
