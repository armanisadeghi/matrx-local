/**
 * media-gen.spec.ts — Media Generation page smoke coverage.
 *
 * Highest-value cheap assertions:
 *  - the page + layout switcher render after login;
 *  - all 5 layout variants mount without crashing (catches mount-crash drift
 *    across the UI bake-off variants — the exact class of bug that shipped
 *    invisibly before this harness existed);
 *  - the Library surface renders (Classic layout tab) and the Private-vault
 *    panel opens with its lock/create UI. We never create a vault and never
 *    trigger generation — live engine usage is READ-ONLY.
 */
import { test, expect, type Page } from "@playwright/test";
import {
  dismissEngineMonitorIfOpen,
  loadTestCreds,
  loginViaUI,
  probeEngine,
} from "./helpers";

const creds = loadTestCreds();

const VARIANT_LABELS = [
  "Classic tabs",
  "Studio split-pane",
  "Workspace nav",
  "Gallery first",
  "Focus flow",
] as const;

async function openMediaGeneration(page: Page): Promise<void> {
  await loginViaUI(page, creds!);
  await dismissEngineMonitorIfOpen(page);
  await page.getByRole("link", { name: "Media Generation" }).click();
  await expect(
    page.getByRole("heading", { name: "Media Generation" }),
  ).toBeVisible();
}

function layoutSwitcher(page: Page) {
  // The PageHeader layout <Select> is the only combobox in the header row.
  return page.getByRole("combobox").first();
}

async function selectLayout(page: Page, label: string): Promise<void> {
  await layoutSwitcher(page).click();
  await page.getByRole("option", { name: label }).click();
}

async function expectNoCrash(page: Page): Promise<void> {
  // ErrorBoundary fallback — if any variant crashes on mount this appears.
  await expect(page.getByText("Something went wrong")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Media Generation" }),
  ).toBeVisible();
}

test.describe("media generation", () => {
  test.skip(
    !creds,
    "desktop/.env.test missing — run: node e2e/setup/create-test-user.mjs (see docs/UI_TESTING.md)",
  );

  test("page renders with the layout switcher", async ({ page }) => {
    await openMediaGeneration(page);
    await expect(
      page.getByText("On-device AI image and video generation"),
    ).toBeVisible();
    await expect(layoutSwitcher(page)).toBeVisible();
    await expectNoCrash(page);
  });

  test("all 5 layout variants mount without crashing", async ({ page }) => {
    await openMediaGeneration(page);
    for (const label of VARIANT_LABELS) {
      await selectLayout(page, label);
      // Trigger reflects the active variant → the switch actually happened.
      await expect(layoutSwitcher(page)).toContainText(label);
      await expectNoCrash(page);
    }
  });

  test("Library tab renders and the Private-vault panel opens", async ({
    page,
  }) => {
    const engineUrl = await probeEngine();
    test.skip(
      !engineUrl,
      "No live engine (~/.matrx/local.json missing or /health not responding) — the media library and vault status are engine-backed. Start the engine (uv run python run.py) to cover this spec.",
    );

    await openMediaGeneration(page);
    // Classic layout has the explicit Library tab — the canonical surface.
    await selectLayout(page, "Classic tabs");
    await page.getByRole("tab", { name: "Library" }).click();

    // Library toolbar renders (independent of item count).
    await expect(
      page.getByRole("button", { name: "Private", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Refresh", exact: true }),
    ).toBeVisible();

    // Open the Private-vault panel. READ-ONLY: assert the lock/create UI
    // renders; never create a vault or unlock anything.
    await page.getByRole("button", { name: "Private", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("Private").first()).toBeVisible();
    // Valid states: no vault yet (create flow), locked (unlock form), or
    // already unlocked on the user's machine ("Lock now" control). Anything
    // else — e.g. "Vault status unavailable" with a live engine — is a bug.
    await expect(
      dialog
        .getByText("Create your Private vault")
        .or(dialog.getByRole("button", { name: "Unlock", exact: true }))
        .or(dialog.getByRole("button", { name: "Lock now" }))
        .first(),
    ).toBeVisible();

    // Close without touching anything.
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expectNoCrash(page);
  });
});
