/**
 * boot.spec.ts — the "does the app even start" guard.
 *
 * This exists because v1.3.104 shipped a boot-blocking crash: a component that
 * called useNavigate() was mounted outside <HashRouter>, so React threw during
 * the very first render and every user saw the ErrorBoundary fallback instead
 * of the app. Typecheck passed. The bundle built. Nothing in CI ever RENDERED
 * the app, so nothing caught it.
 *
 * The contract here is deliberately dumb and deliberately strict:
 *   1. The app renders SOMETHING real (the Login page — no creds needed, so
 *      this runs anywhere, including a cold CI box with no test account).
 *   2. The ErrorBoundary fallback is NOT on screen.
 *   3. Nothing threw an uncaught error during boot.
 *
 * Run against the production bundle (SMOKE_PREVIEW=1) as well as the dev
 * server — a crash can be introduced by minification/tree-shaking alone.
 *
 * Allowlists below are NARROW on purpose. When the app runs in a browser
 * instead of Tauri, the Tauri IPC bridge is absent and engine discovery
 * port-scans a machine with no engine on it; both are expected to be noisy.
 * Everything else is a real defect — widen these only with a comment saying
 * why, or you are re-opening the hole this file was written to close.
 */
import { test, expect, type Page } from "@playwright/test";
import { loadTestCreds, loginViaUI } from "./helpers";

/** Errors that are artifacts of browser-mode, not app defects. */
const EXPECTED_IN_BROWSER_MODE = [
  /__TAURI/i, // Tauri IPC bridge does not exist outside the desktop shell
  /tauri/i,
  /Failed to load resource/i, // engine port-scan 22140-22159 against nothing
  /ERR_CONNECTION_REFUSED/i,
  /net::ERR_/i,
  /Failed to fetch/i,
];

const isExpected = (text: string): boolean =>
  EXPECTED_IN_BROWSER_MODE.some((re) => re.test(text));

/**
 * Attach console/pageerror listeners BEFORE the first navigation and return a
 * getter for whatever was not allowlisted. A render crash surfaces as a
 * pageerror, so this is the check that actually has teeth.
 */
function collectFatalErrors(page: Page): () => string[] {
  const fatal: string[] = [];
  page.on("pageerror", (err) => {
    const text = `${err.name}: ${err.message}`;
    if (!isExpected(text)) fatal.push(`[pageerror] ${text}`);
  });
  page.on("console", (msg) => {
    if (msg.type() !== "error") return;
    const text = msg.text();
    if (!isExpected(text)) fatal.push(`[console.error] ${text}`);
  });
  return () => fatal;
}

/** The ErrorBoundary fallback headline (components/ErrorBoundary.tsx). */
const CRASH_HEADLINE = "Something went wrong";

async function expectNoCrashScreen(page: Page): Promise<void> {
  await expect(
    page.getByText(CRASH_HEADLINE),
    "the ErrorBoundary fallback is rendered — the app crashed during render",
  ).toHaveCount(0);
}

test.describe("boot", () => {
  for (const theme of ["light", "dark"] as const) {
    test(`applies the ${theme} theme before the app renders`, async ({ page }) => {
      await page.addInitScript((value) => {
        localStorage.setItem("matrx-theme", value);
      }, theme);

      await page.goto("/");
      await expect(page.getByRole("heading", { name: "Matrx Local" })).toBeVisible({
        timeout: 45_000,
      });
      await expect(page.getByTitle(/^Matrx Local version \d+\.\d+\.\d+$/)).toBeVisible();

      const state = await page.evaluate(() => ({
        darkClass: document.documentElement.classList.contains("dark"),
        colorScheme: getComputedStyle(document.documentElement).colorScheme,
        background: getComputedStyle(document.body).backgroundColor,
        foreground: getComputedStyle(document.body).color,
      }));

      expect(state.darkClass).toBe(theme === "dark");
      expect(state.colorScheme).toBe(theme);
      expect(state.background).not.toBe(state.foreground);
      await expectNoCrashScreen(page);
    });
  }

  test("app boots to the Login page with no crash and no uncaught errors", async ({
    page,
  }) => {
    const fatalErrors = collectFatalErrors(page);

    await page.goto("/");

    // The real Login page — proof we rendered the app, not a blank body.
    await expect(page.getByRole("heading", { name: "Matrx Local" })).toBeVisible(
      { timeout: 45_000 },
    );
    await expectNoCrashScreen(page);

    // Late-mounting providers/overlays (DownloadManagerModal, UpdateBanner,
    // DevTerminalPanel...) render after the first paint — the v1.3.104 crash
    // came from exactly one of those. Give them a beat, then re-assert.
    await page.waitForTimeout(2_000);
    await expectNoCrashScreen(page);

    expect(
      fatalErrors(),
      `unexpected errors during boot:\n${fatalErrors().join("\n")}`,
    ).toEqual([]);
  });

  test("authenticated shell boots with no crash and no uncaught errors", async ({
    page,
  }) => {
    const creds = loadTestCreds();
    test.skip(
      !creds,
      "desktop/.env.test missing — run: node e2e/setup/create-test-user.mjs (see docs/UI_TESTING.md)",
    );

    const fatalErrors = collectFatalErrors(page);

    await loginViaUI(page, creds!);
    await expectNoCrashScreen(page);

    // The authenticated tree mounts far more than the login tree (AppLayout,
    // every provider, every app-level overlay). Settle, then re-assert.
    await page.waitForTimeout(2_000);
    await expectNoCrashScreen(page);
    await expect(page.getByRole("link", { name: "Dashboard" })).toBeVisible();
    await expect(page.getByTitle(/^Matrx Local version \d+\.\d+\.\d+$/)).toBeVisible();

    // Confidential Chat depends on native Rust commands. Browser mode must
    // present that boundary explicitly instead of calling the importable
    // @tauri-apps/api shim and crashing on a missing runtime bridge.
    await page.getByRole("link", { name: "Confidential Chat" }).click();
    await expect(
      page.getByText("Desktop app required", { exact: true }),
    ).toBeVisible();
    await expectNoCrashScreen(page);

    expect(
      fatalErrors(),
      `unexpected errors during boot:\n${fatalErrors().join("\n")}`,
    ).toEqual([]);
  });
});
