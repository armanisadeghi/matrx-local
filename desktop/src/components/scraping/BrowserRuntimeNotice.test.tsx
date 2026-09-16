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

import { TooltipProvider } from "@ai-matrx/design-system";

import { BrowserRuntimeNotice } from "./BrowserRuntimeNotice";
import { MethodSelector } from "./MethodSelector";
import { captureBrowserRuntimeFailureTransition } from "@/hooks/use-browser-runtime";

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

  it("says the browser is starting instead of claiming it is missing", () => {
    // The engine lost one pool launch to a starved event loop and has already
    // scheduled the retry. Chromium is on disk, so the install wording would be
    // false and an Install button would be a dead control.
    runtime = makeRuntime({
      status: {
        ...makeRuntime().status!,
        code: "browser_starting",
        reason: "The built-in browser is still starting",
        pool_restart_pending: true,
      },
    });
    const html = renderToStaticMarkup(<BrowserRuntimeNotice />);

    expect(html).toContain("still starting");
    expect(html).not.toContain("isn't installed yet");
    expect(html).not.toContain("<button");
  });

  it("offers repair and restart guidance for a terminal launch failure", () => {
    runtime = makeRuntime({
      status: { ...makeRuntime().status!, code: "browser_launch_failed" },
    });
    const html = renderToStaticMarkup(<BrowserRuntimeNotice />);

    expect(html).toContain("needs repair");
    expect(html).toContain("restart the app");
    expect(html).toContain("Repair browser");
  });

  it("offers an update for a browser build mismatch", () => {
    runtime = makeRuntime({
      status: {
        ...makeRuntime().status!,
        code: "browser_build_mismatch",
        action_needed: {
          action: { label: "Update browser" },
        } as NonNullable<UseBrowserRuntimeReturn["status"]>["action_needed"],
      },
    });
    const html = renderToStaticMarkup(<BrowserRuntimeNotice />);

    expect(html).toContain("needs an update");
    expect(html).toContain("Update browser");
  });
});

describe("browser runtime durable capture", () => {
  it("captures once per terminal transition and retries after identity refusal", () => {
    const capture = vi.fn().mockReturnValueOnce(false).mockReturnValue(true);

    let marker = captureBrowserRuntimeFailureTransition(
      null,
      "browser_launch_failed",
      capture,
    );
    expect(marker).toBeNull();
    marker = captureBrowserRuntimeFailureTransition(
      marker,
      "browser_launch_failed",
      capture,
    );
    expect(marker).toBe("browser_launch_failed");
    marker = captureBrowserRuntimeFailureTransition(
      marker,
      "browser_launch_failed",
      capture,
    );
    expect(capture).toHaveBeenCalledTimes(2);

    marker = captureBrowserRuntimeFailureTransition(marker, "ready", capture);
    expect(marker).toBeNull();
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

  it("says (starting), never (not installed), while the pool retry is pending", () => {
    runtime = makeRuntime({
      status: { ...makeRuntime().status!, code: "browser_starting" },
    });
    const html = renderSelector();

    expect(html).toContain("(starting)");
    expect(html).not.toContain("(not installed)");
  });

  it("names an update rather than an install for a build mismatch", () => {
    runtime = makeRuntime({
      status: { ...makeRuntime().status!, code: "browser_build_mismatch" },
    });
    const html = renderSelector();
    expect(html).toContain("(update needed)");
    expect(html).not.toContain("(not installed)");
  });

  it("stays enabled when no provider is mounted (panel windows)", () => {
    runtime = null;
    const html = renderSelector();
    expect(html).not.toContain('aria-disabled="true"');
  });
});
