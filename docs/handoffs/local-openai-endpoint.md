---
status: code-shipped-needs-live-verification
updated: 2026-07-14
repos: [matrx-local]
owner-context: OpenAI-compatible /v1 endpoint over local models — "the user's machine becomes a little worker", reachable locally and remotely
next-gate: Tunnel drill — second-device OpenAI SDK call through the Cloudflare tunnel (live engine was not running 2026-07-14, so the tunnel leg is still unexercised) + real llama-server chat streaming.
---

# OpenAI-compatible local endpoint — work order

## Goal

Expose the user's desktop engine as an authenticated OpenAI-compatible `/v1`
endpoint so web, mobile, and scripts can use the user's machine as a local
model worker over loopback or the existing owner-locked Cloudflare tunnel.

Auth ruling (Arman, 2026-07-13): normal Supabase auth is the auth model for
local and remote use. OpenAI SDKs should pass the Supabase JWT as the
`api_key`, which arrives as `Authorization: Bearer <jwt>`.

## Done

- `/v1/chat/completions` proxies streaming and non-streaming OpenAI chat bodies to the Rust-owned llama-server, preserving unknown request params and mapping `local/<name>` or bare model names to the active registered model: `app/api/openai_compat_routes.py`, `app/services/ai/local_llm_registry.py`.
- `/v1/models` lists the active local chat model and the local embedding model when `fastembed` is importable: `app/api/openai_compat_routes.py`.
- `/v1/audio/speech` maps OpenAI-style speech requests to the existing Kokoro `TtsService`, currently WAV-only with a 50,000-character input cap: `app/api/openai_compat_routes.py`, `app/services/tts/service.py`.
- `/v1/audio/transcriptions` maps OpenAI-style multipart transcription uploads to the existing Python-side Whisper file transcription tool, with a 25 MB upload cap and temp-file cleanup: `app/api/openai_compat_routes.py`, `app/tools/tools/audio.py`.
- `/v1/embeddings` adds optional local embeddings via `fastembed` using `BAAI/bge-small-en-v1.5` by default, offloaded from the event loop: `app/api/openai_compat_routes.py`, `pyproject.toml`.
- `/v1/*` remains protected by the standard `AuthMiddleware`; auth failures on `/v1/*` now return OpenAI-shaped error objects while preserving existing non-`/v1` auth responses: `app/api/auth.py`.
- Proxy hardening avoids stale `Content-Encoding`/decoded-body mismatches and uses a finite non-streaming read timeout while keeping streaming reads open-ended: `app/api/openai_compat_routes.py`.
- `/v1` request bodies are redacted in engine request logs so prompts, speech text, embeddings input, and audio request bodies do not land in DEBUG/error logs: `app/main.py`.
- Local AI client-host runtime defects found during adjacent smoke coverage were fixed: DB-less matrx-ai queue/drain/rollup paths are guarded, and Decimal tool-call costs persist cleanly to SQLite: `app/services/ai/engine.py`, `app/services/ai/conversation_handler.py`.
- Focused smoke coverage pins the OpenAI-compatible surface, auth envelope, proxy behavior, audio limits, transcription temp path seam, embeddings shape, and log redaction: `tests/smoke/test_openai_compat_surface.py`.

## Remaining work

