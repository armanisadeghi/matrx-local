import { describe, expect, it } from "vitest";

import type { ClaudeHistoryPreview, ClaudeHistorySessionPreview } from "@/lib/api";
import { classifyHistoryChange, historyChangeCounts } from "./HistoryInventoryTable";

function session(
  sessionId: string,
  revision: string,
  overrides: Partial<ClaudeHistorySessionPreview> = {},
): ClaudeHistorySessionPreview {
  return {
    session_id: sessionId,
    source_revision: revision,
    import_available: true,
    import_blocked_reason: null,
    title: `Session ${sessionId}`,
    title_from_claude_index: true,
    claude_title_source: "index",
    is_archived: false,
    worktree_name: null,
    project_name: "Project",
    project_key: "project",
    git_branch: "main",
    bytes: 100,
    file_count: 1,
    subagent_count: 0,
    last_modified_ns: 1,
    ...overrides,
  };
}

function preview(sessions: ClaudeHistorySessionPreview[]): ClaudeHistoryPreview {
  return {
    schema_version: 1,
    source: "claude_local_jsonl",
    explicit_action_required: true,
    account_identity_available: true,
    provider_account_key: "key",
    account_fingerprint: "fingerprint",
    provider_account_key_version: 2,
    provider_account_label: "person@example.com",
    account_blocked_reason: null,
    claude_client_version: "1",
    matrx_user_available: true,
    import_ready: true,
    totals: { session_count: sessions.length, file_count: sessions.length, bytes: 100, project_count: 1 },
    limits: { preview_sessions: 200, selected_sessions: 10, import_bytes: 1_000, line_bytes: 1_000 },
    sessions,
    truncated: false,
  };
}

describe("history review change evidence", () => {
  it("distinguishes new, changed, unchanged, and no-longer-returned sessions", () => {
    const before = preview([
      session("same", "r1"),
      session("changed", "r1"),
      session("missing", "r1"),
    ]);
    const after = preview([
      session("same", "r1"),
      session("changed", "r2"),
      session("new", "r1"),
    ]);

    expect(classifyHistoryChange(after.sessions[0]!, before)).toBe("unchanged");
    expect(classifyHistoryChange(after.sessions[1]!, before)).toBe("changed");
    expect(classifyHistoryChange(after.sessions[2]!, before)).toBe("new");
    expect(historyChangeCounts(after, before)).toEqual({
      new: 1,
      changed: 1,
      unchanged: 1,
      noLongerReturned: 1,
    });
  });

  it("does not claim changes without a baseline", () => {
    expect(classifyHistoryChange(session("one", "r1"), null)).toBe("not_compared");
  });
});
