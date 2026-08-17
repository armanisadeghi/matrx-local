# Local AI host

Matrx Local is a `matrx-ai` client host. The shared orchestrator runs model
and tool turns; `SQLiteConversationStore` owns local-first chat persistence.

## Invariants

- 🚨 **Which agent runs a step is a DATABASE answer — resolved through a Mandate, never a constant.**
  `matrx-ai` runs here as a **CLIENT**, not a server. That is not "DB-less": this host has its own
  database and persists everything (`SQLiteConversationStore`, the local chat mirror, the request
  ledger). What a client cannot do is reach server-only tables directly, so for exactly those it
  calls an **API**. Mandate resolution is one of them: `matrx_ai.configure()` auto-installs
  `ServerMandateSource` because `engine.py` supplies both `server_url` and `get_jwt`, so a Mandate
  resolves over `GET {server_url}/api/mandates/{mandate_key}/resolution` with the SAME precedence
  the server uses for itself (system default → org binding → user binding), and a user's rebind
  reaches desktop with no deploy. A missing capability is a missing API call to add, never a licence
  to hardcode an agent id. Cross-repo law:
  `/Users/armanisadeghi/code/common-docs/systems/mandates/RUNTIME.md`.
- A successful terminal turn must contain user-visible output. The shared
  orchestrator enforces this; `local_ai_task` repeats the check as a packaging
  backstop and converts reasoning-only stops into a persisted failed turn plus
  an error event.
- Client-host initialization suppresses the ORM-backed conversation labeler;
  the desktop intentionally has no `AgentMemoryBase`. This host guard remains
  while older embedded `matrx-ai` releases are supported.
- The local llama runtime profile requests
  `stream_options.include_usage=true`; llama-server otherwise omits terminal
  usage and the request ledger records false zeros.
- Request metadata is recursively stripped of credential fields before it is
  written to the chat mirror.
- For server-owned tools, the outer local executor owns the one canonical
  `chat.tool_call` row. The HTTP execution bridge receives `store=false` so it
  executes the tool without creating a duplicate cloud ledger row.
- Conversation rows preserve organization/project/task scope from
  `AppContext`, use `visibility=personal`, and keep request-summary totals from
  `CompletedRequest.to_storage_dict()`.
- Every provider iteration is durably upserted into `chat.request` with the
  executor-reserved request ID when available. Arbitrary local model files use
  the non-dispatchable `local/runtime` model-definition sentinel while keeping
  their exact name in request metadata; missing non-local model identity is a
  loud persistence error.
- `chat.user_request.iterations` and `total_tool_calls` are cross-checked
  against the child request/tool ledgers so a populated run cannot finalize
  with false zero counts.
- Every local AI run is bracketed on aidream's runtime spine
  (`app/services/ai/runtime_spine.py`): `POST /api/v2/runtime/open` before the
  loop (JWT-authed; the returned `execution_id` is stamped into
  `AppContext.metadata` as both `runtime_execution_id` and
  `runtime_root_execution_id`, which matrx-ai reads and chat_sync mirrors into
  `chat.request.execution_id`), a background heartbeat every `lease_seconds/3`
  while it runs, and `settle` exactly once on every exit path
  (completed/failed/cancelled, with token/usd meters from the run). ALL spine
  calls are best-effort and offline-safe — a failure loud-logs and never
  breaks or delays the local run; a lost/terminal lease never kills the local
  run (the server reaper settles abandoned executions). Anonymous
  (JWT-less) runs are intentionally not spine-tracked.
- Provider API keys resolve in ONE place — `key_manager` — in ONE order:
  local `ApiKeysRepo` store, then the platform Credential Vault, then
  `.env`/shell. The two stores are additive: the local one is offline-first
  and unchanged, and a Vault value never shadows it. `get_cached_user_keys()`
  is therefore the EFFECTIVE snapshot (what a request would actually use);
  `get_local_user_keys()` is the "saved on this machine" view. Read
  `app/services/credential_vault/FEATURE.md` before touching either tier.
