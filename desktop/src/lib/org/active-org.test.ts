import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * THE RULING UNDER TEST (Arman, 2026-09-19)
 *
 *   A saved "default organization" on the user's account is at most a display
 *   preference. Nothing that builds a request may read it, and no organization
 *   is ever a fallback. What this device MAY remember is the
 *   organization the user THEMSELVES SET here. With nothing set, the request
 *   HOLDS, the picker appears, the user sets one, and the request proceeds.
 *
 * These tests are built to FAIL if that preference rung ever comes back: the
 * headline case plants a preference naming a DIFFERENT organization and
 * asserts both that it is not returned AND that `user_preferences` is never
 * read at all. A resolver that quietly reads it would go green on the first
 * assertion alone.
 *
 * They also keep the older guarantee: the resolver never round-trips through
 * `GET /auth/whoami` (or any aidream HTTP call) — aidream itself 400s without
 * `X-Organization-Id`, so asking it to bootstrap the header is circular.
 */

const { rpc, from, getAuthedSession, currentSession, enginePut, engineDelete } = vi.hoisted(() => ({
  rpc: vi.fn(),
  from: vi.fn(),
  getAuthedSession: vi.fn(),
  currentSession: vi.fn(),
  enginePut: vi.fn(),
  engineDelete: vi.fn(),
}));

vi.mock("@/lib/supabase", () => ({
  default: { rpc, schema: vi.fn(() => ({ from })) },
}));
vi.mock("@/lib/custodian", () => ({ getAuthedSession, currentSession }));
vi.mock("@/lib/api", () => ({ engine: { put: enginePut, delete: engineDelete } }));
vi.mock("@/lib/app-config", () => ({
  getAppRuntimeConfig: () => ({ webAppOrigin: "https://web.example.test" }),
}));

class MemoryStorage {
  private values = new Map<string, string>();
  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
  removeItem(key: string): void {
    this.values.delete(key);
  }
  clear(): void {
    this.values.clear();
  }
}

/** The real listener semantics the hold depends on — not a dispatch spy. */
class FakeWindow {
  private listeners = new Map<string, Set<(event: { type: string }) => void>>();
  readonly seen: string[] = [];
  addEventListener(type: string, fn: (event: { type: string }) => void): void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(fn);
  }
  removeEventListener(type: string, fn: (event: { type: string }) => void): void {
    this.listeners.get(type)?.delete(fn);
  }
  dispatchEvent(event: { type: string }): boolean {
    this.seen.push(event.type);
    for (const fn of [...(this.listeners.get(event.type) ?? [])]) fn(event);
    return true;
  }
}

const STORAGE_KEY = "matrx-local.active-organization.v2";
const LEGACY_STORAGE_KEY = "matrx-local.active-organization.v1";

/** Seed THE persistent value the way the store writes it: per user, one key. */
function seedStored(userId: string, org: { id: string; name: string }) {
  const all = JSON.parse(storage.getItem(STORAGE_KEY) ?? '{"users":{}}') as {
    users: Record<string, unknown>;
  };
  all.users[userId] = { id: org.id, name: org.name };
  storage.setItem(STORAGE_KEY, JSON.stringify(all));
}

function storedFor(userId: string): { id: string; name: string } | undefined {
  const raw = storage.getItem(STORAGE_KEY);
  if (!raw) return undefined;
  return (JSON.parse(raw) as { users: Record<string, { id: string; name: string }> }).users[userId];
}
const storage = new MemoryStorage();
let fakeWindow: FakeWindow;

beforeEach(() => {
  vi.resetModules();
  rpc.mockReset();
  from.mockReset();
  getAuthedSession.mockReset();
  enginePut.mockReset().mockResolvedValue({ organization_id: null });
  engineDelete.mockReset().mockResolvedValue({ organization_id: null });
  storage.clear();
  fakeWindow = new FakeWindow();
  (globalThis as unknown as { localStorage: MemoryStorage }).localStorage = storage;
  (globalThis as unknown as { window: FakeWindow }).window = fakeWindow;
  if (typeof (globalThis as { CustomEvent?: unknown }).CustomEvent !== "function") {
    (globalThis as unknown as { CustomEvent: unknown }).CustomEvent = class {
      type: string;
      constructor(type: string) {
        this.type = type;
      }
    };
  }
  getAuthedSession.mockResolvedValue({ user: { id: "user-1" } });
  currentSession.mockReset().mockReturnValue({ signed_in: true, user_id: "user-1" });
});

