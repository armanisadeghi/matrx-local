/**
 * Rendering guard for Settings → About.
 *
 * The 2026-09-17 screen showed one unlabelled version and an "up to date"
 * sentence while a newer build sat on disk. These assertions are about what
 * reaches the glass: three labelled builds, and — when running ≠ installed — a
 * loud restart state with a one-click remedy.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { deriveVersionState } from "@/lib/version-facts";
import { VersionFacts } from "./VersionFacts";

function render(
  input: Parameters<typeof deriveVersionState>[0],
  restarting = false,
) {
  return renderToStaticMarkup(
    <VersionFacts
      versions={deriveVersionState(input)}
      restarting={restarting}
      onRestart={() => {}}
    />,
  );
}

const INCIDENT = {
  runningDesktop: "1.4.145",
  runningEngine: "1.4.145",
  installedOnDisk: "1.4.147",
  updaterStatus: "up_to_date" as const,
};

describe("About with a build waiting on disk", () => {
  it("announces the restart with the remedy", () => {
    const html = render(INCIDENT);
    expect(html).toContain("Update installed — restart AI Matrx to finish");
    expect(html).toContain("Restart now");
    expect(html).toContain('data-testid="restart-required-banner"');
    expect(html).toContain("1.4.147 is installed on disk");
    expect(html).toContain("keeps running 1.4.145");
  });

  it("shows both numbers, each labelled", () => {
    const html = render(INCIDENT);
    expect(html).toContain("Running");
    expect(html).toContain("Installed on disk");
    expect(html).toContain("Latest available");
    expect(html).toContain("1.4.145");
    expect(html).toContain("1.4.147");
    expect(html).toContain("Starts on next launch");
    // The bug: a number with nothing to distinguish it from the others.
    expect(html).not.toContain(">Version<");
  });

  it("does not say up to date anywhere on the block", () => {
    const html = render(INCIDENT);
    expect(html).not.toMatch(/up to date/i);
    expect(html).not.toMatch(/latest version/i);
  });

  it("disables the restart button only while restarting", () => {
    expect(render(INCIDENT, false)).not.toContain("Restarting…");
    const busy = render(INCIDENT, true);
    expect(busy).toContain("Restarting…");
    expect(busy).toContain("disabled");
  });
});

describe("About with everything in agreement", () => {
  const agreed = {
    runningDesktop: "1.4.147",
    runningEngine: "1.4.147",
    installedOnDisk: "1.4.147",
    updaterStatus: "up_to_date" as const,
  };

  it("shows no restart state and no alarm", () => {
    const html = render(agreed);
    expect(html).not.toContain("restart-required-banner");
    expect(html).not.toContain("Restart now");
    expect(html).toContain("Same as running");
  });
});

describe("About with the engine on a different build", () => {
  it("states it as a fact rather than hiding it", () => {
    const html = render({
      runningDesktop: "1.4.147",
      runningEngine: "1.4.145",
      installedOnDisk: "1.4.147",
      updaterStatus: "up_to_date",
    });
    expect(html).toContain("Engine build");
    expect(html).toContain("Different from the desktop build");
    expect(html).toContain('data-testid="version-fact-engine"');
  });
});

describe("About when the disk version cannot be read", () => {
  it("says so instead of implying agreement", () => {
    const html = render({ runningDesktop: "1.4.147", installedOnDisk: null });
    expect(html).toContain("Cannot be read on this platform");
    expect(html).not.toContain("Same as running");
    expect(html).not.toContain("restart-required-banner");
  });
});
