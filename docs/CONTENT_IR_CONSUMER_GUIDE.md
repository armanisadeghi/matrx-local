# Content IR Consumer Guide — Matrx Local

**Audience:** coding agents building Matrx Local features that display, validate,
route, cache, or pass through Content IR.

**Scope:** consumption only. This guide does not authorize a consumer to create
or mutate kinds, schemas, components, or other Content IR definitions.

**Verified against code and public registry:** 2026-08-24

## The short version

Content IR lets the platform give structured content a stable kind identity,
schema, version, and rendering contract. A kind may be platform supplied or
created by a user through the in-app `kind_creator` agent. Once published, a
user-created kind can be live immediately and can have a bespoke component.

Matrx Local must treat kinds as **runtime data**, not as a compile-time enum:

1. Obtain the RLS-authorized resolved kind catalog from the cloud.
2. Preserve an inbound Content IR envelope exactly; never reparse and rewrite
   it just to display it.
3. Validate and route it using the resolved kind/version when available.
4. Render a known bundled component only when the local app owns that mapping.
5. Otherwise use a safe generic structured fallback. A missing, stale,
   unauthorized, or unsupported component must never make the content vanish.

Do **not** connect this desktop app directly to `content_ir.*` tables, use a
service-role key, duplicate the canonical KindSchema model, or execute database
component source as application code.

## What is actually live today

The platform now supports this flow:

```text
kind_creator agent
  -> content_ir kind definition + optional component records
  -> resolved catalog / kind descriptor
  -> canonical Content IR envelope or kind-tagged value
  -> consumer cache + validator + router
  -> bundled component | vetted custom-component shell | generic fallback
```

The user-facing Shapes studio in Matrx Frontend already creates, tests, saves,
and renders user-defined kinds and their instances. Its current custom database
component path is React-specific: an allowlisted compile path and an iframe HTML
flavor. It is not safe or portable to copy as arbitrary code evaluation.

### Content at the boundary

Consumers will encounter either of these forms:

- A canonical envelope in `metadata.__ir`. This is authoritative framing for
  an already-processed value.
- A raw structured value containing `__kind`. This needs normal Content IR
  parsing/normalization at an appropriate content boundary.

The precise envelope types and helpers are exported by the shared core. The
important behavioral rule is simple: **when `metadata.__ir` is valid, retain
the original value and envelope.** Do not serialize, tokenize, or normalize it
again merely because the UI needs to render it.

## Matrx Local's integration contract

Matrx Local is an untrusted desktop client. Both the React UI and Python
sidecar run on the user's machine. Therefore:

- Authenticate as the signed-in user and call an aidream platform API (or a
  platform client built on that API).
- Let the server/RLS decide which public and user-owned kinds, instances, and
  components are visible.
- Keep privileged schema/component administration in aidream.
- Cache only data the signed-in user was entitled to receive, and invalidate it
  on auth change, explicit catalog version/ETag change, or a kind version
  mismatch.

The generated API types currently expose `GET /workflow/kinds` and
`GET /workflow/kinds/{slug}` as catalog-shaped endpoints. They are useful
starting points, but their documentation describes a **public** catalog. Before
using them for a Shapes-created private kind, verify that the exact endpoint
returns RLS-visible user kinds and their active components. If it does not,
add/consume an authenticated resolved-catalog endpoint; do not bypass it with
a direct database query.

### Minimum local boundaries

Keep the new feature behind small host-specific adapters rather than spreading
cloud and rendering assumptions through chat/tool UI code:

```ts
interface KindCatalogClient {
  list(options?: { etag?: string }): Promise<ResolvedKindCatalog>;
  get(slug: string, options?: { version?: number }): Promise<KindDescriptor>;
}

interface ContentIrDiagnostics {
  report(event: ContentIrDiagnostic): void;
}

interface KindComponentPolicy {
  resolve(descriptor: KindDescriptor): RenderPlan;
}
```

`ResolvedKindCatalog`, `KindDescriptor`, and `RenderPlan` are application
adapters around platform DTOs. Do not make a second definition language. Map a
resolved descriptor into the shared `KindSchema` with the shared storage
transform, retaining the server-provided kind, version, schema, facets, and
component descriptors.

A sensible local home is `desktop/src/features/content-ir/` with separate
`catalog/`, `runtime/`, `render/`, and `adapters/` modules. It keeps the
feature usable by chat, tools, artifacts, and later desktop surfaces without
binding the core to any one UI.

## Required consumption flow

### 1. Resolve the catalog before relying on a bespoke renderer

Fetch the user-authorized catalog at a controlled boundary. Use the endpoint's
ETag/version semantics if supplied; retain a last-known-good cache for offline
display, labelled as such. Lookup by `kind`/slug and version, not display label.

