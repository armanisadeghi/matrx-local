/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ getCodexUsage: vi.fn(), getCodexAllowance: vi.fn() }));
vi.mock("@/lib/api", () => ({ engine: mocks }));
vi.mock("@ai-matrx/design-system", () => ({ Badge: ({ children }: { children: ReactNode }) => <span>{children}</span>, Button: ({ children, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) => <button {...props}>{children}</button>, BasicInput: (props: InputHTMLAttributes<HTMLInputElement>) => <input {...props} /> }));

import { codexUsageRangeFor } from "./CodexUsagePanel";
import { CodexUsagePanel } from "./CodexUsagePanel";

let container: HTMLDivElement;
let root: Root;
const snapshot = { collected_at: "2026-09-13T12:00:00Z", range: { start: "2026-09-13T00:00:00Z", end: "2026-09-13T12:00:00Z" }, collection: { state: "refreshed", in_progress: false }, coverage: { complete: true, scan_exhausted: true, index_available: true, scanned_files: 0, indexed_files: 0, successfully_read_candidates: 0, completed_candidates: 0, total_candidates: 0, can_resume: false, notes: [] }, totals: { total_tokens: 0, response_count: 0 }, credits: { estimated_standard: 0, measured_allowance: null, label: "", unknown_models: [] }, models: [], model_effort: [], projects: [], cells: [], conversations: [], workers: [], qualification: [], activity: { classification: "", outbound_peer_calls: 0, collaboration_message_calls: 0, child_calls: 0, inbound_peer_wakes: "unknown", causal_cost: "unknown" } };

beforeEach(() => { (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true; vi.setSystemTime(new Date("2026-09-13T12:00:00Z")); mocks.getCodexUsage.mockReset().mockResolvedValue(snapshot); mocks.getCodexAllowance.mockReset().mockResolvedValue({ status: "unavailable", observed_at: "", limits: [] }); container = document.createElement("div"); document.body.append(container); root = createRoot(container); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.useRealTimers(); });

describe("codexUsageRangeFor", () => {
  it("makes a same-day custom range end at the following local midnight", () => {
    const range = codexUsageRangeFor("custom", "2026-09-12", "2026-09-12", new Date("2026-09-13T08:00:00Z"));
    expect(new Date(range.end).getTime() - new Date(range.start).getTime()).toBe(86_400_000);
  });

  it("keeps the last twelve hours relative to refresh time", () => {
    const now = new Date("2026-09-13T12:00:00Z");
    const range = codexUsageRangeFor("last12h", "", "", now);
    expect(range.end).toBe(now.toISOString());
    expect(new Date(range.end).getTime() - new Date(range.start).getTime()).toBe(43_200_000);
  });

  it("recomputes the last-twelve-hour request in the refresh handler", async () => {
    await act(async () => { root.render(<CodexUsagePanel />); await Promise.resolve(); });
    const last12h = [...container.querySelectorAll("button")].find(button => button.textContent === "Last 12h")!;
    await act(async () => { last12h.click(); await Promise.resolve(); });
    vi.setSystemTime(new Date("2026-09-13T14:00:00Z"));
    const refresh = [...container.querySelectorAll("button")].find(button => button.textContent?.includes("Refresh now"))!;
    await act(async () => { refresh.click(); await Promise.resolve(); });
    const latest = mocks.getCodexUsage.mock.calls[mocks.getCodexUsage.mock.calls.length - 1]![0];
    expect(latest.end).toBe("2026-09-13T14:00:00.000Z");
    expect(latest.start).toBe("2026-09-13T02:00:00.000Z");
    expect(mocks.getCodexAllowance).toHaveBeenLastCalledWith(true);
  });

  it("selects a model scope from the interactive model table", async () => {
    const row = { model: "gpt-5.6-terra", total_tokens: 10, response_count: 1, estimated_standard_credits: 2, input_tokens: 0, cached_input_tokens: 0, uncached_input_tokens: 0, output_tokens: 0, reasoning_output_tokens: 0 };
    mocks.getCodexUsage.mockResolvedValue({ ...snapshot, credits: { ...snapshot.credits, estimated_standard: 2 }, models: [row], cells: [row] });
    await act(async () => { root.render(<CodexUsagePanel />); await Promise.resolve(); });
    const model = [...container.querySelectorAll("button")].find(button => button.textContent === "gpt-5.6-terra")!;
    await act(async () => { model.click(); });
    expect(container.textContent).toContain("model: gpt-5.6-terra");
    expect(container.textContent).toContain("Clear model scope");
  });
});
