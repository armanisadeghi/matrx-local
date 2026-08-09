/**
 * The typed boundary between the Scrape tool's result metadata and the UI.
 *
 * Every scrape already produces a heading outline, tables as rows, images,
 * videos, code blocks, head metadata and a redirect chain — the parse pays for
 * it whether or not anyone looks. `app/tools/tools/network.py`
 * (`_scrape_result_to_metadata`) forwards that under `get_extraction: true`;
 * this module is the ONE place it becomes typed data.
 *
 * Two rules hold here:
 *
 *  1. **Nothing throws.** The payload crosses a tool envelope from Python, and
 *     a page whose table cell came back as a nested object must not blank the
 *     whole Scraping page. Every reader coerces and drops what it cannot read.
 *  2. **Absent means "this page had none", never "empty".** A PDF, an image or
 *     a JSON scrape has no outline and no tables at all; the panels for them
 *     must not render as empty scaffolding. `hasAnything()` and the per-section
 *     lengths are what the UI gates on.
 *
 * Caps and their truth: the engine bounds each list and reports what it
 * dropped (`truncated`, `link_counts`, `rows_total`). Those numbers are
 * carried through here so a surface can say "500 of 12,431", never a silent
 * half-truth.
 */

// ── Shapes (mirror matrx_scraper's extraction-rule output) ───────────────────

export interface ScrapeOutlineHeader {
  /** 1–6 for real headings; 0 is the parser's "unassociated" pre-header slot. */
  level: number;
  text: string;
}

/** A cell value after coercion — the parser emits strings, but nested cells
 * (a table inside a `<td>`) can arrive as arrays or objects. */
export type ScrapeTableCell = string;

export interface ScrapeTable {
  columns: string[];
  rows: ScrapeTableCell[][];
  /** Rows before the engine's cap, when it capped. */
  rowsTotal: number;
}

export interface ScrapeImage {
  src: string;
  alt: string;
  caption: string;
  title: string;
  width: number | null;
  height: number | null;
}

export interface ScrapeMediaItem {
  src: string;
  title: string;
}

export interface ScrapeCodeBlock {
  content: string;
  truncated: boolean;
}

export interface ScrapeLinkBucket {
  name: string;
  urls: string[];
  /** True count before the per-bucket cap. */
  total: number;
}

export interface ScrapeRedirectHop {
  status: number | null;
  url: string;
}

export interface ScrapePageMetadata {
  canonicalUrl: string | null;
  robots: string | null;
  description: string | null;
  ogTitle: string | null;
  ogType: string | null;
  siteName: string | null;
  /** Every remaining `<meta>` tag, for the raw view. */
  metaTags: Record<string, string>;
  structuredDataCount: number;
}

export interface ScrapeExtraction {
  responseUrl: string | null;
  scrapedAt: string | null;
  publishedAt: string | null;
  modifiedAt: string | null;
  cms: string | null;
  firewall: string | null;
  mainImage: string | null;
  outline: ScrapeOutlineHeader[];
  tables: ScrapeTable[];
  images: ScrapeImage[];
  videos: ScrapeMediaItem[];
  audios: ScrapeMediaItem[];
  codeBlocks: ScrapeCodeBlock[];
  links: ScrapeLinkBucket[];
  linkTotal: number;
  markdown: string | null;
  page: ScrapePageMetadata | null;
  redirectChain: ScrapeRedirectHop[];
  hashes: Record<string, string>;
  /** Sections the engine capped, by payload key (`tables`, `links`, …). */
  truncated: Record<string, boolean>;
}

