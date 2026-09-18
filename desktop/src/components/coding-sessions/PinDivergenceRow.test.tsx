/** @vitest-environment jsdom */
/**
 * The pin row says one honest sentence in every state it can be in.
 *
 * This is the guard for the defect it was built for: on 2026-09-18 Claude Code
 * held 218 pins, AI Matrx held 160 favourites, 58 of them were not pinned
 * here, and no screen said a word. The four cases below are the four ways this
 * row can be wrong:
 *
 *  1. nothing measured  → the REASON, and no numbers at all (never "0")
 *  2. measured, equal   → says plainly that they agree
 *  3. measured, 58 out  → the real numbers, with the age of the check
 *  4. pass failed       → says the check did not finish before it says numbers
 *
 * Each assertion is the exact sentence a person reads, not a substring, so a
 * reworded line has to be re-read by a human before it ships.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ClaudePinDivergence } from "@/lib/api";

import { PinDivergenceRow } from "./PinDivergenceRow";

/** Fixed clock: "3m ago" must be the same sentence on every machine. */
const NOW = Date.parse("2026-09-18T12:00:00Z");
const THREE_MINUTES_AGO = "2026-09-18T11:57:00+00:00";

function divergence(partial: Partial<ClaudePinDivergence> = {}): ClaudePinDivergence {
  return {
    checked: true,
    reason: null,
    local: 218,
    ai_matrx: 160,
    to_pin: 116,
    to_unpin: 58,
    to_reconcile: 58,
    last_pass_at: THREE_MINUTES_AGO,
    last_pass_status: "completed",
    compared_sessions: 2033,
    ...partial,
  };
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(value: ClaudePinDivergence | null | undefined): string | null {
  act(() => root.render(<PinDivergenceRow divergence={value} now={NOW} />));
  const row = container.querySelector('[data-testid="pin-divergence"]');
  return row ? row.textContent : null;
}

describe("PinDivergenceRow", () => {
  it("states the reason and NO numbers when no pass has ever finished", () => {
    const text = render(
      divergence({
        checked: false,
        reason:
          "no reconcile pass has finished on this Mac yet, so the difference between this Mac's pins and AI Matrx's is not known",
        local: null,
        ai_matrx: null,
        to_pin: null,
        to_unpin: null,
        to_reconcile: null,
        last_pass_at: null,
        last_pass_status: null,
      }),
    );
    expect(text).toBe(
      "Pins: not compared yet — no reconcile pass has finished on this Mac yet, so the difference between this Mac's pins and AI Matrx's is not known.",
    );
    // The lie this row exists to end.
    expect(text).not.toContain("0");
  });

  it("says plainly that they agree when nothing is left to reconcile", () => {
    const text = render(
      divergence({ ai_matrx: 218, to_pin: 0, to_unpin: 0, to_reconcile: 0 }),
    );
    expect(text).toBe(
      "Pins: 218 on this Mac · 218 in AI Matrx · they agree · last checked 3m ago",
    );
  });

  it("states the real divergence and the age of the check", () => {
    expect(render(divergence())).toBe(
      "Pins: 218 on this Mac · 160 in AI Matrx · 58 still to reconcile · last checked 3m ago",
    );
  });

  it("says the last pass did not finish cleanly before presenting its numbers", () => {
    expect(render(divergence({ last_pass_status: "failed" }))).toBe(
      "Pins: the last check did not finish cleanly, so these numbers are incomplete · 218 on this Mac · 160 in AI Matrx · 58 still to reconcile so far · last tried 3m ago",
    );
  });

  it("renders nothing at all when the engine says nothing about pins", () => {
    expect(render(undefined)).toBeNull();
  });
});
