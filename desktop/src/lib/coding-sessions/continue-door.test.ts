import { describe, expect, it } from "vitest";

import type { BridgeCapabilityReport } from "@/lib/aidream-client";
import type { LocalRuntimeCapabilities } from "@/lib/api";
import {
  continuationRoute,
  continueDoorView,
  runStatusSentence,
  type ContinueDoorInput,
} from "@/lib/coding-sessions/continue-door";

const COMMAND = "claude --resume 5f0e0e2a-0000-4000-8000-000000000001";

function report(
  resume: { supported: boolean; reason?: string },
): BridgeCapabilityReport {
  return {
    provider: "claude_code",
    origin: "matrx_local",
    runtime: "matrx_local",
    available: true,
    operations: [
      {
        operation: "resume_native",
        supported: resume.supported,
        ...(resume.reason ? { reason: resume.reason } : {}),
      },
    ],
    fidelity: { native_resume: resume.supported },
    supported_actions: ["capabilities", "append_native"],
  };
}

function machine(
  overrides: Partial<LocalRuntimeCapabilities> = {},
): LocalRuntimeCapabilities {
  return {
    schema_version: 1,
    available: true,
    reasons: [],
    sdk_available: true,
    claude_cli: "/usr/local/bin/claude",
    claude_account_label: "someone@example.test",
    claude_client_version: "2.1.228",
    matrx_user_available: true,
    auth_path: "user_subscription_login",
    workspace_roots: ["/Users/someone/code"],
    approved_folders: ["/Users/someone/code/matrx-local"],
    active_runs: 0,
    capabilities: {
      start: true,
      send: true,
      cancel: true,
      resume_native: true,
      fork_native: false,
      stream: true,
    },
    ...overrides,
  };
}

function input(overrides: Partial<ContinueDoorInput> = {}): ContinueDoorInput {
  return {
    copyCommand: COMMAND,
    platform: { report: report({ supported: true }), error: null },
    machine: { capabilities: machine(), error: null },
    session: {
      verdict: {
        resumable: true,
        session_id: "5f0e0e2a-0000-4000-8000-000000000001",
        workspace: "/Users/someone/code/matrx-local",
        transcript_present: true,
      },
      error: null,
    },
    ...overrides,
  };
}

describe("the Continue control offers a native continue only when all three verdicts agree", () => {
  it("is ready when the platform, this Mac, and the transcript all say yes", () => {
    const view = continueDoorView(input());
    expect(view.status).toBe("ready");
    expect(view.canStart).toBe(true);
    expect(view.workspace).toBe("/Users/someone/code/matrx-local");
    expect(view.resumeSessionId).toBe("5f0e0e2a-0000-4000-8000-000000000001");
    expect(view.reasons[0]).toContain("on your own Claude login");
  });

  it("never claims native resume the bridge says does not exist, and quotes its reason", () => {
    const reason =
      "Codex sessions are mirrored, never executed by AI Matrx: no Codex runtime exists yet.";
    const view = continueDoorView(
      input({ platform: { report: report({ supported: false, reason }), error: null } }),
    );
    expect(view.status).toBe("refused");
    expect(view.canStart).toBe(false);
    expect(view.reasons).toEqual([reason]);
    // The fallback is still handed over — a refusal is never a dead end.
    expect(view.copyCommand).toBe(COMMAND);
  });

  it("names every missing prerequisite this Mac reported, verbatim", () => {
    const view = continueDoorView(
      input({
        machine: {
          capabilities: machine({
            available: false,
            reasons: [
              "Claude Code is not installed on this machine (no `claude` binary found)",
              "Sign in to AI Matrx in the desktop app",
            ],
          }),
          error: null,
        },
      }),
    );
    expect(view.status).toBe("refused");
    expect(view.reasons).toEqual([
      "Claude Code is not installed on this machine (no `claude` binary found)",
      "Sign in to AI Matrx in the desktop app",
    ]);
  });

  it("says plainly when Claude's own transcript is not on this Mac", () => {
    const view = continueDoorView(
      input({
        session: {
          verdict: { resumable: false, reason: "transcript_not_on_this_machine" },
          error: null,
        },
      }),
    );
    expect(view.status).toBe("refused");
    expect(view.reasons[0]).toContain("not on this Mac");
  });

  it("refuses an unapproved folder by naming it and the remedy", () => {
    const view = continueDoorView(
      input({
        session: {
          verdict: {
            resumable: true,
            session_id: "s-1",
            workspace: "/Users/someone/code/other-repo",
          },
          error: null,
        },
      }),
    );
    expect(view.status).toBe("refused");
    expect(view.reasons[0]).toContain("/Users/someone/code/other-repo");
    expect(view.reasons[0]).toContain("not approved for agent execution");
  });

  it("reports a failed read as a failed read, not as unsupported", () => {
    const view = continueDoorView(
      input({ platform: { report: null, error: "HTTP 503 Service Unavailable" } }),
    );
    expect(view.status).toBe("refused");
    expect(view.reasons[0]).toContain("HTTP 503 Service Unavailable");
  });

  it("is still checking while any verdict is missing", () => {
    const view = continueDoorView(input({ session: { verdict: null, error: null } }));
    expect(view.status).toBe("checking");
    expect(view.canStart).toBe(false);
  });
});