When a streamed value names a kind that is not yet local, show the generic
structured fallback, request or refresh the descriptor, then repaint when the
kind becomes available. This matters for a kind that an agent created moments
earlier.

### 2. Ingest the envelope without destroying provenance

Use the shared helpers for the appropriate shape:

- `sanitizeInboundEnvelopeMetadata` / envelope readers for incoming metadata.
- `envelopeFromCompleteValue` for a known complete structured value.
- `normalizeJsonRegion` only when processing a raw detected JSON region.

Invalid or untrusted envelope metadata should be stripped or rejected through
the documented helper and reported through `ContentIrDiagnostics`; rendering
may still fall back to the raw content. Never silently invent a kind identity.

### 3. Validate at the right time

Use the resolved KindSchema and the shared JSON-schema/KindSchema utilities to
validate structured values at boundaries where validity matters (for example,
before an editable form writes a value, or before a tool consumes one).

Rendering should be resilient: a value that fails a non-security validation
check still gets a clearly marked generic viewer. A component or schema version
mismatch is a reason to refresh/fallback, never a reason to evaluate a less
trusted renderer.

### 4. Choose a renderer by policy

Apply this order:

1. **Bundled component:** a Local-owned component registered for the exact
   supported kind/component contract.
2. **Vetted custom component shell:** only after the platform, source type,
   component version, and trust policy say the host can support it safely.
3. **Generic structured fallback:** a stable recursive object/array/scalar UI,
   with the kind label/version and raw-data affordances.

The generic fallback is a product requirement, not a temporary error state. It
is what makes broad adoption safe while custom components propagate.

### 5. Handle components as untrusted remote content

`source: "db"` is data from a remote database; it is not permission to call
`eval`, `new Function`, arbitrary dynamic imports, or unbounded
`dangerouslySetInnerHTML`. Matrx Frontend's DB component implementation has a
specific allowlisted compiler and sandboxed iframe protocol. Matrx Local must
either use an extracted, reviewed cross-platform version of that protocol or
choose the generic fallback.

The same applies even more strongly to the Chrome extension, where extension
CSP and permissions impose additional constraints.

## Non-negotiable invariants

- **One source of schema truth:** consume canonical resolved definitions; do
  not infer a replacement schema from samples or component props.
- **Identity is stable:** use kind slug and version, not labels, file names,
  prompt wording, or component names.
- **Definitions and instances differ:** a consumer may read a definition and
  render an instance. It does not write a definition as a side effect.
- **Preserve framing:** valid `metadata.__ir` is preserved byte-for-byte in
  spirit and semantic content; no reparse/rewrite loop.
- **No privileged client access:** no service role, JWT secret, or direct
  administrative table query in Local/extension code.
- **Fail closed on executable presentation; fail open on display:** unsupported
  component execution falls back to a safe viewer, not a weaker sandbox.
- **Diagnostics are injectable:** report malformed envelopes, unresolved
  kinds, version drift, and rejected components without coupling the portable
  core to a particular telemetry product.

## Shared TypeScript portability verdict

### What is portable now

**Install immutable registry artifacts.** Matrx Local pins
`@ai-matrx/content-ir@0.2.0` and `@ai-matrx/content-ir-react@0.1.0` exactly.
Both packages ship built ESM/CommonJS and declarations; this repo contains no
workspace link, copied kernel, or vendored package tarball. The core parser,
normalization, envelope, schema, fingerprint, session, storage transform, and
JSON Schema conversion remain independent of Local.

That is enough to reuse the **core data-processing layer** in:

| Target | Core parse/schema/envelope logic | Full kind consumption/UI |
| --- | --- | --- |
| Matrx Local (React/Vite) | Yes | Needs local catalog adapter and renderer |
| Regular HTML/JavaScript | Yes | Needs catalog adapter and a non-React renderer |
| Chrome extension | Yes | Needs catalog adapter plus extension-safe component policy |

### What is not turnkey yet

Further, the following remain host-coupled:

- RLS-aware catalog retrieval, caching, and descriptor-to-schema hydration.
- Streaming/region integration and telemetry wiring.
- React route/registry and frontend state bindings.
- The user-authored database-component compiler/sandbox runtime.

**Conclusion:** packaging is complete for both the core and React registry.
End-to-end consumption still requires each host's authorized catalog,
diagnostics, streaming integration, and component trust policy.

## WHAT IS BUILT (2026-08-23) — the first consumer slice landed

Prerequisites 1–3 below are DONE for this app; 4–5 remain open. What shipped:

