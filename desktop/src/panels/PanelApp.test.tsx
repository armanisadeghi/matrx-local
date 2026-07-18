/**
 * Panel runtime contract pins.
 *
 * These are server-rendered because the unit suite deliberately runs in a
 * Node environment. HashRouter is represented by MemoryRouter while retaining
 * the same router context contract. The probes intentionally call strict
 * hooks: a missing router, AccessHealthProvider, or DownloadManagerProvider
 * fails the render instead of producing a false-positive snapshot.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { useLocation } from "react-router-dom";
import { useAccessHealthContext } from "@/contexts/AccessHealthContext";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";
import type { PanelPage } from "@/lib/window-role";

function RouteProbe({ name }: { name: string }) {
  const location = useLocation();
  return <div data-page={name} data-router-path={location.pathname} />;
}

function DocumentsProbe() {
  const location = useLocation();
  useAccessHealthContext();
  return (
    <div
      data-page="notes"
      data-router-path={location.pathname}
      data-access-health="mounted"
    />
  );
}

function DownloadModalProbe() {
  const location = useLocation();
  useDownloadManager();
  return <div data-download-modal data-router-path={location.pathname} />;
}

function ActionBannerProbe({ engineStatus }: { engineStatus: string }) {
  const location = useLocation();
  return (
    <div
      data-action-banner={engineStatus}
      data-router-path={location.pathname}
    />
  );
}

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, HashRouter: actual.MemoryRouter };
});

vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    loading: false,
    isAuthenticated: true,
    user: { id: "panel-test-user" },
  }),
}));
vi.mock("@/hooks/use-engine", () => ({
  useEngine: () => ({
    status: "connected",
    url: "http://127.0.0.1:22240",
    tools: [],
  }),
}));
vi.mock("@/hooks/use-theme", () => ({ useTheme: () => undefined }));
vi.mock("@/components/layout/AppActionBanner", () => ({
  AppActionBanner: ActionBannerProbe,
}));
vi.mock("@/components/downloads/DownloadManagerModal", () => ({
  DownloadManagerModal: DownloadModalProbe,
}));

vi.mock("@/pages/Chat", () => ({
  Chat: () => <RouteProbe name="chat" />,
}));
vi.mock("@/pages/CloudChat", () => ({
  CloudChat: () => <RouteProbe name="cloud-chat" />,
}));
vi.mock("@/pages/Documents", () => ({ Documents: DocumentsProbe }));
vi.mock("@/pages/Activity", () => ({
  Activity: () => <RouteProbe name="activity" />,
}));
vi.mock("@/pages/Ports", () => ({
  Ports: () => <RouteProbe name="ports" />,
}));
vi.mock("@/pages/Voice", () => ({
  Voice: () => <RouteProbe name="transcription" />,
}));
vi.mock("@/pages/TextToSpeech", () => ({
  TextToSpeech: () => <RouteProbe name="tts" />,
}));
vi.mock("@/pages/MediaGeneration", () => ({
  MediaGeneration: () => <RouteProbe name="media-generation" />,
}));
vi.mock("@/panels/pages/MediaGalleryPanel", () => ({
  MediaGalleryPanel: () => <RouteProbe name="media-gallery" />,
}));

import { PanelApp } from "./PanelApp";
import { PANEL_MANIFEST } from "./manifest";

const PANEL_PAGES = Object.keys(PANEL_MANIFEST) as PanelPage[];
const DOWNLOAD_PANELS = new Set<PanelPage>([
  "transcription",
  "media-generation",
  "media-gallery",
]);

describe("PanelApp runtime composition", () => {
  it.each(PANEL_PAGES)(
    "%s boots inside router and its declared provider stack",
    (page) => {
      const html = renderToStaticMarkup(<PanelApp page={page} />);

      expect(html).toContain(`data-page="${page}"`);
      expect(html).toContain('data-router-path="/"');
      expect(html).toContain('data-action-banner="connected"');
      expect(html).toContain('aria-label="Open main window"');

      if (page === "notes") {
        expect(html).toContain('data-access-health="mounted"');
      }
      if (DOWNLOAD_PANELS.has(page)) {
        expect(html).toContain("data-download-modal");
      } else {
        expect(html).not.toContain("data-download-modal");
      }
    },
  );

  it("keeps the download UI inseparable from the download provider layer", () => {
    for (const page of DOWNLOAD_PANELS) {
      expect(PANEL_MANIFEST[page].providers.map(({ id }) => id)).toContain(
        "download-manager",
      );
    }
  });
});
