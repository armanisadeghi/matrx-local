import { expect, test } from "@playwright/test";

import { loadTestCreds, loginViaUI } from "./helpers";

/**
 * Every number on the Coding Sessions screen is a door (Arman, 2026-09-08):
 * plain-word columns, status judged against AI Matrx, a pinned column, and a
 * full diagnosis behind every row. This spec proves the doors exist and open
 * against whatever engine is running; it never asserts specific counts.
 */
test("Coding Sessions explains every number and opens a diagnosis per row", async ({
  page,
}) => {
  const creds = loadTestCreds();
  test.skip(!creds, "desktop/.env.test missing — see docs/UI_TESTING.md");

  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  await loginViaUI(page, creds!);

  await page.getByRole("link", { name: "Coding Sessions" }).click();
  await expect(page.getByRole("heading", { name: "Coding Sessions" })).toBeVisible();

  // Plain-word provider columns. The old "Editor / Installed / Uploading /
  // Failed / Last sent" headers named nothing a person could act on.
  for (const header of [
    "App",
    "Installed here",
    "Waiting to send",
    "Refused, preserved",
    "Last accepted by AI Matrx",
  ]) {
    await expect(page.getByRole("columnheader", { name: header })).toBeVisible();
  }
  await expect(page.getByRole("columnheader", { name: "In AI Matrx?" })).toBeVisible();

  // Status cards carry the cloud vocabulary and are buttons, not labels.
  await expect(page.getByRole("button", { name: /In AI Matrx$/ })).toBeVisible({ timeout: 90_000 });
  await expect(page.getByRole("button", { name: /Not in AI Matrx/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Pinned in Claude Code/ })).toBeVisible();
  // The old vocabulary is gone from THIS screen (other panels own their own
  // "Synced" badges, so scope to the page's scroll region).
  const scroller = page.getByTestId("coding-sessions-scroll");
  await expect(scroller.getByText(/^Synced$/)).toHaveCount(0);
  await expect(scroller.getByText(/^Not synced$/)).toHaveCount(0);
  await expect(scroller.getByText(/^Uploading$/)).toHaveCount(0);
  await expect(scroller.getByRole("columnheader", { name: "Editor" })).toHaveCount(0);

  // A pause is a banner with its remedy and a door, never a bare count.
  const blocker = page.getByTestId("delivery-blocker");
  if (await blocker.count()) {
    await expect(blocker).toContainText("Delivery to AI Matrx is paused");
    await expect(blocker.getByRole("button", { name: /Retry delivery now/ })).toBeVisible();
  }

  // Every row opens the evidence it was judged on.
  const rows = page.getByTestId("conversation-row");
  if (await rows.count()) {
    await rows.first().click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("Every fact this Mac used to judge the row");
    await expect(dialog).toContainText("AI Matrx (the server's own record)");
    await expect(dialog).toContainText("Claude's sidebar record");
    await expect(dialog).toContainText("Delivery from this Mac");
    await page.keyboard.press("Escape");
  }

  expect(pageErrors).toEqual([]);
});
