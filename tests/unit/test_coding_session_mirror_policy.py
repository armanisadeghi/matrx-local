"""The generic desktop mirror must never copy raw provider ledgers."""

from __future__ import annotations

import json
from pathlib import Path

from app.services.local_db.mirror_schema import MIRROR_TABLES
from scripts.generate_mirror_schema import generate


def test_owner_only_raw_bridge_tables_are_excluded_from_generation_and_runtime() -> None:
    assert "coding_session" not in MIRROR_TABLES["chat"]
    assert "coding_session_entry" not in MIRROR_TABLES["chat"]

    snapshot_path = Path(__file__).parents[2] / "schema_mirror" / "snapshot.json"
    snapshot = json.loads(snapshot_path.read_text())
    table_spec = {
        "kind": "table",
        "pk": ["id"],
        "columns": [
            {
                "name": "id",
                "udt": "uuid",
                "data_type": "uuid",
                "nullable": False,
                "default": None,
            }
        ],
    }
    snapshot["schemas"]["chat"]["coding_session"] = table_spec
    snapshot["schemas"]["chat"]["coding_session_entry"] = table_spec
    rendered = generate(snapshot)
    assert '"coding_session"' not in rendered
    assert '"coding_session_entry"' not in rendered
