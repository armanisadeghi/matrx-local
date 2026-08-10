/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AppNotification } from "@/hooks/use-notifications";
import {
  NotificationCenter,
  NotificationToastContainer,
} from "./NotificationCenter";

const notification: AppNotification = {
  id: "notification-1",
  title: "Download complete",
  message: "The model is ready to use.",
  level: "success",
  timestamp: Date.now(),
  read: false,
};

describe("NotificationCenter overlays", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
      .IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  it("portals the bell panel onto an opaque elevated surface", async () => {
    const onMarkRead = vi.fn();
    await act(async () => {
      root.render(
        <NotificationCenter
          notifications={[notification]}
          unreadCount={1}
          onMarkRead={onMarkRead}
          onMarkAllRead={vi.fn()}
          onDismiss={vi.fn()}
          onClearAll={vi.fn()}
        />,
      );
    });

    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('[aria-label="Notifications"]')!
        .click();
    });

    const panel = [
      ...document.body.querySelectorAll<HTMLElement>('[role="dialog"]'),
    ].find((element) => element.textContent?.includes("Download complete"));
    expect(panel).toBeDefined();
    expect(container.contains(panel!)).toBe(false);
    expect(panel!.className).toContain("bg-popover");
    expect(panel!.className).toContain("z-[100]");
    expect(onMarkRead).toHaveBeenCalledWith(notification.id);
  });

  it("renders notification toasts on an opaque popover surface", async () => {
    await act(async () => {
      root.render(
        <NotificationToastContainer
          toasts={[notification]}
          onHide={vi.fn()}
          onDismiss={vi.fn()}
        />,
      );
    });

    const toast = [...container.querySelectorAll<HTMLElement>("div")].find(
      (element) =>
        element.className.includes("bg-popover") &&
        element.textContent?.includes("Download complete"),
    );
    expect(toast).toBeDefined();
    expect(toast!.className).toContain("text-popover-foreground");
  });
});
