# Downloads — universal manager + the errors-vs-states doctrine

One queue for every large download (LLM GGUFs, whisper, image/video weights,
TTS, NER, file-sync). SQLite-backed (`downloads` table), survives restarts,
streams progress over `GET /downloads/stream` (SSE). Entry points:
`manager.py::get_download_manager()`; routes in `routes.py`.

## The doctrine: a failure the user can fix is a STATE, not an error

**"Even if I didn't have my token set, the system should not have an error.
That's not an error. It's a fact."** (Arman, repeatedly.)

- Expected user-actionable conditions — HF token missing, HF license gate not
  accepted, gate approval pending, Civitai key missing/rejected/restricted,
  AI packages not installed — MUST surface as a `DownloadResolution`
  (`failures.py`): plain-English title + message + ONE action. Never a raw
  401 string, never an ERROR log, never a red row.
- Engine logging: actionable failures log **INFO with the `[action-needed]`
  marker** (no stack trace). The periodic STATE log splits `action_needed`
  (code only) from `fails` (raw text). Genuine errors (network, disk, 500s)
  stay `logger.error` with traceback.
- UI contract: `entry.resolution` (hoisted in `to_dict()`, rides SSE events).
  The Download Manager modal renders resolution entries as "Needs your
  action" prompt cards (action button + "Check again & retry"); they never
  render in the history table. Client log lines for them are info-level
  `[action-needed]`, not ERROR (`DownloadManagerContext.tsx`).
- Adding a new self-fixable failure = one constructor in `failures.py`
  (adapted through `actionNeededFromDownload` and the canonical dispatcher).
- Reconnect/history snapshots stay inside the Download indicator/modal. They
  are discoverable and dismissible there, but are not promoted into a fresh
  global banner or toast when no download was requested in the current app
  session. A live retry clears the snapshot marker, so a new failure can alert.

## Attribution precision (the FLUX lesson)

Never tell a user with a configured token to (re-)enter it.
`_classify_hf_auth_failure` (manager.py) is the ladder for every HF 401/403:

1. no token → `hf_token_missing` (deep-link Settings → API Keys)
2. token invalid per the shared key validator (whoami) → `hf_token_invalid`
3. token valid + "awaiting/pending" in the error → `hf_gate_pending`
4. token valid otherwise → `hf_gate_not_accepted` (open the license page)

Civitai has the mirror-image ladder inline in `_download_part`
(key missing / key rejected / access restricted).

## Token attachment — request time, key store only

Every Hugging Face request resolves the token AT REQUEST TIME via
`app/services/media_gen/paths.py::read_hf_token` (app key store via the
key-manager cache first; `.env`/environ is a dev shim; huggingface_hub's own
cache last). This applies to ALL of:

- HF snapshot path (`_download_hf_snapshot`: `HfApi` + `hf_hub_download`)
- raw-URL path (`_download`: `Authorization: Bearer` for `huggingface.co` /
  `hf.co` hosts only — `_is_hf_url`; httpx correctly drops the header on the
  cross-origin redirect to HF's signed CDN)
- `app/api/model_repo_routes.py` HF repo analysis
- `app/services/ner/service.py` snapshot_download
- custom models (`custom_models.py::_hf_headers`)

Never read `.env` directly for user tokens; never cache a token across
requests — rotation must take effect on the next call.

## Startup resume RECONCILES against disk — it never blind-re-downloads

`_resume_incomplete` runs on every boot over rows still in `queued`/`active`
(the app closed or crashed mid-download, or an item never dispatched). It does
**not** blindly re-queue them — that historic behavior re-downloaded models the
user ALREADY HAD from scratch, then failed on gated-repo 401s / missing keys for
weights sitting on disk the whole time ("the system isn't checking if I have
them"). Every stale row is triaged:

- **already on disk** (`artifact_present`) → settle as `completed`, broadcast a
  completed event, **never re-fetch**.
- **malformed** (no `dest_dir` — can never download) → mark `failed` with a
  clear reason so it stops resurrecting every boot (this is the `total=106`
  junk-row class from the shipped-v1.3.113 logs).
- **genuinely incomplete** → re-queue (restart from scratch; no range resume).

`artifact_present(metadata, filename)` is the **single completion contract**,
shared by `enqueue`'s idempotency check and resume so the two can never
disagree:

| Download kind | "present" means |
|---|---|
| HF snapshot (`hf_repo_id`) | `.download-complete` marker in `dest_dir` |
| Civitai / marker-gated single file (`write_complete_marker`) | marker **and** the weight file present |
| Plain single-file (GGUF, transfer, …) | the destination file present |

The marker is written LAST by each writer, so its presence is a true
"fully done" signal — a half-finished download (no marker) correctly reads as
absent and re-downloads.

## One artifact = one row; records are dismissible, never immortal

`enqueue` treats filename+category as the artifact identity. A queued/active
row wins; a completed row wins while its artifact is on disk. A failed or
cancelled row is **re-queued in place** (same id, resolution and error
cleared) — never left standing while a second row downloads beside it. Any
other failed/cancelled duplicates of the winning row are removed as
superseded. Before this, a `hf_gate_not_accepted` prompt card survived the
user accepting the license AND the successful re-download, forever.

`POST /downloads/{id}/dismiss` (`DownloadManager.dismiss`) permanently deletes
a terminal record from memory + SQLite and broadcasts a `removed` event
(handled by `DownloadManagerContext` → row disappears from every surface,
including the global action-needed chips). Queued/active rows 409 — cancel
first. This is the user's "address it and make it go away" affordance: a
months-old failure must not be restored as a warning on every boot for the
rest of time. The UI exposes it on prompt cards ("Dismiss") and history rows
(X), Python-backed entries only.

## Stale-row re-triage

Failed rows written before this taxonomy carry only `error_msg`.
`_load_history` re-triages them at startup via
`failures.retriage_stale_failure` (pattern-mapping, no network), with
CURRENT key presence deciding attribution, and persists the resolution so
old rows render through the prompt UI. Unrecognizable messages stay errors.

## Tests

`tests/unit/test_hf_token_and_failure_states.py` pins request-time key-store
resolution, the classification ladder, URL/host guards, re-triage mapping,
and the resolution-catalog contract. Extend it with every new failure kind.

## Known defects

- MXL-D-051 (`FOUND_DEFECTS.md`): a `--live` source engine ignores SIGTERM /
  `/admin/shutdown` (even idle; SIGINT works) — surfaced during this
  feature's live verification, not download-specific.
