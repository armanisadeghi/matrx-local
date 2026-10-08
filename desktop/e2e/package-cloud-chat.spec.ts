/**
 * Real-use proof for Cloud Chat on `@ai-matrx/chat` (L1, opt-in `/cloud-chat?chat=package`), signed
 * in as the harness account (admin@admin.com — see e2e/setup/harness-session.mjs), headless: the
 * package chat renders, the composer is ready, history loads, and a past conversation opens by its
 * address. No message is sent (no AI spend). Screenshots: test-results/package-cloud-chat/.
 *
 *   eval "$(node e2e/setup/harness-session.mjs --export)"
 *   SMOKE_PORT=1437 pnpm exec playwright test package-cloud-chat.spec.ts
 */
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { type Page } from "@playwright/test";
import { HARNESS_NOT_SIGNED_IN, harnessIdentity, signInViaHarness } from "./helpers";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SHOTS = path.join(__dirname, "..", "test-results", "package-cloud-chat");

function readEnv(file: string): Record<string, string> {
  if (!existsSync(file)) return {};
  const out: Record<string, string> = {};
  for (const line of readFileSync(file, "utf8").split("\n")) {
    const m = /^\s*([A-Za-z0-9_]+)\s*=\s*(.*)\s*$/.exec(line);
    if (m?.[1] && m[2] !== undefined) out[m[1]] = m[2].trim().replace(/^(['"])(.*)\1$/, "$2");
  }
  return out;
}

/** The newest conversation with an answer, read as the harness account (password grant, never printed). */
/** The Engine Monitor opens on its own when no source engine runs (this proof needs none); close it by its button. */
async function closeEngineMonitor(page: Page): Promise<void> {
  const dialog = page.getByRole("dialog").filter({ hasText: "Engine Monitor" });
  if (await dialog.isVisible({ timeout: 3_000 }).catch(() => false)) {
    await dialog.getByRole("button", { name: /close/i }).first().click();
    await dialog.waitFor({ state: "hidden", timeout: 5_000 }).catch(() => undefined);
  }
}

async function newestAnsweredConversation(): Promise<string | null> {
  const desktop = readEnv(path.join(__dirname, "..", ".env"));
  const aidream = readEnv(path.resolve(__dirname, "..", "..", "..", "aidream", ".env"));
  const url = desktop.VITE_SUPABASE_URL;
  const key = desktop.VITE_SUPABASE_PUBLISHABLE_DEFAULT_KEY;
  if (!url || !key || !aidream.AI_ADMIN_USERNAME || !aidream.AI_ADMIN_PASSWORD) return null;
  const grant = await fetch(`${url}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: { "Content-Type": "application/json", apikey: key },
    body: JSON.stringify({ email: aidream.AI_ADMIN_USERNAME, password: aidream.AI_ADMIN_PASSWORD }),
  });
  if (!grant.ok) return null;
  const { access_token: token } = (await grant.json()) as { access_token: string };
  const rows = await fetch(
    `${url}/rest/v1/message?select=conversation_id&role=eq.assistant&order=created_at.desc&limit=1`,
    { headers: { apikey: key, Authorization: `Bearer ${token}`, "Accept-Profile": "chat" } },
  ).then((r) => (r.ok ? (r.json() as Promise<{ conversation_id: string }[]>) : []));
  return rows[0]?.conversation_id ?? null;
}

test.describe("Cloud Chat on the chat package (opt-in)", () => {
  test("signed in: renders, composer ready, history, a past conversation opens", async ({ page }) => {
    test.setTimeout(240_000);
    test.skip(!(await harnessIdentity()), HARNESS_NOT_SIGNED_IN);
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(String(e)));

    await signInViaHarness(page);
    await page.goto("/#/cloud-chat?chat=package");
    await closeEngineMonitor(page);
    const root = page.locator("[data-package-chat]");
    await expect(root).toBeVisible({ timeout: 60_000 });
    const composer = root.locator("textarea").first();
    await expect(composer).toBeEditable({ timeout: 60_000 });
    await expect(page.getByText(/Sign in to|signed out/i)).toHaveCount(0);
    await page.screenshot({ path: path.join(SHOTS, "1-new-chat.png") });

    await expect(root.getByText(/^Archived/).first()).toBeVisible({ timeout: 45_000 });
    const standIns = await root
      .locator("[data-chat-slot-fallback]")
      .evaluateAll((els) => [...new Set(els.map((e) => e.getAttribute("data-chat-slot-fallback")))]);
    console.log(`history slot stand-ins: ${standIns.join(", ") || "none"}`);
    await page.screenshot({ path: path.join(SHOTS, "2-history.png") });

    const pastId = await newestAnsweredConversation();
    expect(pastId, "a past conversation exists for the harness account").toBeTruthy();
    await page.goto(`/#/cloud-chat?chat=package&conversation=${pastId}`);
    await closeEngineMonitor(page);
    await expect(root).toBeVisible({ timeout: 60_000 });
    await expect
      .poll(async () => (await root.innerText().catch(() => "")).trim().length, { timeout: 60_000 })
      .toBeGreaterThan(200);
    const roomStandIns = await root
      .locator("[data-chat-slot-fallback]")
      .evaluateAll((els) => [...new Set(els.map((e) => e.getAttribute("data-chat-slot-fallback")))]);
    console.log(`conversation ${pastId}: ${(await root.innerText()).length} chars; stand-ins: ${roomStandIns.join(", ") || "none"}`);
    await page.screenshot({ path: path.join(SHOTS, "3-conversation.png") });
    expect(errors, errors.join("\n")).toEqual([]);
  });
});
