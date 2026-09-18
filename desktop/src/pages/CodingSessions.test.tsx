/** @vitest-environment jsdom */
/**
 * The three complaints, as tests.
 *
 * Every case here fails against the screen as it shipped in 1.4.110:
 *  · the Refresh button had no loading state after first mount, so a click
 *    changed nothing on screen for the whole ~4.3s read;
 *  · a row click opened the delivery-diagnosis dialog, never the conversation;
 *  · usage lived on its own route with its own sidebar entry, so there was no
 *    tab to render.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CodingSessionProvider, CodingSessionsOverview } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  getCodingSessionsOverview: vi.fn(),
  getCodingSessionStatus: vi.fn(),
  getCodingSessionProviderReadiness: vi.fn(),
  getCodingSessionArtifactsStatus: vi.fn(),
  getCodingSessionArtifactsSessions: vi.fn(),
  getClaudeLabelStatus: vi.fn(),
  syncClaudeEverything: vi.fn(),
  resumeCodingSessionDelivery: vi.fn(),
  syncCodingSessionArtifacts: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ engine: mocks }));
vi.mock("@ai-matrx/design-system", () => ({
  Badge: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  Button: ({ children, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props}>{children}</button>
  ),
  BasicInput: (props: InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
}));
vi.mock("@ai-matrx/kit/format", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@ai-matrx/kit/format")>();
  return {
    ...actual,
    formatFileSize: (value: number) => `${value} B`,
    formatCount: (value: number) => String(value),
  };
});
vi.mock("@/components/coding-sessions/AgentRuntimeCard", () => ({
  AgentRuntimeCard: () => <div data-testid="agent-runtime-card" />,
}));
vi.mock("@/components/coding-sessions/DeliveryEvidenceDialog", () => ({
  DeliveryEvidenceDialog: () => null,
}));
vi.mock("@/components/coding-sessions/SessionDiagnosisDialog", () => ({
  SessionDiagnosisDialog: ({ sessionId }: { sessionId: string | null }) =>
    sessionId ? <div data-testid="diagnosis-open">{sessionId}</div> : null,
  SESSION_STATE_LABEL: {
    in_cloud: "In AI Matrx",
    changed: "Changed since delivery",
    queued: "Queued",
    failed: "Failed",
    not_in_cloud: "Not in AI Matrx",
    unknown: "Unknown",
  },
  SESSION_STATE_HINT: {
    in_cloud: "hint",
    changed: "hint",
    queued: "hint",
    failed: "hint",
    not_in_cloud: "hint",
    unknown: "hint",
  },
  SESSION_STATE_TONE: {
    in_cloud: "",
    changed: "",
    queued: "",
    failed: "",
    not_in_cloud: "",
    unknown: "",
  },
}));
// The native-continue dialog owns three server reads of its own; its logic is
// tested at `@/lib/coding-sessions/continue-door`. Here it only has to prove
// the row's Continue control opens it and hands it the row.
vi.mock("@/components/coding-sessions/ContinueSessionDialog", () => ({
  ContinueSessionDialog: ({ row }: { row: { session_id: string } | null }) =>
    row ? <div data-testid="continue-open">{row.session_id}</div> : null,
}));
vi.mock("@/components/coding-sessions/SessionArtifactsDialog", () => ({
  ArtifactsCell: () => <span>—</span>,
  SessionArtifactsDialog: () => null,
}));
vi.mock("@/components/coding-sessions/tabs/UsageTab", () => ({
  UsageTab: () => <div data-testid="usage-tab" />,
}));
vi.mock("@/lib/org/active-org", () => ({ requestOrganizationPicker: vi.fn() }));
vi.mock("@/lib/app-config", () => ({
  getWebAppOrigin: () => Promise.resolve("https://aimatrx.com"),
}));
vi.mock("@/lib/open-external", () => ({ openExternal: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

import { CodingSessions } from "./CodingSessions";

const CLAUDE_NOTE =
  "Claude Code lists every transcript on this Mac; nothing is hidden from this list.";
const CODEX_NOTE =
  "Codex records no message count for an older rollout, so those rows show their entries as unknown.";
const CURSOR_NOTE =
  "Cursor does not expose a size or a message count for a chat: its chats are rows in a shared database.";
const VSCODE_NOTE =
  "VS Code is on this Mac but the AI Matrx extension is not installed in it, so this Mac keeps no local record of its chats.";

function providerBlock(
  provider: CodingSessionProvider,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    provider,
    index: {
      state: "fresh",
      refreshing: false,
      files_read: 1,
      updated_at: null,
      changed_files: null,
      duration_seconds: null,
      limit_reached: false,
      unreadable: 0,
      error: null,
    },
    cloud: { checked: true, reason: null, detail: null, sessions: 1, checked_at: null },
    totals: { sessions: 1 },
    note: null,
    supports_pins: provider === "claude_code",
    supports_resume: provider === "claude_code" || provider === "codex",
    lists_locally: provider !== "vscode",
    continuation: { command: null, note: "", native_resume: false },
    ...overrides,
  };
}

function overviewPayload(titles: string[], conversationId: string | null = "conv-1"): CodingSessionsOverview {
  return {
    schema_version: 3,
    account_id: "acct",
    accounts: [],
    listed_providers: ["claude_code"],
    providers: [providerBlock("claude_code", { note: CLAUDE_NOTE })],
    cloud: { checked: true, sessions: titles.length, checked_at: null, reason: null, detail: null },
    conversations: titles.map((title, index) => ({
      session_id: `session-${index}`,
      provider: "claude_code",
        continuation: {
        command: `claude --resume session-${index}`,
        note: "only while local",
        native_resume: true,
      },
      facts: {},
      title,
      title_source: null,
      project: "matrx-local",
      last_activity_at: 1,
      bytes: 10,
      on_disk: true,
      state: conversationId ? "in_cloud" : "queued",
      pinned: false,
      pinned_rank: null,
      category: null,
      archived: false,
      in_claude_sidebar: true,
      cloud: conversationId
        ? { conversation_id: `${conversationId}-${index}`, fidelity: null, last_seen_at: null }
        : null,
      delivery: { pending: 0, quarantined: 0 },
    })),
    totals: {
      conversations: titles.length,
      pinned: 0,
      index_files_read: 1,
      transcript_only: 0,
      transcripts_on_disk: titles.length,
      index_limit_reached: false,
      unreadable: 0,
      in_cloud: titles.length,
      changed: 0,
      queued: 0,
      failed: 0,
      not_in_cloud: 0,
      unknown: 0,
      waiting: 0,
      quarantined: 0,
    },
  } as unknown as CodingSessionsOverview;
}

const bridge = {
  publisher: {
    active: true,
    blocker: null,
    ticks: null,
    transport_circuit: { config: {} },
  },
  pending: { total: 0, item_count: 0, payload_bytes: 0, by_provider: {} },
  quarantine: { total: 0, by_provider: {} },
};
const readiness = {
  providers: {
    claude_code: { product: { installed: true, version: "1" }, activity: {} },
    codex: { product: { installed: true }, activity: {} },
  },
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

let container: HTMLDivElement;
let root: Root;

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

async function render(initial: string) {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[initial]}>
        <CodingSessions />
        <LocationProbe />
      </MemoryRouter>,
    );
    await Promise.resolve();
  });
}

function findButton(text: string): HTMLButtonElement {
  const match = [...container.querySelectorAll("button")].find((button) =>
    button.textContent?.includes(text),
  );
  if (!match) throw new Error(`no button containing ${text}`);
  return match as HTMLButtonElement;
}

/**
 * The pin comparison the Sessions tab states out loud. Measured numbers from
 * the 2026-09-18 divergence: 218 pinned here, 160 favourite in AI Matrx.
 */
