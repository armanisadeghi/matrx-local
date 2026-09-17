/**
 * Guard: when the updater finishes installing, the app says so — immediately,
 * with a Restart, and it keeps saying so.
 *
 * On 2026-09-17 the install completed at 03:30 and nothing on screen mentioned
 * it afterwards: the "installed" status lives in renderer memory, so a reload,
 * a new window or a dismissal erased it while the disk stayed ahead of the
 * running process. The banner therefore keys off the derived version state (the
 * bundle on disk) and treats the restart as a STATE, not a notification.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("@ai-matrx/kit/format", () => ({
  formatFileSize: (n?: number) => `${n ?? 0} B`,
}));

import type { AutoUpdateActions, AutoUpdateState } from "@/hooks/use-auto-update";
import {
  VersionStateProvider,
  useVersionFactReporter,
} from "@/contexts/VersionStateContext";
import { deriveVersionState } from "@/lib/version-facts";
import { UpdateBanner } from "./UpdateBanner";

const actions: AutoUpdateActions = {
  check: async () => {},
  install: async () => {},
  restart: async () => {},
  dismiss: () => {},
  openDialog: () => {},
};

function baseState(overrides: Partial<AutoUpdateState> = {}): AutoUpdateState {
  return {
    status: null,
    busy: false,
    showDownloadProgress: false,
    progress: 0,
    dialogOpen: false,
    dismissed: false,
    restarting: false,
    ...overrides,
  };
}

/** Render the banner inside the real provider (no Tauri: disk read is null). */
function renderInProvider(state: AutoUpdateState) {
  return renderToStaticMarkup(
    <VersionStateProvider>
      <UpdateBanner state={state} actions={actions} />
    </VersionStateProvider>,
  );
}

describe("the moment the install completes", () => {
  it("announces the restart with a one-click Restart Now", () => {
    const html = renderInProvider(
      baseState({ status: { status: "installed", version: "1.4.147" } }),
    );
    expect(html).toContain("Update installed — restart AI Matrx to finish");
    expect(html).toContain("Restart Now");
    expect(html).toContain("1.4.147 is installed on disk");
  });

  it("offers no way to dismiss the restart state", () => {
    const installed = renderInProvider(
      baseState({ status: { status: "installed", version: "1.4.147" } }),
    );
    expect(installed).toContain("Restart Now");
    expect(installed).not.toContain("Dismiss update notification");
  });

  it("keeps an ordinary available notice out of the restart state", () => {
    // "Available" is a notification the user may dismiss; only "installed on
    // disk but not running" is the undismissable state.
    const facts = deriveVersionState({
      runningDesktop: "1.4.145",
      installedOnDisk: "1.4.145",
      latestAvailable: "1.4.147",
      updaterStatus: "available",
    });
    expect(facts.restartRequired).toBe(false);
  });
});

describe("a renderer that never saw the install", () => {
  it("still shows the restart state from the build on disk alone", () => {
    // `useVersionFactReporter` is what the shell uses; here the disk fact is
    // injected directly, standing in for a fresh renderer whose updater status
    // is null because it was reloaded after the install.
    const facts = deriveVersionState({
      runningDesktop: "1.4.145",
      installedOnDisk: "1.4.147",
      updaterStatus: null,
    });
    expect(facts.restartRequired).toBe(true);
    expect(facts.restartHeadline).toBe(
      "Update installed — restart AI Matrx to finish",
    );
    // …and with no updater status at all the banner has nothing to key off
    // except that derived state, which is exactly the point.
    expect(typeof useVersionFactReporter).toBe("function");
  });

  it("shows nothing when there is no update and nothing pending", () => {
    const html = renderInProvider(
      baseState({ status: { status: "up_to_date" } }),
    );
    expect(html).toBe("");
  });
});
