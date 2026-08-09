/** @vitest-environment jsdom */
/**
 * The point of the one-contract change: the SAME url scraped via "engine" and
 * via "remote" must render the same. This drives the real `useScrapeOne` hook
 * against each transport's real response shape and diffs the rendered DOM.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { engine } from "@/lib/api";
import { useScrapeOne, type ScrapeMethod } from "@/hooks/use-scrape";
import { ScrapeResultViewer } from "./ScrapeResultViewer";

const URL = "https://example.com";

/** One page, as the engine emits it — both lanes send exactly this. */
const PAGE = {
  url: URL,
  response_url: "https://example.com/",
  success: true,
  failure_reason: null,
  status_code: 200,
  content_type: "html",
  title: "Example Domain",
  text_data: "# Example Domain\nThis domain is for use in documentation examples.",
  scraped_at: "2026-08-09T12:00:00+00:00",
  cms: "unknown",
  firewall: "cloudflare",
  links: { external: ["https://iana.org/domains/example"] },
  overview: { page_title: "Example Domain" },
  elapsed_ms: 694,
};

function Harness({ method }: { method: ScrapeMethod }) {
  const { scrape, result } = useScrapeOne();
  return (
    <div>
      <button onClick={() => void scrape(URL, method, false)}>go</button>
      <ScrapeResultViewer result={result} url={URL} />
    </div>
  );
}

async function renderScrape(method: ScrapeMethod): Promise<string> {
  const container = document.createElement("div");
  document.body.append(container);
  const root: Root = createRoot(container);
  await act(async () => {
    root.render(<Harness method={method} />);
  });
  await act(async () => {
    container.querySelector("button")!.dispatchEvent(
      new MouseEvent("click", { bubbles: true }),
    );
  });
  const html = container.innerHTML;
  act(() => root.unmount());
  container.remove();
  return html;
}

describe("engine and remote render identically", () => {
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
      .IS_REACT_ACT_ENVIRONMENT = true;
    // The hook writes scrape history; give it a store that exists in jsdom here.
    localStorage.removeItem?.("matrx:scrape-history");
    // Local lane: the Scrape tool's metadata carries the contract in `results`.
    vi.spyOn(engine, "invokeTool").mockResolvedValue({
      type: "success",
      output: "URL: https://example.com\nStatus: 200\n\n# Example Domain",
      metadata: { ...PAGE, results: [PAGE], total: 1, success_count: 1 },
    } as Awaited<ReturnType<typeof engine.invokeTool>>);
    // Remote lane: the proxy normalises the server's pages to the same shape.
    vi.spyOn(engine, "scrapeRemotely").mockResolvedValue({
      results: [PAGE],
      total: 1,
      success_count: 1,
      elapsed_ms: 694,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("produces the same DOM for the same page", async () => {
    const viaEngine = await renderScrape("engine");
    const viaRemote = await renderScrape("remote");
    expect(viaRemote).toBe(viaEngine);
  });

  it("shows the page's real content, title and status", async () => {
    const html = await renderScrape("remote");
    expect(html).toContain("Example Domain");
    expect(html).toContain("This domain is for use in documentation examples.");
    expect(html).toContain("200");
    expect(html).not.toContain("(no content returned)");
  });

  it("surfaces a failure reason instead of a blank success", async () => {
    vi.spyOn(engine, "scrapeRemotely").mockResolvedValue({
      results: [
        { ...PAGE, success: false, failure_reason: "403 Forbidden", status_code: 403, text_data: "" },
      ],
      total: 1,
      success_count: 0,
      elapsed_ms: 12,
    });
    const html = await renderScrape("remote");
    expect(html).toContain("Scrape failed");
    expect(html).toContain("403 Forbidden");
  });
});
