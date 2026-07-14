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
| `library.json` | Reusable pools + variables (option lists with names) |
| `templates.json` | Named full `MatrixSpec` templates |

Writes are atomic (temp file + `replace`). Corrupt / missing files reset to an
empty valid document and log loudly — never crash the engine, never silently
invent options.

## API

`app/api/prompt_matrix_routes.py` — prefix `/prompt-matrix`:

- `GET  /library` / `PUT /library`
- `GET  /templates` / `PUT /templates`
- `GET  /paths` — absolute paths for the UI ("stored at …") so the user can
  find the files on disk without hunting

The renderer loads on panel mount and writes on every save. No cloud sync in
v1 — these are local creative assets, same tier as media-gen custom LoRA
metadata.
