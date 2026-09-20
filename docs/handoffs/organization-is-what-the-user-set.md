# The organization is what the user SET — handoff

**Ruling (Arman, 2026-09-19, confirmed).** A saved "default organization" on a user's account
is at most a per-client display preference. Nothing that builds a request may read it, and the
personal organization is never a fallback. A client may remember the organization the user
THEMSELVES SET on this device. With nothing set, the request is HELD, the picker is shown, the
user sets one, and the request proceeds. It never fails with "no default organization".

> "one missed org check that should have just failed turns into 50 in a month and 5,000 in a
> year, and suddenly we don't have orgs any more, we have a user and a default org, which means
> we just have user now."

## Done in this repo

| Where | What |
|---|---|
| `desktop/src/lib/org/active-org.ts` | Preference rung deleted. Order is device selection → sole membership → hold. `requireActiveOrganizationId({ timeoutMs? })` IS the hold: it raises the picker, waits for the set, and resumes. Every set is pushed to the engine. |
| `desktop/src/features/org/OrganizationPickerDialog.tsx` | Also registers the `choose_organization` action-needed handler (the sidecar's route to this dialog) and re-states this Mac's pick to the engine on mount. |
| `app/services/aidream/organization.py` | Preference rung AND personal-org rung deleted. Order is this device's SET organization → sole membership → hold. The hold publishes `organization_required` through the action-needed registry and clears it once answered. Device selection is per-user in local SQLite (`active_organization`), never the cloud settings sync. |
| `app/api/organization_routes.py` (new, mounted in `app/main.py`) | `GET/PUT/DELETE /organization/active`. Membership is verified before anything is stored. |
| `app/services/action_needed/models.py` | New `ORGANIZATION` kind + `organization_required_needed()`; mirrored in `desktop/src/features/action-needed/types.ts`. |
| `app/services/coding_sessions/{cloud_state,service}.py` | Copy rewritten — no "default organization" anywhere. |
| `desktop/scripts/check-org-default-ban.mjs` | Blocking guard, in `scripts/release.sh` beside the other gates. Self-test 14/14. |

## Verified

- Guard: self-test PASS; RED on a real planted preference rung in `desktop/src/lib/org/active-org.ts`
  and on a planted `current_personal_org_id` call in `app/services/aidream/organization.py`; GREEN
  after removing each.
- `desktop/src/lib/org/active-org.test.ts` (10) and `tests/unit/test_organization_resolver.py` (6)
  both go RED on the same plants.
- Full suites: `pnpm test:unit` 813 passed; `uv run pytest tests/unit` 1472 passed, 2 skipped
  (needs a private `MATRX_HOME_DIR`; the shared checkout's `~/.matrx-dev` has a symlink `run.py`
  refuses).
- `./scripts/smoke.sh` CLEAN after the `main.py` router change.
- Live, in-process, against the real database as `admin@admin.com` (26 active memberships,
  including a personal org): the call HELD instead of returning the personal org, the
  `choose_organization` ask was published, a SET resolved the same call to the set id, the ask
  cleared itself, and a second account inherited nothing. Route probed on a source engine:
  401 with no bearer, `{"organization_id": null}` with nothing set, refusal on a non-membership.

## Adversarial review + finish (2026-09-19, second pass)

The review attacked the claim rather than the file list, and it did not hold as
written. What changed:

| Where | What |
|---|---|
| `desktop/native-vault-provider/NativeVaultPassword.swift` | **A surviving read of the account-level default.** The macOS AutoFill credential provider decoded `default_organization_id` out of `GET /api/auth/organizations` and used it to pick the organization that built the Vault `matches` and `materialize` requests — a saved password handed out of a tenant nobody chose on this Mac. The rung is deleted; the ladder is now device pick → sole membership → ask. |
| `desktop/scripts/check-org-default-ban.mjs` | Scanned only `.ts/.tsx/.js/.jsx/.mjs/.cjs/.py`, which is why the Swift read was invisible. Now scans `.swift` and `.rs` too. Proven RED on a planted Swift rung, GREEN after. |
| `app/services/aidream/organization.py` | `OrganizationNotResolvedError` is now a `RuntimeError`, and `organization_refusal()` / `organization_refusal_text()` are THE one description of an unresolved organization (`code`/`message`/`remedy`/`action`/`held`). The device's choice is read BEFORE the membership network call and is honoured when that call fails — the server verifies membership anyway, so a Supabase blink no longer re-asks an answered question. |
| The six clients (`file_sync`, `delegation`, `scraper`, `credential_vault`, `coding_sessions/service`, `coding_sessions/artifacts`) + `aidream/client.py` | All render their refusal from `organization_refusal()`. A held operation says "waiting for you to choose an organization" and carries the `choose_organization` action; a genuinely blocked one keeps its own words and offers no button. |
| `app/services/file_sync/client.py` | Stopped flattening the typed error into a bare `RuntimeError`. That flattening made `coding_sessions/artifacts.py`'s `except OrganizationNotResolvedError` **unreachable**: a held upload fell to the generic handler, set no blocker, and burned an upload attempt toward `MAX_UPLOAD_ATTEMPTS` every tick. |
| `app/services/coding_sessions/service.py` | Recognises the refusal BY TYPE (`AIDreamError.organization_refusal`), not by matching the sentence `"Cannot name an organization for this request"`. |
| `desktop/src/pages/CodingSessions.tsx`, `components/coding-sessions/SessionDiagnosisDialog.tsx`, `components/coding-sessions/tabs/SettingsTab.tsx`, `pages/Settings.tsx` | The "Choose organization" button is rendered off `blocker.action`, not off a hardcoded `code ===` branch. Three surfaces each matched their own string, so the next lane's held refusal would have shipped with no button. The Vault card in Settings gained the held state and its button. |
| `desktop/src/lib/org/active-org.ts` + `features/org/OrganizationPickerDialog.tsx` + `App.tsx` | **The boot double-ask.** `republishActiveOrganizationToEngine()` ran once, at dialog mount — normally before the sidecar is reachable. The PUT failed, warned to the console, and was never retried, so the engine stayed empty and every background job held and published `organization_required`. (The comment claiming `App.tsx` re-pushed on reconnect described something that did not exist.) It now reports whether the engine took it and is driven off `engineStatus`, retried with backoff until it lands. |
| `app/services/credential_vault/FEATURE.md`, `app/services/coding_sessions/FEATURE.md` | Both still documented abolished ladders ("owner membership first, then oldest"; "the picker writes the same `users.user_preferences` default the resolver reads"). Rewritten. |

### Verified this pass

- `desktop/scripts/check-org-default-ban.mjs`: self-test 16/16 PASS; RED on a planted Swift `default_organization_id` rung in `NativeVaultPassword.swift`, GREEN after removing it.
- `tests/unit/test_organization_held_refusals.py` (10) — proven forcing: restoring file_sync's `RuntimeError` flattening and the Vault's old `no_organization` mapping turns 2 of them RED.
- `tests/unit/test_organization_resolver.py` (8, two new) — a blinking membership lookup with a device choice set no longer asks; with nothing set it is NOT held (a network problem is not a question).
- `desktop/src/lib/org/active-org.test.ts` (13, three new) — proven forcing: making `republishActiveOrganizationToEngine()` swallow the failure again turns one RED.
- `desktop/scripts/test-native-vault-password.sh`: the Swift corpus passes, including a new case proving a report whose `default_organization_id` names a non-membership decodes to the memberships and nothing else.

### Still left behind

1. **No desktop UI click-through** (unchanged from the first pass). Everything is engine-live plus unit-level; the Tauri window was never opened. The picker rising from a real held request, and the new "Choose organization" buttons on the coding-session and Vault cards, are unproven by a human eye.
2. **`matrx-extend` scans `src/`, `scripts/` and `tests/`** after this pass (it scanned only `src/`), and its guard is now an explicit blocking step in `release.sh` — it previously rode only in the `prebuild`/`prezip` hooks, and `pnpm zip:store` fires no `prezip`, so the store artifact was already built before anything checked.
3. **aidream (the server) is out of scope here and still describes a banned shape.** `app/services/aidream/client.py` records that the owner-scoped coding-session routes "resolve the organization INSIDE the handler from the signed-in user's own default/personal organization" (`coding_session_bridge/ownership.py`). If that is still true on the server, the ruling has an unclosed door one repo over.

## Left behind (first pass — items 2 and 4 are now DONE; see above)

1. **No desktop UI click-through.** Everything above is engine-live plus unit-level; the Tauri
   window was never opened (Arman's screen is not ours to take). `desktop/src/features/org/OrganizationPickerDialog.tsx`
   opening from a real held request is unproven by a human eye.
2. **`held` is not yet used by the callers.** `OrganizationNotResolvedError.held` distinguishes
   "waiting on one choice" from "genuinely broken", but `app/services/coding_sessions/service.py:757`,
   `app/services/coding_sessions/artifacts.py:87,916`, `app/services/file_sync/client.py:155`,
   `app/services/delegation/client.py:141`, `app/services/scraper/remote_client.py:200` and
   `app/services/credential_vault/client.py:102` all still map it to their generic refusal. A
   held operation could say "waiting for you to choose" instead of reading like a failure.
3. **Deliberately NOT held** (holding would throw a picker at somebody who asked for nothing):
   `desktop/src/features/content-ir/catalog/client.ts:22`, `desktop/src/features/compute/SandboxPicker.tsx:38`,
   `desktop/src/hooks/use-chat.ts:714` and the error-outbox identity snapshots in
   `desktop/src/App.tsx:381,407`. All are filters, labels or identity comparisons, not request builders.
4. **The engine's copy can lag a reinstall.** `republishActiveOrganizationToEngine()` runs when
   `OrganizationPickerDialog` mounts. A background job that runs before the desktop window ever
   opens will hold once and publish the ask — correct, but it means a headless-first boot asks
   even though the user already chose on this Mac.
