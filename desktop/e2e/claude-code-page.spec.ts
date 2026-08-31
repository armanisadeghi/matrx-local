import { expect, test } from "@playwright/test";

import { loadTestCreds, loginViaUI } from "./helpers";

test("Coding Sessions shows every provider, scrolls, and syncs in one action", async ({
  page,
}) => {
  const creds = loadTestCreds();
  test.skip(!creds, "desktop/.env.test missing — see docs/UI_TESTING.md");

  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  await loginViaUI(page, creds!);

  await page.getByRole("link", { name: "Coding Sessions" }).click();
  await expect(
    page.getByRole("heading", { name: "Coding Sessions" }),
  ).toBeVisible();

  // Every provider stays visible. Deleting the old screen must never again
  // mean deleting Codex, Cursor and VS Code along with it.
  for (const editor of ["Claude Code", "Codex", "Cursor", "VS Code"]) {
    await expect(page.getByRole("cell", { name: editor, exact: true })).toBeVisible();
  }

  // The list must own a scroll region: AppLayout is overflow-hidden, so a page
  // without one is clipped at the viewport and the rest is unreachable.
  const scroller = page.getByTestId("coding-sessions-scroll");
  await expect(scroller).toBeVisible();
  await expect(scroller).toHaveClass(/overflow-y-auto/);

  // The whole point of the screen: one action, no selection, no preview step.
  await expect(page.getByRole("button", { name: /Sync everything/ })).toBeVisible();

  // Plain words only — the outbox vocabulary must never reach the user again.
  await expect(page.getByText(/envelope/i)).toHaveCount(0);
  await expect(page.getByText(/delivery pipeline/i)).toHaveCount(0);
  await expect(page.getByText(/adapter spool/i)).toHaveCount(0);

  // Prove the region actually scrolls rather than merely declaring the class.
  const scrollable = await scroller.evaluate(
    (node) => node.scrollHeight > node.clientHeight,
  );
  if (scrollable) {
    await scroller.evaluate((node) => node.scrollTo(0, node.scrollHeight));
    expect(await scroller.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
  }

  await page.screenshot({ path: "coding-sessions.png", fullPage: true });
  expect(pageErrors).toEqual([]);
});