const labelStatus = {
  schema_version: 3,
  source: "claude_desktop_session_index",
  index_available: true,
  index_writable: true,
  pushed_sessions: 0,
  index_files: 1,
  index_records: 1,
  synced_sessions: 0,
  last_sync: null,
  pin_divergence: {
    checked: true,
    reason: null,
    local: 218,
    ai_matrx: 160,
    to_pin: 116,
    to_unpin: 58,
    to_reconcile: 58,
    last_pass_at: "2026-09-18T11:57:00+00:00",
    last_pass_status: "completed",
    compared_sessions: 2033,
  },
} as const;

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
    true;
  // This environment may have no localStorage at all — which is exactly the
  // case the overview cache has to survive, so it is not stubbed in.
  try {
    localStorage?.clear();
  } catch {
    /* no storage here; the cache reports that itself */
  }
  mocks.getCodingSessionsOverview.mockReset().mockResolvedValue(overviewPayload(["First session"]));
  mocks.getCodingSessionStatus.mockReset().mockResolvedValue(bridge);
  mocks.getCodingSessionProviderReadiness.mockReset().mockResolvedValue(readiness);
  mocks.getCodingSessionArtifactsStatus.mockReset().mockResolvedValue(null);
  mocks.getCodingSessionArtifactsSessions.mockReset().mockResolvedValue({ sessions: [] });
  mocks.getClaudeLabelStatus.mockReset().mockResolvedValue(labelStatus);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("Coding Sessions tabs", () => {
  it("shows the session list by default", async () => {
    await render("/coding-sessions");
    expect(container.querySelector("[data-testid='sessions-table'] tbody")).not.toBeNull();
    expect(container.querySelector("[data-testid='usage-tab']")).toBeNull();
    expect(container.textContent).toContain("First session");
  });

  it("keeps every session column reachable through the canonical horizontal scroll owner", async () => {
    await render("/coding-sessions");
    const scrollOwner = container.querySelector("[data-matrx-table-scroll]");
    expect(scrollOwner).not.toBeNull();
    expect(scrollOwner?.className).toContain("overflow-auto");
    expect(scrollOwner?.textContent).toContain("Actions");
    expect(scrollOwner?.textContent).toContain("Delivery");
  });

  it("renders usage inside this feature when the URL asks for that tab", async () => {
    await render("/coding-sessions?tab=usage");
    expect(container.querySelector("[data-testid='usage-tab']")).not.toBeNull();
    expect(container.querySelector("[data-testid='sessions-table']")).toBeNull();
  });

  it("puts the operational blocks on the settings tab, not over the list", async () => {
    await render("/coding-sessions?tab=settings");
    expect(container.querySelector("[data-testid='agent-runtime-card']")).not.toBeNull();
    expect(container.textContent).toContain("Installed here");
    expect(container.querySelector("[data-testid='sessions-table']")).toBeNull();
  });

  it("moves the tab into the URL when one is clicked", async () => {
    await render("/coding-sessions");
    await act(async () => {
      (container.querySelector("[data-testid='coding-sessions-tab-usage']") as HTMLButtonElement).click();
    });
    expect(container.querySelector("[data-testid='location']")?.textContent).toBe(
      "/coding-sessions?tab=usage",
    );
  });

  it("derives the provider chips from the engine, never from a hard-coded name", async () => {
    await render("/coding-sessions");
    const chips = container.querySelector("[data-testid='provider-chips']")?.textContent ?? "";
    expect(chips).toContain("Claude Code");
    expect(chips).toContain("Codex");
  });
});

