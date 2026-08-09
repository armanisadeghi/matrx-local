/**
 * THE scrape result type — one shape for every scrape method.
 *
 * Local ("engine"), browser ("local-browser") and "remote" scrapes all run the
 * same `matrx_scraper` engine, and since 2026-08-09 they all arrive here in the
 * same payload: the engine emits it via `app/services/scraper/result_contract.py`
 * and the `/remote-scraper/scrape*` proxy runs the server's pages through that
 * same converter. So this module does not translate three dialects — it reads
 * ONE contract and defends against a missing field.
 *
 * The contract is the package's: `success: boolean` + `failure_reason`. There is
 * no `status` string and no `error` field. If you are tempted to add either,
 * you are re-introducing the split this replaced.
 *
 * Not generated: `src/types/python-generated/api-types.ts` is generated from
 * aidream's OpenAPI schema, and this shape travels as free-form tool metadata
 * (`dict[str, Any]`), which no OpenAPI schema describes. The Python side is the
 * source of truth; `tests/unit/test_scrape_result_contract.py` fails if the
 * field list here and `CLIENT_FIELDS` there drift apart.
 */

export interface ScrapeLinks {
  internal?: string[];
  external?: string[];
  images?: string[];
  documents?: string[];
  audio?: string[];
  videos?: string[];
  archives?: string[];
  others?: string[];
}

/** One scraped page, whichever lane produced it. */
export interface ScrapeResultData {
  url: string;
  response_url: string;
  success: boolean;
  failure_reason: string | null;
  status_code: number | null;
  content_type: string | null;
  title: string;
  text_data: string;
  scraped_at: string | null;
  cms: string | null;
  firewall: string | null;
  links: ScrapeLinks | null;
  overview: Record<string, unknown> | null;
  elapsed_ms: number;
}

/** Canonical result plus the optional rich extraction requested by UI lanes. */
export type ScrapeResultViewData = ScrapeResultData & {
  extraction: ScrapeExtraction | null;
};

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function nullableStr(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

/**
 * Read one engine payload into the canonical result.
 *
 * This is the ONLY place a raw scrape payload becomes a `ScrapeResultData` —
 * the engine tool's `metadata.results[]`, the remote batch response's
 * `results[]`, and each remote `page_result` stream event all pass through
 * here. Add a call site, not a second mapping.
 */
export function toScrapeResult(
  raw: unknown,
  fallbackUrl = "",
): ScrapeResultViewData {
  const r = (raw ?? {}) as Record<string, unknown>;
  const url = str(r.url) || fallbackUrl;
  return {
    url,
    response_url: str(r.response_url) || url,
    success: r.success === true,
    failure_reason: nullableStr(r.failure_reason),
    status_code:
      typeof r.status_code === "number" && Number.isFinite(r.status_code)
        ? r.status_code
        : null,
    content_type: nullableStr(r.content_type),
    title: str(r.title),
    text_data: str(r.text_data),
    scraped_at: nullableStr(r.scraped_at),
    cms: nullableStr(r.cms),
    firewall: nullableStr(r.firewall),
    links: (r.links as ScrapeLinks | undefined) ?? null,
    overview: (r.overview as Record<string, unknown> | undefined) ?? null,
    elapsed_ms: num(r.elapsed_ms),
    extraction: parseScrapeExtraction(r),
  };
}

/** A failure the engine never got to describe (transport error, user abort). */
export function failedScrapeResult(
  url: string,
  failureReason: string,
): ScrapeResultViewData {
  return toScrapeResult({ url, success: false, failure_reason: failureReason }, url);
}
import {
  parseScrapeExtraction,
  type ScrapeExtraction,
} from "@/lib/scrape-extraction";
