/**
 * THE agent catalog for this desktop — TWO CLIENTS, ONE PACKAGE, ONE PICKER.
 *
 * 🚨 Ruling D4 (Arman, 2026-09-08 —
 * `common-docs/projects/npm-package-extraction/AGENT-PICKER-DESIGN.md`):
 *
 * > "the SQL light mirror is simply designed to give a user offline access, and
 * > so nothing should ever change … matrx local has an option to work locally.
 * > And when it works locally, the data is saved locally. But the structure,
 * > the format, and everything else must be absolutely identical."
 *
 * So this file contains NO list code, NO sort, NO filter, NO row shape and no
 * agent name. All of that is `@ai-matrx/agents/catalog`, the same build every
 * other Matrx client renders. The only thing that differs between Cloud Chat
 * and local chat is WHERE the identical `agx_get_list_full` rows come from:
 *
 *   CLOUD  (`/cloud-chat`) → the real Supabase client → `agx_get_list_full()`.
 *   LOCAL  (`/chat`)       → a structural client that POSTs the same RPC name
 *                            to the sidecar's offline door
 *                            `POST {engine}/agents/catalog/rpc`, which replays
 *                            the SQLite mirror of those same rows in the
 *                            database's own order (see
 *                            `app/api/agent_catalog_routes.py` and
 *                            `app/services/agent_catalog/FEATURE.md`).
 *
 * The structural client is a subset of `SupabaseClient` — `rpc()` (thenable,
 * `.order()`, `.range()`) and `schema().from().update().eq()` — exactly the
 * seam the package already reads online. It is a TRANSPORT, never a second
 * implementation.
 *
 * FAILURE POSTURE — nothing silent, nothing faked:
 *   - an RPC the offline mirror does not serve (`agx_search`,
 *     `agx_resolve_agent_address`) answers with a real PostgREST-shaped error
 *     naming what IS served. The package reports it and keeps the local
 *     scorer; it never sees an empty array it would read as "no matches".
 *   - the ONE write (`agent.definition.is_favorite`) REFUSES offline with the
 *     reason. There is deliberately no offline write queue: a star that
 *     silently vanishes on reconnect is worse than a refusal (FEATURE.md
 *     § Favourites).
 *
 * canonical-agent-picker-exempt: this file is the TRANSPORT under the one
 * package picker, not a picker. It names `agx_get_list_full` exactly once — as
 * the string the sidecar's offline door is keyed on — and renders no list, no
 * row, no sort and no filter. Every agent-selection UI in this repo must still
 * render `@ai-matrx/agents/catalog/react`.
 */

import {
  applyOrganizationContextHeader,
} from "@ai-matrx/agents/matrx";
import {
  createAgentCatalog,
  type AgentCatalog,
  type AgentCatalogClient,
  type AgentCatalogRpcCall,
  type AgentCatalogTableQuery,
  type AgentCatalogTransport,
  type PgResultLike,
} from "@ai-matrx/agents/catalog";

import { engine } from "@/lib/api";
import { getAIDreamServerUrl } from "@/lib/app-config";
import { logError, logWarn } from "@/lib/error-reporting";
import { requireActiveOrganizationId } from "@/lib/org/active-org";
import supabase from "@/lib/supabase";

/** The one RPC the offline mirror replays. Mirrors `client.py::CATALOG_RPC`. */
const OFFLINE_RPC = "agx_get_list_full";

/** The sidecar door lane a's contract pins. */
const OFFLINE_RPC_PATH = "/agents/catalog/rpc";

// ── Identity ─────────────────────────────────────────────────────────────────

/**
 * Who is asking. Set from `useAuth().user.id` in `App.tsx` — the app already
 * owns the session, and the catalog must never run a second auth read that
 * could disagree with the screen.
 */
let signedInUserId: string | null = null;

export function setAgentCatalogUserId(userId: string | null): void {
  signedInUserId = userId;
}

