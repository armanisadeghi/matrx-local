/**
 * The LOCAL structural client is a TRANSPORT, not a second catalog.
 *
 * Ruling D4: offline is a data LOCATION — never a different list, structure,
 * sort, filter or UI. These tests pin the three ways that promise could quietly
 * break: the list read must reach the sidecar's one door and come back in the
 * exact PostgREST result shape the package reads online; an RPC the mirror does
 * not replay must ERROR rather than answer an empty array the package would
 * read as "no matches"; and the offline favourite write must REFUSE rather than
 * report a success that vanishes on reconnect.
 *
 * canonical-agent-picker-exempt: this file tests the transport under the one
 * package picker; it renders nothing and builds no list.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const { post, get } = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));
vi.mock("@/lib/api", () => ({ engine: { post, get } }));
vi.mock("@/lib/supabase", () => ({ default: { auth: { getSession: vi.fn() } } }));

import { getLocalAgentCatalogClient } from "./agent-catalog";

const ROW_A = { id: "b", name: "Beta", agent_type: "user", is_active: true };
const ROW_B = { id: "a", name: "Alpha", agent_type: "builtin", is_active: true };

beforeEach(() => {
  post.mockReset();
  get.mockReset();
});

describe("the offline catalog client", () => {
  it("proxies the list read to the sidecar door in the PostgREST result shape", async () => {
    post.mockResolvedValue([ROW_A, ROW_B]);

    const result = await getLocalAgentCatalogClient()
      .rpc("agx_get_list_full", {}, { count: "exact" })
      .order("id", { ascending: true })
      .range(0, 999);

    expect(post).toHaveBeenCalledWith("/agents/catalog/rpc", {
      fn: "agx_get_list_full",
    });
    expect(result.error).toBeNull();
    // `.order("id")` is HONOURED, not ignored — the package pages on it, and a
    // page that ignores its own ordering is a silently truncated catalogue.
    expect((result.data as { id: string }[]).map((r) => r.id)).toEqual(["a", "b"]);
    // `count` is the WHOLE mirror, so a short page is detectable as short.
    expect(result.count).toBe(2);
  });

  it("pages by range instead of handing every page the whole mirror", async () => {
    post.mockResolvedValue([ROW_A, ROW_B]);

    const page = await getLocalAgentCatalogClient()
      .rpc("agx_get_list_full")
      .order("id", { ascending: true })
      .range(1, 1);

    expect((page.data as { id: string }[]).map((r) => r.id)).toEqual(["b"]);
    expect(page.count).toBe(2);
  });

  it("REFUSES an RPC the mirror does not replay — never an empty array", async () => {
    const result = await getLocalAgentCatalogClient().rpc("agx_search", {
      p_query: "anything",
    });

    expect(post).not.toHaveBeenCalled();
    expect(result.data).toBeNull();
    expect(result.error?.code).toBe("matrx_local_rpc_not_mirrored");
    expect(result.error?.message).toContain("agx_get_list_full");
  });

  it("exposes schema RPCs required by the associations data source and refuses unsupported reads", async () => {
    const result = await getLocalAgentCatalogClient()
      .schema("public")
      .rpc("ues_get_bulk", { p_entity_type: "agent" });

    expect(post).not.toHaveBeenCalled();
    expect(result.error?.code).toBe("matrx_local_rpc_not_mirrored");
  });

  it("REFUSES a supported RPC in the wrong schema", async () => {
    const result = await getLocalAgentCatalogClient()
      .schema("platform")
      .rpc("agx_get_list_full");

    expect(post).not.toHaveBeenCalled();
    expect(result.error?.code).toBe("matrx_local_rpc_not_mirrored");
  });

  it("REFUSES table reads outside the offline catalog mirror", () => {
    expect(() => getLocalAgentCatalogClient().schema("public").from("agents"))
      .toThrow("reconnect to read this table from Supabase");
    expect(post).not.toHaveBeenCalled();
  });

  it("REFUSES the offline favourite write instead of faking success", async () => {
    // Stars are written through `ues_set` (package 0.16+); offline it must be
    // refused loudly, never answered with a fake success.
    const { error } = await getLocalAgentCatalogClient().rpc("ues_set", {
      p_entity_type: "agent",
      p_entity_id: "b",
    });

    expect(error).not.toBeNull();
    expect(error?.code).toBe("matrx_local_rpc_not_mirrored");
    // The refusal must carry the remedy, not just the fact.
    expect(error?.message).toContain("agx_get_list_full");
  });

  it("surfaces an unreachable mirror as a loud error, not as zero agents", async () => {
    post.mockRejectedValue(new Error("Engine not discovered"));

    const result = await getLocalAgentCatalogClient()
      .rpc("agx_get_list_full")
      .range(0, 999);

    expect(result.data).toBeNull();
    expect(result.error?.code).toBe("matrx_local_mirror_unavailable");
    expect(result.error?.message).toContain("Engine not discovered");
  });
});
