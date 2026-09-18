import { describe, expect, it } from "vitest";

import { diagnosisView } from "@/lib/coding-sessions/diagnosis-view";
import type {
  ClaudeSessionDiagnosis,
  CodingSessionProviderDiagnosis,
} from "@/lib/api";

function claudePayload(): ClaudeSessionDiagnosis {
  return {
    schema_version: 1,
    session_id: "session-1",
    state: "in_cloud",
    verdict: { summary: "In AI Matrx.", remedy: null },
    index: {
      in_claude_sidebar: true,
      title: "A Claude session",
      title_source: "claude",
      project: "/Users/someone/code",
      git_branch: null,
      worktree_name: null,
      pinned: true,
      pinned_rank: 1,
      category: null,
      archived: false,
      last_activity_at: 0,
      record_count: 3,
      accounts: ["acct"],
    },
    transcript: { on_disk: true, bytes: 2_048, modified_at: null },
    cloud: {
      checked: true,
      reason: null,
      detail: null,
      sessions: 1_671,
      checked_at: "",
      binding: {
        provider_session_id: "session-1",
        conversation_id: "conv-1",
        fidelity: "native",
        last_seen_at: null,
        conversation_title: "A Claude session",
        title_source: "claude",
      },
    },
    delivery: { publisher_blocker: null, envelopes: [], delivered_by_this_mac_at: null },
    capture: [],
    labels: { metadata_sent: null, title_pushed: null },
  };
}

function cursorPayload(): CodingSessionProviderDiagnosis {
  return {
    schema_version: 3,
    provider: "cursor",
    session_id: "cursor-1",
    state: "in_cloud",
    verdict: { summary: "AI Matrx holds this chat.", remedy: null },
    continuation: {
      command: null,
      note: "Cursor has no command or link that reopens one chat.",
      native_resume: false,
    },
    local: {
      title: "A Cursor chat",
      title_source: "cursor",
      project: "/Users/someone/code",
      last_activity_at: 0,
      bytes: null,
      on_disk: true,
      archived: false,
      facts: { entries: null, workspace_id: "ws-1" },
    },
    local_note: "Cursor does not expose a size or a message count for a chat.",
    cloud: {
      meta: { checked: true, reason: null, detail: null, sessions: 4, checked_at: "" },
      binding: {
        provider_session_id: "cursor-1",
        conversation_id: "conv-9",
        fidelity: "mirrored",
        last_seen_at: null,
        conversation_title: "A Cursor chat",
        title_source: "cursor",
      },
    },
    delivery: {
      meta: { checked: true, reason: null, detail: null },
      envelopes: [],
      queue: { pending: 0, quarantined: 0 },
      publisher_blocker: null,
    },
    capture: { meta: { checked: true, reason: null, detail: null }, attempts: [] },
  } as unknown as CodingSessionProviderDiagnosis;
}

describe("one dialog, both diagnosis shapes", () => {
  it("reads the Claude payload as Claude Code, with its sidebar and label ledgers", () => {
    const view = diagnosisView(claudePayload());
    expect(view.provider).toBe("claude_code");
    expect(view.label).toBe("Claude Code");
    expect(view.sidebar).not.toBeNull();
    expect(view.labels).not.toBeNull();
    expect(view.notApplicable).toEqual([]);
    expect(view.cloud.boundCountSentence).toContain("Claude Code sessions are bound there");
    expect(view.delivery.neverDeliveredSentence).toContain("Claude Code");
  });

  it("names the row's real provider instead of writing Claude over it", () => {
    const view = diagnosisView(cursorPayload());
    expect(view.provider).toBe("cursor");
    expect(view.label).toBe("Cursor");
    expect(view.cloud.boundCountSentence).toContain("Cursor sessions are bound there");
    expect(view.cloud.boundCountSentence).not.toContain("Claude");
    expect(view.delivery.neverDeliveredSentence).not.toContain("Claude Code itself");
    expect(view.title).toBe("A Cursor chat");
  });

  it("says plainly which sections this provider has none of, rather than showing empty boxes", () => {
    const view = diagnosisView(cursorPayload());
    expect(view.sidebar).toBeNull();
    expect(view.labels).toBeNull();
    const titles = view.notApplicable.map((item) => item.title);
    expect(titles).toContain("Claude's sidebar record");
    expect(titles).toContain("Label sync ledgers");
    for (const item of view.notApplicable) {
      expect(item.sentence).toContain("Cursor");
      expect(item.sentence.length).toBeGreaterThan(20);
    }
  });

  it("keeps a missing size missing instead of calling it zero", () => {
    const view = diagnosisView(cursorPayload());
    expect(view.transcript?.bytes).toBeNull();
    expect(view.localNote).toContain("does not expose a size");
  });

  it("carries the provider's own facts through for display", () => {
    const view = diagnosisView(cursorPayload());
    expect(view.facts.map(([key]) => key)).toContain("workspace_id");
  });

  it("says a provider keeps no local record here when it has none", () => {
    const payload = cursorPayload();
    const view = diagnosisView({
      ...payload,
      provider: "vscode",
      local: null,
      local_note: "VS Code is on this Mac but the AI Matrx extension is not installed in it.",
    });
    expect(view.transcript).toBeNull();
    expect(view.notApplicable.map((item) => item.title)).toContain("Record on this Mac");
    expect(view.localNote).toContain("extension is not installed");
  });
});
