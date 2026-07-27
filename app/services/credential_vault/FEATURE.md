# FEATURE — Credential Vault consumer

Scope: `app/services/credential_vault/*`, the Vault tier inside
`app/services/ai/key_manager.py`, and the `/settings/api-keys/vault*` routes.

**Not** `app/services/media_vault` — that is the local, password-locked media
store. Different system, no overlap. Only the name is similar.

## What this is

The platform has ONE credential system: `users.credential_items` +
`users.user_secrets`, served by aidream at `{BACKEND}/api/vault/*` and
authorized by a real user JWT. matrx-local is a **consumer** of it, so a key
the user saved once — in the web app, the Chrome extension, or here — is
available everywhere.

**Consumer only.** This repo never writes to the vault, never holds a vault
encryption key, and never sees a `sealed` value (the server refuses to release
one). Cross-repo plan:
`/Users/armanisadeghi/code/common-docs/projects/credential-sharing-browser-login/PLAN.md`;
foundation: `.../projects/unified-credential-vault/PLAN.md`.

## THE RESOLUTION ORDER

Decided in exactly one place — `app/services/ai/key_manager.py`. When any part
of the app needs a provider API key:

1. **Local key store** (`ApiKeysRepo` → `_user_keys`) — the user's own key
   saved on THIS machine. Offline-first; works with no network and no session.
2. **Credential Vault** (`_vault_keys`) — released by aidream for the
   signed-in user. Fills gaps ONLY.
3. **`.env` / shell** — developer-only (CLAUDE.md § Security posture rule 1);
   matrx-ai falls through to `os.environ` when the resolver returns None.

**Additive, never a replacement.** `ApiKeysRepo` and the whole
`/settings/api-keys/*` surface are untouched and remain the offline path. A
Vault value can never shadow a local key. Deleting a local key hands the
provider back to the Vault tier (and re-injects the env alias) rather than
leaving the resolver and the env shim disagreeing.

Because the order lives in `key_manager`, `get_cached_user_keys()` returns the
**effective** snapshot, so every existing consumer — scraper (Brave),
media-gen (Civitai / Hugging Face), key validation, `/chat/ai-status` — gets
Vault keys with no change of its own. `get_local_user_keys()` is the
"saved here" view; `get_vault_key_origins()` names the item behind each
Vault-supplied key.

## Invariants

- **Unavailable is a STATE, not an error** (CLAUDE.md § Security posture rule
  3). No session, no server URL, offline, or denied all come back as a
  `VaultProviderSnapshot.state` with a user-facing message; every
  `/settings/api-keys/vault*` route answers 200 with that state.
- **Never a second HTTP client or auth path.** Transport is
  `app.services.aidream.client.get_aidream_client()`; identity is
  `app.services.scraper.auth_helper.get_active_user_token()` (the repo's one
  "signed-in user's JWT for a background call" helper — it lives under
  `scraper/` for historical reasons and has no scraper dependency).
- **Our secrets never exist on the client.** These calls read the USER's own
  credentials with the USER's own session. No service-role key, no signing
  secret, no dev-owned provider key may ever appear here.
- **Provider identity is looked up, never guessed.** An item matches a local
  provider by its vault `provider_key` (e.g. `anthropic`) or by a field's
  `env_key` alias, resolved through `provider_grants.ENV_VAR_TO_PROVIDER` (the
  ONE canonical env→provider index, shared with `key_manager`) and, underneath
  it, the `api_key_provider` Remote Catalog — the SAME alias table the .env
  bulk import uses. One alias list, both import paths; never a second
  hand-maintained one.
- **The wrong field is worse than no field.** The vault groups unrelated
  credentials under one provider slug (a Google Analytics service-account JSON
  is also `provider_key=google`). A field is only used when it carries one of
  the provider's own env aliases, or a conventional single-secret name, or is
  the item's lone field with no foreign alias. Otherwise the item is skipped.
- **Only what is needed is decrypted.** `refresh_vault_keys()` resolves
  plaintext only for providers with no local key. A secret the desktop already
  owns is never fetched again.
- **Personal scope only.** `principal_type=user`. Organization-owned items are
  deliberately not pulled onto a personal machine — that is a sharing decision
  nobody made here.
- **The Vault tier is never stale.** An unavailable vault DISCARDS the previous
  Vault values instead of keeping them, so the resolver can never serve a key
  the session no longer has. Sign-out clears the tier; local keys survive.
- **Loud recovery.** `/vault/resolve` is all-or-nothing server-side; the
  predictable denials (`sealed`, `revealable` without `can_reveal`) are
  filtered client-side, and a batch failure falls back to per-ref resolution
  with a WARN. That warning means a denial we did not model — investigate it.

## Entry points

| Where | What |
|---|---|
| `client.py` | The four calls: `list_user_items` / `get_item` / `reveal` / `resolve`. Raises `VaultUnavailable(state, message)`. |
| `provider_keys.py` | Item → provider matching (`build_candidates`), `fetch_provider_snapshot`, `resolve_one`. |
| `key_manager.refresh_vault_keys()` | Populates tier 2. Startup (fire-and-forget), sign-in (`POST /auth/token`), and the refresh route. |
| `key_manager.clear_vault_keys()` | Sign-out (`DELETE /auth/token`). |
| `GET /settings/api-keys` | Adds `source: local\|vault\|none` and `vault_item_name`. `configured` still means "saved locally" and still drives Remove. |
| `GET /settings/api-keys/vault` | Which Vault credentials map to AI providers (metadata only). |
| `POST /settings/api-keys/vault/refresh` | Re-read and re-activate now. |
| `POST /settings/api-keys/vault/import` | Copy ONE Vault value into the local store, so it also works offline. |
| `desktop/src/pages/Settings.tsx` | The API Keys tab: an "AI Matrx Vault" card listing available credentials, and a "From Vault" badge naming the item on each provider row. |

## Known gaps

- **Four local providers have no vault `credential_definition`:** `brave`,
  `cerebras`, `civitai`, `fastino`. The user cannot store those in the vault at
  all, so they can never arrive here. Fixing it is an aidream catalog change
  (`aidream/services/catalogs/seeds/credential_definitions.json`), not a
  matrx-local one. The eight overlapping providers already agree on slug AND
  env alias — nothing needs renaming on either side.
- **MXL-D-073** — the LIVE `api_key_provider` catalog is missing its `brave`
  row (12 live vs 13 compiled), so a Brave key stored in the vault as
  `BRAVE_SEARCH_API_KEY` still will not match until that row is added.
- **Recent-auth ceiling.** aidream refuses a `revealable` resolve when
  `REVEAL_RECENT_AUTH_MAX_SESSION_AGE_SECONDS` is set and the session is older
  (the launch value in the plan is 900s). A desktop app cannot silently
  re-authenticate, so at launch this tier would go quiet for long-lived
  sessions and the fallback WARN would fire on every refresh. Needs a product
  decision — see `.matrx/ARMAN_TASKS.md`.

## Change log

- 2026-07-26 — Built: vault client, provider matching, the Vault tier in
  `key_manager`, three settings routes, and the API Keys tab UI.
