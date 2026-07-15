import { describe, expect, it } from "vitest";
import {
  DesktopBridgeUnavailableError,
  invokeTauri,
  isTauri,
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
});