| Piece | Where |
|---|---|
| Shared packages consumed (no local fork) | Exact public `@ai-matrx/content-ir@0.2.0` + `@ai-matrx/content-ir-react@0.1.0` registry artifacts |
| Catalog client — authenticated, RLS-filtered, ETag'd | `desktop/src/features/content-ir/catalog/client.ts` → `GET /workflow/kinds` + `/{slug}` |
| Registries (identity + component bindings, ONE fetch) | `desktop/src/features/content-ir/runtime/registry.ts` |
| Envelope ingestion (preserve valid, strip + report malformed) | `desktop/src/features/content-ir/runtime/inbound.ts` |
| Bundled components + policy + generic floor | `desktop/src/features/content-ir/render/` |
| Stream integration — a kind block, not flattened markdown | `desktop/src/lib/chat-blocks.ts` (`ChatKindBlock`) → `ChatMessages.tsx` |
| Fixture tests on REAL server-built envelopes | `desktop/src/features/content-ir/kind-blocks.test.tsx` |

**The platform token is `desktop`.** `content_ir.kind_component.platform` is a
CHECK-constrained vocabulary; a host that lies there renders the wrong
component everywhere. Registered rows (migration `010_kind_component_desktop.sql`):
`markdown` · `web_search_results` / `google_search_results` /
`news_search_results` · `flashcard_set` · `quiz_set`. Adding a component here
is a ROW plus a `dispatch.tsx` entry — two explicit halves, never one.

**Custom DB components remain OFF** (prerequisite 4). `source='db'` rows carry
user-authored code and this app has no reviewed sandbox protocol, so the
registry never carries the body and such a binding falls to the generic floor.
Do not change that without the extracted protocol.

**Auth invalidation is wired**: `use-auth.ts` calls `resetContentIr()` on every
auth-state change and re-warms when a session exists — a catalog RLS-filtered
for one identity is never reused for another.

## Prerequisites to make consumption repeatable

Complete these in this order. They are deliberately small and independently
verifiable.

1. **DONE — publish the shared distributions.** The core and React registry
   are exact public dependencies with ESM/CommonJS and declarations. Consumer
   builds prove the installed artifacts; never restore a workspace link or
   committed tarball.
2. **DONE — provide an authorized resolved-kind API contract.** It returns all
   RLS-visible public and user-owned kinds/components a consumer may render,
   with kind/version/ETag semantics and a by-slug read. Local sends the current
   user session to `/workflow/kinds`; the server remains visibility authority.
3. **DONE — build Matrx Local's generic Content IR viewer.** It integrates the package,
   catalog adapter, cache, envelope ingestion, generic fallback, and a small
   bundled-component registry. This is the first high-value consumer slice.
4. **Extract a shared custom-component sandbox protocol.** Make the existing
   Frontend trust rules, input/output contract, and iframe isolation reusable.
   Each host must still opt in with its own CSP and capability policy.
5. **Add the Chrome/vanilla adapters.** Reuse steps 1–2 and the generic viewer;
   custom components remain disabled until the protocol is approved there.

## Acceptance checklist for a Matrx Local consumer change

- [ ] Uses the shared core package; no local fork/copy of parser or envelope
  logic.
- [ ] Uses an authenticated, RLS-authorized platform catalog path—not a direct
  `content_ir` table query.
- [ ] Handles an unknown newly-created kind with a useful generic viewer.
- [ ] Preserves a valid inbound `metadata.__ir` envelope without reprocessing.
- [ ] Invalid envelope metadata reports diagnostics and degrades safely.
- [ ] Refreshes/repaints when kind or component version changes.
- [ ] Renders unsupported or rejected DB components through the generic
  fallback, never arbitrary JavaScript/HTML.
- [ ] Has fixture tests for a bundled kind, a user-defined kind, missing kind,
  malformed envelope, schema mismatch, offline cache, and rejected component.
- [ ] Does not change a kind definition or require privileged credentials.

## Primary source references

- Current system status and user-authored kinds:
  `/Users/armanisadeghi/code/common-docs/systems/content-ir-system/FEATURE.md`
- Frontend architecture and Shapes studio:
  `/Users/armanisadeghi/code/matrx-frontend/features/content-ir/FEATURE.md`
- Portable-core twin rules and manifest:
  `/Users/armanisadeghi/code/aidream/apps/shared/content-ir-core/FEATURE.md`
  and `TWIN_MANIFEST.json`
- Core package entry point:
  `/Users/armanisadeghi/code/aidream/apps/shared/content-ir-core/index.ts`
- Current catalog DTO types in Local:
  `desktop/src/types/python-generated/api-types.ts` (`/workflow/kinds` and
  `KindDescriptor`)

When a reference conflicts with working code, treat the code and synchronized
manifest as authoritative. Do not edit twin files in aidream; Content IR core
is authored in Matrx Frontend and synchronized into aidream by its prescribed
scripts.
