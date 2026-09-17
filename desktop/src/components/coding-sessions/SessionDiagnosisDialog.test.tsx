/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { ClaudeSessionDiagnosis } from "@/lib/api";

const mocks = vi.hoisted(() => ({ diagnosis: vi.fn() }));

vi.mock("@/lib/api", () => ({ engine: { getClaudeSessionDiagnosis: mocks.diagnosis } }));
vi.mock("@ai-matrx/design-system", () => ({
  Badge: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  Button: ({ children, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props}>{children}</button>
  ),
}));
// This suite was RED on origin/main before CS-25 touched it: importing the
// dialog pulls in `@/lib/org/active-org`, which constructs a Supabase client at
// module load and throws "supabaseUrl is required" under vitest. The org picker
// is a click-through this suite never exercises, so it is stubbed here rather
// than the production import being contorted for a test.
vi.mock("@/lib/org/active-org", () => ({ requestOrganizationPicker: vi.fn() }));
vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogHeader: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
}));

import { SessionDiagnosisDialog } from "./SessionDiagnosisDialog";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

function legacyDiagnosis(): ClaudeSessionDiagnosis {
  return {
    schema_version: 1,
    session_id: "session-1",
    state: "not_in_cloud",
    verdict: { summary: "Not in AI Matrx.", remedy: null },
    index: {
      in_claude_sidebar: true,
      title: "Legacy session",
      title_source: null,
      project: null,
      git_branch: null,
      worktree_name: null,
      pinned: false,
      pinned_rank: null,
      category: null,
      archived: false,
      last_activity_at: 0,
      record_count: 1,
      accounts: [],
    },
    transcript: { on_disk: true, bytes: 1, modified_at: null },
    cloud: { checked: true, reason: null, detail: null, sessions: 0, checked_at: "", binding: null },
    delivery: { publisher_blocker: null, envelopes: [], delivered_by_this_mac_at: null },
    capture: [],
    labels: { metadata_sent: null, title_pushed: null },
  };
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  mocks.diagnosis.mockResolvedValue(legacyDiagnosis());
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

it("renders an older diagnosis payload with no delivery availability field", async () => {
  await act(async () => {
    root.render(<SessionDiagnosisDialog sessionId="session-1" onClose={() => {}} onChanged={async () => {}} />);
    await Promise.resolve();
  });

  expect(container.textContent).toContain("Never (a hook-mirrored session");
  expect(container.textContent).toContain("None queued or preserved for this session.");
  expect(container.textContent).not.toContain("could not read its delivery ledger");
});