describe("a running continue always has a sentence, never a bare spinner", () => {
  it("says what each status means", () => {
    expect(runStatusSentence({ status: "starting", turns_completed: 0, error: null })).toContain(
      "Starting Claude Code",
    );
    expect(runStatusSentence({ status: "running", turns_completed: 2, error: null })).toBe(
      "Running — 2 turns completed.",
    );
    expect(runStatusSentence({ status: "completed", turns_completed: 1, error: null })).toContain(
      "Finished after 1 turn",
    );
    expect(runStatusSentence({ status: "cancelled", turns_completed: 0, error: null })).toContain(
      "Cancelled",
    );
    expect(
      runStatusSentence({ status: "failed", turns_completed: 0, error: "the CLI exited 1" }),
    ).toBe("Failed: the CLI exited 1");
  });

  it("does not invent a meaning for a status it does not know", () => {
    expect(runStatusSentence({ status: "quantum", turns_completed: 0, error: null })).toContain(
      "unrecognised status (quantum)",
    );
  });
});

/**
 * FOUR PROVIDERS, ONE CONTROL (Arman, 2026-09-17: "coding sessions is one
 * feature"). The three verdicts above are Claude Code's own runtime door and
 * nothing else may enter it: Codex has a command and no local runtime here,
 * Cursor and VS Code have neither. Every case below fails against the screen
 * as it shipped in 1.4.155, which sent every provider's row through the
 * Claude-only reads and wrote Claude's prose over the answer.
 */
describe("the route a row actually has to be reopened", () => {
  const codexContinuation = {
    command: "codex resume 0199a1d0-0000-7000-8000-000000000002",
    note:
      "Open the original local Codex thread with codex resume <session-id> only while that local rollout file, workspace, and login remain available.",
    native_resume: true,
  };
  const cursorContinuation = {
    command: null,
    note:
      "Cursor has no command or link that reopens one chat: open the workspace in Cursor and pick the chat from its own history. The mirrored conversation in AI Matrx is the copy you can open from here.",
    native_resume: false,
  };

  it("sends a Claude Code row to this Mac's runtime door", () => {
    const route = continuationRoute({
      provider: "claude_code",
      label: "Claude Code",
      continuation: { command: COMMAND, note: "only while local", native_resume: true },
      supportsResume: true,
      hasMirroredConversation: true,
    });
    expect(route.kind).toBe("native_runtime");
    expect(route.copyCommand).toBe(COMMAND);
  });

  it("gives Codex its own command from the engine, never a Claude one", () => {
    const route = continuationRoute({
      provider: "codex",
      label: "Codex",
      continuation: codexContinuation,
      supportsResume: true,
      hasMirroredConversation: false,
    });
    expect(route.kind).toBe("command_only");
    expect(route.copyCommand).toBe("codex resume 0199a1d0-0000-7000-8000-000000000002");
    expect(route.sentence).toBe(codexContinuation.note);
    expect(route.sentence).not.toContain("Claude");
  });

  it("offers Cursor no command at all, and the mirrored conversation instead", () => {
    const route = continuationRoute({
      provider: "cursor",
      label: "Cursor",
      continuation: cursorContinuation,
      supportsResume: false,
      hasMirroredConversation: true,
    });
    expect(route.kind).toBe("no_local_reopen");
    expect(route.copyCommand).toBeNull();
    expect(route.sentence).toBe(cursorContinuation.note);
    expect(route.offerMirrored).toBe(true);
  });

  it("does not offer a mirrored conversation that does not exist", () => {
    const route = continuationRoute({
      provider: "vscode",
      label: "VS Code",
      continuation: { ...cursorContinuation, note: "VS Code has no command or link that reopens one chat." },
      supportsResume: false,
      hasMirroredConversation: false,
    });
    expect(route.offerMirrored).toBe(false);
    expect(route.sentence).toContain("VS Code");
  });

  it("never enters the Claude runtime door for a provider the engine says cannot resume", () => {
    const route = continuationRoute({
      provider: "codex",
      label: "Codex",
      continuation: codexContinuation,
      supportsResume: false,
      hasMirroredConversation: false,
    });
    expect(route.kind).not.toBe("native_runtime");
  });

  it("says the engine did not answer rather than guessing a command", () => {
    const route = continuationRoute({
      provider: "cursor",
      label: "Cursor",
      continuation: null,
      supportsResume: false,
      hasMirroredConversation: false,
    });
    expect(route.kind).toBe("unreported");
    expect(route.copyCommand).toBeNull();
    expect(route.sentence).toContain("Cursor");
  });
});

describe("the runtime door's own prose names the provider it asked about", () => {
  it("does not call a non-Claude runtime Claude's", () => {
    const view = continueDoorView(
      input({
        label: "Codex",
        machine: {
          capabilities: machine({
            capabilities: {
              start: true,
              send: true,
              cancel: true,
              resume_native: false,
              fork_native: false,
              stream: true,
            },
          }),
          error: null,
        },
      }),
    );
    expect(view.status).toBe("refused");
    expect(view.reasons[0]).toContain("Codex");
    expect(view.reasons[0]).not.toContain("Claude Code runtime");
  });
});
