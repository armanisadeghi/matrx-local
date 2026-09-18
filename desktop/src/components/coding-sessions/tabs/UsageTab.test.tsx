/** @vitest-environment jsdom */
/**
 * The Usage tab renders ONE table for every provider from the shared
 * UsageReport, and every provider state is words on screen, never a dead tab.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { UsageReport, UsageRow } from "@/lib/api";
import type { CodingSessionsSnapshot } from "@/lib/coding-sessions/overview-store";

const mocks = vi.hoisted(() => ({ getCodingSessionUsage: vi.fn() }));
vi.mock("@/lib/api", () => ({ engine: mocks }));
vi.mock("@ai-matrx/design-system", () => ({
  Badge: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  Button: ({ children, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) => <button {...props}>{children}</button>,
  BasicInput: (props: InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
}));

import { UsageTab, tzOffsetMinutes, usageRangeFor } from "./UsageTab";

let container: HTMLDivElement;
let root: Root;

function row(partial: Partial<UsageRow> & { key: string }): UsageRow {
  return {
    label: partial.key,
    model: null,
    project: null,
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_creation_tokens: 0,
    total_tokens: 0,
    requests: 0,
    main_requests: 0,
    main_total_tokens: 0,
    subagent_requests: 0,
    subagent_total_tokens: 0,
    cost: null,
    extra: {},
    ...partial,
  };
}

function report(partial: Partial<UsageReport> & { provider: UsageReport["provider"] }): UsageReport {
  return {
    generated_at: "2026-09-17T12:00:00+00:00",
    range: { start: "2026-09-17T07:00:00+00:00", end: "2026-09-17T19:00:00+00:00" },
    tz_offset_minutes: -420,
    source: { kind: "local_transcripts", description: "From transcripts.", complete: true, can_resume: false, pending_sessions: 0, updated_at: "2026-09-17T11:59:00+00:00", notes: [] },
    metrics: { tokens: true, requests: true, cost: true, lines: false, subagents: false },
    totals: row({ key: "total", label: "Total" }),
    by_day: [],
    by_model: [],
    by_session: [],
    by_project: [],
    cost: { available: true, unit: "usd", label: "Estimated cost (USD, list price)", reason: null, unpriced_models: [] },
    limits: { status: "unavailable", observed_at: null, reason: "No limits cached.", plan: null, windows: [] },
    ...partial,
  };
}

const snapshot = {
  overview: null,
  readiness: {
    providers: Object.fromEntries(
      ["claude_code", "codex", "cursor", "vscode"].map((provider) => [provider, { product: { installed: true }, activity: {} }]),
    ),
  },
} as unknown as CodingSessionsSnapshot;

const claude = report({
  provider: "claude_code",
  metrics: { tokens: true, requests: true, cost: true, lines: false, subagents: true },
  totals: row({ key: "total", label: "Total", input_tokens: 10, output_tokens: 20, cache_read_tokens: 30, cache_creation_tokens: 40, total_tokens: 100, requests: 3, main_requests: 1, main_total_tokens: 25, subagent_requests: 2, subagent_total_tokens: 75, cost: 1.5 }),
  by_day: [
    row({ key: "2026-09-16", label: "2026-09-16", input_tokens: 4, output_tokens: 8, cache_read_tokens: 12, cache_creation_tokens: 16, total_tokens: 40, requests: 1, main_requests: 1, main_total_tokens: 40, cost: 0.5 }),
    row({ key: "2026-09-17", label: "2026-09-17", input_tokens: 6, output_tokens: 12, cache_read_tokens: 18, cache_creation_tokens: 24, total_tokens: 60, requests: 2, subagent_requests: 2, subagent_total_tokens: 60, cost: 1.0 }),
  ],
  by_model: [row({ key: "claude-fable-5-1", label: "claude-fable-5-1", model: "claude-fable-5-1", total_tokens: 100, requests: 3, main_total_tokens: 25, main_requests: 1, subagent_total_tokens: 75, subagent_requests: 2, cost: 1.5 })],
  by_session: [row({ key: "s1", label: "Fix the usage tab", project: "matrx-local", total_tokens: 100, requests: 3, main_total_tokens: 25, main_requests: 1, subagent_total_tokens: 75, subagent_requests: 2, cost: 1.5 })],
  by_project: [row({ key: "matrx-local", label: "matrx-local", total_tokens: 100, requests: 3, main_total_tokens: 25, main_requests: 1, subagent_total_tokens: 75, subagent_requests: 2, cost: 1.5 })],
  limits: {
    status: "available",
    observed_at: "2026-09-17T11:00:00+00:00",
    reason: null,
    plan: "stripe_subscription · default_claude_max_20x",
    windows: [
      { label: "Session (5-hour)", used_percent: 4, remaining_percent: 96, window_minutes: 300, resets_at: "2026-09-17T15:00:00+00:00" },
      { label: "Weekly (all models)", used_percent: 40, remaining_percent: 60, window_minutes: 10080, resets_at: null },
    ],
  },
});

const cursor = report({
  provider: "cursor",
  source: { kind: "local_state_db", description: "Daily lines from Cursor's state database.", complete: true, can_resume: false, pending_sessions: null, updated_at: null, notes: ["Cursor keeps token and request usage on cursor.com."] },
  metrics: { tokens: false, requests: false, cost: false, lines: true, subagents: false },
  totals: row({ key: "total", label: "Total", extra: { suggested_lines: 169, accepted_lines: 131 } }),
  by_day: [row({ key: "2026-09-13", label: "2026-09-13", extra: { suggested_lines: 169, accepted_lines: 131 } })],
  cost: { available: false, unit: null, label: "Cost", reason: "Cursor keeps no token or cost record on this Mac; see cursor.com → Settings → Usage.", unpriced_models: [] },
  limits: { status: "unavailable", observed_at: null, reason: "Cursor keeps its request quotas on cursor.com; nothing on this Mac records them. Plan on this Mac: ultra.", plan: "ultra", windows: [] },
});

const vscode = report({
  provider: "vscode",
  source: { kind: "none", description: "No local usage source.", complete: true, can_resume: false, pending_sessions: null, updated_at: null, notes: ["VS Code exposes no local usage data, and the AI Matrx VS Code extension is not installed on this Mac."] },
  metrics: { tokens: false, requests: false, cost: false, lines: false, subagents: false },
  cost: { available: false, unit: null, label: "Cost", reason: "VS Code exposes no local usage data.", unpriced_models: [] },
  limits: { status: "unavailable", observed_at: null, reason: "VS Code exposes no local usage data.", plan: null, windows: [] },
});

function findButton(text: string): HTMLButtonElement {
  const button = [...container.querySelectorAll("button")].find((item) => item.textContent?.trim() === text);
  if (!button) throw new Error(`no button "${text}"`);
  return button;
}

function setInputValue(input: HTMLInputElement, value: string): void {
  const setValue = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )?.set;
  setValue?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

async function render() {
  await act(async () => {
    root.render(<UsageTab snapshot={snapshot} />);
    await Promise.resolve();
  });
}

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.setSystemTime(new Date("2026-09-17T12:00:00Z"));
  mocks.getCodingSessionUsage.mockReset().mockImplementation(async ({ provider }: { provider: UsageReport["provider"] }) => {
    if (provider === "claude_code") return claude;
    if (provider === "cursor") return cursor;
    if (provider === "vscode") return vscode;
    return report({ provider: "codex", cost: { available: true, unit: "credits", label: "Estimated standard credits", reason: null, unpriced_models: [] } });
  });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("usageRangeFor", () => {
  it("makes today run from local midnight to now and the last 7 days start six days back", () => {
    const now = new Date("2026-09-17T12:00:00Z");
    const today = usageRangeFor("today", "", "", now);
    expect(today.end).toBe(now.toISOString());
    const midnight = new Date(now);
    midnight.setHours(0, 0, 0, 0);
    expect(today.start).toBe(midnight.toISOString());
    const week = usageRangeFor("last7", "", "", now);
    expect((new Date(today.start).getTime() - new Date(week.start).getTime()) / 86_400_000).toBe(6);
    const custom = usageRangeFor("custom", "2026-09-12", "2026-09-12", now);
    expect(new Date(custom.end).getTime() - new Date(custom.start).getTime()).toBe(86_400_000);
  });

  it("hands the engine the viewer's minutes ahead of UTC", () => {
    const now = new Date("2026-09-17T12:00:00Z");
    expect(tzOffsetMinutes(now)).toBe(-now.getTimezoneOffset());
  });
});

describe("UsageTab", () => {
  it("offers every provider as a chip and asks the engine for the first one with the viewer's timezone", async () => {
    await render();
    const chips = container.querySelector("[data-testid='usage-provider-chips']")!;
    expect(chips.textContent).toContain("Claude Code");
    expect(chips.textContent).toContain("Codex");
    expect(chips.textContent).toContain("Cursor");
    expect(chips.textContent).toContain("VS Code");
    const call = mocks.getCodingSessionUsage.mock.calls[0]![0];
    expect(call.provider).toBe("claude_code");
    expect(call.tzOffsetMinutes).toBe(tzOffsetMinutes(new Date()));
    expect(call.refresh).toBe(false);
  });

  it("renders Claude Code tokens by day with a totals row, then the same grid by session", async () => {
    await render();
    const table = container.querySelector("[data-testid='usage-table']")!;
    expect(table.textContent).toContain("Cache read");
    expect(table.querySelectorAll("tbody tr").length).toBe(2);
    expect(table.querySelector("[data-testid='usage-total-row']")?.textContent).toContain("100");
    expect(table.querySelector("[data-testid='usage-total-row']")?.textContent).toContain("$1.50");
    await act(async () => {
      findButton("By session").click();
    });
    const rows = container.querySelectorAll("[data-testid='usage-table'] tbody tr");
    expect(rows.length).toBe(1);
    expect(rows[0]!.textContent).toContain("Fix the usage tab");
    expect(rows[0]!.textContent).toContain("matrx-local");
  });

  it("shows the sub-agent share as its own column, and no such column for a provider that never records one", async () => {
    await render();
    const table = container.querySelector("[data-testid='usage-table']")!;
    expect(table.textContent).toContain("Sub-agent tokens");
    // The by-day rows: one is all main, one is all sub-agent, and the column
    // says which — 0% vs 100% — instead of leaving the reader to guess.
    const rows = [...table.querySelectorAll("tbody tr")].map((item) => item.textContent ?? "");
    expect(rows[0]).toContain("0%");
    expect(rows[1]).toContain("100%");
    const total = table.querySelector("[data-testid='usage-total-row']")!;
    expect(total.textContent).toContain("75");
    expect(total.textContent).toContain("75%");
    // And the totals card names the split in words.
    expect(container.querySelector("[data-testid='usage-totals']")?.textContent).toContain("1 main · 2 sub-agent");
    // Cursor records no sub-agents, so there is no column pretending to zero.
    await act(async () => {
      findButton("Cursor").click();
      await Promise.resolve();
    });
    expect(container.querySelector("[data-testid='usage-table']")!.textContent).not.toContain("Sub-agent");
  });

  it("shows the provider's limits as windows with the plan", async () => {
    await render();
    const limits = container.querySelector("[data-testid='usage-limits']")!;
    expect(limits.textContent).toContain("Session (5-hour)");
    expect(limits.textContent).toContain("4% used");
    expect(limits.textContent).toContain("default_claude_max_20x");
    expect(limits.textContent).toContain("not a live account read");
  });

  it("labels a Codex allowance observation without calling it a provider cache", async () => {
    mocks.getCodingSessionUsage.mockImplementation(async ({ provider }: { provider: UsageReport["provider"] }) => {
      if (provider === "claude_code") return claude;
      return report({
        provider: "codex",
        limits: {
          status: "available",
          observed_at: "2026-09-17T11:00:00+00:00",
          reason: null,
          plan: null,
          windows: [
            { label: "Five-hour", used_percent: 25, remaining_percent: 75, window_minutes: 300, resets_at: null },
          ],
        },
      });
    });
    await render();
    await act(async () => {
      findButton("Codex").click();
      await Promise.resolve();
    });
    const limits = container.querySelector("[data-testid='usage-limits']")!;
    expect(limits.textContent).toContain("Account reading observed");
    expect(limits.textContent).toContain("not continuously live");
    expect(limits.textContent).not.toContain("last cached it");
    expect(limits.textContent).not.toContain("not a live account read");
  });

  it("renders Cursor as lines, says where its tokens live, and shows its plan", async () => {
    await render();
    await act(async () => {
      findButton("Cursor").click();
      await Promise.resolve();
    });
    const table = container.querySelector("[data-testid='usage-table']")!;
    expect(table.textContent).toContain("Accepted lines");
    expect(table.textContent).not.toContain("Cache read");
    expect(table.querySelector("[data-testid='usage-total-row']")?.textContent).toContain("131");
    expect(container.querySelector("[data-testid='usage-totals']")?.textContent).toContain("Not recorded");
    expect(container.querySelector("[data-testid='usage-limits-unavailable']")?.textContent).toContain("cursor.com");
    expect(container.querySelector("[data-testid='usage-limits']")?.textContent).toContain("ultra");
  });

  it("says exactly what VS Code is missing instead of an empty grid", async () => {
    await render();
    await act(async () => {
      findButton("VS Code").click();
      await Promise.resolve();
    });
    expect(container.querySelector("[data-testid='usage-table']")).toBeNull();
    expect(container.querySelector("[data-testid='usage-no-source']")?.textContent).toContain("extension is not installed");
    expect(container.querySelector("[data-testid='usage-totals']")?.textContent).toContain("Nothing");
  });

  it("shows a read failure with the remedy and keeps the previous answer on screen", async () => {
    await render();
    mocks.getCodingSessionUsage.mockRejectedValueOnce(new Error("engine timeout"));
    await act(async () => {
      findButton("Refresh").click();
      await Promise.resolve();
    });
    const alert = container.querySelector("[data-testid='usage-error']")!;
    expect(alert.textContent).toContain("Couldn't read Claude Code usage");
    expect(alert.textContent).toContain("engine timeout");
    expect(alert.textContent).toContain("last answer this Mac read");
    expect(container.querySelector("[data-testid='usage-table']")).not.toBeNull();
    expect(mocks.getCodingSessionUsage.mock.calls[mocks.getCodingSessionUsage.mock.calls.length - 1]![0].refresh).toBe(true);
  });

  it("replaces rapid provider and range changes with one newest read after an active failure", async () => {
    let rejectActive: (cause: Error) => void = () => {};
    let resolveQueued: (value: UsageReport) => void = () => {};
    mocks.getCodingSessionUsage.mockImplementationOnce(
      () =>
        new Promise<UsageReport>((_resolve, reject) => {
          rejectActive = reject;
        }),
    );
    mocks.getCodingSessionUsage.mockImplementationOnce(
      () =>
        new Promise<UsageReport>((resolve) => {
          resolveQueued = resolve;
        }),
    );
    await render();

    await act(async () => {
      findButton("Custom").click();
    });
    await act(async () => {
      const [startDate] = container.querySelectorAll<HTMLInputElement>("input[type='date']");
      setInputValue(startDate!, "2026-09-12");
    });
    await act(async () => {
      const [, endDate] = container.querySelectorAll<HTMLInputElement>("input[type='date']");
      setInputValue(endDate!, "2026-09-14");
    });
    await act(async () => {
      findButton("Codex").click();
    });

    expect(mocks.getCodingSessionUsage).toHaveBeenCalledTimes(1);
    await act(async () => {
      rejectActive(new Error("first read failed"));
      await Promise.resolve();
    });
    expect(mocks.getCodingSessionUsage).toHaveBeenCalledTimes(2);
    const latestCall = mocks.getCodingSessionUsage.mock.calls[1]![0];
    expect(latestCall.provider).toBe("codex");
    expect(latestCall.start).toContain("2026-09-12");
    expect(latestCall.end).toContain("2026-09-15");
    await act(async () => {
      resolveQueued(report({ provider: "codex", totals: row({ key: "total", label: "latest", total_tokens: 90 }) }));
      await Promise.resolve();
    });
    expect(container.querySelector("[data-testid='usage-error']")).toBeNull();
    expect(container.querySelector("[data-testid='usage-totals']")?.textContent).toContain("90");
  });

  it("shows the loading state before the first answer", async () => {
    let resolve: (value: UsageReport) => void = () => {};
    mocks.getCodingSessionUsage.mockReturnValueOnce(new Promise<UsageReport>((done) => { resolve = done; }));
    await render();
    expect(container.querySelector("[data-testid='usage-loading']")?.textContent).toContain("Reading this Mac's Claude Code usage");
    await act(async () => {
      resolve(claude);
      await Promise.resolve();
    });
    expect(container.querySelector("[data-testid='usage-loading']")).toBeNull();
  });
});
