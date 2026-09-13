import { describe, expect, it } from "vitest";

import { browserCardPresentation, permissionStatusLabel } from "./Dashboard";

const base = { available: false, install_percent: null };

describe("Browser card — one truth, the engine's browser-runtime status", () => {
  it("says 'Checking…' until the first probe answers, never 'Not installed'", () => {
    const view = browserCardPresentation(null, false, false);
    expect(view.value).toBe("Checking…");
    expect(view.action).toBeNull();
  });

  it("says Ready when the engine says the browser is available", () => {
    const view = browserCardPresentation({ ...base, code: "ready", available: true }, true, false);
    expect(view.value).toBe("Ready");
    expect(view.variant).toBe("success");
    expect(view.action).toBeNull();
  });

  it("does not offer a download when Chromium is installed but did not start", () => {
    // The 2026-09-13 lie: card said "Not Installed" + "Install Chromium" while
    // the banner said "needs a restart" about a browser that was on disk.
    const view = browserCardPresentation({ ...base, code: "browser_launch_failed" }, true, false);
    expect(view.value).toBe("Not running");
    expect(view.action).toBe("repair");
  });

  it("shows a transient starting state with no action while the engine retries", () => {
    const view = browserCardPresentation({ ...base, code: "browser_starting" }, true, false);
    expect(view.value).toBe("Starting…");
    expect(view.action).toBeNull();
  });

  it("offers the install only when the browser is genuinely missing", () => {
    for (const code of ["browser_not_installed", "playwright_package_missing"]) {
      const view = browserCardPresentation({ ...base, code }, true, false);
      expect(view.value).toBe("Not installed");
      expect(view.action).toBe("install");
    }
  });

  it("shows download progress while an install runs, from any surface", () => {
    const view = browserCardPresentation({ ...base, code: "browser_not_installed", install_percent: 42 }, true, true);
    expect(view.value).toBe("Installing…");
    expect(view.description).toContain("42%");
    expect(view.action).toBeNull();
  });
});

describe("permission row wording — every status has an honest word", () => {
  it("never calls an unasked permission 'Not Granted'", () => {
    expect(permissionStatusLabel("not_determined")).toBe("Not asked yet");
    expect(permissionStatusLabel("first_use")).toBe("Asked on first use");
    expect(permissionStatusLabel("unknown")).toBe("Could not read");
    expect(permissionStatusLabel("limited")).toBe("Limited");
  });
});
