/**
 * NotesAccessPrompt — render-logic pins (node env, react-dom/server).
 *
 * The prompt is the user-facing half of the notes access-degraded STATE
 * (engine: notes_access_guard + GET /notes/access). These tests pin that
 * each degraded kind/platform renders the right explanation and actions:
 *
 *   - macOS permission  → Full Disk Access copy + "Open System Settings"
 *   - missing_dir       → "Create folder" action, no macOS settings button
 *   - non-mac permission→ the engine's actionable reason verbatim, no
 *                         macOS settings button
 *
 * No jsdom in this repo, so we assert on static markup — interaction
 * behavior (recheck/poll) lives in the engine tests
 * (tests/unit/test_notes_access_state.py) and the hook wiring.
 */

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { NotesAccessStatus } from "@/lib/api";
import { PermissionsProvider } from "@/contexts/PermissionsContext";
import { NotesAccessPrompt } from "./NotesAccessPrompt";

function render(access: NotesAccessStatus): string {
  return renderToStaticMarkup(
    <PermissionsProvider>
      <NotesAccessPrompt access={access} onRecheck={async () => null} />
    </PermissionsProvider>,
  );
}

const base = {
  degraded: true as const,
  base_dir: "/Users/test/Documents/Matrx/Notes",
};

describe("NotesAccessPrompt", () => {
  it("macOS permission denial → Full Disk Access explanation + System Settings action", () => {
    const html = render({
      ...base,
      kind: "permission",
      reason: "macOS denied access to ~/Documents — grant Full Disk Access…",
      platform: "darwin",
    });
    expect(html).toContain("Matrx needs access to your notes folder");
    expect(html).toContain("Full Disk Access");
    expect(html).toContain("Open System Settings");
    expect(html).toContain("Check again");
    expect(html).toContain(base.base_dir);
    expect(html).not.toContain("Create folder");
  });

  it("missing notes folder → Create folder action, no macOS settings button", () => {
    const html = render({
      ...base,
      kind: "missing_dir",
      reason: `Notes folder does not exist: ${base.base_dir}`,
      platform: "win32",
    });
    expect(html).toContain("Your notes folder is missing");
    expect(html).toContain("Create folder");
    expect(html).toContain("Check again");
    expect(html).not.toContain("Open System Settings");
  });

  it("non-mac permission denial → renders the engine's actionable reason", () => {
    const reason =
      "The operating system denied access to the notes folder — check the " +
      "folder's permissions and ownership";
    const html = render({
      ...base,
      kind: "permission",
      reason,
      platform: "linux",
    });
    expect(html).toContain("check the\nfolder&#x27;s permissions".replace("\n", " "));
    expect(html).not.toContain("Open System Settings");
    expect(html).not.toContain("Create folder");
  });
});