function requireUserId(): string {
  if (!signedInUserId) {
    throw new Error(
      "matrx-local agent catalog: no signed-in user. The catalog is read AS " +
        "the user (row membership is theirs) — sign in, then reopen the picker.",
    );
  }
  return signedInUserId;
}

const identity = { requireUserId };

// ── The aidream transport (mandate default-row resolution only) ──────────────

/**
 * The package resolves a picker's `defaultMandateKey` through the aidream door
 * `GET /mandates/{key}/resolution` — THE PLATFORM RULE D-R1: a client never
 * walks the mandate ladder itself. This is the same authenticated, org-scoped
 * call `lib/aidream-client.ts` already makes, expressed as the package's
 * transport port.
 */
const aidreamTransport: AgentCatalogTransport = {
  async fetch(path, init) {
    const base = await getAIDreamServerUrl();
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (!session?.access_token) {
      throw new Error(
        "Sign in before the picker can resolve the platform default agent.",
      );
    }
    let headers: Record<string, string> = {
      ...init.headers,
      Authorization: `Bearer ${session.access_token}`,
    };
    // aidream refuses every authenticated request that names no organization
    // before it routes; the header kernel is the package's own.
    headers = applyOrganizationContextHeader(
      headers,
      await requireActiveOrganizationId(),
    );
    return fetch(`${base}/api${path}`, {
      ...init,
      headers,
      ...(init.signal ? { signal: init.signal } : {}),
    });
  },
};

// ── The LOCAL structural client ──────────────────────────────────────────────

function refusal(message: string, code: string): PgResultLike {
  return { data: null, error: { message, code }, count: null };
}

function compareValues(a: unknown, b: unknown): number {
  const left = a == null ? "" : String(a);
  const right = b == null ? "" : String(b);
  return left < right ? -1 : left > right ? 1 : 0;
}

/**
 * Read the whole offline mirror once per RPC call. The sidecar answers with
 * the bare row array `supabase.rpc("agx_get_list_full")` returns — byte-shape
 * identical, in the database's own order — or throws (503 when the mirror has
 * never synced, because an empty array would be a lie).
 */
async function readOfflineRows(): Promise<unknown[]> {
  const payload = await engine.post(OFFLINE_RPC_PATH, { fn: OFFLINE_RPC });
  if (!Array.isArray(payload)) {
    throw new Error(
      `${OFFLINE_RPC_PATH} answered with ${typeof payload}, not the row array ` +
        "its contract pins (app/api/agent_catalog_routes.py).",
    );
  }
  return payload;
}

function offlineListCall(): AgentCatalogRpcCall {
  let orderColumn: string | null = null;
  let ascending = true;

  const settle = async (
    from?: number,
    to?: number,
  ): Promise<PgResultLike> => {
    let rows: unknown[];
    try {
      rows = await readOfflineRows();
    } catch (error) {
      return refusal(
        `The offline agent catalog is unavailable: ${
          error instanceof Error ? error.message : String(error)
        }`,
        "matrx_local_mirror_unavailable",
      );
    }
    if (orderColumn) {
      const column = orderColumn;
      rows = [...rows].sort((a, b) => {
        const direction = compareValues(
          (a as Record<string, unknown> | null)?.[column],
          (b as Record<string, unknown> | null)?.[column],
        );
        return ascending ? direction : -direction;
      });
    }
    const count = rows.length;
    const page =
      from === undefined || to === undefined ? rows : rows.slice(from, to + 1);
    return { data: page, error: null, count };
  };

  const call: AgentCatalogRpcCall = {
    order(column, options) {
      orderColumn = column;
      ascending = options?.ascending !== false;
      return call;
    },
    range(from, to) {
      return settle(from, to);
    },
    then(onFulfilled, onRejected) {
      return settle().then(onFulfilled, onRejected);
    },
  };
  return call;
}