1. **Live verification gate:** From a second device or phone hotspot, call `OpenAI(base_url="https://<tunnel-url>/v1", api_key="<supabase_jwt>")` and verify `client.chat.completions.create(model="local/<name>", stream=True)` streams tokens from a running local llama-server. Also verify a different user's valid JWT gets 403.
2. **Loopback SDK drill — DONE 2026-07-14** (stock `openai` 2.36.0 SDK against a dev engine, real Supabase JWT as api_key): `/v1/models` ✓ (served `local/BAAI/bge-small-en-v1.5`; no chat model — no llama-server on the machine), speech ✓, embeddings ✓, transcription ✓, auth envelope ✓. Chat streaming returned the intended clean 503 `local_model_not_available` (llama-server absent) — real llama-server streaming remains item 1.
3. **Real-service audio/RAG drill — DONE 2026-07-14:** `/v1/audio/speech` synthesized real Kokoro WAV (128,044 bytes RIFF); `/v1/audio/transcriptions` ran real Whisper (auto-downloaded `base.pt`, 145 MB) and transcribed the Kokoro WAV back verbatim ("The quick brown fox jumps over the lazy dog."); `/v1/embeddings` ran real fastembed (already installed) → 2 vectors, dim 384.
3a. **SDK default `encoding_format` breaks embeddings:** the official OpenAI Python SDK sends `encoding_format="base64"` by default; the endpoint only supports `float` and 400s (`unsupported_encoding_format`), so the stock SDK call fails unless the caller passes `encoding_format="float"` explicitly. Support base64 (it's just base64-packed little-endian float32) or the most common SDK path stays broken.
4. **Settings UX:** Add Settings -> Local API UI showing loopback URL, tunnel URL when active, curl snippets, OpenAI Python/JS snippets, current auth requirement, active model, and copy buttons. Do not put `/v1/*` in `_PUBLIC_PATHS`.
5. **matrx-frontend/mobile consumption:** In the frontend repo, surface remote machine endpoints from `app_instances` and let model pickers label "Your computer (local/<name>)"; this repo's contract is `/v1/models` plus `app_instances.tunnel_url`.
6. **Packaging and installer policy:** Decide whether embeddings stays developer-only optional extra or gets an in-app capability installer and PyInstaller hidden-import/data treatment. Delete this item when packaged builds can either install/use fastembed or show a deliberate "not installed" capability prompt.
7. **Audio format parity:** `/v1/audio/speech` currently supports `response_format="wav"` only. Add mp3/opus/flac/aac/pcm support or document WAV-only compatibility in the Local API UI.
8. **Transcription consolidation:** The endpoint uses the existing Python `tool_transcribe_audio` path; Rust live mic/wake-word remains separate. If the product wants one canonical transcription stack, consolidate model install/status/error reporting across Rust streaming and Python file transcription.
9. **Multi-model/cold-model behavior:** llama-server still runs one model at a time. Keep `/v1/models` truthful; later, Python may request Rust to start a model on demand, but Rust must remain lifecycle owner.
10. **Delete workaround later:** Remove `install_client_host_queue_guard()` from `app/services/ai/engine.py` only after matrx-ai upstream guarantees `queue_helpers.get_coordinator()`, `dynamic_drain.drain_pending_injections()`, and `apply_authoritative_user_request_rollup()` are no-ops or ConversationStore-backed in client-host mode without ORM bases, and after `tests/smoke/test_ai_surface.py` passes without the guard.

## Remaining work — verification

- EXERCISED 2026-07-14 (loopback, dev engine, stock openai SDK 2.36.0, real Supabase JWT): `/v1/models`, `/v1/audio/speech` (real Kokoro), `/v1/audio/transcriptions` (real Whisper), `/v1/embeddings` (real fastembed, `encoding_format="float"`), and the auth envelope (no token → OpenAI-shaped 401 `authorization_required`; over loopback a merely-present invalid Bearer passes per the documented presence-only loopback posture in `app/api/remote_auth.py` — strict Supabase verification applies to tunnel traffic).
- Not yet exercised: Cloudflare tunnel from another network/device (live engine was not running during the 2026-07-14 drill; no `~/.matrx/local.json`).
- Not yet exercised: wrong-owner Supabase JWT rejection on tunnel traffic.
- Not yet exercised: real llama-server streaming through `/v1/chat/completions` (no llama-server binary/model on the drill machine; endpoint returned the designed 503 `local_model_not_available`).
- Not yet exercised: packaged PyInstaller sidecar behavior for optional embeddings/transcription dependencies.

## Progress log

- 2026-07-14: Implemented and committed `/v1/chat/completions`, `/v1/models`, `/v1/audio/speech`, `/v1/audio/transcriptions`, and `/v1/embeddings` in matrx-local.
- 2026-07-14: Ran adversarial review agents over the chat/proxy/auth path, client-host runtime guard, and full `/v1` surface; fixed findings for content encoding, auth envelope shape, non-stream timeout, ORM guard breadth, Decimal persistence, log redaction, and audio input caps.
- 2026-07-14: Exercised with `UV_PYTHON=3.13 uv run --frozen pytest tests/smoke/test_openai_compat_surface.py -q` — 18 passed. This is mocked/in-process coverage for llama-server, TTS, transcription, embeddings, auth envelope shape, resource caps, and log redaction.
- 2026-07-14: Exercised with `UV_PYTHON=3.13 uv run --frozen ruff check app/api/openai_compat_routes.py tests/smoke/test_openai_compat_surface.py` — passed.
- 2026-07-14: Exercised with `UV_PYTHON=3.13 uv run --frozen python -m py_compile app/main.py app/api/openai_compat_routes.py tests/smoke/test_openai_compat_surface.py` — passed.
- 2026-07-14: Adjacent `tests/smoke/test_ai_surface.py` was green earlier after the ORM guard fixes, but later broader runs were interrupted by unrelated active mirror/file-sync work in the same checkout. Treat current `/v1` verification as focused smoke/unit coverage, not an end-to-end engine drill.
- 2026-07-14 (verification agent, W4 drill): loopback OpenAI SDK drill against a real booted dev engine — models/speech/transcription/embeddings all real-service green, auth envelope verified (401 no-token; loopback presence posture confirmed by design), chat = clean 503 without llama-server. Found item 3a (SDK default base64 encoding_format 400s). Tunnel leg NOT run — live engine down. `test_ai_surface.py` + `test_openai_compat_surface.py` 24/24 green on the same checkout.
