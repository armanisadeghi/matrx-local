/**
 * NotesAccessPrompt — render-logic pins (node env, react-dom/server).
 *
 * The prompt is a PURE renderer of the shared access-health store: it shows
 * the presentation derived by deriveAccessPresentation and the resource's
 * resolved path, with the right actions per evidence. Polling and state live
 * in AccessHealthContext, so these tests only pin markup.
 */

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { AccessResourceHealth } from "@/lib/api";
import { deriveAccessPresentation } from "@/hooks/use-access-health";
import { PermissionsProvider } from "@/contexts/PermissionsContext";
import { NotesAccessPrompt } from "./NotesAccessPrompt";

function resource(
  overrides: Partial<AccessResourceHealth> = {},
): AccessResourceHealth {
  return {
    resource_id: "notes-canonical",
    label: "Notes folder",
    root: "/Users/test/Documents/Matrx/Notes",
    provenance: "default",
    status: "degraded",
    kind: "permission",
    message: "The OS denied write on /Users/test/Documents/Matrx/Notes (errno 13).",
    capabilities: {},
    last_success_at: null,
    last_failure: null,
    recent: [],
    generation: 1,
    ...overrides,
  };
}

function render(
  res: AccessResourceHealth,
  platform: string,
  fdaStatus: "granted" | "denied" | "indeterminate" | null,
  parentFdaProbe: boolean | null = null,
): string {
  const presentation = deriveAccessPresentation(
    res,
    {
      platform,
      fda: fdaStatus
        ? {
            status: fdaStatus,
            evidence: [],
            source: "engine-process probe",
            checked_at: 0,
          }
        : null,
    },
    parentFdaProbe,
  );
  return renderToStaticMarkup(
    <PermissionsProvider>
      <NotesAccessPrompt
        resource={res}
        presentation={presentation}
        checking={false}
        onRecheck={async () => null}
      />
    </PermissionsProvider>,
  );
}

describe("NotesAccessPrompt", () => {
  it("corroborated macOS FDA denial → FDA copy + System Settings action", () => {
    const html = render(resource(), "darwin", "denied", false);
    expect(html).toContain("Full Disk Access");
    expect(html).toContain("Open System Settings");
    expect(html).toContain("/Users/test/Documents/Matrx/Notes");
    expect(html).toContain("Check again");
  });

  it("helper-only denial never tells the user to grant FDA again", () => {
    const html = render(resource(), "darwin", "denied", true);
    expect(html).toContain("does not prove");
    expect(html).not.toContain("Matrx needs Full Disk Access");
    expect(html).not.toContain("Open System Settings");
  });

  it("unestablished macOS cause → evidence copy, settings offered as secondary", () => {
    const html = render(resource(), "darwin", "indeterminate");
    expect(html).toContain("errno 13");
    expect(html).toContain("Open System Settings");
    expect(html).not.toContain("Privacy controls are blocking");
  });

  it("missing_dir → Create folder action, no System Settings button", () => {
    const html = render(
      resource({ kind: "missing_dir" }),
      "darwin",
      "indeterminate",
    );
    expect(html).toContain("Create folder");
    expect(html).not.toContain("Open System Settings");
  });

  it("non-mac permission denial → evidence message, never mentions FDA", () => {
    const html = render(resource(), "linux", null);
    expect(html).toContain("errno 13");
    expect(html).not.toContain("Full Disk Access");
    expect(html).not.toContain("Open System Settings");
  });
});