function offlineRefusalCall(fn: string): AgentCatalogRpcCall {
  const result = refusal(
    `"${fn}" is not served by the offline agent catalog. The local mirror ` +
      `replays exactly one RPC — ${OFFLINE_RPC} — so the row set, its shape ` +
      "and its order stay identical to the platform's. Reconnect to run this " +
      "read against Supabase.",
    "matrx_local_rpc_not_mirrored",
  );
  const call: AgentCatalogRpcCall = {
    order() {
      return call;
    },
    range() {
      return Promise.resolve(result);
    },
    then(onFulfilled, onRejected) {
      return Promise.resolve(result).then(onFulfilled, onRejected);
    },
  };
  return call;
}

const offlineWriteTable: AgentCatalogTableQuery = {
  update() {
    return {
      eq() {
        return Promise.resolve({
          error: {
            message:
              "Favourites cannot be changed while this chat is reading the " +
              "offline catalog. There is no offline write queue on purpose — a " +
              "star that silently disappeared on reconnect would be worse than " +
              "this refusal. Reconnect, or star the agent in Cloud Chat.",
            code: "matrx_local_offline_write",
          },
        });
      },
    };
  },
};

/**
 * The LOCAL client: the sidecar's offline mirror, wearing the exact interface
 * the package already speaks to Supabase. Exported so its contract can be
 * tested directly — this seam is the whole of ruling D4's enforcement.
 */
export const localCatalogClient: AgentCatalogClient = {
  rpc(fn) {
    return fn === OFFLINE_RPC ? offlineListCall() : offlineRefusalCall(fn);
  },
  schema() {
    return {
      from() {
        return offlineWriteTable;
      },
    };
  },
};

// ── The two catalogs ─────────────────────────────────────────────────────────

/**
 * A PRECONDITION, not a fault: the picker is mounted (every app page stays
 * mounted in `AppLayout`) before the thing it needs exists yet. The local
 * engine is still being discovered; the person has not chosen an organization
 * yet and the org picker is already on screen asking. Both are STATES with a
 * visible remedy — the same posture this repo takes for a missing API key or an
 * unavailable Credential Vault — and neither is silent: the package renders its
 * own error row / banner the moment someone opens the picker, and this still
 * lands in the unified log as a warning.
 *
 * Everything else is a fault and is reported as one.
 */
function isPrecondition(message: string): boolean {
  return (
    message.includes("Engine not discovered") ||
    message.includes("matrx_local_mirror_unavailable") ||
    message.includes("The offline agent catalog is unavailable") ||
    message.includes("No organization is selected") ||
    message.includes("no signed-in user")
  );
}

function errorSink(scope: "cloud" | "local") {
  return (event: { code: string; message: string; context?: object }) => {
    const report = isPrecondition(event.message) ? logWarn : logError;
    report(`agent-catalog:${scope}`, `${event.code}: ${event.message}`, event.context);
  };
}

let cloudCatalog: AgentCatalog | null = null;
let localCatalog: AgentCatalog | null = null;

/** Cloud Chat's catalog — the real Supabase client, the live platform rows. */
export function getCloudAgentCatalog(): AgentCatalog {
  cloudCatalog ??= createAgentCatalog({
    catalogId: "matrx-local.cloud",
    // supabase-js satisfies the structural client as-is.
    client: supabase as unknown as AgentCatalogClient,
    identity,
    transport: aidreamTransport,
    errorSink: errorSink("cloud"),
  });
  return cloudCatalog;
}

/** Local chat's catalog — the SAME rows, read from the sidecar's SQLite mirror. */
export function getLocalAgentCatalog(): AgentCatalog {
  localCatalog ??= createAgentCatalog({
    catalogId: "matrx-local.offline",
    client: localCatalogClient,
    identity,
    transport: aidreamTransport,
    errorSink: errorSink("local"),
  });
  return localCatalog;
}

/** The offline structural client, for tests and for the local catalog above. */
export function getLocalAgentCatalogClient(): AgentCatalogClient {
  return localCatalogClient;
}
