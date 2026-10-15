/**
 * Real send proof for Cloud Chat on `@ai-matrx/chat` (L1, `/cloud-chat`), signed in as
 * the harness account (admin@admin.com). One short message to a DEDICATED agent (Content Writing
 * Partner, never General Chat) on the cheapest plumbing model (GPT OSS 20B, per the
 * test-model-selection skill), then: the answer streams, a message action works, the conversation
 * is in history after a page reload, and a Confidential conversation REFUSES a cloud send on screen.
 * Local-engine-offline sends are out of scope (local Chat owns them).
 *
 *   eval "$(node e2e/setup/harness-session.mjs --export)"
 *   SMOKE_PORT=1420 pnpm exec playwright test package-cloud-chat-send.spec.ts
 */
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";
import { HARNESS_NOT_SIGNED_IN, harnessIdentity, signInViaHarness } from "./helpers";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SHOTS = path.join(__dirname, "..", "test-results", "package-cloud-chat-send");
const AGENT = "Content Writing Partner";
const MODEL = "GPT OSS 20B";

async function closeEngineMonitor(page: Page): Promise<void> {
  const dialog = page.getByRole("dialog").filter({ hasText: "Engine Monitor" });
  if (await dialog.isVisible({ timeout: 3_000 }).catch(() => false)) {
    await dialog.getByRole("button", { name: /close/i }).first().click();
    await dialog.waitFor({ state: "hidden", timeout: 5_000 }).catch(() => undefined);
  }
}

test.describe("Cloud Chat on the chat package: real send", () => {
  test("dedicated agent streams an answer, message action, history after reload, Confidential refuses", async ({
    page,
  }) => {
    test.setTimeout(240_000);
    test.skip(!(await harnessIdentity()), HARNESS_NOT_SIGNED_IN);
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(String(e)));

    // Record what the copy action writes (headless Chromium has no real clipboard to read).
    await page.addInitScript(() => {
      const copied: string[] = [];
      (window as unknown as { __copied: string[] }).__copied = copied;
      const write = navigator.clipboard?.writeText?.bind(navigator.clipboard);
      if (navigator.clipboard) {
        navigator.clipboard.writeText = async (text: string) => {
          copied.push(text);
          return write?.(text).catch(() => undefined);
        };
      }
    });
    await signInViaHarness(page);
    await page.goto("/#/cloud-chat");
    await closeEngineMonitor(page);
    const root = page.locator("[data-package-chat]");
    await expect(root).toBeVisible({ timeout: 60_000 });
    const composer = root.locator("textarea").first();
    await expect(composer).toBeEditable({ timeout: 60_000 });

    // A dedicated agent (never General Chat) and the plumbing model, for this message only.
    await root.getByRole("button", { name: /^Agent: / }).click();
    await page.getByText(AGENT, { exact: true }).first().click();
    await expect(root.getByRole("button", { name: new RegExp(`^Agent: ${AGENT}`) })).toBeVisible({ timeout: 15_000 });
    await root.getByRole("button", { name: /^Agent: / }).click();
    await page.getByText(/^Model: /).click();
    await page.getByText(MODEL, { exact: true }).first().click();
    await page.keyboard.press("Escape");

    const marker = `MXL_PKG_${Date.now().toString(36)}`;
    await composer.fill(`Reply with exactly this one word and nothing else: ${marker}`);
    await page.getByRole("button", { name: "Send message" }).click();

    // The conversation opens on its own address and the answer streams in.
    await expect(root.getByText(marker).nth(1)).toBeVisible({ timeout: 120_000 });
    await expect(page.getByText(/request failed|validation_error|could not start/i)).toHaveCount(0);
    await page.screenshot({ path: path.join(SHOTS, "1-answer.png") });
    const address = await page.evaluate(() => window.sessionStorage.getItem("matrx-local.package-chat.address"));
    const conversationId = address?.match(/\/chat\/([0-9a-f-]{36})/)?.[1];
    expect(conversationId, "the send opened a conversation").toBeTruthy();
    console.log(`conversation ${conversationId} agent "${AGENT}" model "${MODEL}"`);

    // One message action: copy the answer.
    const answer = root.getByText(marker).last();
    await answer.hover();
    await root.getByRole("button", { name: /^Copy message/i }).last().evaluate((el) => (el as HTMLElement).click());
    await expect
      .poll(() => page.evaluate(() => (window as unknown as { __copied: string[] }).__copied.join("\n")), { timeout: 10_000 })
      .toContain(marker);

    // History after a reload of the page.
    await page.reload();
    await closeEngineMonitor(page);
    await page.goto("/#/cloud-chat");
    await closeEngineMonitor(page);
    await expect(root).toBeVisible({ timeout: 60_000 });
    await expect(root.getByText(/^Today/i).first()).toBeVisible({ timeout: 45_000 });
    await expect(root.getByRole("button", { name: /^Options for .+/ }).first()).toBeVisible({ timeout: 45_000 });
    await page.goto(`/#/cloud-chat?conversation=${conversationId}`);
    await closeEngineMonitor(page);
    await expect(root.getByText(marker).first()).toBeVisible({ timeout: 60_000 });
    await page.screenshot({ path: path.join(SHOTS, "2-after-reload.png") });

    // Confidential: a conversation pinned to this computer. The package's composer menu draws no
    // Confidential switch today (ConfidentialToggle is only in the legacy PlusAttachMenu), so the
    // pin is set in the host's own device store, exactly where `createDeviceRouting` keeps it.
    await page.evaluate((id) => {
      window.localStorage.setItem("matrx-local.package-chat.confidential", JSON.stringify([id]));
    }, conversationId);
    await page.goto("/#/cloud-chat");
    await page.reload();
    await closeEngineMonitor(page);
    await page.goto(`/#/cloud-chat?conversation=${conversationId}`);
    await closeEngineMonitor(page);
    await expect(root.getByText(marker).first()).toBeVisible({ timeout: 60_000 });
    const second = `MXL_CONF_${Date.now().toString(36)}`;
    await root.locator("textarea").first().fill(`Reply with exactly: ${second}`);
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(page.getByText(/marked confidential|confidential/i).filter({ hasNotText: /^Confidential$/ }).first()).toBeVisible({
      timeout: 30_000,
    });
    await page.waitForTimeout(8_000);
    await expect(root.getByText(second)).toHaveCount(1); // only the composer/user text, never an echoed answer
    await page.screenshot({ path: path.join(SHOTS, "3-confidential-refused.png") });
    // Leave no pin behind: the device-local flag must not outlive the run.
    await page.evaluate(() => window.localStorage.removeItem("matrx-local.package-chat.confidential"));
    expect(errors, errors.join("\n")).toEqual([]);
  });
});
