"""LIVE proof: the desktop records mirror against the REAL custom record store.

Run it:

    MATRX_LIVE_CHECKS=1 MATRX_STORE_DSN_FILE=<path to a DSN file> \
      uv run pytest tests/parity/test_records_mirror_live.py -s

What it proves, on the one database (`db.matrxserver.com`), as
`admin@admin.com`, in that account's own Workspace organization:

1. **Sync down is real.** A record written through the store's write door is
   pulled by the engine and then read back OUT OF THE DESKTOP CLIENT'S OWN
   SQLITE and diffed field by field against what `custom.read_records` says.
   A mirror that only appeared to sync would fail that diff.
2. **Sync up survives being offline.** A record authored while the engine
   cannot reach the network is queued with a device-minted `client_key`,
   drains on reconnect, and exists exactly ONCE in the store. Replaying the
   same deferred write leaves the record count unchanged and increments
   `custom.anon_replay.replays` — the DOOR-21 contract.

Everything it creates (home, table, fields, records) is disposable and is
deleted at the end.

THE WIRE IS THE REAL ONE.  Every door call in this test goes through the
SHIPPED transport — PostgREST over HTTPS, publishable key + the user's JWT,
`Content-Profile: custom` — because `custom` is exposed to PostgREST on the
main database since 2026-09-18. The only thing this test does outside that wire
is READ the idempotency ledger `custom.anon_replay` for its assertion, which no
client may read (no table grant); that verification read uses a DSN and is not
part of the client path. Run it without a DSN and the ledger assertion is
skipped with a printed note — the no-duplicate proof does not depend on it.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.services.local_db import database as database_module
from app.config import SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL
from app.services.local_db.database import LocalDatabase
from app.services.records_sync.client import (
    FIELD_KERNEL_ID,
    HOME_KERNEL_ID,
    CustomStoreClient,
    PostgrestDoorTransport,
    RecordsStoreError,
)
from app.services.records_sync.engine import RecordsSyncEngine

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.getenv("MATRX_LIVE_CHECKS") != "1",
        reason="live store proof — set MATRX_LIVE_CHECKS=1 (plus admin credentials) to run",
    ),
]

ORG = os.getenv("MATRX_STORE_ORG", "884d1ce8-7b49-4fba-a2f3-0f7dd7c83d4f")
ADMIN_UID = os.getenv("MATRX_STORE_USER", "87a6e699-3622-4869-8843-d0867456c0dd")


def _dsn() -> str | None:
    """Verification-only Postgres access (the anon_replay ledger). Optional."""
    path = os.getenv("MATRX_STORE_DSN_FILE")
    if path:
        return Path(path).read_text().strip()
    return os.getenv("MATRX_STORE_DSN")


def _admin_jwt() -> str:
    """Sign in as the shared admin test account, exactly as the desktop does."""
    token = os.getenv("MATRX_STORE_JWT")
    if token:
        return token.strip()
    email = os.getenv("AI_ADMIN_USERNAME")
    password = os.getenv("AI_ADMIN_PASSWORD")
    if not email or not password:
        pytest.skip("set MATRX_STORE_JWT, or AI_ADMIN_USERNAME + AI_ADMIN_PASSWORD")
    request = urllib.request.Request(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"apikey": SUPABASE_PUBLISHABLE_KEY, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["access_token"]


class FlakyWire:
    """The SHIPPED PostgREST transport, with a switch that unplugs the network.

    Every call it forwards is a real HTTPS RPC to the real doors. `offline`
    exists because a device on a plane is the thing being proved, and pulling a
    laptop's wifi from a test is not otherwise expressible.
    """

    def __init__(self, inner: PostgrestDoorTransport) -> None:
        self._inner = inner
        self.offline = False
        self.calls: list[str] = []

    async def call(self, schema: str, function: str, args: dict[str, Any]) -> Any:
        if self.offline:
            raise RecordsStoreError(function, 0, "transport failure (no HTTP response)")
        self.calls.append(f"{schema}.{function}")
        return await self._inner.call(schema, function, args)


def _psql_bin() -> str:
    prefix = subprocess.run(
        ["brew", "--prefix", "libpq"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return f"{prefix}/bin/psql"


@pytest.mark.timeout(240)
def test_records_mirror_round_trips_against_the_live_store(tmp_path: Path) -> None:
    asyncio.run(_scenario(tmp_path))


async def _scenario(tmp_path: Path) -> None:
    http = PostgrestDoorTransport()
    http.set_jwt(_admin_jwt())
    transport = FlakyWire(http)
    client = CustomStoreClient(transport=transport)
    engine = RecordsSyncEngine(client)

    db = LocalDatabase(tmp_path / "matrx.db")
    await db.connect()
    previous = database_module._instance
    database_module._instance = db

    tag = uuid.uuid4().hex[:8]
    created_records: list[str] = []
    table_id = home_id = None
    try:
        # -- the throwaway Table, with REAL Field records ---------------
        home_id = await client.record_write(ORG, HOME_KERNEL_ID, {"name": f"W6-LOCAL throwaway home {tag}"})
        created_records.append(home_id)
        table_id = await client.table_declare(
            ORG,
            {
                "name": f"W6-LOCAL throwaway {tag}",
                "slug": f"w6_local_throwaway_{tag}",
                "type": "entity",
                "fields": [{"name": "title"}, {"name": "note"}],
                "weight": "light",
                "display": "list",
                "ordered": False,
                "row_order": "manual",
                "parent_id": home_id,
                "title_field": "title",
                "default_sort": [{"field": "title", "direction": "asc"}],
                "label_singular": "W6 throwaway",
                "label_plural": "W6 throwaways",
                "agent_writable": True,
                "retention_days": 365,
            },
        )
        created_records.append(table_id)
        for key, label, sensitivity in (("title", "Title", "public"), ("note", "Note", "internal")):
            created_records.append(
                await client.record_write(
                    ORG,
                    FIELD_KERNEL_ID,
                    {
                        "key": key,
                        "type": "text",
                        "dated": False,
                        "label": label,
                        "multi": False,
                        "rules": [],
                        "source": "manual",
                        "depends_on": [],
                        "sensitivity": sensitivity,
                        "context_policy": "include",
                        "applies_to_types": [],
                        "entity_definition_id": table_id,
                    },
                )
            )

        engine.configure(
            user_id=ADMIN_UID, jwt=None, organization_id=ORG, table_ids=[table_id], transport=transport
        )

        # -- 1. SYNC DOWN, then diff the desktop's OWN SQLite -----------
        seeded = await client.record_write(
            ORG, table_id, {"title": f"cloud row {tag}", "note": "written through the store"}
        )
        created_records.append(seeded)
        await engine.sync_cycle()

        print(f"\nWIRE: {len(transport.calls)} PostgREST RPC calls so far, e.g. {transport.calls[:3]}")
        door_rows = {r["id"]: r["document"] for r in await client.read_records(ORG, table_id)}
        sqlite_rows = {
            r["record_id"]: json.loads(r["document"])
            for r in await db.fetchall(
                "SELECT record_id, document FROM custom_record_mirror WHERE table_id = ?", (table_id,)
            )
        }
        diffs = []
        for rid, document in door_rows.items():
            local = sqlite_rows.get(rid)
            if local is None:
                diffs.append(f"{rid}: missing from the desktop mirror")
                continue
            for field, value in document.items():
                if local.get(field) != value:
                    diffs.append(f"{rid}.{field}: store={value!r} desktop={local.get(field)!r}")
        for rid in sqlite_rows.keys() - door_rows.keys():
            diffs.append(f"{rid}: in the desktop mirror but not in the store")
        print(f"\nROW-BY-ROW DIFF: {len(door_rows)} store rows vs {len(sqlite_rows)} desktop rows -> "
              f"{'IDENTICAL' if not diffs else diffs}")
        assert not diffs, diffs
        assert seeded in sqlite_rows

        # -- 2. OFFLINE AUTHORING, RECONNECT, AND NO DUPLICATE ----------
        transport.offline = True
        local_id = await engine.create_record(table_id, {"title": f"offline row {tag}"})
        await engine.edit_record(local_id, {"note": "edited while offline"})
        transport.offline = False

        await engine.sync_cycle()
        row = await db.fetchone(
            "SELECT record_id, client_key, state FROM custom_record_mirror WHERE local_id = ?",
            (local_id,),
        )
        assert row["state"] == "synced"
        captured_id = row["record_id"]
        created_records.append(captured_id)
        after_first = await client.read_records(ORG, table_id)
        print(f"AFTER RECONNECT: {len(after_first)} records in the store; captured id {captured_id}")

        # Replay the SAME deferred write — a crash between write and ack.
        await db.execute(
            "INSERT INTO sync_queue (entity_type, entity_id, action, payload) "
            "VALUES ('custom.record', ?, 'capture', '{}')",
            (local_id,),
        )
        await db.commit()
        await engine.sync_cycle()
        after_replay = await client.read_records(ORG, table_id)
        print(f"AFTER REPLAY: {len(after_replay)} records in the store "
              f"(was {len(after_first)} before the replay)")
        assert len(after_replay) == len(after_first), "a replay wrote a duplicate record"
        replays = await _replay_ledger(row["client_key"])
        if replays is None:
            print("REPLAY LEDGER: not checked (no DSN for the verification read)")
        else:
            print(f"REPLAY LEDGER: replays={replays['replays']} record_id={replays['record_id']}")
            assert replays["record_id"] == captured_id
            assert replays["replays"] >= 1
        # The record the desktop authored is really in the store, with both fields.
        captured_doc = await client.read_record(ORG, captured_id)
        assert captured_doc["title"] == f"offline row {tag}"
        assert captured_doc["note"] == "edited while offline"
    finally:
        for record_id in reversed(created_records):
            try:
                await client.record_delete(ORG, record_id)
            except RecordsStoreError as exc:  # noqa: PERF203 — cleanup must try every row
                print(f"cleanup: could not delete {record_id}: {exc}")
        database_module._instance = previous
        await db.close()


async def _replay_ledger(client_key: str) -> dict[str, Any] | None:
    """Read the idempotency ledger. VERIFICATION ONLY — no client may read it.

    `custom.anon_replay` carries no grant for `authenticated`, by design: the
    door writes it and nothing else reads it. Without a DSN this returns None
    and the caller prints that the counter was not checked.
    """
    dsn = _dsn()
    if not dsn:
        return None
    sql = (
        "select coalesce(json_agg(to_json(x)), '[]'::json) from ("
        "select record_id::text, replays from custom.anon_replay "
        f"where organization_id = '{ORG}' and client_key = $lit${client_key}$lit$) x;"
    )
    proc = subprocess.run(
        [_psql_bin(), dsn, "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1"],
        input=sql, capture_output=True, text=True, check=True,
    )
    rows = json.loads(proc.stdout.strip().splitlines()[-1])
    assert rows, "the deferred write left no row in custom.anon_replay"
    return rows[0]
