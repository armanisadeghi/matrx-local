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

import type { ClaudeOverview } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  getClaudeOverview: vi.fn(),
  getCodingSessionStatus: vi.fn(),
  getCodingSessionProviderReadiness: vi.fn(),
  getCodingSessionArtifactsStatus: vi.fn(),
  getCodingSessionArtifactsSessions: vi.fn(),
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
vi.mock("@/components/coding-sessions/CodexUsagePanel", () => ({
  CodexUsagePanel: () => <div data-testid="codex-usage-panel" />,
}));
vi.mock("@/lib/org/active-org", () => ({ requestOrganizationPicker: vi.fn() }));
vi.mock("@/lib/app-config", () => ({
  getWebAppOrigin: () => Promise.resolve("https://aimatrx.com"),
}));
vi.mock("@/lib/open-external", () => ({ openExternal: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

import { CodingSessions } from "./CodingSessions";

function overviewPayload(titles: string[], conversationId: string | null = "conv-1"): ClaudeOverview {
  return {
    schema_version: 2,
    account_id: "acct",
    accounts: [],
    listed_providers: ["claude_code"],
    cloud: { checked: true, sessions: titles.length, checked_at: null, reason: null, detail: null },
    conversations: titles.map((title, index) => ({
      session_id: `session-${index}`,
      provider: "claude_code",
      continuation: { command: `claude --resume session-${index}`, note: "only while local" },
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
  } as unknown as ClaudeOverview;
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
  mocks.getClaudeOverview.mockReset().mockResolvedValue(overviewPayload(["First session"]));
  mocks.getCodingSessionStatus.mockReset().mockResolvedValue(bridge);
  mocks.getCodingSessionProviderReadiness.mockReset().mockResolvedValue(readiness);
  mocks.getCodingSessionArtifactsStatus.mockReset().mockResolvedValue(null);
  mocks.getCodingSessionArtifactsSessions.mockReset().mockResolvedValue({ sessions: [] });
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
    expect(container.querySelector("[data-testid='sessions-tbody']")).not.toBeNull();
    expect(container.querySelector("[data-testid='codex-usage-panel']")).toBeNull();
    expect(container.textContent).toContain("First session");
  });

  it("renders usage inside this feature when the URL asks for that tab", async () => {
    await render("/coding-sessions?tab=usage");
    expect(container.querySelector("[data-testid='codex-usage-panel']")).not.toBeNull();
    expect(container.querySelector("[data-testid='sessions-tbody']")).toBeNull();
  });

  it("says plainly that a provider has no usage source instead of hiding it", async () => {
    await render("/coding-sessions?tab=usage");
    const claudeChip = findButton("Claude Code");
    await act(async () => {
      claudeChip.click();
    });
    expect(container.querySelector("[data-testid='usage-no-source']")?.textContent).toContain(
      "No usage source for Claude Code yet",
    );
  });

  it("puts the operational blocks on the settings tab, not over the list", async () => {
    await render("/coding-sessions?tab=settings");
    expect(container.querySelector("[data-testid='agent-runtime-card']")).not.toBeNull();
    expect(container.textContent).toContain("Installed here");
    expect(container.querySelector("[data-testid='sessions-tbody']")).toBeNull();
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
    const slow = deferred<ClaudeOverview>();
    mocks.getClaudeOverview.mockReturnValue(slow.promise);
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
      container.querySelector("[data-testid='sessions-tbody']")?.getAttribute("data-refreshing"),
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
    mocks.getClaudeOverview.mockRejectedValue(new Error("index unreadable"));
    await act(async () => {
      (container.querySelector("[data-testid='coding-sessions-refresh']") as HTMLButtonElement).click();
      await Promise.resolve();
    });
    expect(container.querySelector("[data-testid='sessions-error']")?.textContent).toContain(
      "index unreadable",
    );
    expect(container.textContent).toContain("First session");
  });
});

describe("A row is a door", () => {
  it("opens the session's conversation in the app's chat surface", async () => {
    await render("/coding-sessions");
    await act(async () => {
      (container.querySelector("[data-testid='conversation-row']") as HTMLElement).click();
    });
    expect(container.querySelector("[data-testid='location']")?.textContent).toBe(
      "/cloud-chat?conversation=conv-1-0&from=coding-sessions",
    );
  });

  it("says why there is nothing to open, and keeps delivery one click away", async () => {
    mocks.getClaudeOverview.mockResolvedValue(overviewPayload(["Queued session"], null));
    await render("/coding-sessions");
    await act(async () => {
      (container.querySelector("[data-testid='conversation-row']") as HTMLElement).click();
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
  function coldPayload(filesRead: number): ClaudeOverview {
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
    } as unknown as ClaudeOverview;
  }

  it("shows the first read with its counter instead of '0 conversations'", async () => {
    mocks.getClaudeOverview.mockResolvedValue(coldPayload(4_096));
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
      mocks.getClaudeOverview.mockResolvedValue(coldPayload(10));
      await render("/coding-sessions");
      expect(mocks.getClaudeOverview).toHaveBeenCalledTimes(1);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3_000);
      });
      expect(mocks.getClaudeOverview).toHaveBeenCalledTimes(2);
      // A fresh answer ends the polling.
      mocks.getClaudeOverview.mockResolvedValue(overviewPayload(["First session"]));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3_000);
      });
      expect(mocks.getClaudeOverview).toHaveBeenCalledTimes(3);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      expect(mocks.getClaudeOverview).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("A refresh running behind the answer", () => {
  function refreshingPayload(): ClaudeOverview {
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
    } as unknown as ClaudeOverview;
  }

  it("wears the announced-refresh state and says what the engine is re-reading", async () => {
    mocks.getClaudeOverview.mockResolvedValue(refreshingPayload());
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
      mocks.getClaudeOverview.mockResolvedValue(refreshingPayload());
      await render("/coding-sessions");
      expect(mocks.getClaudeOverview).toHaveBeenCalledTimes(1);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4_500);
      });
      expect(mocks.getClaudeOverview).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("The cloud check that has not happened yet", () => {
  function inFlightPayload(): ClaudeOverview {
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
    } as unknown as ClaudeOverview;
  }

  it("says 'not asked yet' quietly instead of 'could not be asked'", async () => {
    mocks.getClaudeOverview.mockResolvedValue(inFlightPayload());
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
    mocks.getClaudeOverview.mockResolvedValue(inFlightPayload());
    await render("/coding-sessions");
    const cell = container.querySelector("[data-testid='state-checking']");
    expect(cell?.textContent).toContain("Checking…");
    expect(container.textContent).not.toContain("Unknown");
  });

  it("shows how old a real answer is", async () => {
    const base = overviewPayload(["First session"]);
    mocks.getClaudeOverview.mockResolvedValue({
      ...base,
      cloud: { ...base.cloud, sessions: 1_671, age_seconds: 240, refreshing: false },
    } as unknown as ClaudeOverview);
    await render("/coding-sessions");
    expect(container.querySelector("[data-testid='cloud-checked-note']")?.textContent).toContain(
      "checked 4m ago",
    );
    expect(container.querySelector("[data-testid='coding-sessions-subtitle']")?.textContent).toContain(
      "AI Matrx holds 1,671 of them (checked 4m ago)",
    );
  });
});