// ── Coercion helpers (nothing here throws) ───────────────────────────────────

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asString(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function asOptionalString(value: unknown): string | null {
  const s = asString(value).trim();
  return s ? s : null;
}

function asOptionalNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const n = Number(value);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

/**
 * Flatten one table cell to text.
 *
 * A `<td>` holding a nested table or a list comes back as an array (or an
 * object with a `content` key) rather than a string. Rendering `[object
 * Object]` in a data table is worse than rendering the text inside it.
 */
function flattenCell(value: unknown, depth = 0): string {
  if (depth > 4) return "";
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) {
    return value
      .map((v) => flattenCell(v, depth + 1))
      .filter(Boolean)
      .join(" ");
  }
  const record = asRecord(value);
  if (record) {
    if ("content" in record) return flattenCell(record["content"], depth + 1);
    if ("text" in record) return flattenCell(record["text"], depth + 1);
    return "";
  }
  return asString(value);
}

// ── Section parsers ──────────────────────────────────────────────────────────

function parseOutline(value: unknown): ScrapeOutlineHeader[] {
  const out: ScrapeOutlineHeader[] = [];
  for (const entry of asArray(value)) {
    const record = asRecord(entry);
    if (!record) continue;
    const text = asString(record["content"]).trim();
    const level = asOptionalNumber(record["level"]) ?? 0;
    // Level 0 is the parser's synthetic "unassociated" bucket for content that
    // appears before the first real heading — never a section a user navigates.
    if (!text || level <= 0 || text === "unassociated") continue;
    out.push({ level, text });
  }
  return out;
}

function parseTables(value: unknown): ScrapeTable[] {
  const out: ScrapeTable[] = [];
  for (const entry of asArray(value)) {
    const record = asRecord(entry);
    if (!record) continue;
    const rawRows = asArray(record["rows"]);
    // Column order comes from the first row's key order — the parser builds
    // every row from the same header list, so this is the table's real order.
    const columns: string[] = [];
    for (const rawRow of rawRows) {
      const row = asRecord(rawRow);
      if (!row) continue;
      for (const key of Object.keys(row)) {
        if (!columns.includes(key)) columns.push(key);
      }
    }
    if (columns.length === 0) continue;
    const rows: ScrapeTableCell[][] = [];
    for (const rawRow of rawRows) {
      const row = asRecord(rawRow);
      if (!row) continue;
      const cells = columns.map((c) => flattenCell(row[c]).trim());
      if (cells.some((c) => c.length > 0)) rows.push(cells);
    }
    if (rows.length === 0) continue;
    out.push({
      columns,
      rows,
      rowsTotal: asOptionalNumber(record["rows_total"]) ?? rows.length,
    });
  }
  return out;
}

