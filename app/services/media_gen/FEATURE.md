# FEATURE — Media library gallery thumbs

**Self-healing thumbs. No backfill job.**

## Contract

| Surface | Bytes |
|---|---|
| Gallery / library / filmstrip / queue tiles | `GET /media-library/thumb/{id}` → small JPEG (≤320px) |
| Lightbox / download / remix / canvas select | `GET /media-library/file/{id}` → full PNG/MP4 |

On disk beside each library item:

```
~/.matrx/media/generated/images/<uuid>.png
~/.matrx/media/generated/images/<uuid>.json
~/.matrx/media/generated/images/<uuid>.thumb.jpg   ← this
```

If the thumb is missing, corrupt, or never written (crash between save and
thumb write, older libraries, deleted by hand), the **next** `GET /thumb/{id}`
regenerates it from the full media, writes it, and returns it. That is the
entire backfill strategy — there is none other.

## Client sweep

`MediaItemThumb` mounts → placeholder. `getThumbUrl` enqueues the id into a
concurrency-capped queue (6). As each request returns, the placeholder is
replaced. Full files are **not** fetched for the grid.

Opening a tile: fetch full file for that id (+ prefetch neighbors), then open
the lightbox. Sibling full URLs fill in and expand the viewing set.

## Vault

`/media-library/thumb/{id}` resolves vaulted ids the same way `/file` does
(423 when locked). Unlocked vault images get an in-memory JPEG — never written
as plaintext beside the vault.
