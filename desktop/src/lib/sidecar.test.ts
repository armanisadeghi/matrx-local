import { describe, expect, it } from "vitest";
import {
  DesktopBridgeUnavailableError,
  invalidateNativeVaultHostActor,
  invokeTauri,
  isTauri,
  openNativeVaultProviderSettings,
  reconcileNativeVaultHostActor,
  requestNativeVaultProviderEnable,
  waitForOwnedEngineProbe,
} from "./sidecar";

describe("Tauri runtime boundary", () => {
  it("does not mistake an importable Tauri package for an available bridge", () => {
    expect(isTauri()).toBe(false);
  });

  it("reports a useful host-capability error instead of dereferencing invoke", async () => {
    await expect(invokeTauri("test_command")).rejects.toBeInstanceOf(
      DesktopBridgeUnavailableError,
    );
    await expect(invokeTauri("test_command")).rejects.toThrow(
      "requires the Matrx Local desktop app",
    );
  });

  it("uses the explicit unsupported-platform transition in ordinary browser development", async () => {
    expect(isTauri()).toBe(false);
    await expect(invalidateNativeVaultHostActor()).resolves.toBe("unsupported_platform");
    await expect(reconcileNativeVaultHostActor("actor-a")).resolves.toBe("unsupported_platform");
    await expect(requestNativeVaultProviderEnable()).resolves.toBeNull();
    await expect(openNativeVaultProviderSettings()).resolves.toBeNull();
  });
});

describe("owned engine startup wait", () => {
  it("keeps waiting while the child is alive, then returns its actual port", async () => {
    const outcomes = [
      { outcome: "running" as const, url: null },
      { outcome: "running" as const, url: null },
      { outcome: "ready" as const, url: "http://127.0.0.1:22147" },
    ];

    const result = await waitForOwnedEngineProbe(
      async () => outcomes.shift()!,
      5,
      0,
    );

    expect(result).toEqual({
      outcome: "ready",
      url: "http://127.0.0.1:22147",
    });
  });

  it("fails immediately only after native recovery is terminal", async () => {
    let probes = 0;
    const result = await waitForOwnedEngineProbe(
      async () => {
        probes += 1;
        return { outcome: "exited", url: null };
      },
      300,
      0,
    );

    expect(result).toEqual({ outcome: "exited", url: null });
    expect(probes).toBe(1);
  });

  it("waits across the empty child slot between automatic restart generations", async () => {
    const outcomes = [
      { outcome: "recovering" as const, url: null },
      { outcome: "running" as const, url: null },
      { outcome: "ready" as const, url: "http://127.0.0.1:22140" },
    ];

    await expect(
      waitForOwnedEngineProbe(async () => outcomes.shift()!, 5, 0),
    ).resolves.toEqual({ outcome: "ready", url: "http://127.0.0.1:22140" });
  });

  it("does not treat a transient no-child observation as terminal", async () => {
    let probes = 0;
    const result = await waitForOwnedEngineProbe(
      async () => {
        probes += 1;
        return probes === 1
          ? { outcome: "recovering" as const, url: null }
          : { outcome: "exited" as const, url: null };
      },
      3,
      0,
    );

    expect(result).toEqual({ outcome: "exited", url: null });
    expect(probes).toBe(2);
  });

  it("reports a timeout only after every live-process probe", async () => {
    let probes = 0;
    const result = await waitForOwnedEngineProbe(
      async () => {
        probes += 1;
        return { outcome: "running", url: null };
      },
      3,
      0,
    );

    expect(result).toEqual({ outcome: "timed_out", url: null });
    expect(probes).toBe(3);
  });
});
