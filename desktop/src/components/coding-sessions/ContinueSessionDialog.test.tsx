/** @vitest-environment jsdom */
/**
 * Continue, for a provider that is not Claude Code.
 *
 * Both cases fail against the dialog as it shipped in 1.4.155: it asked the
 * Claude-only runtime endpoints for every row, defaulted the copy command to
 * `claude --resume <id>` whoever wrote the session, and wrote Claude's prose
 * over Codex and Cursor rows. A Cursor chat has no command that reopens it,
 * so the honest answer is the engine's sentence plus the mirrored
 * conversation — never a control that looks like it would work.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CodingSessionRow } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  getRuntimeCapabilities: vi.fn(),
  getRuntimeResumable: vi.fn(),
  startRuntimeSession: vi.fn(),
  getRuntimeRun: vi.fn(),
  cancelRuntimeSession: vi.fn(),
  fetchBridgeCapabilities: vi.fn(),
  getAuthedSession: vi.fn(),
  requireActiveOrganizationId: vi.fn(),
  writeText: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  engine: {
    getRuntimeCapabilities: mocks.getRuntimeCapabilities,
    getRuntimeResumable: mocks.getRuntimeResumable,
    startRuntimeSession: mocks.startRuntimeSession,
    getRuntimeRun: mocks.getRuntimeRun,
    cancelRuntimeSession: mocks.cancelRuntimeSession,
  },
}));
vi.mock("@/lib/aidream-client", () => ({
  fetchBridgeCapabilities: mocks.fetchBridgeCapabilities,
}));
vi.mock("@/lib/custodian", () => ({ getAuthedSession: mocks.getAuthedSession }));
vi.mock("@/lib/org/active-org", () => ({
  isOrganizationNotSelectedError: () => false,
  requireActiveOrganizationId: mocks.requireActiveOrganizationId,
}));
vi.mock("@ai-matrx/design-system", () => ({
  Button: ({ children, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props}>{children}</button>
  ),
}));
vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogHeader: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
}));

import { ContinueSessionDialog } from "./ContinueSessionDialog";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const CODEX_COMMAND = "codex resume 0199a1d0-0000-7000-8000-000000000002";
const CURSOR_NOTE =
  "Cursor has no command or link that reopens one chat: open the workspace in Cursor and pick the chat from its own history. The mirrored conversation in AI Matrx is the copy you can open from here.";

function row(overrides: Partial<CodingSessionRow>): CodingSessionRow {
  return {
    session_id: "s-1",
    provider: "codex",
    title: "A session",
    project: "/Users/someone/code",
    state: "in_cloud",
    bytes: null,
    on_disk: true,
    pinned: null,
    pinned_rank: null,
    in_claude_sidebar: null,
    cloud: { conversation_id: "conv-1", fidelity: null, last_seen_at: null },
    delivery: { pending: 0, quarantined: 0 },
    ...overrides,
  } as unknown as CodingSessionRow;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  mocks.writeText.mockReset().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: mocks.writeText },
  });
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

function findButton(text: string): HTMLButtonElement | null {
  return (
    ([...container.querySelectorAll("button")].find((button) =>
      button.textContent?.includes(text),
    ) as HTMLButtonElement | undefined) ?? null
  );
}

describe("a Codex row", () => {
  it("copies the engine's own codex command and never asks the Claude runtime", async () => {
    await act(async () => {
      root.render(
        <ContinueSessionDialog
          row={row({
            provider: "codex",
            continuation: {
              command: CODEX_COMMAND,
              note: "Open the original local Codex thread with codex resume <session-id> only while that local rollout file, workspace, and login remain available.",
              native_resume: true,
            },
          })}
          supportsResume
          onClose={() => {}}
        />,
      );
      await Promise.resolve();
    });

    expect(container.textContent).toContain(CODEX_COMMAND);
    expect(container.textContent).not.toContain("claude --resume");
    expect(mocks.getRuntimeCapabilities).not.toHaveBeenCalled();
    expect(mocks.getRuntimeResumable).not.toHaveBeenCalled();
    expect(mocks.fetchBridgeCapabilities).not.toHaveBeenCalled();
    // No local-run control: this Mac cannot run a Codex turn.
    expect(findButton("Continue on this Mac")).toBeNull();

    const copy = findButton("Copy");
    expect(copy).not.toBeNull();
    await act(async () => {
      copy!.click();
      await Promise.resolve();
    });
    expect(mocks.writeText).toHaveBeenCalledWith(CODEX_COMMAND);
  });
});

describe("a Cursor row", () => {
  it("shows the engine's sentence, no command, and the mirrored conversation instead", async () => {
    const open = vi.fn();
    await act(async () => {
      root.render(
        <ContinueSessionDialog
          row={row({
            provider: "cursor",
            continuation: { command: null, note: CURSOR_NOTE, native_resume: false },
          })}
          supportsResume={false}
          onOpenConversation={open}
          onClose={() => {}}
        />,
      );
      await Promise.resolve();
    });

    expect(container.textContent).toContain("no command or link that reopens one chat");
    expect(container.textContent).not.toContain("claude --resume");
    expect(container.textContent).not.toContain("codex resume");
    expect(findButton("Copy")).toBeNull();
    expect(findButton("Continue on this Mac")).toBeNull();
    // Nothing on screen is disabled-looking: every control here does something.
    expect([...container.querySelectorAll("button")].some((button) => button.disabled)).toBe(false);

    const mirrored = findButton("Open the conversation in AI Matrx");
    expect(mirrored).not.toBeNull();
    await act(async () => {
      mirrored!.click();
    });
    expect(open).toHaveBeenCalledTimes(1);
  });

  it("does not offer a mirrored conversation the server does not hold", async () => {
    await act(async () => {
      root.render(
        <ContinueSessionDialog
          row={row({
            provider: "cursor",
            cloud: null,
            state: "not_in_cloud",
            continuation: { command: null, note: CURSOR_NOTE, native_resume: false },
          })}
          supportsResume={false}
          onClose={() => {}}
        />,
      );
      await Promise.resolve();
    });
    expect(findButton("Open the conversation in AI Matrx")).toBeNull();
    expect(container.textContent).toContain("no command or link that reopens one chat");
  });
});
