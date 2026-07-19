# Filesystem discovery and index

This service is the canonical read/discovery layer for the user's **host
filesystem**. It is not the cloud agent workspace and it is not the managed
Files sync engine. Managed cloud files already appear beneath the local Files
root and are discovered here like every other local path.

## Boundary

- `roots.py` resolves Home, OS standard user folders, configured Matrx paths,
  user priority roots, and readable mounted volumes.
- `index.py` owns the dedicated SQLite metadata/content/embedding store and
  durable leased crawl queue.
- `service.py` owns direct paging, indexed search, bounded disk fallback,
  progressive crawling, watchers, reconciliation, and idle enrichment.
- `app/api/filesystem_routes.py` and `local_file` are adapters. They must not
  grow independent traversal implementations.

## Cross-platform root policy

No absolute macOS, Windows, or Linux user path is compiled into the feature.
Windows relocated/localized folders use the Known Folder API, Linux honors
XDG user directories, and macOS uses folders relative to `Path.home()`
(`Movies`, not Windows/Linux `Videos`). Volumes come from
`psutil.disk_partitions()`; virtual kernel filesystems are ignored.

Configured priority roots deepen first but never narrow scope. Home and every
readable real volume remain in the persistent low-priority queue. Overlapping
watch roots are collapsed to their common ancestor, and a per-generation
scanned-directory ledger prevents a lower-priority parent from repeatedly
re-enqueuing a priority subtree.

## Progressive tiers

1. Places and direct paged listing work before the index exists or is warm.
2. Metadata (path/name/kind/size/mtime) is crawled breadth-first and searched
   with SQLite FTS5. A leased queue survives crashes without losing subtrees.
3. Text-eligible files up to 1 MiB are indexed into local content FTS while
   CPU use is low.
4. If `fastembed` is installed and semantic indexing is enabled, one durable
   vector per current text file is generated at low priority. The bounded
   cosine-search consumer is exposed through `/filesystem/search-semantic` and
   `local_file.semantic_find`. FastEmbed is optional and never imported during
   startup.

Every tier is additive: unavailable content/embedding work cannot disable
Places, browsing, or metadata search.

## Lifecycle and maintenance

`app/main.py` starts `filesystem_index` through `app/launcher.py` after the
local database phase. Startup opens the small dedicated SQLite database,
discovers roots, then immediately returns while crawl/watch/enrichment tasks
run in the background. Shutdown sets a cooperative thread stop event, awaits
the crawl, cancels watchers/enrichment, and reports stopped only afterward.

Configured/user roots are watched with `watchfiles`. Low-priority volumes are
maintained through periodic six-hour reconciliation to avoid installing an
expensive recursive watcher on every mount. Crawl work yields above 70% CPU;
content and embeddings yield above 45% CPU.

## Settings

Stored under `filesystem_index` in the existing settings service:

- `priority_roots`: `{label,path}` entries; priority only, never an allowlist.
- `content_enabled`: defaults `true`.
- `semantic_enabled`: defaults `false`; reports unavailable when FastEmbed is
  not installed.
- `embedding_model`: defaults `BAAI/bge-small-en-v1.5`.
- `max_content_bytes`: defaults to 512 MiB (16 MiB–20 GiB).
- `max_embedding_entries`: defaults to 10,000 (maximum 50,000).

The structured APIs are `PUT /filesystem/priority-roots` and
`PUT /filesystem/indexing-settings`; `GET /filesystem/status` reports policy,
progress, FTS/capability state, and enrichment counts.

## Result contracts

All results carry `namespace: "host"` and a discriminating `kind`:

- `filesystem.places`
- `filesystem.directory-page` (`entries`, `next_cursor`, `total`, `source`)
- `filesystem.search-page` (`entries`, `next_cursor`, `index_complete`)
- `filesystem.content-search`
- `filesystem.semantic-search`

Tool adapters keep their short human-readable `output` for model context and
place the complete contract in `ToolResult.metadata` for UI renderers.

## Exclusions and bounds

Metadata traversal does not follow symlinks. Dependency/VCS/cache internals
(`node_modules`, `.git`, `.hg`, `.svn`, `__pycache__`, `.cache`) and Windows
trash/system-volume metadata are skipped. These exclusions avoid generated
noise; they do not omit sibling user directories or mounted volumes.

Directory pages cap at 500 entries. Direct listings use expiring, single-use,
query-scoped opaque cursors and an incremental SQLite snapshot. Each request
observes at most 20,000 raw entries, each snapshot caps at 1,000,000 visible
entries, and service-wide session/count limits evict abandoned snapshots.
Snapshot construction may return an empty progress page with a non-null cursor;
only an empty page without a cursor means the directory is empty. This keeps
memory and event-loop work bounded while preserving directory-first lexical
order across mutations. Fallback discovery observes both a time
deadline and a 50,000-entry budget. Index scans check a cooperative stop event
between entries and keep the durable directory lease until the complete
directory has been observed; exceptionally large directories are never marked
complete after a truncated prefix.

Canonical `path_key`/`parent_key` values use Windows case-folding when running
on Windows and native case semantics elsewhere. Descendant search and deletion
walk parent components recursively—never wildcard `LIKE` prefixes—so `%`, `_`,
drive, and UNC names cannot broaden an operation. Hidden state combines dotfile
names with Windows `FILE_ATTRIBUTE_HIDDEN` and macOS `UF_HIDDEN`.

Content and embedding commits compare the claimed size/mtime against both the
current disk and metadata row. A changed file is returned to the queue instead
of publishing a stale representation. Quotas bound persistent content/vector
growth; semantic search also caps each cosine scan at 10,000 vectors.

## Verification

Run:

```bash
uv run pytest tests/unit/test_filesystem_service.py -q
uv run pytest tests/parity/test_tool_count.py -q
./scripts/smoke.sh
```

Because the service starts during lifespan, the smoke harness is mandatory
after lifecycle changes. Packaged smoke is not required unless imports or
packaging inputs change; this feature uses only core/standard dependencies.
