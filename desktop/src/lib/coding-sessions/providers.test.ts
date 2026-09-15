import { describe, expect, it } from "vitest";

import {
  filterRowsByProvider,
  listedProviders,
  providerChips,
  unlistedProviderNote,
} from "@/lib/coding-sessions/providers";
import type {
  ClaudeConversation,
  ClaudeOverview,
  CodingSessionProvider,
  CodingSessionProviderReadinessStatus,
} from "@/lib/api";

function row(provider?: CodingSessionProvider): ClaudeConversation {
  return { session_id: Math.random().toString(), ...(provider ? { provider } : {}) } as unknown as ClaudeConversation;
}

function overview(
  conversations: ClaudeConversation[],
  listed?: CodingSessionProvider[],
): ClaudeOverview {
  return {
    conversations,
    ...(listed ? { listed_providers: listed } : {}),
  } as unknown as ClaudeOverview;
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

  it("grows on its own when the engine starts listing another provider", () => {
    const chips = providerChips(
      overview([row("claude_code"), row("codex"), row("codex")], ["claude_code", "codex"]),
      readiness({ claude_code: true, codex: true }),
    );
    expect(chips[0]).toMatchObject({ provider: "codex", count: 2, listed: true });
    expect(chips[1]).toMatchObject({ provider: "claude_code", count: 1, listed: true });
  });

  it("treats an older engine's unlabelled rows as its single listed provider", () => {
    const payload = overview([row(), row()], ["claude_code"]);
    expect(listedProviders(payload)).toEqual(["claude_code"]);
    expect(filterRowsByProvider(payload.conversations, payload, "claude_code")).toHaveLength(2);
    expect(filterRowsByProvider(payload.conversations, payload, "codex")).toHaveLength(0);
  });

  it("filters rows by the chip that was picked", () => {
    const payload = overview([row("claude_code"), row("codex")], ["claude_code", "codex"]);
    expect(filterRowsByProvider(payload.conversations, payload, null)).toHaveLength(2);
    expect(filterRowsByProvider(payload.conversations, payload, "codex")).toHaveLength(1);
  });

  it("never leaves an empty chip without a reason", () => {
    const note = unlistedProviderNote({
      provider: "cursor",
      label: "Cursor",
      count: 0,
      listed: false,
      installed: true,
    });
    expect(note).toContain("does not list Cursor sessions yet");
    expect(note).toContain("Settings & diagnostics");
  });
});
