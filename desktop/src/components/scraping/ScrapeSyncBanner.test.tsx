import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/supabase", () => ({
  default: { auth: { getSession: vi.fn() } },
}));
vi.mock("@/lib/api", () => ({
  engine: {
    getScrapeSyncStatus: vi.fn(),
    triggerScrapeSync: vi.fn(),
    setAuthToken: vi.fn(),
  },
}));

import type { ScrapeSyncStatus } from "@/lib/api";
import { ScrapeSyncStrip } from "./ScrapeSyncBanner";

function status(over: Partial<ScrapeSyncStatus>): ScrapeSyncStatus {
  return {
    total: 8, synced: 2, pending: 0, failed: 6, deleted: 0,
    blocked_auth: 0, blocked_offline: 0, unsynced: 6,
    healthy: false, state: "rejected", message: "", action: "retry",
    ...over,
  };
}

const render = (s: ScrapeSyncStatus | null, justSynced = 0) =>
  renderToStaticMarkup(<ScrapeSyncStrip status={s} justSynced={justSynced} />);

describe("ScrapeSyncStrip", () => {
  it("renders nothing when everything is synced", () => {
    expect(
      render(status({ state: "synced", healthy: true, unsynced: 0, failed: 0, action: "none" })),
    ).toBe("");
  });

  it("stays silent when the engine is unreachable — EngineDownBanner owns that", () => {
    expect(render(null)).toBe("");
  });

  it("offers one-click sign-in when the engine has no usable token", () => {
    const html = render(
      status({
        state: "signed_out",
        action: "sign_in",
        pending: 6,
        failed: 0,
        blocked_auth: 6,
        message: "Sign in to sync your scrapes to the cloud. They are saved on this computer.",
      }),
    );
    expect(html).toContain("6 scrapes not yet in the cloud");
    expect(html).toContain("Sign in to sync");
    // A state, not a loss — the copy must say the scrapes are safe.
    expect(html).toContain("saved on this computer");
  });

  it("does not ask the user to fix an outage they cannot fix", () => {
    const html = render(
      status({
        state: "offline",
        action: "none",
        pending: 6,
        failed: 0,
        blocked_offline: 6,
        message: "The cloud is unreachable right now.",
      }),
    );
    expect(html).toContain("not yet in the cloud");
    expect(html).not.toContain("Sign in to sync");
    expect(html).not.toContain("Retry upload");
  });

  it("still offers retry on a genuine rejection", () => {
    expect(render(status({ message: "The cloud would not accept 6 scrape(s)." }))).toContain(
      "Retry upload",
    );
  });

  it("singularises one scrape", () => {
    expect(render(status({ unsynced: 1, failed: 1, message: "x" }))).toContain(
      "1 scrape not yet in the cloud",
    );
  });

  it("confirms an upload only right after the user acted on it", () => {
    const synced = status({ state: "synced", healthy: true, unsynced: 0, failed: 0, action: "none" });
    expect(render(synced, 6)).toContain("6 scrapes uploaded to the cloud");
    expect(render(synced, 0)).toBe("");
  });
});
