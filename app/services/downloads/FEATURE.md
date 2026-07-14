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
  (+ a `useResolutionAction` case only for a brand-new action KIND).

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
