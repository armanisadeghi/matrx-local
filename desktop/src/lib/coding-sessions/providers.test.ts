import { describe, expect, it } from "vitest";

import {
  filterRowsByProvider,
  listedProviders,
  noSizeSentence,
  providerBlock,
  providerChips,
  providerNote,
  rowSupportsPins,
  unlistedProviderNote,
} from "@/lib/coding-sessions/providers";
import type {
  CodingSessionProvider,
  CodingSessionProviderBlock,
  CodingSessionProviderReadinessStatus,
  CodingSessionRow,
  CodingSessionsOverview,
} from "@/lib/api";

function row(provider?: CodingSessionProvider): CodingSessionRow {
  return { session_id: Math.random().toString(), ...(provider ? { provider } : {}) } as unknown as CodingSessionRow;
}

function block(
  provider: CodingSessionProvider,
  overrides: Partial<CodingSessionProviderBlock> = {},
): CodingSessionProviderBlock {
  return {
    provider,
    index: { state: "fresh", refreshing: false, files_read: 0, updated_at: null, changed_files: null, duration_seconds: null, limit_reached: false, unreadable: 0, error: null },
    cloud: { checked: true, reason: null, detail: null, sessions: 0, checked_at: "" },
    totals: { sessions: 0 },
    note: null,
    supports_pins: provider === "claude_code",
    supports_resume: provider === "claude_code" || provider === "codex",
    lists_locally: provider !== "vscode",
    continuation: { command: null, note: "no note", native_resume: false },
    ...overrides,
  } as CodingSessionProviderBlock;
}

function overview(
  conversations: CodingSessionRow[],
  listed?: CodingSessionProvider[],
  providers?: CodingSessionProviderBlock[],
): CodingSessionsOverview {
  return {
    conversations,
    ...(listed ? { listed_providers: listed } : {}),
    ...(providers ? { providers } : {}),
  } as unknown as CodingSessionsOverview;
}

function readiness(
  providers: Partial<Record<CodingSessionProvider, boolean | null>>,
): CodingSessionProviderReadinessStatus {
  return {
    providers: Object.fromEntries(
      Object.entries(providers).map(([provider, installed]) => [
        provider,
        { product: { installed } },
      ]),
    ),
  } as unknown as CodingSessionProviderReadinessStatus;
}

describe("provider filter", () => {
  it("makes a chip for every provider the engine knows, not just the listed one", () => {
    const chips = providerChips(
      overview([row("claude_code"), row("claude_code")], ["claude_code"]),
      readiness({ claude_code: true, codex: true, cursor: false, vscode: null }),
    );
    expect(chips.map((chip) => chip.provider).sort()).toEqual([
      "claude_code",
      "codex",
      "cursor",
      "vscode",
    ]);
    expect(chips[0]).toMatchObject({ provider: "claude_code", count: 2, listed: true });
    expect(chips.find((chip) => chip.provider === "codex")).toMatchObject({
      count: 0,
      listed: false,
    });
  });

  it("lists all four providers with their own counts when the engine lists all four", () => {
    const chips = providerChips(
      overview(
        [
          row("claude_code"),
          row("codex"),
          row("codex"),
          row("cursor"),
          row("vscode"),
          row("vscode"),
          row("vscode"),
        ],
        ["claude_code", "codex", "cursor", "vscode"],
        [block("claude_code"), block("codex"), block("cursor"), block("vscode")],
      ),
      readiness({ claude_code: true, codex: true, cursor: true, vscode: true }),
    );
    // The engine's own screen order, never a count-ranked order that reshuffles
    // under a person's cursor between two reads.
    expect(chips.map((chip) => chip.provider)).toEqual([
      "claude_code",
      "codex",
      "cursor",
      "vscode",
    ]);
    expect(chips.map((chip) => chip.count)).toEqual([1, 2, 1, 3]);
    expect(chips.every((chip) => chip.listed)).toBe(true);
  });

  it("treats an older engine's unlabelled rows as its single listed provider", () => {
    const payload = overview([row(), row()], ["claude_code"]);
    expect(listedProviders(payload)).toEqual(["claude_code"]);
    expect(filterRowsByProvider(payload.conversations, payload, "claude_code")).toHaveLength(2);
    expect(filterRowsByProvider(payload.conversations, payload, "codex")).toHaveLength(0);
  });

  it("isolates each provider's rows when its chip is picked", () => {
    const payload = overview(
      [row("claude_code"), row("codex"), row("cursor"), row("vscode")],
      ["claude_code", "codex", "cursor", "vscode"],
    );
    expect(filterRowsByProvider(payload.conversations, payload, null)).toHaveLength(4);
    for (const provider of ["claude_code", "codex", "cursor", "vscode"] as const) {
      const only = filterRowsByProvider(payload.conversations, payload, provider);
      expect(only).toHaveLength(1);
      expect(only[0]?.provider).toBe(provider);
    }
  });

  it("never leaves an empty chip without a reason", () => {
    const note = unlistedProviderNote({
      provider: "cursor",
      label: "Cursor",
      count: 0,
      listed: false,
      installed: true,
      note: null,
      listsLocally: true,
      supportsPins: false,
      supportsResume: false,
    });
    expect(note).toContain("does not list Cursor sessions yet");
    expect(note).toContain("Settings & diagnostics");
  });
});

