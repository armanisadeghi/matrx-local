/**
 * A redirect-only route may never live in `appPages`.
 *
 * `AppLayout` mounts EVERY entry of `appPages` for the life of the session and merely hides the
 * inactive ones (`display: none`). A `<Navigate>` there is therefore mounted forever and fires on
 * every render, which pins the whole app to its destination: from 2026-09-14 the `/codex-usage`
 * redirect sat in `appPages` and no other page in Matrx Local could be reached at all — every
 * navigation snapped back to `/coding-sessions?tab=usage` within a frame. Nobody saw it because
 * the custody cutover had removed the harness's ability to sign in (MXL-D-091), so no agent could
 * reach an authenticated screen to notice.
 *
 * `App.tsx` already carries the rule in a comment. This is the rule with teeth: it reads the
 * source, isolates the `appPages` literal, and fails on any redirect element inside it.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const HERE = path.dirname(fileURLToPath(import.meta.url));

/**
 * The text of the `appPages` array literal in `App.tsx`.
 *
 * Found by brace matching from the `const appPages: PageEntry[] = useMemo(` declaration, so it
 * cannot drift with formatting, and it throws rather than silently matching nothing if the
 * declaration is ever renamed — a guard that quietly stops looking is not a guard.
 */
function appPagesSource(): string {
  const source = readFileSync(path.join(HERE, "App.tsx"), "utf8");
  const declaration = "const appPages: PageEntry[] = useMemo(";
  const start = source.indexOf(declaration);
  expect(
    start,
    "App.tsx no longer declares `const appPages: PageEntry[] = useMemo(`; this guard must be " +
      "pointed at whatever replaced it before it can protect anything",
  ).toBeGreaterThan(-1);
  const open = source.indexOf("[", start + declaration.length);
  let depth = 0;
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === "[") depth += 1;
    else if (source[i] === "]") {
      depth -= 1;
      if (depth === 0) return source.slice(open, i + 1);
    }
  }
  throw new Error("the appPages array literal in App.tsx is not closed");
}

describe("appPages", () => {
  it("contains no redirect element", () => {
    const pages = appPagesSource();
    // `<Navigate` is react-router's redirect element; `redirect(` is its data-router equivalent.
    // Either one, mounted for the life of the session, pins the app to one route.
    for (const forbidden of ["<Navigate", "redirect("]) {
      expect(
        pages.includes(forbidden),
        `appPages contains ${forbidden}. AppLayout keeps every appPage mounted, so a redirect ` +
          "there fires on every render and no other page in the app can be reached. Put the " +
          "redirect-only route in the <Routes> block instead.",
      ).toBe(false);
    }
  });

  it("still holds the real pages, so the guard is reading the right array", () => {
    const pages = appPagesSource();
    for (const page of ["/cloud-chat", "/coding-sessions", "/settings"]) {
      expect(pages).toContain(`"${page}"`);
    }
  });
});
