import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * THE LOAD LADDER UNDER TEST (Arman, 2026-10-07)
 *
 *   The window sets its organization once, at load, and it is never none for
 *   a person with a membership: this device's own last choice -> the account's
 *   last active organization -> its start-up organization -> the first
 *   (oldest) organization, each kept only if a current membership. A ladder
 *   answer is NOT stored as the device choice and NOT pushed to the headless
 *   engine; only a deliberate switch writes the device choice, tells the
 *   engine, and calls `users.set_last_active_organization`. There is no hold
 *   and no picker on a window request.
 *
 * They also keep the older guarantee: the resolver never round-trips through
 * `GET /auth/whoami` (or any aidream HTTP call) — aidream itself 400s without
 * `X-Organization-Id`, so asking it to bootstrap the header is circular.
 */

const { rpc, schemaRpc, prefsMaybeSingle, from, getAuthedSession, currentSession, enginePut, engineDelete } = vi.hoisted(() => ({
  rpc: vi.fn(),
  schemaRpc: vi.fn(),
  prefsMaybeSingle: vi.fn(),
  from: vi.fn(),
  getAuthedSession: vi.fn(),
  currentSession: vi.fn(),
  enginePut: vi.fn(),
  engineDelete: vi.fn(),
}));

vi.mock("@/lib/supabase", () => ({
  default: { rpc, schema: vi.fn(() => ({ from, rpc: schemaRpc })) },
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
  schemaRpc.mockReset().mockResolvedValue({ error: null });
  prefsMaybeSingle.mockReset().mockResolvedValue({ data: null, error: null });
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
  // In the order given = oldest first (created_at ascending).
  rpc.mockResolvedValue({
    data: containerIds.map((id, i) => ({
      container_id: id,
      status: "active",
      created_at: `2026-01-0${i + 1}T00:00:00Z`,
    })),
    error: null,
  });
}

/** The account's two saved organization columns (null row = no preferences row). */
function mockAccountChoice(lastActive: string | null, startup: string | null) {
  prefsMaybeSingle.mockResolvedValue({
    data: { last_active_organization_id: lastActive, startup_organization_id: startup },
    error: null,
  });
}

/**
 * `organizations` and `user_preferences` answer; ANY other table throws, so a
 * resolver reaching for a stray table cannot pass silently.
 */
function mockOrganizationsTable(
  rows: Array<{ id: string; name: string }>,
) {
  const inFn = vi.fn().mockResolvedValue({ data: rows, error: null });
  const select = vi.fn().mockReturnValue({ in: inFn });
  from.mockImplementation((table: string) => {
    if (table === "organizations") return { select };
    if (table === "user_preferences") {
      return { select: () => ({ eq: () => ({ maybeSingle: prefsMaybeSingle }) }) };
    }
    throw new Error(`the resolver read the "${table}" table — only the ladder's tables are allowed`);
  });
}

const TWO_ORGS = [
  { id: "org-1", name: "First Org" },
  { id: "org-2", name: "Second Org" },
];

describe("resolveActiveOrganization — the load ladder", () => {
  it("never makes an HTTP call (e.g. GET /auth/whoami) — Supabase and this device only", async () => {
    const fetchSpy = vi.fn();
    (globalThis as unknown as { fetch: typeof fetch }).fetch = fetchSpy as unknown as typeof fetch;
    mockMemberships(["org-1"]);
    mockOrganizationsTable([{ id: "org-1", name: "Solo Org" }]);

    const { resolveActiveOrganization } = await import("./active-org");
    expect((await resolveActiveOrganization())?.id).toBe("org-1");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("rung 1: this device's own last choice beats the account", async () => {
    seedStored("user-1", { id: "org-1", name: "First Org" });
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable(TWO_ORGS);
    mockAccountChoice("org-2", "org-2");

    const { resolveActiveOrganization } = await import("./active-org");
    expect((await resolveActiveOrganization())?.id).toBe("org-1");
  });

  it("rung 2: with no device choice, the account's last active organization wins", async () => {
    mockMemberships(["org-1", "org-2", "org-3"]);
    mockOrganizationsTable([...TWO_ORGS, { id: "org-3", name: "Third Org" }]);
    mockAccountChoice("org-2", "org-3");

    const { resolveActiveOrganization } = await import("./active-org");
    expect((await resolveActiveOrganization())?.id).toBe("org-2");
  });

  it("rung 3: a last active organization that is no longer a membership falls to the start-up organization", async () => {
    mockMemberships(["org-1", "org-3"]);
    mockOrganizationsTable([
      { id: "org-1", name: "First Org" },
      { id: "org-3", name: "Third Org" },
    ]);
    mockAccountChoice("org-gone", "org-3");

    const { resolveActiveOrganization } = await import("./active-org");
    expect((await resolveActiveOrganization())?.id).toBe("org-3");
  });

  it("rung 4: with neither saved, the FIRST (oldest) organization — never none", async () => {
    mockMemberships(["org-acme", "org-ddi"]);
    // The database returns rows in no particular order.
    mockOrganizationsTable([
      { id: "org-ddi", name: "Data Destruction Inc" },
      { id: "org-acme", name: "Acme Recycling" },
    ]);

    const { resolveActiveOrganization } = await import("./active-org");
    expect((await resolveActiveOrganization())?.id).toBe("org-acme");
  });

  it("a person with no preferences row at all still opens on their first organization", async () => {
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable(TWO_ORGS);
    prefsMaybeSingle.mockResolvedValue({ data: null, error: null });

    const { resolveActiveOrganization } = await import("./active-org");
    expect((await resolveActiveOrganization())?.id).toBe("org-1");
  });

  it("a ladder answer is NOT stored as a device choice, but IS pushed to the engine as the window's organization", async () => {
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable(TWO_ORGS);
    mockAccountChoice("org-2", null);

    const mod = await import("./active-org");
    expect((await mod.resolveActiveOrganization())?.id).toBe("org-2");

    expect(mod.getActiveOrganizationSnapshot().organization?.id).toBe("org-2");
    expect(storage.getItem(STORAGE_KEY)).toBeNull();
    // The window SHOWS org-2, so the engine acts in it — nobody is asked.
    await vi.waitFor(() =>
      expect(enginePut).toHaveBeenCalledWith("/organization/active", { organization_id: "org-2" }),
    );
    // Opening the app is not a switch: nothing is saved to the account.
    expect(schemaRpc).not.toHaveBeenCalled();
    // Set once at load: the next request reuses it with no second ladder run.
    rpc.mockClear();
    expect(await mod.getActiveOrganizationId()).toBe("org-2");
    expect(rpc).not.toHaveBeenCalled();
  });

  it("drops a device choice the user is no longer a member of and re-runs the ladder", async () => {
    seedStored("user-1", { id: "org-gone", name: "Removed Org" });
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable(TWO_ORGS);
    mockAccountChoice("org-2", null);

    const { resolveActiveOrganization } = await import("./active-org");
    expect((await resolveActiveOrganization())?.id).toBe("org-2");
    expect(storedFor("user-1")).toBeUndefined();
  });

  it("excludes an archived organization from the ladder", async () => {
    mockMemberships(["org-old", "org-1"]);
    // The first membership's organization is archived: the ladder skips it.
    const inFn = vi.fn().mockResolvedValue({
      data: [
        { id: "org-old", name: "Closed Org", archived_at: "2026-09-30T00:00:00Z" },
        { id: "org-1", name: "First Org", archived_at: null },
      ],
      error: null,
    });
    from.mockImplementation((table: string) =>
      table === "organizations"
        ? { select: vi.fn().mockReturnValue({ in: inFn }) }
        : { select: () => ({ eq: () => ({ maybeSingle: prefsMaybeSingle }) }) },
    );

    const { resolveActiveOrganization } = await import("./active-org");
    expect((await resolveActiveOrganization())?.id).toBe("org-1");
  });

  it("a failed account read is an honest error, never a guessed first organization", async () => {
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable(TWO_ORGS);
    prefsMaybeSingle.mockResolvedValue({ data: null, error: { message: "boom" } });

    const mod = await import("./active-org");
    await expect(mod.resolveActiveOrganization()).rejects.toThrow("Could not read your saved");
    expect(mod.getActiveOrganizationSnapshot().organization).toBeNull();
  });
});

describe("requireActiveOrganizationId — no hold, no picker", () => {
  it("returns the ladder's organization immediately and never raises the picker", async () => {
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable(TWO_ORGS);

    const mod = await import("./active-org");
    await expect(mod.requireActiveOrganizationId()).resolves.toBe("org-1");
    expect(fakeWindow.seen).not.toContain(mod.REQUEST_PICKER_EVENT);
  });

  it("a switch tells the engine, saves the account's last active organization, and is what the next request carries", async () => {
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable(TWO_ORGS);

    const mod = await import("./active-org");
    await mod.setActiveOrganization("org-2");

    expect(enginePut).toHaveBeenCalledWith("/organization/active", {
      organization_id: "org-2",
    });
    expect(schemaRpc).toHaveBeenCalledWith("set_last_active_organization", {
      p_organization_id: "org-2",
    });
    expect(storedFor("user-1")).toMatchObject({ id: "org-2" });
    await expect(mod.requireActiveOrganizationId()).resolves.toBe("org-2");
  });

  it("a failed account save is VISIBLE on the snapshot while the local switch stands", async () => {
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable(TWO_ORGS);
    schemaRpc.mockResolvedValue({ error: { message: "rpc down" } });
    vi.spyOn(console, "error").mockImplementation(() => {});

    const mod = await import("./active-org");
    await mod.setActiveOrganization("org-2");

    const snap = mod.getActiveOrganizationSnapshot();
    expect(snap.organization?.id).toBe("org-2");
    expect(storedFor("user-1")).toMatchObject({ id: "org-2" });
    expect(snap.saveError).toMatch(/could not be saved to your account/i);

    // The next successful switch clears it.
    schemaRpc.mockResolvedValue({ error: null });
    await mod.setActiveOrganization("org-1");
    expect(mod.getActiveOrganizationSnapshot().saveError).toBeNull();
  });

  it("refuses to switch to an organization the user is not a member of — nothing is written", async () => {
    mockMemberships(["org-1"]);
    mockOrganizationsTable([{ id: "org-1", name: "Solo Org" }]);

    const mod = await import("./active-org");
    await expect(mod.setActiveOrganization("org-9")).rejects.toThrow("not a member");
    expect(schemaRpc).not.toHaveBeenCalled();
    expect(enginePut).not.toHaveBeenCalled();
  });

  it("zero memberships is the one honest none: it settles immediately with a create-or-join remedy and raises nothing", async () => {
    mockMemberships([]);
    mockOrganizationsTable([]);

    const mod = await import("./active-org");
    const err = await mod.requireActiveOrganizationId().catch((e: unknown) => e);

    expect(err).toBeInstanceOf(mod.OrganizationNoMembershipsError);
    const remedy = (err as InstanceType<typeof mod.OrganizationNoMembershipsError>).remedy;
    expect(remedy).toMatch(/do not belong to any organization/i);
    expect(remedy).toMatch(/organizations/i);
    expect(fakeWindow.seen).not.toContain(mod.REQUEST_PICKER_EVENT);
    expect(prefsMaybeSingle).not.toHaveBeenCalled();
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

  it("re-states the window's ladder organization to the engine, so a multi-org person is never asked", async () => {
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable(TWO_ORGS);
    mockAccountChoice("org-2", null);

    const mod = await import("./active-org");
    await mod.resolveActiveOrganization();
    enginePut.mockClear();
    await expect(mod.republishActiveOrganizationToEngine()).resolves.toBe(true);
    expect(enginePut).toHaveBeenCalledWith("/organization/active", { organization_id: "org-2" });
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
