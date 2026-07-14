# Tier 2 — Instance-scoped desktop tool delegation (dev vs live)

**Status:** design + LIVE REVIEW of the aidream & matrx-frontend agents' work,
2026-07-14.

## What the other agents actually built (reviewed, mostly CORRECT)

The aidream agent chose a **new `chat.tool_call.target_instance_id` column +
atomic claim lease** (migration `0172_targetable_desktop_tool_calls.sql`),
keyed on the matrx-local **instance_id string** — NOT the compute-target
`app_instance_id` UUID. That is a valid, arguably better choice: it aligns with
the `?instance_id=<string>` matrx-local already sends and adds a proper
`FOR UPDATE SKIP LOCKED` claim lease (`_claim_pending_calls_for_instance` in
`tool_results.py`) that also de-dupes untargeted rows. **Confirmed good:**
  - Read side: `GET /ai/user/pending_calls?instance_id=` → `caller_instance_id`
    + atomic claim. Matches matrx-local `client.list_pending_calls`. ✓
  - Claim filter: `target_instance_id IS NULL OR = caller`, lease renew/expiry. ✓
  - Write side (aidream): `desktop_target.metadata_with_desktop_target(client=…)`
    → metadata `desktop_target_instance_id` → `logger`/`executor` stamp
    `tool_call.target_instance_id`. ✓ within aidream.

## ⚠️ THE ONE OPEN SEAM — a silent cross-repo mismatch (must fix)

The **frontend** sends the selection as a **top-level request field**
`request.target_instance_id` (`useRunAgent.ts`, `execute-instance.thunk.ts`,
`execute-manual-instance.thunk.ts`, `resume-instance.thunk.ts`; typed in
`request.types.ts`).

The **aidream** write path reads the target ONLY from
`request.client.state["desktop-native"].{target_instance_id|instance_id}`
(`desktop_target.target_instance_id_from_client`). It never reads the top-level
`request.target_instance_id`, and `chat_run.prepare_chat_run(request: Any)` does
not reject unknown fields — so the FE's field is **silently dropped**. Result:
`target_instance_id` is never stamped on the row from the FE selection,
targeting no-ops, and both desktops fall back to claim-lease-only
(nondeterministic which one runs) — the exact false-signal failure this feature
exists to kill.

**Fix (minimal, one side):** aidream should consume the top-level field. The
`metadata_with_desktop_target(metadata, *, client, fallback)` helper ALREADY has
a `fallback` param built for this — pass `fallback=request.target_instance_id`
at each call site (`chat_run.py:183`, `agent_run.py:222,508`,
`continue_conversation.py:186,456,777`) and add `target_instance_id` to the
request schema so it survives parsing. FE is already correct; no FE change
needed. (Alternative: FE also writes `client.state["desktop-native"]
.target_instance_id` — more work, less clean. Prefer the aidream fallback.)

## The problem (root)

`GET /ai/user/pending_calls` ([aidream `conversations.py:139`], impl
`services/ai_execution/tool_results.py:307 list_pending_calls`) filters delegated
`cx_tool_call` rows by **`user_id` only**. So every `matrx-local` engine logged
into an account claims the *same* delegated calls — an installed app and a
source-run dev engine double-execute and race the resume (409s). That is the
"false test signals" the desktop dev world produces today.

matrx-local Tier 1 (shipped) makes a **dev engine coordination-silent** by
default (`MATRX_CLOUD_PARTICIPATION=0`) so it never claims cloud work. Tier 2
makes a dev engine **deliberately targetable** so the frontend can drive it
without the live app stealing (or leaking into) the work.

## Remaining work per repo (post-review)

### aidream — ONE fix (close the seam above)
- Add `target_instance_id` to the request schema so the FE's top-level field
  survives parsing, and pass `fallback=request.target_instance_id` into every
  `metadata_with_desktop_target(...)` call site (`chat_run.py:183`,
  `agent_run.py:222,508`, `continue_conversation.py:186,456,777`). This is the
  only thing between "compiles + silently no-ops" and "actually targets."
- Migration `0172` must be APPLIED to live Supabase + types regenerated (a
  `.sql` on disk changes nothing). Confirm `chat.tool_call.target_instance_id`
  and the claim columns exist live.
- Leave `/conversations/{id}/pending_calls` (matrx-extend's path) unchanged. ✓

### matrx-local — DONE (this repo)
- `DelegationApiClient.list_pending_calls` now sends
  `?instance_id=<get_instance_manager().instance_id>` — matches the reviewed
  aidream read side exactly. Forward-compatible; harmless pre-migration.
- Dev default stays coordination-silent (Tier 1). To make a dev engine a
  targetable desktop, run it with `MATRX_CLOUD_PARTICIPATION=1` — then confirm
  it registers a distinct `local-pc` compute-target (its salted instance
  already differs). That path is Tier-2 follow-up, not yet exercised.

### matrx-frontend — DONE (verify the seam once aidream lands)
- Sends top-level `request.target_instance_id` from `selectDesktopTargetInstanceId`
  on all execution/resume thunks + `useRunAgent`. Correct — pending the aidream
  fallback wiring above. After aidream lands, verify end-to-end that selecting
  the dev desktop stamps `tool_call.target_instance_id` and only the dev engine
  claims.

### matrx-extend — compatibility only (NOT a feature)
- Stream/conversation-scoped (`GET /ai/conversations/{id}/pending_calls` +
  `claimResume(userRequestId, instanceId)`); it is a browser client, not a
  `local-pc` compute-target, so desktop instance-targeting does not apply.
- Only action: regenerate `types/python-generated/*` after aidream's
  `chat.tool_call` column lands, and confirm the new columns + the user-scoped
  claim path do not alter the conversation-scoped endpoint it uses. No
  functional change expected.

## Why this is safe / minimal
- `target_instance_id IS NULL` = today's behavior; single-desktop users
  unaffected. The claim lease de-dupes even null-target rows.
- One new column set + one query param + one aidream fallback wiring.
- Dev/live separation becomes a *product* guarantee: select the dev desktop →
  only the dev engine claims; select the installed app → only it does.
