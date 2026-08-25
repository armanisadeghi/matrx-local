import { describe, expect, it } from "vitest";

import {
  notificationToastKey,
  shouldShowNotificationToast,
  type AppNotification,
} from "./use-notifications";

function actionNotification(observedAt: number): AppNotification {
  return {
    id: "action:permission:camera",
    title: "Camera access needed",
    message: "Allow camera access to continue.",
    level: "warning",
    timestamp: observedAt * 1000,
    read: false,
    actionNeeded: {
      fingerprint: "permission:camera",
      code: "camera_required",
      kind: "os_permission",
      feature: "camera",
      title: "Camera access needed",
      message: "Allow camera access to continue.",
      action: {
        kind: "navigate",
        label: "Review access",
        route: "/devices?permission=camera",
      },
      source: "permissions",
      status: "active",
      observed_at: observedAt,
    },
  };
}

describe("notificationToastKey", () => {
  it("allows a recurring action fingerprint to produce a new toast", () => {
    expect(notificationToastKey(actionNotification(10))).not.toBe(
      notificationToastKey(actionNotification(20)),
    );
  });

  it("keeps ordinary notification dismissal keyed by notification id", () => {
    const notification: AppNotification = {
      id: "ordinary",
      title: "Done",
      message: "The operation completed.",
      level: "success",
      timestamp: 1,
      read: false,
    };
    expect(notificationToastKey(notification)).toBe("ordinary");
  });
});

describe("shouldShowNotificationToast", () => {
  it("does not duplicate durable action-needed state as a popup", () => {
    expect(shouldShowNotificationToast(actionNotification(10))).toBe(false);
  });

  it("keeps ordinary event notifications in the toast lane", () => {
    expect(
      shouldShowNotificationToast({
        id: "ordinary",
        title: "Done",
        message: "The operation completed.",
        level: "success",
        timestamp: 1,
        read: false,
      }),
    ).toBe(true);
  });
});
