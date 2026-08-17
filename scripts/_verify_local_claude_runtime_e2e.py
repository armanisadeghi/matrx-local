"""REAL end-to-end proof of the LOCAL Claude Code runtime on this machine.

What this drives, with nothing faked:

1. START — a real Claude Code session via the user's own installed `claude`
   and subscription login, in a scratch repo under ~/code.
2. STREAM — SDK events consumed live from the runtime's subscribe seam.
3. MIRROR + DELIVER — the runtime's per-turn targeted import lands
   `append_native` envelopes in a durable outbox, which this script then
   drains to PRODUCTION aidream with the user's real JWT.
4. VERIFY — asyncpg probe of the production database: the native binding, its
   entry ledger, and the projected canonical conversation/messages.
5. RESUME — a follow-up turn on the same session id, with history, verified to
   append to the SAME binding.
6. CANCEL — a run interrupted mid-flight settles as `cancelled`.

Run:  uv run python scripts/_verify_local_claude_runtime_e2e.py

It uses the signed-in user's own credentials (Claude subscription + Matrx JWT
from the installed app's local DB). Costs a few subscription turns on haiku.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SCRATCH = Path.home() / "code" / "matrx-scratch-runtime-e2e"
LIVE_DB = Path.home() / ".matrx" / "matrx.db"
AIDREAM_ENV = Path.home() / "code" / "aidream" / ".env"

MARKER = "MATRX-LOCAL-RUNTIME-E2E-OK"
RESUME_MARKER = "RESUME-CONFIRMED"


def _live_token() -> tuple[str, str, int]:
    conn = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT access_token, user_id, expires_at FROM auth_tokens "
            "WHERE key='current_user'"
        ).fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        raise SystemExit("No signed-in Matrx user in the installed app's DB")
    if int(row[2] or 0) < time.time() + 600:
        raise SystemExit("Matrx JWT is expired/expiring; open the desktop app first")
    return str(row[0]), str(row[1]), int(row[2])


def _pg_dsn_params() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in AIDREAM_ENV.read_text().splitlines():
        line = line.strip()
        if line.startswith("SUPABASE_MATRIX_") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("'\"")
    return values


async def _probe_production(provider_session_id: str) -> dict[str, object]:
    import asyncpg

    params = _pg_dsn_params()
    conn = await asyncpg.connect(
        host=params["SUPABASE_MATRIX_HOST"],
        port=int(params.get("SUPABASE_MATRIX_PORT", "5432")),
        database=params["SUPABASE_MATRIX_DATABASE_NAME"],
        user=params["SUPABASE_MATRIX_USER"],
        password=params["SUPABASE_MATRIX_PASSWORD"],
        statement_cache_size=0,
    )
    try:
        session = await conn.fetchrow(
            "SELECT id, conversation_id, fidelity, origin, status "
            "FROM chat.coding_session WHERE provider_session_id=$1 "
            "AND deleted_at IS NULL",
            provider_session_id,
        )
        if session is None:
            return {"found": False}
        entries = await conn.fetchval(
            "SELECT count(*) FROM chat.coding_session_entry WHERE coding_session_id=$1",
            session["id"],
        )
        messages = await conn.fetch(
            "SELECT role, left(content::text, 200) AS content FROM chat.message "
            "WHERE conversation_id=$1 ORDER BY position",
            session["conversation_id"],
        )
        conversation = await conn.fetchrow(
            "SELECT title, source_app FROM chat.conversation WHERE id=$1",
            session["conversation_id"],
        )
        return {
            "found": True,
            "session_id": str(session["id"]),
            "conversation_id": str(session["conversation_id"]),
            "fidelity": session["fidelity"],
            "origin": session["origin"],
            "entry_count": int(entries),
            "message_count": len(messages),
            "messages": [dict(m) for m in messages],
            "conversation": dict(conversation) if conversation else None,
        }
    finally:
        await conn.close()


async def main() -> None:
    os.environ.setdefault("MATRX_CLOUD_PARTICIPATION", "1")

    from app.services.coding_sessions import local_runtime as runtime_module
    from app.services.coding_sessions.claude_history import ClaudeHistoryImporter
    from app.services.coding_sessions.local_runtime import (
        LocalClaudeRuntime,
        LocalRuntimeStartRequest,
    )
    from app.services.coding_sessions.service import CodingSessionBridgeOutbox
    from app.services.local_db.database import LocalDatabase

    access_token, user_id, _exp = _live_token()
    print(f"[e2e] Matrx user: {user_id}")

    SCRATCH.mkdir(parents=True, exist_ok=True)
    (SCRATCH / "README.md").write_text(
        "# Matrx Local runtime E2E scratch\nSafe to delete.\n"
    )

    tmp = Path(tempfile.mkdtemp(prefix="matrx-runtime-e2e-"))
    db = LocalDatabase(tmp / "matrx.db")
    await db.connect()
    await db.execute(
        """INSERT INTO auth_tokens (key, access_token, user_id, expires_at, updated_at)
           VALUES ('current_user', ?, ?, ?, datetime('now'))""",
        (access_token, user_id, int(time.time()) + 3600),
    )
    await db.commit()

    class _Settings:
        values = {
            runtime_module.WORKSPACE_ROOTS_SETTING: [str(Path.home() / "code")],
            runtime_module.APPROVED_FOLDERS_SETTING: [str(SCRATCH)],
        }

        def get(self, key, default=None):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

    runtime_module._settings = lambda: _Settings()

    outbox = CodingSessionBridgeOutbox(db=db, cloud_enabled=True)
    importer = ClaudeHistoryImporter(db=db, outbox=outbox)
    runtime = LocalClaudeRuntime(importer=importer, db=db)

    capabilities = await runtime.capabilities()
    print("[e2e] capabilities:", json.dumps(
        {k: capabilities[k] for k in ("available", "reasons", "claude_cli", "auth_path")}
    ))
    if not capabilities["available"]:
        raise SystemExit("runtime unavailable — cannot run E2E")

    async def _drain_outbox(label: str) -> None:
        deadline = time.time() + 180
        while time.time() < deadline:
            result = await outbox.sync_pending()
            pending = await outbox.pending_count()
            print(f"[e2e] outbox drain ({label}): sent={result['sent']} "
                  f"blocked={result['blocked']} pending={pending}")
            if pending == 0 and result["blocked"] is None:
                return
            if result["blocked"]:
                raise SystemExit(f"outbox blocked: {result['blocked']}")
            await asyncio.sleep(2)
        raise SystemExit(f"outbox did not drain for {label}")

    async def _watch(runtime_id: str, label: str) -> list[str]:
        kinds: list[str] = []
        async for event in runtime.subscribe(runtime_id):
            kind = event.get("sdk_message_type") or event.get("event")
            kinds.append(str(kind))
            if event.get("event") == "runtime_finished":
                print(f"[e2e] {label} finished: status={event.get('status')} "
                      f"conversation={event.get('conversation_id')}")
        return kinds

    # ------------------------------------------------------------- 1. START
    print("\n[e2e] === START ===")
    started = await runtime.start(
        LocalRuntimeStartRequest(
            workspace=str(SCRATCH),
            prompt=(
                "Reply with exactly this marker and nothing else: "
                f"{MARKER}. Do not use any tools."
            ),
            model="haiku",
            max_turns=2,
        )
    )
    print("[e2e] start:", json.dumps({k: started[k] for k in (
        "runtime_id", "session_id", "status", "conversation_id",
        "provider_session_id")}))
    kinds = await _watch(started["runtime_id"], "start-run")
    print("[e2e] event kinds:", kinds)
    final = runtime.status(started["runtime_id"])
    assert final["status"] == "completed", final
    assert final["conversation_id"], "conversation_id was never established"
    provider_session_id = final["provider_session_id"]
    session_id = final["session_id"]
    await _drain_outbox("start")
    probe = await _probe_production(provider_session_id)
    print("[e2e] production probe:", json.dumps(probe, indent=2)[:2000])
    assert probe["found"], "binding did not land in production"
    assert probe["fidelity"] == "native", probe
    assert probe["conversation_id"] == final["conversation_id"], probe
    start_entries = probe["entry_count"]
    assert start_entries >= 2, probe
    assert any(MARKER in (m.get("content") or "") for m in probe["messages"]), (
        "assistant marker did not project into canonical messages"
    )

    # ------------------------------------------------------------ 2. RESUME
    print("\n[e2e] === RESUME (follow-up with history) ===")
    resumed = await runtime.start(
        LocalRuntimeStartRequest(
            workspace=str(SCRATCH),
            prompt=(
                "Repeat the exact marker you replied with earlier in this "
                f"conversation, then say {RESUME_MARKER}. No tools."
            ),
            resume_session_id=session_id,
            model="haiku",
            max_turns=2,
        )
    )
    await _watch(resumed["runtime_id"], "resume-run")
    resumed_final = runtime.status(resumed["runtime_id"])
    assert resumed_final["status"] == "completed", resumed_final
    assert resumed_final["provider_session_id"] == provider_session_id, (
        "resume changed the provider session identity"
    )
    await _drain_outbox("resume")
    probe2 = await _probe_production(provider_session_id)
    assert probe2["entry_count"] > start_entries, (probe2, start_entries)
    joined = " ".join(m.get("content") or "" for m in probe2["messages"])
    assert RESUME_MARKER in joined, "resume reply did not project"
    assert MARKER in joined, "history marker missing after resume"
    print(f"[e2e] resume verified: entries {start_entries} -> {probe2['entry_count']}")

    # ------------------------------------------------------------ 3. CANCEL
    print("\n[e2e] === CANCEL ===")
    cancel_run = await runtime.start(
        LocalRuntimeStartRequest(
            workspace=str(SCRATCH),
            prompt=(
                "Write a very long, detailed 3000-word essay about databases. "
                "Do not use tools."
            ),
            model="haiku",
            max_turns=2,
        )
    )
    await asyncio.sleep(3)
    cancel_result = await runtime.cancel(cancel_run["runtime_id"])
    print("[e2e] cancel:", cancel_result)
    await _watch(cancel_run["runtime_id"], "cancel-run")
    cancel_final = runtime.status(cancel_run["runtime_id"])
    print("[e2e] cancel final:", cancel_final["status"])
    assert cancel_final["status"] == "cancelled", cancel_final
    # Drain whatever the settle mirror captured; delivery must still succeed.
    await _drain_outbox("cancel")

    print("\n[e2e] ALL CHECKS PASSED")
    print(json.dumps({
        "provider_session_id": provider_session_id,
        "claude_session_id": session_id,
        "conversation_id": final["conversation_id"],
        "binding_row": probe2["session_id"],
        "entries_after_resume": probe2["entry_count"],
        "canonical_messages": probe2["message_count"],
    }, indent=2))
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
