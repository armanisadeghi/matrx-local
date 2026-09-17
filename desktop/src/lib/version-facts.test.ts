/**
 * Guards for the lying screen of 2026-09-17.
 *
 * The updater installed 1.4.147 into `/Applications/AI Matrx.app` at 03:30
 * while the desktop process and its engine kept running 1.4.145. About showed
 * one unlabelled "Version" and "Check for updates" answered "No updates
 * available" — true of the disk, false of what was running. Every assertion
 * here is that exact situation, replayed through the one helper.
 */

import { describe, expect, it } from "vitest";
import { deriveVersionState } from "./version-facts";

/** The measured incident: disk moved to 1.4.147, process still on 1.4.145. */
const INCIDENT = {
  runningDesktop: "1.4.145",
  runningEngine: "1.4.145",
  installedOnDisk: "1.4.147",
  latestAvailable: null,
  updaterStatus: "up_to_date" as const,
};

describe("running ≠ installed", () => {
  it("demands a restart even when the updater says up to date", () => {
    const state = deriveVersionState(INCIDENT);
    expect(state.restartRequired).toBe(true);
    expect(state.pendingVersion).toBe("1.4.147");
    expect(state.restartHeadline).toBe(
      "Update installed — restart AI Matrx to finish",
    );
  });

  it("never claims the latest version while a newer build sits on disk", () => {
    const state = deriveVersionState(INCIDENT);
    expect(state.updateSummary).not.toMatch(/latest/i);
    expect(state.updateSummary).not.toMatch(/up to date/i);
    expect(state.updateSummary).toContain("restart AI Matrx to finish");
    expect(state.updateSummary).toContain("1.4.147");
    expect(state.updateSummary).toContain("1.4.145");
  });

  it("labels all three builds, and marks the disk fact as the one that differs", () => {
    const state = deriveVersionState(INCIDENT);
    const byKey = Object.fromEntries(state.facts.map((f) => [f.key, f]));

    expect(byKey.running?.label).toBe("Running");
    expect(byKey.running?.value).toBe("1.4.145");
    expect(byKey.running?.differsFromRunning).toBeFalsy();

    expect(byKey.installed?.label).toBe("Installed on disk");
    expect(byKey.installed?.value).toBe("1.4.147");
    expect(byKey.installed?.differsFromRunning).toBe(true);
    expect(byKey.installed?.detail).toBe("Starts on next launch");

    // "up to date" is a statement about the disk — so that IS the latest known.
    expect(byKey.latest?.label).toBe("Latest available");
    expect(byKey.latest?.value).toBe("1.4.147");

    // Every fact carries a label. An unlabelled version is the original bug.
    for (const fact of state.facts) expect(fact.label.length).toBeGreaterThan(0);
  });

  it("holds the restart state on the updater's own report alone", () => {
    // Non-macOS installers apply on exit, so the disk fact is unreadable —
    // the updater saying "installed" must still produce the restart state.
    const state = deriveVersionState({
      runningDesktop: "1.4.145",
      installedOnDisk: null,
      latestAvailable: "1.4.147",
      updaterStatus: "installed",
    });
    expect(state.restartRequired).toBe(true);
    expect(state.pendingVersion).toBe("1.4.147");
  });
});

describe("agreement", () => {
  it("says latest only when running, installed and offered all agree", () => {
    const state = deriveVersionState({
      runningDesktop: "1.4.147",
      runningEngine: "1.4.147",
      installedOnDisk: "1.4.147",
      updaterStatus: "up_to_date",
    });
    expect(state.restartRequired).toBe(false);
    expect(state.pendingVersion).toBeNull();
    expect(state.engineMismatch).toBe(false);
    expect(state.restartHeadline).toBeNull();
    expect(state.updateSummary).toBe("You're running the latest version");
  });

  it("tolerates a leading v on either side", () => {
    const state = deriveVersionState({
      runningDesktop: "1.4.147",
      installedOnDisk: "v1.4.147",
      runningEngine: "v1.4.147",
      updaterStatus: "up_to_date",
    });
    expect(state.restartRequired).toBe(false);
    expect(state.engineMismatch).toBe(false);
  });
});

describe("engine build ≠ desktop build", () => {
  it("is a labelled fact on screen, with the reason", () => {
    const state = deriveVersionState({
      runningDesktop: "1.4.147",
      runningEngine: "1.4.145",
      installedOnDisk: "1.4.147",
      updaterStatus: "up_to_date",
    });
    expect(state.engineMismatch).toBe(true);
    const engine = state.facts.find((f) => f.key === "engine");
    expect(engine?.label).toBe("Engine build");
    expect(engine?.value).toBe("1.4.145");
    expect(engine?.differsFromRunning).toBe(true);
    expect(engine?.detail).toContain("Different from the desktop build");
  });

  it("is not claimed when the engine has not been reached", () => {
    const state = deriveVersionState({
      runningDesktop: "1.4.147",
      runningEngine: null,
      installedOnDisk: "1.4.147",
    });
    expect(state.engineMismatch).toBe(false);
    expect(state.facts.some((f) => f.key === "engine")).toBe(false);
  });
});

describe("unknowns are unknown, never agreement", () => {
  it("does not treat an unreadable disk version as a match", () => {
    const state = deriveVersionState({
      runningDesktop: "1.4.147",
      installedOnDisk: null,
    });
    const installed = state.facts.find((f) => f.key === "installed");
    expect(installed?.value).toBeNull();
    expect(installed?.detail).toBe("Cannot be read on this platform");
    expect(state.restartRequired).toBe(false);
  });

  it("does not treat an empty string as a version", () => {
    const state = deriveVersionState({
      runningDesktop: "1.4.147",
      installedOnDisk: "   ",
      runningEngine: "",
    });
    expect(state.restartRequired).toBe(false);
    expect(state.engineMismatch).toBe(false);
    expect(state.facts.find((f) => f.key === "installed")?.value).toBeNull();
  });

  it("says not checked yet before the updater has been asked", () => {
    const state = deriveVersionState({
      runningDesktop: "1.4.147",
      installedOnDisk: "1.4.147",
    });
    expect(state.facts.find((f) => f.key === "latest")?.detail).toBe(
      "Not checked yet",
    );
    expect(state.updateSummary).toBe("Check for new releases");
  });
});

describe("the ordinary update path is unchanged", () => {
  it("reports an available build without demanding a restart", () => {
    const state = deriveVersionState({
      runningDesktop: "1.4.145",
      installedOnDisk: "1.4.145",
      latestAvailable: "1.4.147",
      updaterStatus: "available",
    });
    expect(state.restartRequired).toBe(false);
    expect(state.updateSummary).toContain("1.4.147 available");
    expect(state.facts.find((f) => f.key === "latest")?.value).toBe("1.4.147");
  });

  it("reports a download in progress", () => {
    const state = deriveVersionState({
      runningDesktop: "1.4.145",
      installedOnDisk: "1.4.145",
      latestAvailable: "1.4.147",
      updaterStatus: "downloading",
    });
    expect(state.restartRequired).toBe(false);
    expect(state.updateSummary).toBe("Downloading update…");
  });
});
