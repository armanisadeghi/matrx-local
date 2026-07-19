import { describe, expect, it } from "vitest";
import type { MediaRuntimeStatus, RuntimeState } from "@/lib/api";
import {
  acceptsRuntimeSnapshot,
  isRuntimeActive,
  isRuntimeReady,
  runtimeAction,
} from "./runtime-state";

function status(
  state: RuntimeState,
  patch: Partial<MediaRuntimeStatus> = {},
): MediaRuntimeStatus {
  return {
    state,
    operation: null,
    attempt_id: null,
    runtime_revision: null,
    required_revision: "runtime-v1",
    stage: "",
    percent: 0,
    message: "",
    failure_code: null,
    failure_detail: null,
    repairable: false,
    image_available: state === "ready",
    video_packages_available: state === "ready",
    ...patch,
  };
}

describe("media runtime state", () => {
  it.each([
    "installing",
    "updating",
    "repairing",
    "validating",
    "activating",
  ] satisfies RuntimeState[])("treats %s as active", (state) => {
    expect(isRuntimeActive(status(state))).toBe(true);
  });

  it("requires both the ready state and server image availability", () => {
    expect(isRuntimeReady(status("ready"))).toBe(true);
    expect(
      isRuntimeReady(status("ready", { image_available: false })),
    ).toBe(false);
    expect(isRuntimeReady(status("failed"))).toBe(false);
  });

  it("rejects stale events after an operation supplies an attempt id", () => {
    expect(
      acceptsRuntimeSnapshot(status("validating", { attempt_id: "new" }), "new"),
    ).toBe(true);
    expect(
      acceptsRuntimeSnapshot(status("ready", { attempt_id: "old" }), "new"),
    ).toBe(false);
    expect(
      acceptsRuntimeSnapshot(status("ready", { attempt_id: null }), "new"),
    ).toBe(false);
  });

  it("offers install only for absent and repair only for repairable failures", () => {
    expect(runtimeAction(status("absent"))).toBe("install");
    expect(runtimeAction(status("failed", { repairable: true }))).toBe("repair");
    expect(runtimeAction(status("rolled_back", { repairable: true }))).toBe(
      "repair",
    );
    expect(runtimeAction(status("failed", { repairable: false }))).toBeNull();
    expect(runtimeAction(status("ready"))).toBeNull();
  });

  it("never maps ready to an installer or success-panel action", () => {
    expect(runtimeAction(status("ready"))).toBeNull();
    expect(isRuntimeActive(status("ready"))).toBe(false);
  });
});
