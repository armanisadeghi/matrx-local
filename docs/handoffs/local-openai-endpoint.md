---
status: active
updated: 2026-07-13
repos: [matrx-local]
owner-context: OpenAI-compatible /v1 endpoint over local models — "the user's machine becomes a little worker", reachable locally and remotely
---

# OpenAI-compatible local endpoint — handoff

## Arman's vision (verbatim intent)

"It's supposed to offer an API endpoint that we can hit for an OpenAI
compatible endpoint for running all of the local models so users can easily
run models and have it hit their home or office PC, either locally or
remotely." … "From inside of the web app or the mobile app or wherever they
may be in the universe, if they choose, they can hit their computer's
endpoint and run a local model, as opposed to paying to use one of the big
APIs. The point is that the user's local machine becomes a little worker."

Auth ruling (Arman, 2026-07-13): "Our auth is almost entirely through
Supabase … if the user is local or remote, it would have to be logged into
our app with their normal Supabase auth. So then it would be the same."

## Where things stand (verified 2026-07-13)

- **No `/v1` surface exists.** llama-server's own OpenAI API binds
  `127.0.0.1:{port}` only (`desktop/src-tauri/src/llm/server.rs:356-374`,
  no `--api-key`), never proxied, never tunneled. The engine's only chat
  surface is the bespoke SSE `/ai/chat` (`app/api/ai_routes.py`).
- Local model registration engine-side: `/chat/local-llm/connect` →
  `app/services/ai/local_llm_registry.py:125` `set_local_llm` builds a
  `GenericOpenAIChat` at `http://127.0.0.1:{port}/v1` and registers runtime
  model `local/<name>` with matrx-ai. The auto-start registration race was
  fixed 2026-07-13 (`desktop/src/hooks/use-llm.ts` reconciliation).
- Remote reachability exists TODAY via the Cloudflare tunnel: engine URL is
  registered in cloud `app_instances` (tunnel_url + heartbeat), AuthMiddleware
  (`app/api/auth.py:237-310`) already enforces verified Supabase JWT +
  instance-owner on tunnel traffic. A new engine route is remotely reachable
  and owner-locked for free.
- TTS (`/tts/*`) is complete; transcription has no HTTP endpoint (Rust
  pipeline + a Python file tool, unverified E2E — MXL-D-027/062);
  embeddings/classification don't exist (GLiNER plan TASK-001 awaits Arman).

## Priority work queue

### 1. `/v1/chat/completions` (+ `/v1/models`)
New router `app/api/openai_compat_routes.py`: OpenAI wire format in/out,
streaming (SSE `data:` chunks + `[DONE]`) and non-streaming. Model routing:
`local/<name>` (or the bare llama-server model name) → reverse-proxy to the
registered llama-server `/v1` (the registry above knows the port). Reject
unknown models with an OpenAI-shaped error object. Honor context/params
pass-through — no silent parameter drops (the `LocalChatRequest` silent-drop
bug class, AGENT_TASKS ai-surface follow-ups).
- Auth: standard AuthMiddleware (Supabase JWT; loopback presence-trust,
  tunnel verified+owner). Additionally accept the JWT as an OpenAI-style
  `Authorization: Bearer <jwt>` — which is what OpenAI SDKs send as api_key,
  so `OpenAI(base_url="https://<tunnel>/v1", api_key="<supabase_jwt>")` just
  works.
- Do NOT add these to `_PUBLIC_PATHS`.

### 2. `/v1/audio` surfaces (same pattern, big win, mostly plumbing)
- `/v1/audio/speech` → existing TtsService (kokoro) — OpenAI TTS wire shape.
- `/v1/audio/transcriptions` → whisper. This forces the transcription
  consolidation: one Python-side file-transcription path with an HTTP
  endpoint (the Rust streaming pipeline stays for live mic/wake-word).
### 3. `/v1/embeddings` (opens local RAG)
fastembed (ONNX, BAAI/bge-small-en-v1.5 default — the long-standing
local-RAG plan in memory). New optional extra if weights are heavy; follow
Hard Rule 5 (declare deps) + 6 (spec hidden imports).

### 4. Discovery + UX
Surface the endpoint in the UI (Settings → Local API): the local URL, the
tunnel URL when active, and copy-paste snippets (curl + OpenAI SDK). The
web app's model picker can then list "Your computer (local/<name>)" via
`app_instances` — that consumption belongs to matrx-frontend; the contract
here is: `/v1/models` lists what this machine serves, `app_instances` says
where this machine is.

### 5. Multi-model (after 1)
llama-server runs one model at a time today. `/v1/models` reflects reality
(one entry + downloaded-but-cold models flagged); a later iteration can
auto-start on demand via the Rust `start_llm_server` command — respect the
lifecycle-ownership contract (Rust owns llama-server; Python asks, never
spawns).

## Contracts

- Wire format: OpenAI Chat Completions + Models + Audio + Embeddings
  (2024-era stable shapes; SDK compatibility is THE acceptance bar).
- Lifecycle: llama-server remains Rust-owned (CLAUDE.md Hard Rule 0) —
  the proxy talks to it over HTTP only.
- Auth: Supabase JWT everywhere; tunnel = verified + owner-only
  (`app/api/extension_auth.py`).

## Verification

From another machine (or phone hotspot): `OpenAI(base_url="https://<tunnel-url>/v1",
api_key=<jwt>)` → `client.chat.completions.create(model="local/<name>",
stream=True)` streams tokens from the home PC. Same call on loopback. A wrong
user's JWT gets 403. Pin with smoke tests (httpx against the test engine,
llama-server mocked at the registry seam).
