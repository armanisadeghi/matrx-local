import { expect, test } from "@playwright/test";

import { loadTestCreds, loginViaUI } from "./helpers";

test("Claude Code shows the on-disk inventory and one sync action", async ({
  page,
}) => {
  const creds = loadTestCreds();
  test.skip(!creds, "desktop/.env.test missing — see docs/UI_TESTING.md");

  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  await loginViaUI(page, creds!);

  await page.getByRole("link", { name: "Claude Code" }).click();
  await expect(page.getByRole("heading", { name: "Claude Code" })).toBeVisible();

  // The whole point of the screen: one action, no selection, no preview step.
  await expect(page.getByRole("button", { name: /Sync everything/ })).toBeVisible();

  // Plain words only — the outbox vocabulary must never reach the user again.
  await expect(page.getByText(/envelope/i)).toHaveCount(0);
  await expect(page.getByText(/delivery pipeline/i)).toHaveCount(0);
  await expect(page.getByText(/adapter spool/i)).toHaveCount(0);

  await page.screenshot({ path: "claude-code-page.png", fullPage: true });
  expect(pageErrors).toEqual([]);
});
