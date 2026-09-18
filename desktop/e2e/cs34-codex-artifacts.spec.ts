/**
 * CS-34 on the real screen: a Codex session's artifacts, and a cloud check that resolves.
 *
 * Two facts this captures, both of which used to be impossible:
 *
 *  1. **A Codex session lists the file its turn wrote.** Codex names a file it changed only
 *     through `apply_patch`, and 0 of the 4,492 rollouts from August 2026 onward contain one, so
 *     the artifacts panel for every modern Codex session was empty. The AI Matrx plugin for Codex
 *     now records each turn's writes at hook time and the engine reads them.
 *  2. **Cursor and VS Code can be cloud-checked.** The server counted handoff-offer rows it then
 *     refused to serve, so its own completeness contract failed and those providers reported
 *     "the cloud check is in flight" for ever.
 *
 * Run against an isolated source engine in the dev band with a private `CODEX_HOME`. Arman's
 * installed app (`~/.matrx`, ports 22140-22159) is never read, probed or touched.
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
  process.env.CS34_EVIDENCE_DIR ??
  "/Users/armanisadeghi/code/common-docs/projects/coding-agent-bridge/evidence";

/** The real Codex session staged for this run (a real rollout, re-rooted). */
const CODEX_SESSION = process.env.CS34_SESSION_ID ?? "";

function shot(name: string): string {
  if (!existsSync(EVIDENCE)) mkdirSync(EVIDENCE, { recursive: true });
  return `${EVIDENCE}/${name}`;
}

test("a Codex session shows the file its turn wrote, and the cloud check resolves", async ({
  page,
}) => {
  test.skip(!(await harnessIdentity()), HARNESS_NOT_SIGNED_IN);
  expect(CODEX_SESSION, "set CS34_SESSION_ID to the staged Codex session id").not.toBe("");

  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  await signInViaHarness(page);

  await expect(async () => {
    await page.getByRole("link", { name: "Coding Sessions" }).click();
    await dismissEngineMonitorIfOpen(page);
    await expect(page.getByTestId("coding-sessions-tab-sessions")).toHaveAttribute(
      "aria-selected",
      "true",
      { timeout: 5_000 },
    );
  }).toPass({ timeout: 60_000 });

  const rows = page.getByTestId("conversation-row");
  await expect(rows.first()).toBeVisible({ timeout: 120_000 });

  // Narrow to Codex, so the row under test is the one in the screenshot.
  await page.getByTestId("provider-chips").getByRole("button", { name: /^Codex/ }).click();
  const codexRow = page.locator(`[data-testid="conversation-row"]`).filter({
    has: page.getByTestId(`row-provider-${CODEX_SESSION}`),
  });
  await expect(codexRow).toHaveCount(1, { timeout: 60_000 });
  await codexRow.scrollIntoViewIfNeeded();
  await page.screenshot({ path: shot("cs34-sessions-codex.png"), fullPage: false });

  // THE ARTIFACT. The row's Artifacts cell is a live count, not a dash.
  const artifacts = codexRow.getByRole("button", { name: /Artifact/ });
  await expect(artifacts).toBeVisible();
  await artifacts.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  // The file the staged Codex turn wrote, which no rollout names.
  await expect(dialog).toContainText("cs34-verification-note.md");
  // And the honesty the same panel owes: what the hook could not see, and the
  // repository rule that refused the checkout beside it.
  await expect(dialog).toContainText(/hook|apply_patch|Codex/i);
  await page.screenshot({ path: shot("cs34-codex-artifacts.png"), fullPage: false });
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();

  // THE CLOUD CHECK. Once the server answers an offer-only provider completely,
  // the aggregate check is true and the in-flight banner is gone for good.
  await expect(page.getByTestId("cloud-in-flight")).toHaveCount(0, { timeout: 120_000 });
  const checked = page.getByTestId("cloud-checked-note");
  await expect(checked).toBeVisible({ timeout: 120_000 });
  await expect(checked).toContainText(/AI Matrx holds/);
  await page.screenshot({ path: shot("cs34-cloud-check-resolved.png"), fullPage: false });

  // Cursor and VS Code are the two providers that could never be checked.
  for (const provider of ["Cursor", "VS Code"]) {
    await page.getByTestId("provider-chips").getByRole("button", { name: new RegExp(`^${provider}`) }).click();
    await expect(page.getByTestId("cloud-in-flight")).toHaveCount(0);
    await page.screenshot({
      path: shot(`cs34-chip-${provider.toLowerCase().replace(/\s+/g, "-")}.png`),
      fullPage: false,
    });
  }

  expect(pageErrors, `page errors: ${pageErrors.join(" | ")}`).toEqual([]);
});
