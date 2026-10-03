"""A fixed Claude Code history: one session, a nested sub-agent, a title, a corrupt line, 130 turns."""

from __future__ import annotations

import json
import os
from pathlib import Path


def _line(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode() + b"\n"


def write_fixture(config_dir: Path, session: str) -> None:
    project = config_dir / "projects" / "-tmp-golden-project"
    project.mkdir(parents=True, exist_ok=True)
    records: list[bytes] = [
        _line({"type": "user", "uuid": "11111111-0000-4000-8000-000000000000", "sessionId": session,
               "cwd": "/tmp/golden-project", "gitBranch": "feature/golden", "timestamp": "2026-10-01T09:00:00.000Z",
               "message": {"role": "user", "content": "golden question zero"}}),
    ]
    for i in range(1, 130):
        role = "assistant" if i % 2 else "user"
        content = [{"type": "text", "text": f"golden turn {i} " + "x" * (i * 7)}] if role == "assistant" else f"golden question {i}"
        records.append(_line({"type": role, "uuid": f"11111111-0000-4000-8000-{i:012d}", "sessionId": session,
                              "timestamp": f"2026-10-01T09:{i // 60:02d}:{i % 60:02d}.000Z",
                              "message": {"role": role, "content": content}}))
    records.append(b"{not json}\n")
    records.append(_line({"type": "summary", "summary": "no uuid here", "leafUuid": "x"}))
    records.append(_line({"type": "custom-title", "sessionId": session, "customTitle": "Golden session"}))
    (project / f"{session}.jsonl").write_bytes(b"".join(records))
    nested = project / session / "subagents" / "workflows" / "wf_1"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "agent-n.jsonl").write_bytes(_line({"type": "assistant", "uuid": "22222222-0000-4000-8000-000000000001",
                                                   "sessionId": session, "message": {"role": "assistant", "content": "nested"}}))
    direct = project / session / "subagents"
    (direct / "agent-d.jsonl").write_bytes(_line({"type": "assistant", "uuid": "22222222-0000-4000-8000-000000000002",
                                                   "sessionId": session, "message": {"role": "assistant", "content": "direct"}}))
    stamp = 1_790_000_000
    for path in [project / f"{session}.jsonl", nested / "agent-n.jsonl", direct / "agent-d.jsonl"]:
        os.utime(path, (stamp, stamp))
