/**
 * auth.spec.ts — the login gate that used to make the UI unverifiable.
 *
 * 1. Unauthenticated: the real sign-in screen renders, and it has NO password field. That
 *    absence is asserted, not assumed: the sync daemon is the device's only session holder, and a
 *    password form here would be a second credential holder (SPEC-CUSTODY, D17).
 * 2. Authenticated: the harness signed the dev-world daemon in before the browser started
 *    (`e2e/setup/harness-session.mjs`), and the app adopts that session like any other.
 */
import { test, expect } from "@playwright/test";
import {
  HARNESS_ALREADY_SIGNED_IN,
  HARNESS_NOT_SIGNED_IN,
  harnessIdentity,
  signInViaHarness,
} from "./helpers";


test.describe("authentication", () => {
  test("unauthenticated visitor sees the sign-in screen, with no password field", async ({
    page,
  }) => {
    test.skip(Boolean(await harnessIdentity()), HARNESS_ALREADY_SIGNED_IN);
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: "Matrx Local" }),
    ).toBeVisible();
    await expect(page.getByText("Sign in to your workspace")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Sign in with AI Matrx" }),
    ).toBeVisible();
    // The product must never regain these. The harness signs in through the daemon instead.
    await expect(page.getByLabel("Email")).toHaveCount(0);
    await expect(page.getByLabel("Password")).toHaveCount(0);
  });

  test("the harness session reaches the authenticated shell", async ({
    page,
  }) => {
    test.skip(!(await harnessIdentity()), HARNESS_NOT_SIGNED_IN);
    await signInViaHarness(page);

    // Authenticated shell: sidebar nav with the real destinations.
    await expect(page.getByRole("link", { name: "Dashboard" })).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Media Generation" }),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: "Settings" })).toBeVisible();

    // And we are NOT on the login page anymore.
    await expect(page.getByText("Sign in to your workspace")).toHaveCount(0);
  });

  test("legacy Coding Sessions redirect does not trap later navigation", async ({
    page,
  }) => {
    test.skip(!(await harnessIdentity()), HARNESS_NOT_SIGNED_IN);
    await signInViaHarness(page);

    // The retired link lands on the feature's default tab. It is `sessions`, not `history`:
    // there are three tabs — sessions, usage, settings — and `parseCodingSessionsTab` sends
    // anything else to the default rather than to a blank screen.
    await page.goto("/#/claude-history");
    await expect(page).toHaveURL(/\/#\/coding-sessions$/);

    await page.getByRole("link", { name: "Chat", exact: true }).click();
    await expect(page).toHaveURL(/\/#\/chat$/);

    // Give any keep-alive page effects a chance to run. A redirect mounted in appPages fired
    // again here and forced the hash back to Coding Sessions — from 2026-09-14 until MXL-D-091's
    // harness sign-in made this test runnable and it caught it.
    await page.waitForTimeout(1_000);
    await expect(page).toHaveURL(/\/#\/chat$/);

    // And the retired /codex-usage link still carries its tab.
    await page.goto("/#/codex-usage");
    await expect(page).toHaveURL(/\/#\/coding-sessions\?tab=usage$/);
    await page.getByRole("link", { name: "Notes", exact: true }).click();
    await expect(page).toHaveURL(/\/#\/notes$/);
    await page.waitForTimeout(1_000);
    await expect(page).toHaveURL(/\/#\/notes$/);
  });
});
