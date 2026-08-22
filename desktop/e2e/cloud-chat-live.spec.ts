import { expect, test } from "@playwright/test";
import {
  dismissEngineMonitorIfOpen,
  loadTestCreds,
  loginViaUI,
} from "./helpers";

test.describe("Cloud Chat live integration", () => {
  test("starts, continues, and reloads a persisted AIDream conversation", async ({
    page,
  }) => {
    test.setTimeout(240_000);
    const creds = loadTestCreds();
    test.skip(!creds, "desktop/.env.test is missing canonical AI_ADMIN credentials");

    const runId = Date.now().toString(36);
    const startMarker = `MXL_CHAT_START_${runId}`;
    const continueMarker = `MXL_CHAT_CONTINUE_${runId}`;
    const startPrompt = `Reply exactly: ${startMarker}`;
    const continuePrompt = `Reply exactly: ${continueMarker}`;

    await loginViaUI(page, creds!);
    await page.getByRole("link", { name: "Cloud Chat" }).click();

    const composer = page.getByPlaceholder("Message AI Matrx...");
    await expect(composer).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: "Matrx Desktop Agent" })).toBeVisible({
      timeout: 30_000,
    });

    await composer.fill(startPrompt);
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(page.getByText(startMarker, { exact: true })).toBeVisible({
      timeout: 120_000,
    });
    await expect(page.getByText(/AIDream request failed|validation_error/)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Send message" })).toBeEnabled({
      timeout: 30_000,
    });

    await composer.fill(continuePrompt);
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(page.getByText(continueMarker, { exact: true })).toBeVisible({
      timeout: 120_000,
    });
    await expect(page.getByText(/AIDream request failed|validation_error/)).toHaveCount(0);

    await page.reload();
    const dashboardLink = page.getByRole("link", { name: "Dashboard" });
    await expect(async () => {
      await dismissEngineMonitorIfOpen(page);
      await expect(dashboardLink).toBeVisible({ timeout: 5_000 });
    }).toPass({ timeout: 90_000 });
    await page.getByRole("link", { name: "Cloud Chat" }).click();
    await page.getByRole("button", { name: /^Open chat / }).first().click();

    await expect(page.getByText(startMarker, { exact: true })).toBeVisible({
      timeout: 60_000,
    });
    await expect(page.getByText(continueMarker, { exact: true })).toBeVisible({
      timeout: 60_000,
    });
  });
});
