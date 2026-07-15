import { afterEach, describe, expect, it, vi } from "vitest";
import { recovery } from "./recovery";

afterEach(() => {
  recovery.clearHistory();
  recovery.setEngineRestart(null);
});

describe("application recovery", () => {
  it("awaits a registered surface refresh and reports success", async () => {
    const refresh = vi.fn(async () => undefined);
    const unregister = recovery.registerSurface("/tools", "refresh", refresh);
    const result = await recovery.refreshSurface("/tools");
    unregister();
    expect(refresh).toHaveBeenCalledOnce();
    expect(result).toMatchObject({ ok: true, level: "refresh-surface", target: "/tools" });
  });

  it("falls back to a real view reset when no refresh exists", async () => {
    const reset = vi.fn();
    const unregister = recovery.registerSurface("/activity", "reset", reset);
    const result = await recovery.refreshSurface("/activity");
    unregister();
    expect(reset).toHaveBeenCalledOnce();
    expect(result).toMatchObject({ ok: true, level: "reset-surface" });
  });

  it("returns structured failures instead of swallowing them", async () => {
    const unregister = recovery.registerSurface("/ports", "refresh", () => { throw new Error("probe failed"); });
    const result = await recovery.refreshSurface("/ports");
    unregister();
    expect(result).toMatchObject({ ok: false, level: "refresh-surface", error: "probe failed" });
  });

  it("uses the registered canonical engine restart", async () => {
    const restart = vi.fn(async () => undefined);
    recovery.setEngineRestart(restart);
    const result = await recovery.restartEngine();
    expect(restart).toHaveBeenCalledOnce();
    expect(result.ok).toBe(true);
  });

  it("records capability-driven service recovery through the shared watchdog", async () => {
    const repair = vi.fn(async () => undefined);
    const result = await recovery.repairService("image_gen", "repair", repair);
    expect(repair).toHaveBeenCalledOnce();
    expect(result).toMatchObject({ ok: true, level: "repair-service", target: "image_gen.repair" });
  });
});
