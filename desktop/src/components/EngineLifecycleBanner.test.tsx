/** @vitest-environment jsdom */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/sidecar", () => ({
  checkForUpdates: vi.fn(),
  invokeTauri: vi.fn(),
  isTauri: () => false,
}));

import {
  ENGINE_SUPERVISOR_OK,
  engineSupervisor,
  type EngineSupervisorStatus,
} from "@/lib/engine-supervisor";
import { EngineLifecycleBanner } from "./EngineLifecycleBanner";

const render = (engineStatus: Parameters<typeof EngineLifecycleBanner>[0]["engineStatus"]) =>
  renderToStaticMarkup(
    <EngineLifecycleBanner
      engineStatus={engineStatus}
      onRestartEngine={() => undefined}
      onOpenMonitor={() => undefined}
    />,
  );

function supervisor(over: Partial<EngineSupervisorStatus>): EngineSupervisorStatus {
  return {
    ...ENGINE_SUPERVISOR_OK,
    attempt: 1,
    maxAttempts: 3,
    cause: "zlib.error: incorrect header check",
    ...over,
  };
}

describe("EngineLifecycleBanner", () => {
  it("shows one calm loading state while authenticated startup continues", () => {
    engineSupervisor.push(ENGINE_SUPERVISOR_OK);
    const html = render("starting");
    expect(html).toContain("Loading and syncing your data");
    expect(html).not.toContain("Restart engine");
    expect(html).not.toContain("needs attention");
  });

  it("lets native recovery override a transient React error", () => {
    engineSupervisor.push(supervisor({ phase: "restarting" }));
    const html = render("error");
    expect(html).toContain("Loading and syncing your data");
    expect(html).toContain("Automatic recovery 1 of 3 is in progress");
    expect(html).not.toContain("Restart engine");
  });

  it("shows exactly one terminal action set after automatic recovery is exhausted", () => {
    engineSupervisor.push(supervisor({ phase: "failed", attempt: 3 }));
    const html = render("error");
    expect(html).toContain("The local engine needs attention");
    expect(html).toContain("gave up after 3 tries");
    expect(html.match(/Restart engine/g)).toHaveLength(1);
    expect(html.match(/Reinstall AI Matrx/g)).toHaveLength(1);
  });

  it("stays out of the way when reachability is healthy", () => {
    engineSupervisor.push(supervisor({ phase: "restarting" }));
    expect(render("connected")).toBe("");
  });
});
