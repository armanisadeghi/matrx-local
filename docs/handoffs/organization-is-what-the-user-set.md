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

## Left behind

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
