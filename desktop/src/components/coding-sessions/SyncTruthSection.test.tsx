/** @vitest-environment jsdom */
/**
 * Guards for the Sync section (CS-25 T3, Matrx Local half).
 *
 * Every test names the production change that turns it red. The sentences are
 * typed by hand; nothing here imports a formatter and compares it to itself.
 * The ENGINE owns the words, so these tests assert that the screen shows the
 * engine's words unaltered and adds nothing of its own.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { ButtonHTMLAttributes } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type {
  ClaudeSessionReconcileReport,
  ClaudeSessionSyncTruth,
  SyncVerdictCode,
} from "@/lib/api";

const mocks = vi.hoisted(() => ({ truth: vi.fn(), reconcile: vi.fn() }));

vi.mock("@/lib/api", () => ({
  engine: {
    getClaudeSessionSyncTruth: mocks.truth,
    reconcileClaudeSession: mocks.reconcile,
  },
}));
vi.mock("@ai-matrx/design-system", () => ({
  Button: ({ children, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props}>{children}</button>
  ),
}));

import { SyncTruthSection } from "./SyncTruthSection";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const SESSION = "460c5cf5-39a9-4f93-af06-9bb196509561";

const BASE_COUNTS = {
  transcript: 384,
  delivered: 384,
  cloud_messages: 35,
  mirror_messages: 35,
};

function baseTruth(): ClaudeSessionSyncTruth {
  return {
    schema_version: 1,
    session_id: SESSION,
    provider: "claude_code",
    verdict: {
      code: "in_sync",
      reason: null,
      sentence: "In sync.",
      remedy: null,
      reconcilable: false,
    },
    count_labels: ["Transcript", "Delivered", "In AI Matrx", "On this Mac"],
    counts: { ...BASE_COUNTS },
    transcript: {
      checked: true,
      reason: null,
      on_disk: true,
      entries: 384,
      last_entry_at: null,
      last_entry_id: null,
      unreadable_lines: 0,
      bytes: 271953,
      modified_at: null,
    },
    delivered: {
      checked: true,
      reason: null,
      accepted_entries: 384,
      last_receipt_at: null,
      pending_entries: 0,
      quarantined_entries: 0,
      quarantine_reasons: [],
      publisher_blocker: null,
    },
    cloud: {
      checked: true,
      reason: null,
      session_present: true,
      conversation_id: "cf1d62fc-0000-0000-0000-000000000000",
      fidelity: "native",
      entries: 384,
      projected_entries: 384,
      skipped_entries: 0,
      pending_entries: 0,
      error_entries: 0,
      projection_errors: [],
      last_entry_at: null,
      last_entry_id: null,
      messages: 35,
      last_position: 34,
      last_message_at: null,
      cloud_verdict: null,
      cloud_sentence: null,
      cloud_remedy: null,
    },
    mirror: {
      checked: true,
      reason: null,
      conversation_row: true,
      messages: 35,
      last_pulled_at: null,
    },
    computed_at: "2026-09-17T22:00:00+00:00",
  };
}

function withVerdict(
  code: SyncVerdictCode,
  sentence: string,
  extra: Partial<ClaudeSessionSyncTruth["verdict"]> = {},
): ClaudeSessionSyncTruth {
  const truth = baseTruth();
  truth.verdict = { code, reason: null, sentence, remedy: null, reconcilable: false, ...extra };
  return truth;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  mocks.truth.mockReset();
  mocks.reconcile.mockReset();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function mount(): Promise<void> {
  await act(async () => {
    root.render(<SyncTruthSection sessionId={SESSION} />);
  });
}

function node(testId: string): HTMLElement {
  const found = container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  if (!found) throw new Error(`no [data-testid="${testId}"] on screen`);
  return found;
}

function buttonLabelled(label: string): HTMLButtonElement | null {
  return (
    Array.from(container.querySelectorAll("button")).find((element) =>
      (element.textContent ?? "").trim().startsWith(label),
    ) as HTMLButtonElement | undefined
  ) ?? null;
}

// ---------------------------------------------------------------------------
// One case per verdict code. Red if a verdict stops reaching the screen, or if
// the screen starts re-wording what the engine said.
// ---------------------------------------------------------------------------

const CASES: { code: SyncVerdictCode; label: string; sentence: string }[] = [
  {
    code: "in_sync",
    label: "In sync",
    sentence:
      "In sync. All 384 entries are in AI Matrx as 35 messages; this Mac's copy was last pulled 1 hour ago.",
  },
  {
    code: "partial_by_design",
    label: "Summary only, by design",
    sentence:
      "AI Matrx has the shape of this conversation — the 55 prompts and tool calls its hooks recorded, projected into 2 messages — not the 3526-entry transcript. Hook capture is a summary by design.",
  },
  {
    code: "behind_local",
    label: "AI Matrx is behind this Mac",
    sentence:
      "311 of this conversation's 384 entries have never reached AI Matrx. The last one that did arrived 4 hours ago.",
  },
  {
    code: "behind_cloud",
    label: "Received, not yet turned into messages",
    sentence:
      "AI Matrx received all 384 entries but 11 never became messages: No safe canonical projection exists for 'file-history-delta'; raw entry retained.",
  },
  {
    code: "mirror_stale",
    label: "This Mac's copy is behind AI Matrx",
    sentence:
      "AI Matrx has 35 messages for this conversation; this Mac's local copy has 12 and was last pulled 2 days ago.",
  },
  {
    code: "diverged",
    label: "Claude rewrote the local file",
    sentence:
      "AI Matrx holds 575 entries that are no longer in your local transcript — Claude Code rewrote the file, which compaction does. AI Matrx has the longer, older record; the local file is now the short one.",
  },
  {
    code: "quarantined",
    label: "Held back",
    sentence:
      "14820 entries are held back permanently: AI Matrx already has this session under a different Claude account, and a delivery from this account cannot replace it.",
  },
  {
    code: "not_in_cloud",
    label: "Not in AI Matrx",
    sentence:
      "This conversation is not in AI Matrx at all — no session, no messages. Its 384 local entries have never been delivered.",
  },
  {
    code: "unknown",
    label: "Cannot tell",
    sentence:
      "Cannot tell whether this conversation is in sync: AI Matrx could not be read (the server could not be reached).",
  },
];

for (const { code, label, sentence } of CASES) {
  it(`renders the ${code} verdict's sentence verbatim under its own heading`, async () => {
    mocks.truth.mockResolvedValue(withVerdict(code, sentence));
    await mount();
    expect(node("sync-sentence").textContent).toBe(sentence);
    expect(node("sync-verdict").textContent).toContain(label);
  });
}

// ---------------------------------------------------------------------------
// The four counts.
// ---------------------------------------------------------------------------

it("shows the four counts side by side with the engine's own labels", async () => {
  mocks.truth.mockResolvedValue(baseTruth());
  await mount();
  const counts = node("sync-counts").textContent ?? "";
  for (const label of ["Transcript", "Delivered", "In AI Matrx", "On this Mac"]) {
    expect(counts).toContain(label);
  }
  expect(counts).toContain("384");
  expect(counts).toContain("35");
});

it("shows an unreadable layer as Not readable, never as zero", async () => {
  // THE ANTI-DEAD-FISH GUARD ON THE SCREEN. Red if a layer that could not be
  // read is ever painted as an empty one: "0" is a claim, and a false one.
  const truth = withVerdict("unknown", "Cannot tell.");
  truth.counts = {
    transcript: 384,
    delivered: null,
    cloud_messages: null,
    mirror_messages: null,
  };
  mocks.truth.mockResolvedValue(truth);
  await mount();
  const counts = node("sync-counts").textContent ?? "";
  expect(counts).toContain("Not readable");
  expect(counts).not.toMatch(/\b0\b/);
});

// ---------------------------------------------------------------------------
// The button.
// ---------------------------------------------------------------------------

it("offers Reconcile when the engine says reconciling can help", async () => {
  mocks.truth.mockResolvedValue(
    withVerdict("behind_local", "311 entries have never reached AI Matrx.", {
      reason: "undelivered",
      remedy: "Reconcile to deliver them now.",
      reconcilable: true,
    }),
  );
  await mount();
  expect(buttonLabelled("Reconcile")).not.toBeNull();
  expect(node("sync-remedy").textContent).toBe("Reconcile to deliver them now.");
});

it("offers NO button when reconciling cannot help, and says so instead", async () => {
  // Red if a `provider_account_conflict` ever gets a Reconcile button. That
  // delivery is refused server-side every time, so the control would be a lie
  // — and this repo forbids a dead-looking control with nothing behind it.
  mocks.truth.mockResolvedValue(
    withVerdict("quarantined", "14820 entries are held back permanently.", {
      reason: "provider_account_conflict",
      remedy:
        "Nothing to do — the conversation is in AI Matrx under that other account. Reconcile will not re-send these.",
      reconcilable: false,
    }),
  );
  await mount();
  expect(buttonLabelled("Reconcile")).toBeNull();
  expect(node("sync-no-action").textContent).toBe(
    "There is nothing for Reconcile to do here.",
  );
});

it("shows every step's outcome and then the freshly re-read verdict", async () => {
  const before = withVerdict("behind_local", "311 entries have never reached AI Matrx.", {
    reason: "undelivered",
    remedy: "Reconcile to deliver them now.",
    reconcilable: true,
  });
  const after = withVerdict(
    "in_sync",
    "In sync. All 384 entries are in AI Matrx as 35 messages.",
  );
  const report: ClaudeSessionReconcileReport = {
    schema_version: 1,
    session_id: SESSION,
    actions: [
      {
        step: "deliver",
        outcome: "queued",
        detail: "The transcript was queued for delivery to AI Matrx.",
      },
      {
        step: "reproject",
        outcome: "nothing_to_do",
        detail: "Every entry AI Matrx holds has already become a message.",
      },
      {
        step: "pull",
        outcome: "done",
        detail: "This Mac's copy of the conversation was refreshed from AI Matrx.",
      },
    ],
    truth: after,
  };
  mocks.truth.mockResolvedValue(before);
  mocks.reconcile.mockResolvedValue(report);

  await mount();
  await act(async () => {
    buttonLabelled("Reconcile")?.click();
  });

  const list = node("sync-report").textContent ?? "";
  expect(list).toContain("Deliver the transcript — Queued");
  expect(list).toContain("Turn entries into messages — Nothing to do");
  expect(list).toContain("Copy onto this Mac — Done");
  // Red if the screen keeps showing the stale verdict after reconciling: the
  // whole point is that the answer is re-read, never assumed.
  expect(node("sync-sentence").textContent).toBe(
    "In sync. All 384 entries are in AI Matrx as 35 messages.",
  );
});

it("reports a refused step as Not attempted, not as a failure", async () => {
  // Refusing to re-send an envelope AI Matrx will always reject is CORRECT.
  // Red if it is ever painted as an error.
  const base = withVerdict("quarantined", "Held back.", {
    reason: "provider_account_conflict",
    remedy: "Reconcile will not re-send these.",
    reconcilable: true,
  });
  mocks.truth.mockResolvedValue(base);
  mocks.reconcile.mockResolvedValue({
    schema_version: 1,
    session_id: SESSION,
    actions: [
      {
        step: "deliver",
        outcome: "refused",
        detail:
          "Not re-sent on purpose: AI Matrx already holds this session under a different Claude account.",
      },
    ],
    truth: base,
  } satisfies ClaudeSessionReconcileReport);

  await mount();
  await act(async () => {
    buttonLabelled("Reconcile")?.click();
  });

  expect(node("sync-report").textContent).toContain("Deliver the transcript — Not attempted");
  expect(container.querySelector('[role="alert"]')).toBeNull();
});

it("surfaces a failed read as an error instead of an empty panel", async () => {
  // Red if the engine being unreachable ever renders as a blank section with
  // no sentence — the dead fish in its purest form.
  mocks.truth.mockRejectedValue(new Error("engine not running"));
  await mount();
  const alert = container.querySelector('[role="alert"]');
  expect(alert?.textContent).toContain("engine not running");
});

// ---------------------------------------------------------------------------
// Evidence rows.
// ---------------------------------------------------------------------------

it("lists every quarantine reason with its count and the server's own words", async () => {
  const truth = withVerdict("quarantined", "Held back.");
  truth.delivered.quarantined_entries = 14820;
  truth.delivered.quarantine_reasons = [
    {
      code: "provider_account_conflict",
      message:
        "AI Matrx already holds this session bound to a DIFFERENT Claude account, so a delivery from this account cannot replace it. The conversation is in AI Matrx; discard this delivery.",
      count: 14820,
    },
  ];
  mocks.truth.mockResolvedValue(truth);
  await mount();
  expect(node("sync-quarantine").textContent).toContain(
    "14820 held back — AI Matrx already holds this session bound to a DIFFERENT Claude account",
  );
});

it("lists projection errors with the server's own reason", async () => {
  const truth = withVerdict("behind_cloud", "11 never became messages.");
  truth.cloud.error_entries = 11;
  truth.cloud.projection_errors = [
    {
      code: "unsupported_event",
      detail:
        "No safe canonical projection exists for 'file-history-delta'; raw entry retained.",
      count: 11,
    },
  ];
  mocks.truth.mockResolvedValue(truth);
  await mount();
  expect(node("sync-projection-errors").textContent).toContain(
    "11 not projected — No safe canonical projection exists for 'file-history-delta'; raw entry retained.",
  );
});

it("warns when part of the transcript cannot be read at all", async () => {
  const truth = withVerdict("in_sync", "In sync.");
  truth.transcript.unreadable_lines = 3;
  mocks.truth.mockResolvedValue(truth);
  await mount();
  expect(container.textContent).toContain(
    "3 lines of the transcript could not be read and cannot be delivered.",
  );
});

it("shows the server's own read of its half when it differs from the verdict", async () => {
  // Two systems disagreeing must be visible, not averaged away.
  const truth = withVerdict("behind_local", "311 entries have never reached AI Matrx.");
  truth.cloud.cloud_verdict = "unknown";
  truth.cloud.cloud_sentence =
    "Cannot tell whether this conversation is in sync: this Mac's transcript could not be read (not visible from the server).";
  mocks.truth.mockResolvedValue(truth);
  await mount();
  expect(node("sync-server-claim").textContent).toContain(
    "AI Matrx's own read of its half: Cannot tell whether this conversation is in sync: this Mac's transcript could not be read (not visible from the server).",
  );
});
