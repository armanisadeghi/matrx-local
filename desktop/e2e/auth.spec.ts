/**
 * auth.spec.ts — the login gate that used to make the UI unverifiable.
 *
 * 1. Unauthenticated: the real Login page renders (no creds needed — proves
 *    the harness itself works even if the test account breaks).
 * 2. Authenticated: sign in through the real email/password form with the
 *    dedicated test account (desktop/.env.test) and assert the app shell.
 */
import { test, expect } from "@playwright/test";
import { loadTestCreds, loginViaUI } from "./helpers";

const creds = loadTestCreds();

test.describe("authentication", () => {
  test("unauthenticated visitor sees the Login page", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: "Matrx Local" }),
    ).toBeVisible();
    await expect(page.getByText("Sign in to your workspace")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Sign in with AI Matrx" }),
    ).toBeVisible();
    await expect(page.getByLabel("Email")).toBeVisible();
    await expect(page.getByLabel("Password")).toBeVisible();
  });

  test("test account logs in via the real Login form and reaches the shell", async ({
    page,
  }) => {
    test.skip(
      !creds,
      "desktop/.env.test missing — run: node e2e/setup/create-test-user.mjs (see docs/UI_TESTING.md)",
    );
    await loginViaUI(page, creds!);

    // Authenticated shell: sidebar nav with the real destinations.
    await expect(page.getByRole("link", { name: "Dashboard" })).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Media Generation" }),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: "Settings" })).toBeVisible();

    // And we are NOT on the login page anymore.
    await expect(page.getByText("Sign in to your workspace")).toHaveCount(0);
  });
});
