/**
 * The coding-sessions feature, proven on the real screen.
 *
 * One sidebar entry, three tabs, a row that opens its conversation, and a
 * Refresh that visibly works (Arman's four complaints, 2026-09-14). Captures
 * the evidence screenshots referenced by the CS-11 report.
 */
import { existsSync, mkdirSync } from "node:fs";
import { expect, test } from "@playwright/test";

import {
  HARNESS_NOT_SIGNED_IN,
  dismissEngineMonitorIfOpen,
  harnessIdentity,
  signInViaHarness,
} from "./helpers";

const EVIDENCE =
  process.env.CS11_EVIDENCE_DIR ??
  "/Users/armanisadeghi/code/common-docs/projects/coding-agent-bridge/evidence";

function shot(name: string): string {
  if (!existsSync(EVIDENCE)) mkdirSync(EVIDENCE, { recursive: true });
  return `${EVIDENCE}/${name}`;
}

test("one feature, three tabs, and a row that opens its conversation", async ({ page }) => {
    test.skip(!(await harnessIdentity()), HARNESS_NOT_SIGNED_IN);

  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  await signInViaHarness(page);

  // ONE sidebar entry for the feature. "Codex Usage" was a second one.
  await expect(page.getByRole("link", { name: "Coding Sessions" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Codex Usage" })).toHaveCount(0);

  // Land on the feature's default tab deterministically: the app can still be
  // settling its own navigation right after sign-in, so assert the URL rather
  // than trusting one click.
  await expect(async () => {
    await page.getByRole("link", { name: "Coding Sessions" }).click();
    await dismissEngineMonitorIfOpen(page);
    await expect(page.getByTestId("coding-sessions-tab-sessions")).toHaveAttribute(
      "aria-selected",
      "true",
      { timeout: 5_000 },
    );
  }).toPass({ timeout: 60_000 });
  await expect(page.getByRole("heading", { name: "Coding Sessions" })).toBeVisible();

  // Sessions tab: the list, with the provider facet above it.
  const rows = page.getByTestId("conversation-row");
  await expect(rows.first()).toBeVisible({ timeout: 120_000 });
  await expect(page.getByTestId("provider-chips")).toContainText("Claude Code");
  await expect(page.getByTestId("provider-chips")).toContainText("Codex");
  // Both secondary doors stay reachable on the row itself: the delivery
  // evidence, and the engine's own continuation command.
  await expect(rows.first().getByRole("button", { name: /Delivery/ })).toBeVisible();
  await expect(rows.first().getByRole("button", { name: /Continue/ })).toBeVisible();
  await page.screenshot({ path: shot("cs11-sessions-tab.png"), fullPage: false });

  // Refresh announces itself instead of staring back.
  const refresh = page.getByTestId("coding-sessions-refresh");
  await refresh.click();
  await expect(refresh).toHaveAttribute("aria-busy", "true");
  await expect(page.getByTestId("sessions-tbody")).toHaveAttribute("data-refreshing", "true");
  // The rows it already read are still there while it works.
  await expect(rows.first()).toBeVisible();
  await page.screenshot({ path: shot("cs11-refresh-loading.png"), fullPage: false });
  await expect(refresh).toHaveAttribute("aria-busy", "false", { timeout: 180_000 });

  // A row is a door: either the conversation opens, or the row says why not
  // and keeps the delivery evidence one click away. Never a dead click.
  await rows.first().click();
  const openedChat = page.getByRole("link", { name: /Back to coding sessions/ });
  const refused = page.getByTestId("session-not-openable");
  await expect(async () => {
    expect((await openedChat.count()) + (await refused.count())).toBeGreaterThan(0);
  }).toPass({ timeout: 30_000 });

  if (await openedChat.count()) {
    await expect(page).toHaveURL(/conversation=/);
    await page.screenshot({ path: shot("cs11-session-opened.png"), fullPage: false });
    await openedChat.click();
    await expect(page.getByRole("heading", { name: "Coding Sessions" })).toBeVisible();
  } else {
    await expect(refused).toContainText("is not in AI Matrx yet");
    await refused.scrollIntoViewIfNeeded();
    await page.screenshot({ path: shot("cs11-session-opened.png"), fullPage: false });
    await page.getByRole("button", { name: /See every delivery fact/ }).click();
    await expect(page.getByRole("dialog")).toContainText(
      "Every fact this Mac used to judge the row",
    );
    await page.keyboard.press("Escape");
  }

  // Usage lives INSIDE the feature now.
  await page.getByTestId("coding-sessions-tab-usage").click();
  await expect(page).toHaveURL(/tab=usage/);
  await expect(page.getByTestId("usage-provider-chips")).toBeVisible();
  await page.screenshot({ path: shot("cs11-usage-tab.png"), fullPage: false });

  // Settings & diagnostics holds every operational block.
  await page.getByTestId("coding-sessions-tab-settings").click();
  await expect(page).toHaveURL(/tab=settings/);
  for (const header of [
    "App",
    "Installed here",
    "Waiting to send",
    "Refused, preserved",
    "Last accepted by AI Matrx",
  ]) {
    await expect(page.getByRole("columnheader", { name: header })).toBeVisible();
  }
  await page.screenshot({ path: shot("cs11-settings-tab.png"), fullPage: false });

  expect(pageErrors).toEqual([]);
});

test("the retired Codex Usage route lands on the usage tab", async ({ page }) => {
    test.skip(!(await harnessIdentity()), HARNESS_NOT_SIGNED_IN);
  await signInViaHarness(page);
  await page.goto("/#/codex-usage");
  await expect(page).toHaveURL(/coding-sessions\?tab=usage/);
  await expect(page.getByRole("heading", { name: "Coding Sessions" })).toBeVisible();
});

/**
 * The conversation itself, with real messages.
 *
 * On this Mac the local transcripts belong to a different account than the
 * canonical admin test account, so a row here has no binding to click through
 * (the list says so honestly, which the test above proves). This test takes
 * the same code path with real data the admin account DOES own: one coding-
 * session conversation, opened by id the way a row opens it.
 *
 * Pass CS11_CONVERSATION_ID=<a conversation the signed-in account owns>.
 */
test("a coding-session conversation opens with its messages", async ({ page }) => {
    test.skip(!(await harnessIdentity()), HARNESS_NOT_SIGNED_IN);
  const conversationId = process.env.CS11_CONVERSATION_ID;
  test.skip(!conversationId, "set CS11_CONVERSATION_ID to a conversation this account owns");

  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  await signInViaHarness(page);

  // Same retry reason as above: the shell may still be settling its own
  // navigation when the deep link is set.
  const backLink = page.getByRole("link", { name: /Back to coding sessions/ });
  await expect(async () => {
    await page.evaluate((id) => {
      window.location.hash = `#/cloud-chat?conversation=${id}&from=coding-sessions`;
    }, conversationId!);
    await expect(backLink).toBeVisible({ timeout: 5_000 });
  }).toPass({ timeout: 60_000 });
  // Real messages, not an empty surface and not an error.
  await expect(page.getByTestId("chat-message").first()).toBeVisible({ timeout: 60_000 });
  await page.screenshot({ path: shot("cs11-conversation-messages.png"), fullPage: false });

  await page.getByRole("link", { name: /Back to coding sessions/ }).click();
  await expect(page.getByRole("heading", { name: "Coding Sessions" })).toBeVisible();

  expect(pageErrors).toEqual([]);
});