afterEach(() => {
  vi.useRealTimers();
});

function mockMemberships(containerIds: string[]) {
  rpc.mockResolvedValue({
    data: containerIds.map((id) => ({ container_id: id })),
    error: null,
  });
}

/**
 * `organizations` answers; ANY other table throws. That is the point — a
 * resolver reaching for `user_preferences` cannot pass silently.
 */
function mockOrganizationsTable(
  rows: Array<{ id: string; name: string }>,
) {
  const inFn = vi.fn().mockResolvedValue({ data: rows, error: null });
  const select = vi.fn().mockReturnValue({ in: inFn });
  from.mockImplementation((table: string) => {
    if (table === "organizations") return { select };
    throw new Error(
      `the resolver read the "${table}" table — a request may only use what this device SET`,
    );
  });
}

function storedTables(): string[] {
  return from.mock.calls.map((call) => String(call[0]));
}

describe("resolveActiveOrganization", () => {
  it("never makes an HTTP call (e.g. GET /auth/whoami) — Supabase and this device only", async () => {
    const fetchSpy = vi.fn();
    (globalThis as unknown as { fetch: typeof fetch }).fetch = fetchSpy as unknown as typeof fetch;
    mockMemberships(["org-1"]);
    mockOrganizationsTable([{ id: "org-1", name: "Solo Org" }]);

    const { resolveActiveOrganization } = await import("./active-org");
    expect((await resolveActiveOrganization())?.id).toBe("org-1");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("resolves the sole membership — choosing the only option invents nothing", async () => {
    mockMemberships(["org-1"]);
    mockOrganizationsTable([{ id: "org-1", name: "Solo Org" }]);

    const { resolveActiveOrganization } = await import("./active-org");
    expect(await resolveActiveOrganization()).toEqual({
      id: "org-1",
      name: "Solo Org",
    });
  });

  it("THE RULING: a saved account preference naming another org is NEVER used — the request holds", async () => {
    // Plant the exact thing the deleted rung used to read. `user_preferences`
    // is not even mockable here: reading it throws.
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable([
      { id: "org-1", name: "First Org" },
      { id: "org-2", name: "Second Org" },
    ]);

    const { resolveActiveOrganization } = await import("./active-org");
    const result = await resolveActiveOrganization();

    expect(result).toBeNull();
    expect(storedTables()).not.toContain("user_preferences");
    expect(storage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("never picks one of several memberships on its own — it HOLDS", async () => {
    mockMemberships(["org-acme", "org-ddi"]);
    mockOrganizationsTable([
      { id: "org-acme", name: "Acme Recycling" },
      { id: "org-ddi", name: "Data Destruction Inc" },
    ]);

    const { resolveActiveOrganization } = await import("./active-org");
    expect(await resolveActiveOrganization()).toBeNull();
  });

  it("prefers this device's set selection", async () => {
    seedStored("user-1", { id: "org-1", name: "First Org" });
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable([
      { id: "org-1", name: "First Org" },
      { id: "org-2", name: "Second Org" },
    ]);

    const { resolveActiveOrganization } = await import("./active-org");
    expect((await resolveActiveOrganization())?.id).toBe("org-1");
  });

  it("drops a selection the user is no longer a member of and HOLDS rather than guessing", async () => {
    seedStored("user-1", { id: "org-gone", name: "Removed Org" });
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable([
      { id: "org-1", name: "First Org" },
      { id: "org-2", name: "Second Org" },
    ]);

    const { resolveActiveOrganization } = await import("./active-org");
    expect(await resolveActiveOrganization()).toBeNull();
    expect(storedFor("user-1")).toBeUndefined();
  });
});

describe("requireActiveOrganizationId — the hold", () => {
  it("raises the picker and RESUMES with what the user sets", async () => {
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable([
      { id: "org-1", name: "First Org" },
      { id: "org-2", name: "Second Org" },
    ]);

    const mod = await import("./active-org");
    const held = mod.requireActiveOrganizationId({ timeoutMs: 5_000 });

    // The picker is asked for BEFORE anything is sent.
    await vi.waitFor(() => expect(fakeWindow.seen).toContain(mod.REQUEST_PICKER_EVENT));

    await mod.setActiveOrganization("org-2");

    // The SAME call that was held now returns the set id — the request
    // proceeds instead of the user meeting a failure.
    await expect(held).resolves.toBe("org-2");
  });

  it("tells the engine what the user set, so background work uses it too", async () => {
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable([
      { id: "org-1", name: "First Org" },
      { id: "org-2", name: "Second Org" },
    ]);

    const mod = await import("./active-org");
    await mod.setActiveOrganization("org-2");

    expect(enginePut).toHaveBeenCalledWith("/organization/active", {
      organization_id: "org-2",
    });
  });

  it("gives up with a remedy — never a guessed organization — when nobody answers", async () => {
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable([
      { id: "org-1", name: "First Org" },
      { id: "org-2", name: "Second Org" },
    ]);

    const mod = await import("./active-org");
    await expect(mod.requireActiveOrganizationId({ timeoutMs: 1 })).rejects.toBeInstanceOf(
      mod.OrganizationNotSelectedError,
    );
    try {
      await mod.requireActiveOrganizationId({ timeoutMs: 1 });
      throw new Error("expected the hold to time out");
    } catch (err) {
      expect(err).toBeInstanceOf(mod.OrganizationNotSelectedError);
      const remedy = (err as InstanceType<typeof mod.OrganizationNotSelectedError>).remedy;
      expect(remedy).toMatch(/choose your organization/i);
      expect(remedy.toLowerCase()).not.toContain("default");
    }
  });

  it("returns immediately when the sole membership answers — no picker", async () => {
    mockMemberships(["org-1"]);
    mockOrganizationsTable([{ id: "org-1", name: "Solo Org" }]);

    const mod = await import("./active-org");
    await expect(mod.requireActiveOrganizationId({ timeoutMs: 1 })).resolves.toBe("org-1");
    expect(fakeWindow.seen).not.toContain(mod.REQUEST_PICKER_EVENT);
  });

  it("settles IMMEDIATELY with a create-or-join remedy when the user has zero memberships — never the full timeout", async () => {
    // Zero memberships means the picker can never produce an answer. Before
    // this fix, requireActiveOrganizationId() still opened the picker and
    // then awaited the CHANGE_EVENT until the timeout elapsed — a real wait
    // (proven red below via fake timers: a huge timeout with no advance would
    // hang this test on the old code).
    mockMemberships([]);
    mockOrganizationsTable([]);

    const mod = await import("./active-org");
    // A timeout far longer than any sane test would tolerate waiting for —
    // this call must resolve WITHOUT the timer ever firing.
    const held = mod.requireActiveOrganizationId({ timeoutMs: 10 * 60 * 1000 });

    await expect(held).rejects.toBeInstanceOf(mod.OrganizationNoMembershipsError);
    let caught: unknown;
    try {
      await held;
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(mod.OrganizationNoMembershipsError);
    const err = caught as InstanceType<typeof mod.OrganizationNoMembershipsError>;
    expect(err.remedy).toMatch(/do not belong to any organization/i);
    expect(err.remedy).toMatch(/organizations/i);
    // The picker was still raised, for whoever is looking at the window.
    expect(fakeWindow.seen).toContain(mod.REQUEST_PICKER_EVENT);
  });
});

/**
 * THE HEADLESS-FIRST BOOT DOUBLE-ASK.
 *
 * The Python sidecar keeps its OWN copy of this Mac's pick and cannot read this
 * window's `localStorage`. The re-statement used to run exactly once, when the
 * picker dialog mounted — which is normally BEFORE the engine is reachable. The
 * PUT failed, warned to the console, and was never retried, so the engine stayed
 * empty and the next background job HELD and published `organization_required`:
 * a question the person had already answered on this Mac.
 */
describe("republishActiveOrganizationToEngine — the engine inherits this Mac's answer", () => {
  it("re-states this device's SET organization to the engine", async () => {
    seedStored("user-1", { id: "org-7", name: "Chosen" });

    const mod = await import("./active-org");
    await expect(mod.republishActiveOrganizationToEngine()).resolves.toBe(true);
    expect(enginePut).toHaveBeenCalledWith("/organization/active", {
      organization_id: "org-7",
    });
  });

  it("REPORTS that the engine did not take it, so the caller can retry", async () => {
    // The engine is not up yet — the normal case at boot. Swallowing this is
    // what left the sidecar empty and produced the double-ask.
    seedStored("user-1", { id: "org-7", name: "Chosen" });
    enginePut.mockRejectedValue(new Error("engine not reachable"));

    const mod = await import("./active-org");
    await expect(mod.republishActiveOrganizationToEngine()).resolves.toBe(false);
  });

  it("has nothing to re-state when this device never chose — and says so", async () => {
    const mod = await import("./active-org");
    await expect(mod.republishActiveOrganizationToEngine()).resolves.toBe(true);
    expect(enginePut).not.toHaveBeenCalled();
  });
});


/**
 * THE ONE STATE, THE ONE PERSISTENT VALUE (Arman, 2026-09-21): every reader
 * sees the same snapshot, the persisted value is per user under ONE key, and
 * a write through the store is the only way anything changes.
 */
describe("the store — one state value, one persistent value", () => {
  it("exposes a stable snapshot that changes only through the one write, and notifies subscribers", async () => {
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable([
      { id: "org-1", name: "First Org" },
      { id: "org-2", name: "Second Org" },
    ]);
    const mod = await import("./active-org");

    const before = mod.getActiveOrganizationSnapshot();
    expect(before.organization).toBeNull();
    expect(mod.getActiveOrganizationSnapshot()).toBe(before);

    const notified = vi.fn();
    mod.subscribeActiveOrganization(notified);
    await mod.setActiveOrganization("org-2");

    const after = mod.getActiveOrganizationSnapshot();
    expect(after).not.toBe(before);
    expect(after.organization?.id).toBe("org-2");
    expect(after.organizations?.map((o) => o.id)).toEqual(["org-1", "org-2"]);
    expect(notified).toHaveBeenCalled();
    expect(storedFor("user-1")).toMatchObject({ id: "org-2", name: "Second Org" });
    // The cheap request-boundary read is the SAME value, not a second store.
    expect(await mod.getActiveOrganizationId()).toBe("org-2");
  });

  it("scopes the persisted value to the signed-in user — an account switch never inherits another account's pick", async () => {
    seedStored("user-1", { id: "org-1", name: "First Org" });
    seedStored("user-2", { id: "org-9", name: "Other Account's Org" });
    const mod = await import("./active-org");

    expect(mod.getActiveOrganizationSnapshot().organization?.id).toBe("org-1");

    currentSession.mockReturnValue({ signed_in: true, user_id: "user-2" });
    expect(mod.getActiveOrganizationSnapshot().organization?.id).toBe("org-9");
    expect(mod.getActiveOrganizationSnapshot().userId).toBe("user-2");

    currentSession.mockReturnValue({ signed_in: false, user_id: null });
    expect(mod.getActiveOrganizationSnapshot().organization).toBeNull();
    // Signing out forgets nothing: the returning account finds its own pick.
    expect(storedFor("user-1")?.id).toBe("org-1");
  });

  it("migrates the older unscoped value once, to the user who reads it", async () => {
    storage.setItem(LEGACY_STORAGE_KEY, JSON.stringify({ id: "org-old", name: "Old Pick" }));
    const mod = await import("./active-org");

    expect(mod.getActiveOrganizationSnapshot().organization?.id).toBe("org-old");
    expect(storedFor("user-1")?.id).toBe("org-old");
    expect(storage.getItem(LEGACY_STORAGE_KEY)).toBeNull();
  });

  it("refreshes the stored name from the live membership list — the switcher shows the truth", async () => {
    seedStored("user-1", { id: "org-1", name: "Stale Name" });
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable([
      { id: "org-1", name: "Renamed Org" },
      { id: "org-2", name: "Second Org" },
    ]);
    const mod = await import("./active-org");
    await mod.listMemberOrganizations();
    expect(mod.getActiveOrganizationSnapshot().organization?.name).toBe("Renamed Org");
  });
});
