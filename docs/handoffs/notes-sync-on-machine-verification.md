# Handoff: notes-sync incident — on-machine verification + final recovery

**For:** an agent running on Arman's Mac (live `~/.matrx`, installed AI Matrx app,
Chrome profile, local agent transcripts). A cloud session (2026-08-08) did the DB
forensics and shipped the fixes; the remaining steps need the machine.

**Read first:** `FOUND_DEFECTS.md` → MXL-D-074, `app/services/documents/FEATURE.md`
§ mass-delete circuit breaker, ai-matrx `FOUND_DEFECTS.md` → D131.

## Established facts (do not re-derive)

- 2026-08-06→08: four sequential cloud soft-delete waves on `workbench.notes`
  (arman@armansadeghi.com, 2,773 notes; devices `921d9676-75d`, `5bc17b18-13e`).
  **Content-verified in the DB: every wave-deleted note has a live byte-identical
  copy — the waves were the intentional duplicate-factory cleanup and lost zero
  unique content.** Wave times correlate exactly with the duplicate-notes
  investigation commits (`e02846d`/`24a7e65` @ Aug 6 19:56Z, `34e2b31`/`9c8c95b`
  @ Aug 8 08:09–08:13Z; PR #6 → released v1.4.14 Aug 8 ~14:11Z).
- The duplicate FACTORY (suffix `_2` re-mints) was still minting copies on Aug 7
  22:16–22:39Z ("Table Design 2", "Science Podcast Dialogue Example_2 2", …) —
  i.e. the installed/live engine ran pre-fix code at that time. Two leftover live
  duplicates were verified identical and soft-deleted by the cloud session on
  Aug 8.
- Separate incident (web, NOT sync): auth-cookie identity drift orphaned note
  `cdd74572-…` ("Feature Task Assignment") to `oauth-review@aimatrx.com` and
  silently dropped ~14h of its edits + one whole note (`9d973ee3-…`, never
  reached the DB). Ownership recovered; the identity-drift hard-stop is merged
  (ai-matrx PR #36). The edits/second note exist nowhere server-side.
- Mass-delete circuit breaker: matrx-local PR #7 (branch
  `claude/data-loss-page-refresh-kc6f1p`).

## Tasks, in order

### 1. Version hygiene — make sure the field runs the fixed code
- Installed app must be **≥ v1.4.14** (duplicate-factory fixes). Check the app's
  reported version and `~/.matrx` engine logs for the running version; update if
  older.
- Once PR #7 merges: release it and update again so the delete breaker is live.
- ai-matrx: the identity-drift guard is merged but deploys only via
  `./scripts/release.sh` — confirm a release has shipped since Aug 8 16:00Z.

### 2. Confirm the deletion driver + dev/live hygiene (closes MXL-D-074)
- In `~/.matrx/logs` (and `~/.matrx-dev/logs`) around the four windows
  (Aug 6 19:54Z, Aug 7 21:47–22:32Z, Aug 8 08:07–08:12Z), find what issued the
  deletes: watcher `_handle_external_delete` lines, `full_sync` stats, or
  `DELETE /notes/{id}` route calls.
- Identify which engine is `921d9676-75d` and which is `5bc17b18-13e`
  (`~/.matrx/notes_device_id` vs `~/.matrx-dev/…`). **If a DEV engine was
  writing to live cloud rows, that is an MXL-D-043-class isolation breach —
  file it loudly.**
- Confirm nothing beyond the dedup effort deletes local `.md` files (no iCloud
  Drive eviction, no third-party cleaner touching the notes dir).
- Then close/trim MXL-D-074 to whatever actually remains.

### 3. Verify the factory is dead on THIS machine
- With the updated app: create a note on web, let sync run, edit locally,
  restart the engine twice. Assert: no new `_2`/`_2 2` suffix notes appear in
  the cloud, and the logs show no `duplicate guard fired` warnings (a firing
  means identity loss recovered — investigate, don't shrug).
- Check the live cloud corpus for any REMAINING live suffix-duplicates
  (`label ~ '_2( 2)*$'` with a byte-identical live original) — the sweep missed
  at least two; there may be a few more. Delete only byte-identical copies.

### 4. Recover the lost final version of "Feature Task Assignment"
Arman pasted evolving copies of this prompt to his coding agents last night —
the newest paste is effectively the lost final version.
- Grep local agent history for `"Feature Task Assignment"` / `"Bias to action"`:
  `~/.claude/projects/**/*.jsonl`, Cursor chat/history stores, any other agent
  transcript locations on the machine. Take the LATEST variant.
- Update cloud note `cdd74572-1817-49c8-b3a3-b69730aeb7b0` with it (normal
  authenticated update as Arman — it is owned by his account again).
- Also search the same transcripts for the second tab's lost note
  (`9d973ee3-10cf-4b74-9be9-0e9d0953a441` never persisted; its content is
  unknown — look for other long pastes from the same evening). Long shot:
  Chrome profile localStorage/IndexedDB for aimatrx.com drafts.

### 5. Report
One summary to Arman: versions verified/updated, deletion driver confirmed,
factory-dead verification result, what was recovered in step 4. Update this
doc's status or delete it when everything above is done.

## The bar (Arman's ruling, verbatim intent)
Sync is simple: **don't duplicate things for no reason, and don't delete things
for no reason — a dedup deletes only duplicates.** Any code path or behavior
found violating that is a defect to fix on sight, not to work around.
