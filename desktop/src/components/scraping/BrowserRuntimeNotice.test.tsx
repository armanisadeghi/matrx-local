/**
 * The missing-browser state must be VISIBLE and FIXABLE on the Scraping page,
 * and completely invisible when the browser is there.
 *
 * The bug these pin: the engine logged a warning, reported READY, and the page
 * still offered a "Browser" method that could only fail.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { UseBrowserRuntimeReturn } from "@/hooks/use-browser-runtime";

const install = vi.fn();
const refresh = vi.fn();
let runtime: UseBrowserRuntimeReturn | null = null;

vi.mock("@/contexts/BrowserRuntimeContext", () => ({
  useOptionalBrowserRuntimeContext: () => runtime,
}));

import { TooltipProvider } from "@/components/ui/tooltip";

import { BrowserRuntimeNotice } from "./BrowserRuntimeNotice";
import { MethodSelector } from "./MethodSelector";

function renderSelector() {
  return renderToStaticMarkup(
    <TooltipProvider>
      <MethodSelector value="engine" onChange={() => {}} />
    </TooltipProvider>,
  );
}

function makeRuntime(
  patch: Partial<UseBrowserRuntimeReturn> = {},
): UseBrowserRuntimeReturn {
  return {
    status: {
      available: false,
      code: "browser_not_installed",
      reason: "No Chromium build was found in this app's browser folder (/tmp/x).",
      browsers_path: "/tmp/x",
      installing: false,
      install_percent: null,
      install_message: null,
      pool_restart_pending: false,
      download_size_hint: "~90 MB",
      action_needed: null,
    },
    loaded: true,
    installing: false,
    percent: 0,
    message: null,
    error: null,
    available: false,
    actions: { install, refresh },
    ...patch,
  };
}

describe("BrowserRuntimeNotice", () => {
  beforeEach(() => {
    runtime = null;
    vi.clearAllMocks();
  });

  it("explains the missing browser in plain language and offers the fix", () => {
    runtime = makeRuntime();
    const html = renderToStaticMarkup(<BrowserRuntimeNotice />);

    expect(html).toContain("built-in browser isn&#x27;t installed yet");
    expect(html).toContain("Install browser (~90 MB)");
    // No jargon in the user-facing copy.
    expect(html.toLowerCase()).not.toContain("playwright");
  });

  it("shows live progress instead of a second install button", () => {
    runtime = makeRuntime({
      installing: true,
      percent: 42,
      message: "Downloading Chromium headless shell",
    });
    const html = renderToStaticMarkup(<BrowserRuntimeNotice />);

    expect(html).toContain("Downloading the built-in browser");
    expect(html).toContain("Downloading Chromium headless shell");
    expect(html).toContain("disabled");
  });

  it("renders nothing at all when the browser is available", () => {
    runtime = makeRuntime({
      available: true,
      status: { ...makeRuntime().status!, available: true, code: "ready" },
    });
    expect(renderToStaticMarkup(<BrowserRuntimeNotice />)).toBe("");
  });

  it("renders nothing before the first probe answers", () => {
    runtime = makeRuntime({ loaded: false });
    expect(renderToStaticMarkup(<BrowserRuntimeNotice />)).toBe("");
  });
});

describe("MethodSelector browser gating", () => {
  beforeEach(() => {
    runtime = null;
    vi.clearAllMocks();
  });

  it("disables the Browser method while it cannot work", () => {
    runtime = makeRuntime();
    const html = renderSelector();

    expect(html).toContain("(not installed)");
    expect(html).toContain('aria-disabled="true"');
  });

  it("leaves every method enabled when the browser is available", () => {
    runtime = makeRuntime({
      available: true,
      status: { ...makeRuntime().status!, available: true, code: "ready" },
    });
    const html = renderSelector();

    expect(html).not.toContain('aria-disabled="true"');
    expect(html).not.toContain("(not installed)");
  });

  it("stays enabled when no provider is mounted (panel windows)", () => {
    runtime = null;
    const html = renderSelector();
    expect(html).not.toContain('aria-disabled="true"');
  });
});
