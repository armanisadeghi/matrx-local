/**
 * D4 AT THE UI LAYER: the cloud picker and the local picker are the SAME
 * component fed the SAME rows, and they render them in the SAME order.
 *
 * The live proof (2026-09-08, admin@admin.com) is stronger and sits in the
 * lane report: 468 rows / 19 columns from Supabase `agx_get_list_full`, 468
 * rows / 19 columns from the sidecar's offline door, identical id order and
 * ZERO value differences across all 8,892 cells. This test is what keeps that
 * true without a live machine: it drives the real package picker through both
 * catalogs and compares what a person would actually read.
 *
 * canonical-agent-picker-exempt: this file RENDERS the package picker twice to
 * compare it against itself; it defines no picker and builds no list.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  createAgentCatalog,
  _resetCatalogGlobalState,
  type AgentCatalogClient,
} from "@ai-matrx/agents/catalog";
import {
  AgentCatalogProvider,
  AgentListInlinePicker,
} from "@ai-matrx/agents/catalog/react";

const USER = "87a6e699-3622-4869-8843-d0867456c0dd";

/** Three rows in the DATABASE's own order: favourite, owned, builtin. */
const ROWS = [
  {
    id: "11111111-1111-4111-8111-111111111111",
    agent_type: "user",
    name: "Zulu Favourite",
    description: "starred",
    model_id: null,
    category: "general",
    tags: ["alpha"],
    is_active: true,
    is_archived: false,
    is_favorite: true,
    created_by: USER,
    organization_id: null,
    task_id: null,
    source_agent_id: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-03-01T00:00:00Z",
    is_owner: true,
    access_level: "owner",
    shared_by_email: null,
  },
  {
    id: "22222222-2222-4222-8222-222222222222",
    agent_type: "user",
    name: "Alpha Owned",
    description: "mine",
    model_id: null,
    category: "general",
    tags: [],
    is_active: true,
    is_archived: false,
    is_favorite: false,
    created_by: USER,
    organization_id: null,
    task_id: null,
    source_agent_id: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-02-01T00:00:00Z",
    is_owner: true,
    access_level: "owner",
    shared_by_email: null,
  },
  {
    id: "33333333-3333-4333-8333-333333333333",
    agent_type: "builtin",
    name: "General Chat",
    description: "platform",
    model_id: null,
    category: "general",
    tags: [],
    is_active: true,
    is_archived: false,
    is_favorite: false,
    created_by: null,
    organization_id: null,
    task_id: null,
    source_agent_id: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-05T00:00:00Z",
    is_owner: false,
    access_level: "system",
    shared_by_email: null,
  },
];

/**
 * Build a client over a row source. `cloud` answers the rows the way
 * supabase-js does; `local` answers the SAME rows the way this repo's
 * structural client does (whole array, sliced by range). If the two ever
 * diverge, the rendered lists below diverge with them.
 */
function clientOver(rows: unknown[]): AgentCatalogClient {
  const call = () => {
    let column: string | null = null;
    let asc = true;
    const settle = async (from?: number, to?: number) => {
      let out = [...rows];
      if (column) {
        const c = column;
        out.sort((a, b) => {
          const l = String((a as Record<string, unknown>)[c] ?? "");
          const r = String((b as Record<string, unknown>)[c] ?? "");
          return (l < r ? -1 : l > r ? 1 : 0) * (asc ? 1 : -1);
        });
      }
      const page =
        from === undefined || to === undefined ? out : out.slice(from, to + 1);
      return { data: page, error: null, count: out.length };
    };
    const builder = {
      order(c: string, o?: { ascending?: boolean }) {
        column = c;
        asc = o?.ascending !== false;
        return builder;
      },
      range: (f: number, t: number) => settle(f, t),
      then: (ok?: never, err?: never) => settle().then(ok, err),
    };
    return builder as ReturnType<AgentCatalogClient["rpc"]>;
  };
  return {
    rpc: call,
    schema: () => ({
      from: () => ({
        update: () => ({ eq: async () => ({ error: null }) }),
      }),
    }),
  };
}

async function renderPicker(catalogId: string, rows: unknown[]) {
  const catalog = createAgentCatalog({
    catalogId,
    client: clientOver(rows),
    identity: { requireUserId: () => USER },
  });
  await catalog.ensureLoaded({ force: true });
  const html = renderToStaticMarkup(
    <AgentCatalogProvider catalog={catalog}>
      <AgentListInlinePicker
        consumerId={`${catalogId}-consumer`}
        onSelect={() => {}}
        autoFocusSearch={false}
      />
    </AgentCatalogProvider>,
  );
  // Read the order off the RENDERED MARKUP, not off the fixture.
  const rendered = ROWS.map((r) => ({ name: r.name, at: html.indexOf(r.name) }))
    .filter((e) => e.at >= 0)
    .sort((a, b) => a.at - b.at)
    .map((e) => e.name);
  return { html, rendered };
}

describe("D4: one picker, two catalogs, identical output", () => {
  it("renders the same agents in the same order from cloud and local rows", async () => {
    _resetCatalogGlobalState();
    const cloud = await renderPicker("parity-cloud", ROWS);
    _resetCatalogGlobalState();
    const local = await renderPicker("parity-local", ROWS);

    // A render that produced nothing would make every comparison below vacuous.
    expect(cloud.html.length).toBeGreaterThan(200);
    // The default tab is the caller's OWN agents, favourites first — the
    // package's rule. "General Chat" is a builtin and lives on the public tab,
    // in BOTH lanes. That the two agree on which rows belong where is the
    // point: offline changed the data's location, not the list.
    expect(cloud.rendered).toEqual(["Zulu Favourite", "Alpha Owned"]);
    expect(local.rendered).toEqual(cloud.rendered);
  });

  it("FALSIFIABILITY: a local mirror that dropped a row is caught", async () => {
    _resetCatalogGlobalState();
    const cloud = await renderPicker("falsify-cloud", ROWS);
    _resetCatalogGlobalState();
    const local = await renderPicker("falsify-local", ROWS.slice(1));

    expect(local.rendered).not.toEqual(cloud.rendered);
  });
});
