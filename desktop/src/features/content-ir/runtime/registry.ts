/**
 * THE DESKTOP'S CONTENT IR REGISTRIES — what a kind is, and which component
 * draws it here.
 *
 * Both are fed by ONE catalog fetch (`GET /workflow/kinds`, RLS-filtered by
 * the server) rather than two table reads: the descriptor carries the kind
 * AND its `kind_component` bindings, so a single authorized read answers both
 * questions and there is no second definition language
 * (docs/CONTENT_IR_CONSUMER_GUIDE.md § Non-negotiable invariants).
 *
 * The RESOLVER itself is `ComponentResolver` from
 * `@ai-matrx/content-ir-react` — the same tier rules, the same repaint
 * counters, the same loud recovery matrx-frontend uses. Only the loaders are
 * ours.
 */

import {
  ComponentResolver,
  type KindComponentRow,
  type ComponentRole,
} from "@ai-matrx/content-ir-react";
import type { KindDefinition } from "@ai-matrx/content-ir/registry";
import {
  fetchKind,
  fetchKindCatalog,
  type KindComponentDescriptor,
  type KindDescriptor,
} from "../catalog/client";
import { CONTENT_IR_PLATFORM } from "../platform";
import { reportContentIrError } from "./diagnostics";

// ─── The shared catalog cache ──────────────────────────────────────────────

/**
 * One in-flight catalog fetch shared by both registries. Two independent
 * `ensureWarm()` calls (the resolver's and the kind source's) must not become
 * two HTTP requests.
 */
let catalogPromise: Promise<KindDescriptor[]> | null = null;
let catalogEtag: string | null = null;
let cachedKinds: KindDescriptor[] = [];

function loadCatalog(): Promise<KindDescriptor[]> {
  if (!catalogPromise) {
    catalogPromise = fetchKindCatalog(catalogEtag)
      .then(({ catalog, etag }) => {
        catalogEtag = etag;
        // A 304 means the cache is current — keep it, do not blank it.
        if (catalog) cachedKinds = catalog.kinds;
        return cachedKinds;
      })
      .catch((error: unknown) => {
        // Loud and RETRYABLE: a failed catalog load must not permanently
        // convince this app that every kind is unregistered.
        catalogPromise = null;
        reportContentIrError({
          source: "content-ir",
          message:
            "kind catalog load failed — every kind reads as unregistered until this succeeds: " +
            (error instanceof Error ? error.message : String(error)),
          relation: "kind-catalog",
          raw: error,
        });
        return cachedKinds;
      });
  }
  return catalogPromise;
}

/** Drop the cache — call on auth change (the guide's cache-invalidation rule). */
export function invalidateKindCatalog(): void {
  catalogPromise = null;
  catalogEtag = null;
  cachedKinds = [];
}

// ─── Kind definitions ──────────────────────────────────────────────────────

/**
 * A consuming client's kind definition carries IDENTITY, not behaviour: no
 * compiled bridge (there is no legacy component set here to bridge to). The
 * server already validated the envelope, so the schema is not needed to
 * RENDER — it is fetched by slug only when something needs to validate.
 *
 * Identity alone is load-bearing: the shared route uses "is this kind
 * registered" to separate a KNOWN shape with no component here — which earns
 * the generic structured floor and its honest notice — from a slug the
 * platform has never heard of, which is left untouched.
 */
function definitionOf(descriptor: KindDescriptor): KindDefinition {
  return { kind: descriptor.kind, schema: null, schemaSource: "content_ir", tier: "warm" };
}

class DesktopKindSource {
  private readonly known = new Map<string, KindDefinition>();
  private warmPromise: Promise<void> | null = null;
  private version = 0;
  private readonly kindVersions = new Map<string, number>();
  private readonly kindListeners = new Map<string, Set<() => void>>();

  getDefinition(kind: string): KindDefinition | undefined {
    return this.known.get(kind);
  }