function parseImages(value: unknown): ScrapeImage[] {
  const out: ScrapeImage[] = [];
  const seen = new Set<string>();
  for (const entry of asArray(value)) {
    const record = asRecord(entry);
    if (!record) continue;
    const src = asString(record["src"]).trim();
    // Only web-fetchable sources: a `data:` sprite or an unresolved relative
    // path is not something the user can open, and the strip must not show a
    // thumbnail that leads nowhere.
    if (!/^https?:\/\//i.test(src) || seen.has(src)) continue;
    seen.add(src);
    out.push({
      src,
      alt: asString(record["alt"]).trim(),
      caption: asString(record["caption"]).trim(),
      title: asString(record["title"]).trim(),
      width: asOptionalNumber(record["width"]),
      height: asOptionalNumber(record["height"]),
    });
  }
  return out;
}

function parseMediaItems(value: unknown): ScrapeMediaItem[] {
  const out: ScrapeMediaItem[] = [];
  const seen = new Set<string>();
  for (const entry of asArray(value)) {
    const record = asRecord(entry);
    if (!record) continue;
    const src = asString(record["src"] ?? record["url"]).trim();
    if (!/^https?:\/\//i.test(src) || seen.has(src)) continue;
    seen.add(src);
    out.push({
      src,
      title: asString(record["title"] ?? record["caption"]).trim(),
    });
  }
  return out;
}

function parseCodeBlocks(value: unknown): ScrapeCodeBlock[] {
  const out: ScrapeCodeBlock[] = [];
  for (const entry of asArray(value)) {
    const record = asRecord(entry);
    if (!record) continue;
    const content = asString(record["content"]);
    if (!content.trim()) continue;
    out.push({ content, truncated: record["truncated"] === true });
  }
  return out;
}

/** The engine's 8 buckets, in the order a person reads them. */
const LINK_BUCKET_ORDER = [
  "internal",
  "external",
  "documents",
  "images",
  "videos",
  "audio",
  "archives",
  "others",
];

function parseLinks(
  value: unknown,
  counts: unknown,
): { buckets: ScrapeLinkBucket[]; total: number } {
  const record = asRecord(value);
  if (!record) return { buckets: [], total: 0 };
  const countRecord = asRecord(counts) ?? {};
  const names = Object.keys(record).sort((a, b) => {
    const ai = LINK_BUCKET_ORDER.indexOf(a);
    const bi = LINK_BUCKET_ORDER.indexOf(b);
    return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
  });
  const buckets: ScrapeLinkBucket[] = [];
  let total = 0;
  for (const name of names) {
    const urls = asArray(record[name])
      .map((u) => asString(u).trim())
      .filter((u) => /^https?:\/\//i.test(u));
    if (urls.length === 0) continue;
    const declared = asOptionalNumber(countRecord[name]);
    const bucketTotal = declared !== null && declared >= urls.length ? declared : urls.length;
    total += bucketTotal;
    buckets.push({ name, urls, total: bucketTotal });
  }
  return { buckets, total };
}

/** Meta keys promoted to their own labeled row — not repeated in the raw dump. */
const PROMOTED_META_KEYS = new Set([
  "description",
  "og:title",
  "og:type",
  "og:site_name",
  "robots",
]);

function parsePageMetadata(value: unknown): ScrapePageMetadata | null {
  const record = asRecord(value);
  if (!record) return null;
  const tags = asRecord(record["meta_tags"]) ?? {};
  const metaTags: Record<string, string> = {};
  for (const [key, raw] of Object.entries(tags)) {
    if (PROMOTED_META_KEYS.has(key)) continue;
    const text = Array.isArray(raw)
      ? raw.map((v) => asString(v)).filter(Boolean).join(", ")
      : asString(raw);
    if (text.trim()) metaTags[key] = text.trim();
  }
  const readTag = (key: string): string | null => {
    const raw = tags[key];
    const text = Array.isArray(raw)
      ? raw.map((v) => asString(v)).filter(Boolean).join(", ")
      : asString(raw);
    return text.trim() ? text.trim() : null;
  };
  const page: ScrapePageMetadata = {
    canonicalUrl: asOptionalString(record["canonical_url"]),
    robots: readTag("robots"),
    description: readTag("description"),
    ogTitle: readTag("og:title"),
    ogType: readTag("og:type"),
    siteName: readTag("og:site_name"),
    metaTags,
    structuredDataCount: asArray(record["structured_data"]).length,
  };
  const hasAnything =
    page.canonicalUrl ||
    page.robots ||
    page.description ||
    page.ogTitle ||
    page.ogType ||
    page.siteName ||
    page.structuredDataCount > 0 ||
    Object.keys(page.metaTags).length > 0;
  return hasAnything ? page : null;
}

function parseRedirectChain(value: unknown): ScrapeRedirectHop[] {
  const out: ScrapeRedirectHop[] = [];
  for (const entry of asArray(value)) {
    const record = asRecord(entry);
    if (!record) continue;
    const url = asString(record["url"]).trim();
    if (!url) continue;
    out.push({ status: asOptionalNumber(record["status"]), url });
  }
  return out;
}

function parseHashes(value: unknown): Record<string, string> {
  const record = asRecord(value);
  if (!record) return {};
  const out: Record<string, string> = {};
  for (const [key, raw] of Object.entries(record)) {
    const text = asString(raw).trim();
    if (text) out[key] = text;
  }
  return out;
}

// ── Entry point ──────────────────────────────────────────────────────────────

/**
 * Build a `ScrapeExtraction` from one entry of the Scrape tool's
 * `metadata.results` array. Returns null when the payload carries no
 * extraction at all — a `FetchWithBrowser` result, a remote-scraper result, or
 * a history entry restored from localStorage — so the UI shows the text view
 * alone rather than a row of empty panels.
 */
export function parseScrapeExtraction(meta: unknown): ScrapeExtraction | null {
  const record = asRecord(meta);
  if (!record) return null;

  const { buckets, total } = parseLinks(record["links"], record["link_counts"]);
  const truncatedRecord = asRecord(record["truncated"]) ?? {};
  const truncated: Record<string, boolean> = {};
  for (const [key, raw] of Object.entries(truncatedRecord)) {
    if (raw === true) truncated[key] = true;
  }

  const extraction: ScrapeExtraction = {
    responseUrl: asOptionalString(record["response_url"]),
    scrapedAt: asOptionalString(record["scraped_at"]),
    publishedAt: asOptionalString(record["published_at"]),
    modifiedAt: asOptionalString(record["modified_at"]),
    cms: asOptionalString(record["cms"]),
    firewall: asOptionalString(record["firewall"]),
    mainImage: asOptionalString(record["main_image"]),
    outline: parseOutline(record["document_outline"]),
    tables: parseTables(record["tables"]),
    images: parseImages(record["images"]),
    videos: parseMediaItems(record["videos"]),
    audios: parseMediaItems(record["audios"]),
    codeBlocks: parseCodeBlocks(record["code_blocks"]),
    links: buckets,
    linkTotal: total,
    markdown: asOptionalString(record["markdown_renderable"]),
    page: parsePageMetadata(record["page_metadata"]),
    redirectChain: parseRedirectChain(record["redirect_chain"]),
    hashes: parseHashes(record["hashes"]),
    truncated,
  };

  return hasAnything(extraction) ? extraction : null;
}

/** True when at least one panel has something real to show. */
export function hasAnything(e: ScrapeExtraction): boolean {
  return (
    e.outline.length > 0 ||
    e.tables.length > 0 ||
    e.images.length > 0 ||
    e.videos.length > 0 ||
    e.audios.length > 0 ||
    e.codeBlocks.length > 0 ||
    e.links.length > 0 ||
    e.markdown !== null ||
    e.page !== null ||
    // A single hop is just "no redirect happened" — not worth a panel.
    e.redirectChain.length > 1 ||
    e.cms !== null ||
    (e.firewall !== null && e.firewall !== "none") ||
    e.publishedAt !== null ||
    e.modifiedAt !== null
  );
}

/** A stable anchor id for an outline heading (used by the jump navigator). */
export function outlineAnchorId(index: number): string {
  return `scrape-section-${index}`;
}

/** A page's metadata panel has content worth a tab. */
export function hasPageFacts(e: ScrapeExtraction): boolean {
  return (
    e.page !== null ||
    e.cms !== null ||
    (e.firewall !== null && e.firewall !== "none") ||
    e.publishedAt !== null ||
    e.modifiedAt !== null ||
    e.scrapedAt !== null ||
    e.redirectChain.length > 1 ||
    Object.keys(e.hashes).length > 0
  );
}

/** All media the media strip shows, main image first and marked. */
export function orderedImages(e: ScrapeExtraction): ScrapeImage[] {
  if (!e.mainImage) return e.images;
  const index = e.images.findIndex((i) => i.src === e.mainImage);
  if (index <= 0) {
    if (index === 0) return e.images;
    // The main image (usually the og:image) is often NOT in the body images —
    // it still belongs in the strip, first.
    return [
      {
        src: e.mainImage,
        alt: "",
        caption: "",
        title: "",
        width: null,
        height: null,
      },
      ...e.images,
    ];
  }
  const item = e.images[index];
  if (!item) return e.images;
  return [item, ...e.images.slice(0, index), ...e.images.slice(index + 1)];
}