describe("Refresh", () => {
  it("announces the work the moment it is clicked", async () => {
    await render("/coding-sessions");
    const slow = deferred<CodingSessionsOverview>();
    mocks.getCodingSessionsOverview.mockReturnValue(slow.promise);
    const refresh = container.querySelector(
      "[data-testid='coding-sessions-refresh']",
    ) as HTMLButtonElement;
    expect(refresh.getAttribute("aria-busy")).toBe("false");
    await act(async () => {
      refresh.click();
    });
    expect(refresh.textContent).toContain("Refreshing…");
    expect(refresh.getAttribute("aria-busy")).toBe("true");
    expect(refresh.disabled).toBe(true);
    expect(
      container.querySelector("[data-matrx-table-scroll]")?.getAttribute("aria-busy"),
    ).toBe("true");
    // ...and the rows already read stay on screen while it works.
    expect(container.textContent).toContain("First session");
    await act(async () => {
      slow.resolve(overviewPayload(["Second session"]));
      await Promise.resolve();
    });
    expect(container.textContent).toContain("Second session");
    expect(refresh.getAttribute("aria-busy")).toBe("false");
  });

  it("keeps the list and shows the reason when the read fails", async () => {
    await render("/coding-sessions");
    mocks.getCodingSessionsOverview.mockRejectedValue(new Error("index unreadable"));
    await act(async () => {
      (container.querySelector("[data-testid='coding-sessions-refresh']") as HTMLButtonElement).click();
      await Promise.resolve();
    });
    expect(container.querySelector("[data-testid='sessions-error']")?.textContent).toContain(
      "index unreadable",
    );
    expect(container.textContent).toContain("First session");
  });

  it("keeps the cached list and says when one transient retry is scheduled", async () => {
    await render("/coding-sessions");
    mocks.getCodingSessionsOverview.mockRejectedValue({
      name: "CodingSessionsOverviewReadError",
      kind: "timeout",
      message: "Engine request timed out after 2 minutes: /coding-session/claude/overview",
    });
    await act(async () => {
      (container.querySelector("[data-testid='coding-sessions-refresh']") as HTMLButtonElement).click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(container.querySelector("[data-testid='sessions-retry-scheduled']")?.textContent).toContain(
      "Trying this read once more shortly",
    );
    expect(container.textContent).toContain("First session");
  });
});

describe("A row is a door", () => {
  it("opens the session's conversation in the app's chat surface", async () => {
    await render("/coding-sessions");
    await act(async () => {
      (container.querySelector("[data-row-id='session-0']") as HTMLElement).click();
    });
    expect(container.querySelector("[data-testid='location']")?.textContent).toBe(
      "/cloud-chat?conversation=conv-1-0&from=coding-sessions",
    );
  });

  it("says why there is nothing to open, and keeps delivery one click away", async () => {
    mocks.getCodingSessionsOverview.mockResolvedValue(overviewPayload(["Queued session"], null));
    await render("/coding-sessions");
    await act(async () => {
      (container.querySelector("[data-row-id='session-0']") as HTMLElement).click();
    });
    const notice = container.querySelector("[data-testid='session-not-openable']");
    expect(notice?.textContent).toContain("is not in AI Matrx yet");
    expect(notice?.textContent).toContain("delivery queue");
    expect(container.querySelector("[data-testid='location']")?.textContent).toBe(
      "/coding-sessions",
    );
    await act(async () => {
      findButton("See every delivery fact").click();
    });
    expect(container.querySelector("[data-testid='diagnosis-open']")?.textContent).toBe("session-0");
  });

  /**
   * Continue was a clipboard copy and nothing else (CS-11). It now opens the
   * native-continue door for THAT row: the dialog asks AI Matrx and this Mac
   * whether the session can really be resumed here, runs the turn when it
   * can, and still hands over the same resume command when it cannot. The
   * assertion is that the control reaches the door carrying the right row —
   * a Continue that opened an empty dialog, or someone else's session, is the
   * failure this guards.
   */
  it("opens the native continue door for the row it was clicked on", async () => {
    await render("/coding-sessions");
    expect(container.querySelector("[data-testid='continue-open']")).toBeNull();
    await act(async () => {
      findButton("Continue").click();
    });
    expect(container.querySelector("[data-testid='continue-open']")?.textContent).toBe(
      "session-0",
    );
  });
});

/**
 * The engine answers in milliseconds now (1.4.125), which means a fast answer
 * can be an EMPTY or an INCOMPLETE one. Every case below fails against the
 * screen as it shipped in 1.4.124: it counted whatever arrived, so a first-run
 * engine read "0 conversations on this Mac", a re-read behind the response was
 * invisible, and a cloud check that had not happened YET wore the same warning
 * as one that could not happen at all.
 */
describe("A cold index is never an empty Mac", () => {
  function coldPayload(filesRead: number): CodingSessionsOverview {
    const base = overviewPayload([]);
    return {
      ...base,
      index: {
        state: "cold",
        refreshing: true,
        files_read: filesRead,
        conversations: 0,
        updated_at: null,
        changed_files: null,
        duration_seconds: null,
        limit_reached: false,
        unreadable: 0,
        error: null,
      },
    } as unknown as CodingSessionsOverview;
  }

  it("shows the first read with its counter instead of '0 conversations'", async () => {
    mocks.getCodingSessionsOverview.mockResolvedValue(coldPayload(4_096));
    await render("/coding-sessions");
    const subtitle = container.querySelector("[data-testid='coding-sessions-subtitle']");
    expect(subtitle?.textContent).toContain("Reading your conversations for the first time…");
    expect(subtitle?.textContent).toContain("4,096 index files read so far");
    expect(subtitle?.textContent).not.toContain("0 conversations");
    expect(container.querySelector("[data-testid='index-cold']")).not.toBeNull();
    expect(container.querySelector("[data-testid='sessions-empty-cold']")).not.toBeNull();
    expect(container.textContent).not.toContain("No coding-agent conversations found");
  });

  it("asks again a few seconds later, until the index is fresh", async () => {
    vi.useFakeTimers();
    try {
      mocks.getCodingSessionsOverview.mockResolvedValue(coldPayload(10));
      await render("/coding-sessions");
      expect(mocks.getCodingSessionsOverview).toHaveBeenCalledTimes(1);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3_000);
      });
      expect(mocks.getCodingSessionsOverview).toHaveBeenCalledTimes(2);
      // A fresh answer ends the polling.
      mocks.getCodingSessionsOverview.mockResolvedValue(overviewPayload(["First session"]));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3_000);
      });
      expect(mocks.getCodingSessionsOverview).toHaveBeenCalledTimes(3);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      expect(mocks.getCodingSessionsOverview).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("A refresh running behind the answer", () => {
  function refreshingPayload(): CodingSessionsOverview {
    const base = overviewPayload(["First session"]);
    return {
      ...base,
      index: {
        state: "refreshing",
        refreshing: true,
        files_read: 67_224,
        conversations: 1_806,
        updated_at: "2026-09-15T20:00:00Z",
        changed_files: 7,
        duration_seconds: 1.25,
        limit_reached: false,
        unreadable: 0,
        error: null,
      },
    } as unknown as CodingSessionsOverview;
  }

  it("wears the announced-refresh state and says what the engine is re-reading", async () => {
    mocks.getCodingSessionsOverview.mockResolvedValue(refreshingPayload());
    await render("/coding-sessions");
    const refresh = container.querySelector(
      "[data-testid='coding-sessions-refresh']",
    ) as HTMLButtonElement;
    expect(refresh.getAttribute("aria-busy")).toBe("true");
    expect(refresh.textContent).toContain("Refreshing…");
    const note = container.querySelector("[data-testid='index-refreshing-note']");
    expect(note?.textContent).toContain("Re-reading this Mac's conversations");
    expect(note?.textContent).toContain("7 changed records");
    expect(note?.textContent).toContain("last read took 1.3s");
    // The rows it already has stay on screen.
    expect(container.textContent).toContain("First session");
  });

  it("re-fetches once a few seconds later", async () => {
    vi.useFakeTimers();
    try {
      mocks.getCodingSessionsOverview.mockResolvedValue(refreshingPayload());
      await render("/coding-sessions");
      expect(mocks.getCodingSessionsOverview).toHaveBeenCalledTimes(1);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4_500);
      });
      expect(mocks.getCodingSessionsOverview).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("The cloud check that has not happened yet", () => {
  function inFlightPayload(): CodingSessionsOverview {
    const base = overviewPayload(["First session"]);
    return {
      ...base,
      cloud: {
        checked: false,
        reason: "cloud_check_in_flight",
        detail: "AI Matrx is being asked which of these conversations it holds.",
        sessions: 0,
        checked_at: "2026-09-15T20:00:00Z",
        refreshing: true,
        age_seconds: null,
      },
      conversations: base.conversations.map((row) => ({ ...row, state: "unknown", cloud: null })),
    } as unknown as CodingSessionsOverview;
  }

  it("says 'not asked yet' quietly instead of 'could not be asked'", async () => {
    mocks.getCodingSessionsOverview.mockResolvedValue(inFlightPayload());
    await render("/coding-sessions");
    const quiet = container.querySelector("[data-testid='cloud-in-flight']");
    expect(quiet?.textContent).toContain("has not been asked yet");
    expect(quiet?.getAttribute("role")).toBe("status");
    expect(container.textContent).not.toContain("could not be asked");
    expect(container.querySelector("[data-testid='coding-sessions-subtitle']")?.textContent).toContain(
      "AI Matrx is being asked which of them it holds",
    );
  });

  it("lets the rows read unknown quietly during that window", async () => {
    mocks.getCodingSessionsOverview.mockResolvedValue(inFlightPayload());
    await render("/coding-sessions");
    const cell = container.querySelector("[data-testid='state-checking']");
    expect(cell?.textContent).toContain("Checking…");
    expect(container.textContent).not.toContain("Unknown");
  });

  it("shows how old a real answer is", async () => {
    const base = overviewPayload(["First session"]);
    mocks.getCodingSessionsOverview.mockResolvedValue({
      ...base,
      cloud: { ...base.cloud, sessions: 1_671, age_seconds: 240, refreshing: false },
    } as unknown as CodingSessionsOverview);
    await render("/coding-sessions");
    expect(container.querySelector("[data-testid='cloud-checked-note']")?.textContent).toContain(
      "checked 4m ago",
    );
    expect(container.querySelector("[data-testid='coding-sessions-subtitle']")?.textContent).toContain(
      "AI Matrx holds 1,671 of them (checked 4m ago)",
    );
  });
});

/**
 * ONE FEATURE, FOUR PROVIDERS (Arman, 2026-09-17: "coding sessions is one
 * feature"). Every case below fails against the screen as it shipped in
 * 1.4.155: it read one provider's payload, printed "0 B" for a Cursor chat
 * that has no size, showed an empty pin column for providers with no pins,
 * badged every non-Claude row "CLI only", and left a provider with no rows and
 * no reason on screen.
 */
describe("The pin divergence on the real screen", () => {
  it("states this Mac's pins, AI Matrx's, and what is still to reconcile", async () => {
    await render("/coding-sessions");
    const row = container.querySelector("[data-testid='pin-divergence']");
    expect(row).not.toBeNull();
    expect(row?.textContent).toContain(
      "Pins: 218 on this Mac · 160 in AI Matrx · 58 still to reconcile",
    );
  });

  it("shows the reason and no numbers when nothing has been compared yet", async () => {
    mocks.getClaudeLabelStatus.mockResolvedValue({
      ...labelStatus,
      pin_divergence: {
        checked: false,
        reason: "no reconcile pass has finished on this Mac yet",
        local: null,
        ai_matrx: null,
        to_pin: null,
        to_unpin: null,
        to_reconcile: null,
        last_pass_at: null,
        last_pass_status: null,
      },
    });
    await render("/coding-sessions");
    const row = container.querySelector("[data-testid='pin-divergence']");
    expect(row?.textContent).toBe(
      "Pins: not compared yet — no reconcile pass has finished on this Mac yet.",
    );
  });

  it("says this Mac could not read the comparison instead of showing a zero", async () => {
    mocks.getClaudeLabelStatus.mockRejectedValue(new Error("engine offline"));
    await render("/coding-sessions");
    expect(container.querySelector("[data-testid='pin-divergence']")).toBeNull();
    expect(
      container.querySelector("[data-testid='pin-divergence-unreadable']")?.textContent,
    ).toContain("Pins: this Mac could not read its pin comparison");
  });
});

describe("A mixed four-provider list", () => {
  function mixedPayload(): CodingSessionsOverview {
    const base = overviewPayload(["Claude session"]);
    return {
      ...base,
      listed_providers: ["claude_code", "codex", "cursor", "vscode"],
      providers: [
        providerBlock("claude_code", { note: CLAUDE_NOTE }),
        providerBlock("codex", { note: CODEX_NOTE }),
        providerBlock("cursor", { note: CURSOR_NOTE }),
        providerBlock("vscode", { note: VSCODE_NOTE, lists_locally: false }),
      ],
      conversations: [
        {
          ...base.conversations[0],
          session_id: "claude-1",
          provider: "claude_code",
          title: "Claude session",
          in_claude_sidebar: false,
          pinned: false,
          bytes: 10,
        },
        {
          ...base.conversations[0],
          session_id: "codex-1",
          provider: "codex",
          title: "Codex thread",
          bytes: 2_048,
          pinned: null,
          pinned_rank: null,
          in_claude_sidebar: null,
          continuation: {
            command: "codex resume codex-1",
            note: CODEX_NOTE,
            native_resume: true,
          },
          facts: { entries: 42, cwd: "/Users/someone/code" },
          cloud: { conversation_id: "conv-codex", fidelity: null, last_seen_at: null },
        },
        {
          ...base.conversations[0],
          session_id: "cursor-1",
          provider: "cursor",
          title: "Cursor chat",
          bytes: null,
          pinned: null,
          pinned_rank: null,
          in_claude_sidebar: null,
          continuation: { command: null, note: CURSOR_NOTE, native_resume: false },
          facts: { entries: null, workspace_id: "ws-1" },
          cloud: { conversation_id: "conv-cursor", fidelity: null, last_seen_at: null },
        },
        {
          ...base.conversations[0],
          session_id: "vscode-1",
          provider: "vscode",
          title: "VS Code chat",
          bytes: null,
          on_disk: false,
          pinned: null,
          pinned_rank: null,
          in_claude_sidebar: null,
          continuation: { command: null, note: VSCODE_NOTE, native_resume: false },
          facts: {},
          cloud: { conversation_id: "conv-vscode", fidelity: null, last_seen_at: null },
        },
      ],
    } as unknown as CodingSessionsOverview;
  }

  beforeEach(() => {
    mocks.getCodingSessionsOverview.mockResolvedValue(mixedPayload());
  });

  it("lists every provider's sessions, and says which tool wrote each row", async () => {
    await render("/coding-sessions");
    for (const title of ["Claude session", "Codex thread", "Cursor chat", "VS Code chat"]) {
      expect(container.textContent).toContain(title);
    }
    expect(container.querySelector("[data-testid='row-provider-claude-1']")?.textContent).toBe(
      "Claude Code",
    );
    expect(container.querySelector("[data-testid='row-provider-codex-1']")?.textContent).toBe(
      "Codex",
    );
    expect(container.querySelector("[data-testid='row-provider-cursor-1']")?.textContent).toBe(
      "Cursor",
    );
    expect(container.querySelector("[data-testid='row-provider-vscode-1']")?.textContent).toBe(
      "VS Code",
    );
  });

  it("shows four chips with real counts and isolates each one when picked", async () => {
    await render("/coding-sessions");
    const chips = container.querySelector("[data-testid='provider-chips']")?.textContent ?? "";
    for (const label of ["Claude Code", "Codex", "Cursor", "VS Code"]) {
      expect(chips).toContain(label);
    }
    await act(async () => {
      findButton("Codex").click();
    });
    expect(container.querySelector("[data-testid='sessions-table']")?.textContent).toContain(
      "Codex thread",
    );
    expect(container.querySelector("[data-testid='sessions-table']")?.textContent).not.toContain(
      "Cursor chat",
    );
    await act(async () => {
      findButton("Cursor").click();
    });
    expect(container.querySelector("[data-testid='sessions-table']")?.textContent).toContain(
      "Cursor chat",
    );
    expect(container.querySelector("[data-testid='sessions-table']")?.textContent).not.toContain(
      "Codex thread",
    );
  });

  it("never renders a size a provider does not have as '0 B'", async () => {
    await render("/coding-sessions");
    const cell = container.querySelector("[data-testid='row-size-cursor-1']");
    expect(cell?.textContent).toBe("—");
    expect(cell?.getAttribute("aria-label")).toContain("Cursor");
    expect(container.querySelector("[data-testid='row-size-codex-1']")?.textContent).toBe("2048 B");
  });

  it("renders no pin control for a provider that has no pins, and no dead one either", async () => {
    await render("/coding-sessions");
    expect(container.querySelector("[data-testid='row-pin-claude-1']")).not.toBeNull();
    expect(container.querySelector("[data-testid='row-pin-cursor-1']")).toBeNull();
    await act(async () => {
      findButton("Cursor").click();
    });
    expect(container.querySelector("[data-testid='pinned-column-header']")).toBeNull();
  });

  it("badges 'CLI only' only for a Claude Code row that Claude's sidebar never listed", async () => {
    await render("/coding-sessions");
    const claudeRow = container.querySelector("[data-row-id='claude-1']");
    const cursorRow = container.querySelector("[data-row-id='cursor-1']");
    expect(claudeRow?.textContent).toContain("CLI only");
    expect(cursorRow?.textContent).not.toContain("CLI only");
    expect(container.querySelector("[data-row-id='vscode-1']")?.textContent).not.toContain(
      "CLI only",
    );
  });

  it("says plainly when AI Matrx holds a session this Mac has no copy of", async () => {
    await render("/coding-sessions");
    const row = container.querySelector("[data-row-id='vscode-1']");
    expect(row?.textContent).toContain("In AI Matrx only");
  });

  it("still opens the conversation of a session this Mac does not hold", async () => {
    await render("/coding-sessions");
    await act(async () => {
      (container.querySelector("[data-row-id='vscode-1']") as HTMLElement).click();
    });
    expect(container.querySelector("[data-testid='location']")?.textContent).toBe(
      "/cloud-chat?conversation=conv-vscode&from=coding-sessions",
    );
  });

  it("puts every provider's one sentence about what it cannot show on screen", async () => {
    await render("/coding-sessions");
    const notes = container.querySelector("[data-testid='provider-notes']");
    expect(notes).not.toBeNull();
    for (const [provider, note] of [
      ["claude_code", CLAUDE_NOTE],
      ["codex", CODEX_NOTE],
      ["cursor", CURSOR_NOTE],
      ["vscode", VSCODE_NOTE],
    ] as const) {
      expect(
        container.querySelector(`[data-testid='provider-note-${provider}']`)?.textContent,
      ).toContain(note);
    }
  });

  it("shows the picked provider's sentence without making anyone open a disclosure", async () => {
    await render("/coding-sessions");
    await act(async () => {
      findButton("Cursor").click();
    });
    expect(
      container.querySelector("[data-testid='provider-note-selected']")?.textContent,
    ).toContain(CURSOR_NOTE);
  });
});
