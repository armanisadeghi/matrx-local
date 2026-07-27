# prompt-matrix store — on-disk JSON under `~/.matrx/prompt-matrix/`

Durable rules for the engine-side persistence of prompt-matrix libraries and
templates. Touch this code → update this file in the same change.

## What it is

The desktop prompt-matrix UI (variables, pools, named templates) must survive
browser storage clears, app reinstalls of the renderer cache, and "I closed the
wrong thing." **localStorage is not the source of truth.**

Files (always under `MATRX_HOME_DIR`, so `~/.matrx` or `~/.matrx-dev`):

| File | Contents |
|------|----------|
| `library.json` | **Legacy** v1 option lists (pool/variable kind) — migrated into `lists.json` on first read |
| `lists.json` | Named option lists (v2 — no pool/variable kind) |
| `templates.json` | Named full `MatrixSpec` templates |

List declarations are intentionally independent from token instances. A prompt
may map the `color` list once and use `{{color#1}}`, `{{color#2}}`, and so on;
the renderer owns those independent with-replacement draws and persists only
the once-declared list/mapping here.

Writes are atomic (temp file + `replace`). Corrupt / missing files reset to an
empty valid document and log loudly — never crash the engine, never silently
invent options.

## API

`app/api/prompt_matrix_routes.py` — prefix `/prompt-matrix`:

- `GET  /library` / `PUT /library` — legacy v1 shelf (still used by batch UI until Phase 2 wiring)
- `GET  /lists` / `PUT /lists` — v2 named lists (no pool/variable kind)
- `GET  /templates` / `PUT /templates`
- `GET  /paths` — absolute paths for the UI ("stored at …") so the user can
  find the files on disk without hunting

The renderer loads on panel mount and writes on every save. No cloud sync in
v1 — these are local creative assets, same tier as media-gen custom LoRA
metadata.
