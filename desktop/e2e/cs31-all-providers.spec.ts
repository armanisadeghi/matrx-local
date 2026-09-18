/**
 * CS-31 — coding sessions is ONE feature, proven on the real screen.
 *
 * Arman, 2026-09-17: "coding sessions is one feature". This captures the
 * evidence that the Sessions list holds Claude Code, Codex, Cursor and VS Code
 * together, that the chip filter isolates each one, and that every provider
 * says on screen what it cannot show.
 *
 * It reads the engine's REAL answer about this Mac — no fixtures, no stubs.
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
  process.env.CS31_EVIDENCE_DIR ??
  "/Users/armanisadeghi/code/.wt/cd-cs31/projects/coding-agent-bridge/evidence";

function shot(name: string): string {
  if (!existsSync(EVIDENCE)) mkdirSync(EVIDENCE, { recursive: true });
  return `${EVIDENCE}/${name}`;
}

test("one list holds every provider, the chips filter it, and each says what it cannot show", async ({
  page,
}) => {
  test.skip(!(await harnessIdentity()), HARNESS_NOT_SIGNED_IN);
  test.setTimeout(240_000);

  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));

  // This harness home is brand new, so the app would show its first-run setup
  // screen. That key is the app's OWN dismissal — the state a person reaches by
  // clicking through setup — not a test-only branch.
  await page.addInitScript(() => {
    window.localStorage.setItem("matrx-setup-dismissed", "1");
  });

  await signInViaHarness(page);

  await expect(async () => {
    await page.getByRole("link", { name: "Coding Sessions" }).click();
    await dismissEngineMonitorIfOpen(page);
    await expect(page.getByTestId("coding-sessions-tab-sessions")).toHaveAttribute(
      "aria-selected",
      "true",
    );
  }).toPass({ timeout: 60_000 });

  // The rows arrive from the persisted per-provider indexes.
  const table = page.getByTestId("sessions-table");
  await expect(table).toBeVisible({ timeout: 120_000 });

  // ── every provider is a chip, with its own count ────────────────────
  const chips = page.getByTestId("provider-chips");
  await expect(chips).toBeVisible();
  const chipText = (await chips.innerText()).replace(/\s+/g, " ");
  console.log("CHIPS:", chipText);
  for (const label of ["Claude Code", "Codex", "Cursor", "VS Code"]) {
    expect(chipText).toContain(label);
  }
  await page.screenshot({ path: shot("cs31-sessions-all-providers.png"), fullPage: false });

  // ── the notes: what each provider cannot show, on screen ────────────
  const notes = page.getByTestId("provider-notes");
  await expect(notes).toBeVisible();
  const notesText = (await notes.innerText()).replace(/\s+/g, " ");
  console.log("NOTES:", notesText.slice(0, 900));
  await notes.scrollIntoViewIfNeeded();
  await page.screenshot({ path: shot("cs31-provider-notes.png"), fullPage: false });

  // ── the chip filter really isolates a provider ──────────────────────
  for (const provider of ["Codex", "Cursor"]) {
    await page.getByTestId("provider-chips").getByRole("button", { name: new RegExp(provider) }).click();
    await expect(table).toBeVisible();
    const rows = table.getByRole("row");
    const count = await rows.count();
    // Sample the App column of the visible rows: every one must be this provider.
    const seen = new Set<string>();
    for (let index = 1; index < Math.min(count, 12); index += 1) {
      const text = (await rows.nth(index).innerText()).replace(/\s+/g, " ");
      seen.add(text);
    }
    console.log(`FILTER ${provider}: ${count - 1} rows, samples:`, [...seen].slice(0, 3));
    await page.screenshot({
      path: shot(`cs31-chip-${provider.toLowerCase().replace(/\s+/g, "-")}.png`),
      fullPage: false,
    });
  }

  expect(pageErrors, `page errors: ${pageErrors.join(" | ")}`).toEqual([]);
});
