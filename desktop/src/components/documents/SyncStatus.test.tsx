import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { SyncResult, SyncStatus } from "@/lib/api";
import { SyncStatusBar } from "./SyncStatus";

const status: SyncStatus = {
  configured: true,
  device_id: "test-device",
  last_pull_at: null,
  last_full_sync: null,
  tracked_files: 0,
  conflicts: [],
  conflict_count: 0,
  watcher_active: true,
  base_dir: "/private-test-notes",
};

function render(lastResult: SyncResult) {
  return renderToStaticMarkup(
    <SyncStatusBar status={status} syncing={false} lastResult={lastResult} onSync={() => {}} />,
  );
}

describe("SyncStatusBar result truthfulness", () => {
  it("does not call a deferred local read up to date", () => {
    const html = render({ skipped: 1, deferred_revision: 1 });
    expect(html).toContain("Sync incomplete — local changes will retry");
    expect(html).not.toContain("Up to date");
  });

  it("does not call ownership deferral up to date", () => {
    const html = render({ deferred_account: 1 });
    expect(html).toContain("Sync incomplete — account ownership changed");
    expect(html).not.toContain("Up to date");
  });

  it("does not call an engine error up to date", () => {
    const html = render({ error: "network_error" });
    expect(html).toContain("Sync needs attention");
    expect(html).not.toContain("Up to date");
  });

  it("does not call a failed sync up to date", () => {
    const html = render({ failed: 1 });
    expect(html).toContain("Sync needs attention");
    expect(html).not.toContain("Up to date");
  });

  it("keeps an ordinary skipped result up to date", () => {
    expect(render({ skipped: 1 })).toContain("Up to date");
  });
});
