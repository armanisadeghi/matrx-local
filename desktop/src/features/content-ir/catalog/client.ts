/**
 * THE KIND CATALOG CLIENT — `KindCatalogClient` from
 * docs/CONTENT_IR_CONSUMER_GUIDE.md § Minimum local boundaries.
 *
 * 🚨 This app does NOT read `content_ir.*` tables. The guide's rule is
 * explicit and it is the right one: Matrx Local is an untrusted desktop
 * client, so which kinds and components the signed-in user may see is a
 * SERVER decision. `GET /workflow/kinds` is the authenticated, RLS-filtered,
 * ETag'd catalog that answers it (`load_accessible_kind_catalog` in aidream —
 * the by-slug read is `GET /workflow/kinds/{slug}`), which satisfies the
 * guide's prerequisite #2.
 *
 * Anonymous callers get the public catalog; authenticated callers
 * additionally get every internal Shape RLS grants them. Either way the client
 * never decides visibility.
 */

import type { components } from "@/types/python-generated/api-types";
import { getAIDreamServerUrl } from "@/lib/app-config";
import supabase from "@/lib/supabase";

/** Generated wire types are the source of truth — never hand-mirrored. */
export type KindDescriptor = components["schemas"]["KindDescriptor"];
export type KindComponentDescriptor = components["schemas"]["KindComponentDescriptor"];
export type KindCatalogResponse = components["schemas"]["KindCatalogResponse"];

export interface CatalogFetch {
  /** Null when the server answered 304 — the caller's cache is current. */
  catalog: KindCatalogResponse | null;
  etag: string | null;
}

async function authHeaders(): Promise<Record<string, string>> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * Fetch the caller-visible catalog. Pass the previous `etag` to get a cheap
 * 304 instead of the whole payload.
 */
export async function fetchKindCatalog(etag?: string | null): Promise<CatalogFetch> {
  const base = await getAIDreamServerUrl();
  const response = await fetch(`${base}/workflow/kinds`, {
    headers: {
      ...(await authHeaders()),
      ...(etag ? { "If-None-Match": etag } : {}),
    },
  });

  if (response.status === 304) {
    return { catalog: null, etag: etag ?? null };
  }
  if (!response.ok) {
    throw new Error(`GET /workflow/kinds failed: ${response.status} ${response.statusText}`);
  }

  const catalog = (await response.json()) as KindCatalogResponse;
  return { catalog, etag: response.headers.get("ETag") ?? catalog.etag ?? null };
}

/**
 * Fetch ONE kind by slug — the cold path for a kind an agent created moments
 * ago, which the warm catalog predates. Returns null for an unknown slug (a
 * 404 here is an answer, not a failure).
 */
export async function fetchKind(slug: string): Promise<KindDescriptor | null> {
  const base = await getAIDreamServerUrl();
  const response = await fetch(
    `${base}/workflow/kinds/${encodeURIComponent(slug)}`,
    { headers: await authHeaders() },
  );
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(`GET /workflow/kinds/${slug} failed: ${response.status}`);
  }
  return (await response.json()) as KindDescriptor;
}