describe("what each provider cannot show", () => {
  const cursorNote =
    "Cursor does not expose a size or a message count for a chat: its chats are rows in a shared database.";

  it("carries the engine's own sentence onto the provider's chip", () => {
    const payload = overview(
      [row("cursor")],
      ["claude_code", "cursor"],
      [block("claude_code"), block("cursor", { note: cursorNote })],
    );
    const chips = providerChips(payload, readiness({ claude_code: true, cursor: true }));
    const cursor = chips.find((chip) => chip.provider === "cursor");
    expect(cursor?.note).toBe(cursorNote);
    expect(providerNote(cursor!)).toBe(cursorNote);
  });

  it("falls back to the unlisted reason only when the engine said nothing", () => {
    const payload = overview([], ["claude_code"], [block("claude_code")]);
    const chips = providerChips(payload, readiness({ claude_code: true, vscode: true }));
    const vscode = chips.find((chip) => chip.provider === "vscode");
    expect(vscode?.note).toBeNull();
    expect(providerNote(vscode!)).toContain("does not list VS Code sessions yet");
  });

  it("reports each provider's pin and resume capability from its own block", () => {
    const payload = overview(
      [],
      ["claude_code", "codex", "cursor", "vscode"],
      [block("claude_code"), block("codex"), block("cursor"), block("vscode")],
    );
    expect(providerBlock(payload, "cursor")?.supports_pins).toBe(false);
    expect(providerBlock(payload, "claude_code")?.supports_pins).toBe(true);
    const chips = providerChips(payload, readiness({}));
    expect(chips.find((chip) => chip.provider === "codex")).toMatchObject({
      supportsPins: false,
      supportsResume: true,
    });
    expect(chips.find((chip) => chip.provider === "vscode")).toMatchObject({
      supportsResume: false,
      listsLocally: false,
    });
  });

  it("knows a row has no pin concept at all, which is not 'not pinned'", () => {
    const payload = overview(
      [],
      ["claude_code", "cursor"],
      [block("claude_code"), block("cursor")],
    );
    expect(rowSupportsPins({ provider: "claude_code" } as CodingSessionRow, payload)).toBe(true);
    expect(rowSupportsPins({ provider: "cursor" } as CodingSessionRow, payload)).toBe(false);
  });

  it("says why a size is missing instead of printing a zero", () => {
    expect(noSizeSentence("Cursor")).toContain("Cursor");
    expect(noSizeSentence("Cursor")).not.toContain("0 B");
  });
});
