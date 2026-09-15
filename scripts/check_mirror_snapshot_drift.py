#!/usr/bin/env python3
"""Report cloud columns the local mirror snapshot has never heard of.

WHY THIS EXISTS (SR-10, measured 2026-09-11 → 2026-09-14): the checked-in
``schema_mirror/snapshot.json`` was generated 2026-08-13. The cloud moved on.
For the whole 72h audit window the engine logged

    chat.request_snapshot cloud row carries columns not in the local snapshot
    ['deleted_at', 'pinned_at', 'pin_reason', 'agent_definition_version',
     'workflow_definition_version'] — values not stored

**8,566 times**, once per row, and dropped those values on the floor every
time. Nothing failed. Nothing alerted. The remedy was printed into a log file
nobody reads, and the snapshot stayed a month stale.

The snapshot is deliberately a build artifact, not a runtime fetch: it is the
ONE spec both the local mirror DDL and the sync contract are generated from,
so refreshing it per release is what keeps Matrx Local's chat replica shaped
exactly like the web app's view of the same tables. Fix the mirror, never fork
the consumer. What was missing was anything that NOTICES when it goes stale —
this is that.

How it checks: it asks PostgREST for one real row per mirrored relation
(``select=*&limit=1``) and compares the column names the cloud actually sends
against the generated contract. That is precisely the comparison chat_sync
makes per row at runtime, so a green run here means the runtime warning cannot
fire.

Posture (matching scripts/check_tool_db_drift.py): read-only, exits 1 on real
drift so the signal is visible, and exits 0 after a prominent warning when it
could not verify — "could not verify" is never evidence of no drift.

Usage:
    python scripts/check_mirror_snapshot_drift.py
    python scripts/check_mirror_snapshot_drift.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import (  # noqa: E402
    SUPABASE_PROFILE_HEADERS,
    SUPABASE_PUBLISHABLE_KEY,
    SUPABASE_URL,
)
from app.services.local_db.mirror_schema import (  # noqa: E402
    MIRROR_TABLES,
    RETIRED_MIRROR_COLUMNS,
    SNAPSHOT_GENERATED_AT,
)

TIMEOUT = httpx.Timeout(20.0, connect=8.0)


def known_columns(schema: str, table: str) -> set[str]:
    """Every column name this build can store for one mirrored relation."""
    spec = MIRROR_TABLES[schema][table]
    return set(spec["pg_types"]) | set(
        RETIRED_MIRROR_COLUMNS.get(schema, {}).get(table, ())
    )


def unknown_columns(schema: str, table: str, live: set[str]) -> list[str]:
    """Live cloud columns this build would silently drop. THE detector."""
    return sorted(live - known_columns(schema, table))


def _admin_jwt(client: httpx.Client) -> str | None:
    """A developer-env login, so RLS lets us see a row to introspect.

    Developer-only (``AI_ADMIN_USERNAME``/``AI_ADMIN_PASSWORD``); the shipped
    app never has these and never runs this script.
    """
    email = os.getenv("AI_ADMIN_USERNAME")
    password = os.getenv("AI_ADMIN_PASSWORD")
    if not email or not password:
        return None
    try:
        resp = client.post(
            f"{SUPABASE_URL.rstrip('/')}/auth/v1/token?grant_type=password",
            headers={
                "apikey": SUPABASE_PUBLISHABLE_KEY,
                "Content-Type": "application/json",
            },
            json={"email": email, "password": password},
        )
        resp.raise_for_status()
        token = resp.json().get("access_token")
    except Exception as exc:  # noqa: BLE001 — unreachable is not drift
        print(f"  ! could not sign in to introspect ({exc})")
        return None
    return token if isinstance(token, str) and token else None


def live_columns(
    client: httpx.Client, schema: str, table: str, headers: dict[str, str]
) -> set[str] | None:
    """The column names the cloud actually sends, or None if unverifiable."""
    try:
        resp = client.get(
            f"{SUPABASE_URL.rstrip('/')}/rest/v1/{table}",
            params={"select": "*", "limit": 1},
            headers={**headers, "Accept-Profile": schema},
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception:  # noqa: BLE001 — see module docstring on posture
        return None
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    return set(rows[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="prove the detector can still fail, without touching the network",
    )
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    print(
        f"Mirror snapshot generated {SNAPSHOT_GENERATED_AT}; checking it against "
        f"the live cloud schema at {SUPABASE_URL}"
    )
    drift: dict[str, list[str]] = {}
    unverified: list[str] = []
    checked = 0

    with httpx.Client(timeout=TIMEOUT) as client:
        headers = {
            "apikey": SUPABASE_PUBLISHABLE_KEY,
            **SUPABASE_PROFILE_HEADERS,
        }
        jwt = _admin_jwt(client)
        if jwt:
            headers["Authorization"] = f"Bearer {jwt}"

        for schema, tables in MIRROR_TABLES.items():
            for table in tables:
                live = live_columns(client, schema, table, headers)
                if live is None:
                    unverified.append(f"{schema}.{table}")
                    continue
                checked += 1
                missing = unknown_columns(schema, table, live)
                if missing:
                    drift[f"{schema}.{table}"] = missing

    if unverified:
        print(
            f"  ! {len(unverified)} relation(s) had no readable row to introspect: "
            + ", ".join(unverified)
        )

    if not checked:
        print(
            "\n⚠ COULD NOT VERIFY ANY RELATION — no readable rows and/or no "
            "developer login. This is NOT evidence that the snapshot is current."
        )
        return 0

    if not drift:
        print(f"\n✓ {checked} mirrored relation(s) checked; the snapshot knows every column.")
        return 0

    print(f"\n✗ SNAPSHOT DRIFT — the cloud has {sum(len(v) for v in drift.values())} "
          f"column(s) this build cannot store, across {len(drift)} relation(s):")
    for relation, columns in sorted(drift.items()):
        print(f"    {relation}: {', '.join(columns)}")
    print(
        "\nEvery value in those columns is being DROPPED by chat_sync on every "
        "pulled row (one WARNING per row, and no other signal).\n"
        "WHAT TO DO: refresh schema_mirror/snapshot.json from the live schema "
        "(the SQL is in schema_mirror/README.md), add any column the cloud "
        "REMOVED to schema_mirror/retired_columns.json, run "
        "`python scripts/generate_mirror_schema.py`, and commit all three "
        "together."
    )
    return 1


def self_test() -> int:
    """A green run must mean the detector can still fail (no network needed)."""
    schema, table = "chat", "request_snapshot"
    known = known_columns(schema, table)
    if not known:
        print("✗ self-test: the generated contract is empty")
        return 1

    if unknown_columns(schema, table, known):
        print("✗ self-test: known columns reported as drift")
        return 1

    invented = known | {"a_column_the_cloud_grew_yesterday"}
    if unknown_columns(schema, table, invented) != ["a_column_the_cloud_grew_yesterday"]:
        print("✗ self-test: a NEW cloud column was not reported as drift")
        return 1

    retired = RETIRED_MIRROR_COLUMNS.get(schema, {}).get(table, ())
    if retired and unknown_columns(schema, table, known | set(retired)):
        print("✗ self-test: a retired column was reported as drift")
        return 1

    print(f"✓ self-test: detector flags a new cloud column ({len(known)} known here)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
