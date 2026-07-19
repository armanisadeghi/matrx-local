import { describe, expect, it } from "vitest";

import { actionNeededFromPermission } from "./adapters";

describe("permission action adapter", () => {
  it.each(["denied", "not_determined", "restricted"])(
    "publishes an explicit action for %s",
    (status) => {
      const item = actionNeededFromPermission({
        permission: "screen_recording",
        status,
        feature: "Screen recording",
        source: "devices-permission:screen_recording",
        observedAt: 123,
      });
      expect(item?.fingerprint).toBe(
        "os-permission:screen_recording:Screen recording",
      );
      expect(item?.action).toMatchObject({
        kind: "request_os_permission",
        permission_key: "screen_recording",
        route: "/devices?permission=screen_recording",
      });
    },
  );

  it.each(["granted", "loading", "unknown", "unavailable"])(
    "does not diagnose %s as denied",
    (status) => {
      expect(
        actionNeededFromPermission({
          permission: "camera",
          status,
          feature: "Camera",
          source: "test",
        }),
      ).toBeNull();
    },
  );
});
