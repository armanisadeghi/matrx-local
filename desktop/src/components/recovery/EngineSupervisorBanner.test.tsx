/** @vitest-environment jsdom */
/**
 * Guards for the loud half of the engine supervisor (SR-02).
 *
 * The shipped behaviour for an engine that died at spawn was a passive
 * "The local engine is not running" banner and a manual button — no cause, no
 * attempt count, and for up to 11 minutes no action at all. These assertions
 * fail on any surface that goes back to that: they require the engine's own
 * cause, the attempt number while retrying, and a real control once the
 * automatic attempts are spent.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/sidecar", () => ({
  checkForUpdates: vi.fn(),
  invokeTauri: vi.fn(),
  isTauri: () => false,
}));

import {
  ENGINE_SUPERVISOR_OK,
  engineSupervisorHeadline,
  type EngineSupervisorStatus,
} from "@/lib/engine-supervisor";
import { EngineSupervisorStrip } from "./EngineSupervisorBanner";

const ZLIB = "zlib.error: Error -3 while decompressing data: incorrect header check";

function status(over: Partial<EngineSupervisorStatus>): EngineSupervisorStatus {
  return {
    ...ENGINE_SUPERVISOR_OK,
    attempt: 2,
    maxAttempts: 3,
    cause: ZLIB,
    exitCode: 1,
    updatedAtMs: Date.now(),
    ...over,
  };
}

const render = (s: EngineSupervisorStatus) =>
  renderToStaticMarkup(<EngineSupervisorStrip status={s} onOpenMonitor={() => {}} />);

describe("EngineSupervisorStrip", () => {
  it("stays out of the way while the engine is fine", () => {
    expect(render(ENGINE_SUPERVISOR_OK)).toBe("");
  });

  it("names the cause and the attempt while it is retrying automatically", () => {
    const html = render(status({ phase: "restarting", remedy: "Restarting the engine — attempt 2 of 3." }));
    expect(html).toContain(ZLIB);
    expect(html).toContain("restarting (2/3)");
    expect(html).toContain("Restarting the engine — attempt 2 of 3.");
    // Nothing for the user to do yet — but the log is always one click away.
    expect(html).toContain("Details");
    expect(html).not.toContain("Restart engine");
  });

  it("hands over real controls once the automatic attempts are spent", () => {
    const html = render(
      status({ phase: "failed", attempt: 3, remedy: "3 automatic restarts did not hold." }),
    );
    expect(html).toContain(ZLIB);
    expect(html).toContain("gave up after 3 tries");
    expect(html).toContain("Restart engine");
    expect(html).toContain("Reinstall AI Matrx");
    expect(html).toContain("Details");
  });

  it("never shows a bare 'something went wrong' — the headline always carries the cause", () => {
    expect(engineSupervisorHeadline(status({ phase: "restarting" }))).toBe(
      `Engine failed to start: ${ZLIB} — restarting (2/3)`,
    );
    expect(engineSupervisorHeadline(status({ phase: "failed", attempt: 1 }))).toContain(
      "gave up after 1 try",
    );
    // Even with no cause at all the sentence still says what happened.
    expect(engineSupervisorHeadline(status({ phase: "failed", cause: null }))).toContain(
      "Engine failed to start",
    );
  });
});
