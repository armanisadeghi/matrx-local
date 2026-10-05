# Records mirror sync (custom record store)

Cross-repo system-of-record: /Users/armanisadeghi/code/common-docs/systems/data/custom-data/STATE.md — read it before touching this feature in ANY repo.

`RecordsSyncEngine` mirrors ONE custom Table's records into local SQLite
(`custom_record_mirror`, migration V37) and syncs them both ways through the
store's own doors.

## The doors, and only the doors

| direction | door |
|---|---|
| pull | `custom.read_records(org, table, limit, offset)` |
| new record | `custom.anon_capture(org, client_key, table, payload, device, captured_at)` |
| edit | `custom.record_update(org, record, patch, expected_version)` |
| conflict re-read | `custom.read_record(org, record)` |

Nothing in this service reads or writes `custom.record` (or any other table in
that schema) directly — and the store agrees: `authenticated` holds EXECUTE on
the doors and no table privilege at all.

## The offline contract (DOOR-21)

`create_record()` mints `client_key` on the device BEFORE the first attempt and
writes the row plus one outbox entry; it never touches the network. Every
replay of that key returns the SAME record id and increments
`custom.anon_replay.replays`, so a crash between the write and the
acknowledgement cannot duplicate a record.

## Reachability

Every organization keeps its data in the record store since the final switch
(2026-10-01), so the cycle no longer asks `custom.store_is_open` first (retired
2026-10-03). A cycle whose first read cannot reach the store says why with a
remedy (`RecordsMirrorUnavailable`, HTTP 409 on `/records/mirror/sync`): offline,
or the `custom` doors are not on the client wire. It never half-works and never
writes nowhere.

## Ordering and safety

Pull runs before push (a device offline for a week must not clobber a week of
other people's edits). A pull never overwrites a row with a pending local edit.
A `PT409` refusal keeps BOTH copies (`state='conflict'`, `store_document`) and
is logged loudly. Transport failure or a dead token stops the drain and leaves
the queue intact; a permanent 4xx or five attempts dead-letters the entry.

## The client

`client.py` is this repo's own thin client, not `matrx-records`: that package
reaches the doors through `matrx-orm`'s direct Postgres connection with a
server-side principal, and a desktop holds no database credentials (and this
repo's `pyproject.toml` forbids a path dependency on a sibling repo). The
transport is a seam so the wire can change without the engine knowing.

## Wire status (2026-09-18, proven over HTTP)

`custom` is exposed to PostgREST on the main database, so the
SHIPPED transport works end to end: the live proof in
`tests/parity/test_records_mirror_live.py` drives this engine over HTTPS with
the admin account's JWT and `Content-Profile: custom`, and every door it calls
answers. The only non-HTTP step in that test is reading the `custom.anon_replay`
ledger for its assertion — no client may read that table, by design — and it is
optional.

