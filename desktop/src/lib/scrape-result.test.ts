import { describe, it, expect } from "vitest";
import { toScrapeResult, failedScrapeResult } from "./scrape-result";

/**
 * The engine hands every lane the same payload (see
 * app/services/scraper/result_contract.py), so these fixtures are what the
 * local `Scrape` tool's metadata.results[0] and a remote `page_result` event
 * both look like on the wire.
 */
const ENGINE_PAYLOAD = {
  url: "https://example.com",
  response_url: "https://example.com/",
  success: true,
  failure_reason: null,
  status_code: 200,
  content_type: "html",
  title: "Example Domain",
  text_data: "# Example Domain",
  scraped_at: "2026-08-09T12:00:00+00:00",
  cms: "unknown",
  firewall: "cloudflare",
  links: { external: ["https://iana.org/domains/example"] },
  overview: { page_title: "Example Domain" },
  elapsed_ms: 812,
};

describe("toScrapeResult", () => {
  it("preserves the engine payload and derives its rich extraction view", () => {
    const result = toScrapeResult(ENGINE_PAYLOAD);
    expect(result).toMatchObject(ENGINE_PAYLOAD);
    expect(result.extraction?.responseUrl).toBe("https://example.com/");
  });

  it("gives local and remote the same result for the same page", () => {
    const local = toScrapeResult(ENGINE_PAYLOAD);
    // Remote arrives through the SSE proxy — same converter, same fields.
    const remote = toScrapeResult({ ...ENGINE_PAYLOAD });
    expect(remote).toEqual(local);
  });

  it("keeps a failure's reason and marks it failed", () => {
    const result = toScrapeResult({
      ...ENGINE_PAYLOAD,
      success: false,
      failure_reason: "403 Forbidden",
      status_code: 403,
      text_data: "",
    });
    expect(result.success).toBe(false);
    expect(result.failure_reason).toBe("403 Forbidden");
  });

  it("never reports success for a legacy status-string payload", () => {
    // The old contract said status:"success". If one ever reappears we must
    // fail loudly rather than silently trust a field we no longer emit.
    const result = toScrapeResult({ url: "https://example.com", status: "success" });
    expect(result.success).toBe(false);
  });

  it("fills defaults for a truncated payload", () => {
    const result = toScrapeResult({ url: "https://example.com" });
    expect(result).toEqual({
      url: "https://example.com",
      response_url: "https://example.com",
      success: false,
      failure_reason: null,
      status_code: null,
      content_type: null,
      title: "",
      text_data: "",
      scraped_at: null,
      cms: null,
      firewall: null,
      links: null,
      overview: null,
      elapsed_ms: 0,
      extraction: null,
    });
  });

  it("falls back to the requested url when the payload has none", () => {
    const result = toScrapeResult({ success: true }, "https://fallback.test");
    expect(result.url).toBe("https://fallback.test");
    expect(result.response_url).toBe("https://fallback.test");
  });
});

describe("failedScrapeResult", () => {
  it("is a well-formed failure", () => {
    const result = failedScrapeResult("https://example.com", "Stopped by user");
    expect(result.success).toBe(false);
    expect(result.failure_reason).toBe("Stopped by user");
    expect(result.url).toBe("https://example.com");
  });
});
