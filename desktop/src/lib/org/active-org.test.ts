import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * These tests prove the resolver never round-trips through `GET
 * /auth/whoami` (or any aidream HTTP call at all) — it resolves purely from
 * Supabase (membership RPC + user_preferences) and this device's own
 * stored selection. aidream cannot answer "which organization does this
 * client carry" any more: whoami itself now 400s without a header, so
 * asking it to bootstrap the header would be circular.
 */

const { rpc, from, getSession } = vi.hoisted(() => ({
  rpc: vi.fn(),
  from: vi.fn(),
  getSession: vi.fn(),
}));

vi.mock("@/lib/supabase", () => ({
  default: {
    rpc,
    schema: vi.fn(() => ({ from })),
    auth: { getSession },
  },
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

const storage = new MemoryStorage();

beforeEach(() => {
  vi.restoreAllMocks();
  storage.clear();
  (globalThis as unknown as { localStorage: MemoryStorage }).localStorage = storage;
  (globalThis as unknown as { window: { dispatchEvent: (e: unknown) => void } }).window = {
    dispatchEvent: vi.fn(),
  };
  getSession.mockResolvedValue({ data: { session: { user: { id: "user-1" } } } });
});

function mockMemberships(containerIds: string[]) {
  rpc.mockResolvedValue({
    data: containerIds.map((id) => ({ container_id: id })),
    error: null,
  });
}

function mockOrganizationsTable(
  rows: Array<{ id: string; name: string; is_personal?: boolean }>,
) {
  const inFn = vi.fn().mockResolvedValue({ data: rows, error: null });
  const select = vi.fn().mockReturnValue({ in: inFn });
  from.mockImplementation((table: string) => {
    if (table === "organizations") return { select };
    // user_preferences path — default: no row, no preference.
    const maybeSingle = vi.fn().mockResolvedValue({ data: null, error: null });
    const eq = vi.fn().mockReturnValue({ maybeSingle });
    const prefSelect = vi.fn().mockReturnValue({ eq });
    return { select: prefSelect };
  });
}

function mockPreference(userId: string, organizationId: string | null) {
  const maybeSingle = vi.fn().mockResolvedValue({
    data: { preferences: { organization: { defaultOrganizationId: organizationId } } },
    error: null,
  });
  const eq = vi.fn((col: string, val: string) => {
    expect(col).toBe("user_id");
    expect(val).toBe(userId);
    return { maybeSingle };
  });
  const prefSelect = vi.fn().mockReturnValue({ eq });

  const priorFrom = from.getMockImplementation();
  from.mockImplementation((table: string) => {
    if (table === "user_preferences") return { select: prefSelect };
    return priorFrom!(table);
  });
}

describe("resolveActiveOrganization", () => {
  it("never makes an HTTP call (e.g. GET /auth/whoami) — resolves purely from Supabase + local storage", async () => {
    // aidream's own /auth/whoami now 400s without X-Organization-Id, so a
    // resolver that round-tripped through it to learn the organization
    // would be circular by construction. This asserts the network is never
    // touched at all.
    const fetchSpy = vi.fn();
    (globalThis as unknown as { fetch: typeof fetch }).fetch = fetchSpy as unknown as typeof fetch;

    mockMemberships(["org-1"]);
    mockOrganizationsTable([{ id: "org-1", name: "Solo Org", is_personal: false }]);

    const { resolveActiveOrganization } = await import("./active-org");
    const result = await resolveActiveOrganization();

    expect(result?.id).toBe("org-1");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("resolves the sole membership when nothing is stored and no default is set (positive control)", async () => {
    mockMemberships(["org-1"]);
    mockOrganizationsTable([{ id: "org-1", name: "Solo Org" }]);

    const { resolveActiveOrganization } = await import("./active-org");
    const result = await resolveActiveOrganization();

    expect(result).toEqual({ id: "org-1", name: "Solo Org", isPersonal: false });
  });

  it("refuses with a remedy for a multi-org user with no stored selection and no default preference", async () => {
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable([
      { id: "org-1", name: "First Org" },
      { id: "org-2", name: "Second Org" },
    ]);
    mockPreference("user-1", null);

    const { requireActiveOrganizationId, OrganizationNotSelectedError } = await import(
      "./active-org"
    );

    await expect(requireActiveOrganizationId()).rejects.toBeInstanceOf(
      OrganizationNotSelectedError,
    );
    try {
      await requireActiveOrganizationId();
      throw new Error("expected requireActiveOrganizationId to throw");
    } catch (err) {
      expect(err).toBeInstanceOf(OrganizationNotSelectedError);
      expect((err as InstanceType<typeof OrganizationNotSelectedError>).remedy).toMatch(
        /choose your organization/i,
      );
    }
  });

  it("resolves the user's durable default preference for a multi-org user (positive control)", async () => {
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable([
      { id: "org-1", name: "First Org" },
      { id: "org-2", name: "Second Org" },
    ]);
    mockPreference("user-1", "org-2");
    getSession.mockResolvedValue({ data: { session: { user: { id: "user-1" } } } });

    const { resolveActiveOrganization } = await import("./active-org");
    const result = await resolveActiveOrganization();

    expect(result?.id).toBe("org-2");
  });

  it("prefers a still-valid stored selection over the default preference", async () => {
    storage.setItem(
      "matrx-local.active-organization.v1",
      JSON.stringify({ id: "org-1", name: "First Org" }),
    );
    mockMemberships(["org-1", "org-2"]);
    mockOrganizationsTable([
      { id: "org-1", name: "First Org" },
      { id: "org-2", name: "Second Org" },
    ]);
    mockPreference("user-1", "org-2");

    const { resolveActiveOrganization } = await import("./active-org");
    const result = await resolveActiveOrganization();

    expect(result?.id).toBe("org-1");
  });

  it("drops a stored selection that is no longer an active membership", async () => {
    storage.setItem(
      "matrx-local.active-organization.v1",
      JSON.stringify({ id: "org-gone", name: "Removed Org" }),
    );
    mockMemberships(["org-1"]);
    mockOrganizationsTable([{ id: "org-1", name: "First Org" }]);

    const { resolveActiveOrganization } = await import("./active-org");
    const result = await resolveActiveOrganization();

    expect(result?.id).toBe("org-1");
    // The stale selection is gone — resolution fell through to the
    // sole-membership rule (which re-persists org-1), never keeping the
    // removed organization around.
    expect(storage.getItem("matrx-local.active-organization.v1")).toContain("org-1");
    expect(storage.getItem("matrx-local.active-organization.v1")).not.toContain("org-gone");
  });
});