  getKindVersion(kind: string): number {
    return this.kindVersions.get(kind) ?? this.version;
  }

  subscribeKind(kind: string, listener: () => void): () => void {
    let set = this.kindListeners.get(kind);
    if (!set) {
      set = new Set();
      this.kindListeners.set(kind, set);
    }
    set.add(listener);
    return () => set.delete(listener);
  }

  ensureWarm(): Promise<void> {
    if (!this.warmPromise) {
      this.warmPromise = loadCatalog().then((kinds) => {
        for (const descriptor of kinds) this.known.set(descriptor.kind, definitionOf(descriptor));
        this.bump();
      });
    }
    return this.warmPromise;
  }

  /** Learn ONE kind the warm catalog predates (an agent minted it minutes ago). */
  learn(descriptor: KindDescriptor): void {
    this.known.set(descriptor.kind, definitionOf(descriptor));
    this.bump(descriptor.kind);
  }

  reset(): void {
    this.known.clear();
    this.warmPromise = null;
    this.bump();
  }

  private bump(kind?: string): void {
    this.version += 1;
    const kinds = kind ? [kind] : [...this.kindListeners.keys()];
    for (const slug of kinds) {
      this.kindVersions.set(slug, this.version);
      for (const listener of this.kindListeners.get(slug) ?? []) listener();
    }
  }
}

export const kindRegistry = new DesktopKindSource();

// ─── Component resolution ──────────────────────────────────────────────────

function toRow(kind: string, descriptor: KindComponentDescriptor): KindComponentRow {
  return {
    kind,
    platform: descriptor.platform,
    // The column is free text on the wire; the resolver dispatches on two
    // roles. Reading anything else as `output` would draw an INPUT component
    // where an output was meant.
    role: descriptor.role === "input" ? "input" : ("output" satisfies ComponentRole),
    componentKey: descriptor.component_key,
    source: descriptor.source,
    config: descriptor.config ?? {},
    isActive: descriptor.is_active,
    // `source='db'` is a user-authored component body. THE GUIDE'S RULE
    // (§5, "Handle components as untrusted remote content"): this app has no
    // reviewed sandbox protocol, so it never carries the code and a db-source
    // binding simply falls to the generic floor. Never `eval`, never
    // `new Function`, never unbounded `dangerouslySetInnerHTML`.
    componentSource: null,
    propsTransform: null,
    pinnedKindVersion: null,
    updatedAt: null,
    createdBy: null,
  };
}

/**
 * THE PLATFORM FILTER is correctness, not an optimisation: a `web` row names a
 * Next.js component this app does not have, and resolving one here would type
 * a block as something nothing can draw.
 */
function rowsFor(descriptors: KindDescriptor[]): KindComponentRow[] {
  const rows: KindComponentRow[] = [];
  for (const descriptor of descriptors) {
    for (const component of descriptor.components ?? []) {
      if (component.platform !== CONTENT_IR_PLATFORM) continue;
      rows.push(toRow(descriptor.kind, component));
    }
  }
  return rows;
}

export const componentRegistry = new ComponentResolver({
  loadAll: async () => rowsFor(await loadCatalog()),
  loadForKind: async (kind) => {
    const descriptor = await fetchKind(kind);
    if (!descriptor) return [];
    // A cold hit teaches the kind source too — the block that triggered this
    // fetch repaints once, with both halves known.
    kindRegistry.learn(descriptor);
    return rowsFor([descriptor]);
  },
  reportError: reportContentIrError,
});

/** Warm both registries once (one HTTP request — they share the catalog). */
export function warmContentIr(): void {
  void kindRegistry.ensureWarm();
  void componentRegistry.ensureWarm();
}

/** Auth changed: nothing cached was necessarily visible to the new user. */
export function resetContentIr(): void {
  invalidateKindCatalog();
  kindRegistry.reset();
  componentRegistry.replaceDbRows([]);
}
