"""Rebuild the Codex rollout fixtures from real sessions on this machine.

The Codex adapter is only worth trusting against Codex's REAL file shape, so
these fixtures are copies of two actual ``~/.codex/sessions/**/rollout-*.jsonl``
files rather than hand-written JSON. They are copied READ-ONLY and redacted
here, in a script that is committed next to them, so the provenance of every
fixture line is checkable and the copy can be remade when Codex changes shape.

WHAT IS PRESERVED — everything the adapter and the artifact source read:
``session_meta`` (session_id, cwd, timestamp, originator, source, cli_version),
``turn_context``, every entry's ``timestamp``/``ordinal``/``type``, the first
user message (the title fallback), and the ``*** Add File:`` / ``*** Update
File:`` header lines of every ``apply_patch`` call.

WHAT IS REDACTED — every free-text string longer than 24 characters that is
not one of those structural fields: prompts, assistant text, reasoning, tool
output, base instructions, and patch bodies. Real absolute paths are rewritten
onto a neutral fixture root so the tests are the same on every machine.

Usage (macOS, with real Codex sessions present):
    uv run python tests/fixtures/codex/make_fixtures.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The two real rollouts these fixtures were cut from. Both contain apply_patch
# writes, which is what the artifact source reads.
SOURCES = (
    (
        "session-with-writes.jsonl",
        "2026/07/24/rollout-2026-07-24T08-03-11-019f94a6-dd83-71f0-aee4-4456aff06c27.jsonl",
    ),
    (
        "session-second.jsonl",
        "2026/06/25/rollout-2026-06-25T09-38-21-019effa5-9237-78d3-a133-e723d9ae8276.jsonl",
    ),
)

FIXTURE_CWD = "/Users/fixture/code/demo-repo"
REDACTED = "[redacted]"
MAX_KEPT = 24

# Keys whose string values are structure, never content.
STRUCTURAL_KEYS = frozenset(
    {
        "type",
        "role",
        "name",
        "status",
        "id",
        "session_id",
        "turn_id",
        "call_id",
        "timestamp",
        "originator",
        "source",
        "thread_source",
        "cli_version",
        "model",
        "model_provider",
        "effort",
        "summary",
        "sandbox_mode",
        "approval_policy",
        "cwd",
    }
)


def _redact(value: object, *, real_cwd: str, key: str | None = None) -> object:
    if isinstance(value, dict):
        return {k: _redact(v, real_cwd=real_cwd, key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, real_cwd=real_cwd) for v in value]
    if not isinstance(value, str):
        return value
    text = value.replace(real_cwd, FIXTURE_CWD)
    if key in STRUCTURAL_KEYS:
        return text
    if "*** Begin Patch" in text:
        return _redact_patch(text)
    if len(text) > MAX_KEPT:
        return REDACTED
    return text


def _redact_patch(text: str) -> str:
    """Keep a patch's file headers; drop every body line."""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("***"):
            kept.append(stripped)
        elif kept and kept[-1] != REDACTED:
            kept.append(REDACTED)
    return "\n".join(kept)


def main() -> int:
    root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions"
    for name, relative in SOURCES:
        source = root / relative
        if not source.is_file():
            print(f"missing source, skipped: {source}")
            continue
        lines: list[str] = []
        real_cwd = ""
        first_user_kept = False
        for raw in source.read_text(errors="replace").splitlines():
            if not raw.strip():
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            payload = entry.get("payload")
            if entry.get("type") == "session_meta" and isinstance(payload, dict):
                real_cwd = str(payload.get("cwd") or "")
                payload.pop("base_instructions", None)
                payload.pop("instructions", None)
            keep_text = False
            if (
                not first_user_kept
                and isinstance(payload, dict)
                and payload.get("type") == "message"
                and payload.get("role") == "user"
            ):
                keep_text = first_user_kept = True
            cleaned = _redact(entry, real_cwd=real_cwd or "\0")
            if keep_text and isinstance(cleaned, dict):
                cleaned["payload"]["content"] = [
                    {"type": "input_text", "text": "Fixture first prompt"}
                ]
            lines.append(json.dumps(cleaned, separators=(",", ":")))
        target = HERE / name
        target.write_text("\n".join(lines) + "\n")
        print(f"wrote {target} ({len(lines)} entries) from {source.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# ``hook-declaration.writes.json`` is NOT produced here. It is the byte output
# of the real AI Matrx Codex plugin emitter
# (``matrx-codex-plugin/hooks/emit.py``), captured once (2026-09-18, lane
# CS-34) by running the real hook entry point over a real directory tree:
#
#   PLUGIN_DATA=<tmp>/pdata python3 - <<'EOF'
#   import sys, time; sys.path.insert(0, "<matrx-codex-plugin>/hooks"); import emit
#   emit.observe_writes({"session_id": S, "hook_event_name": "UserPromptSubmit",
#                        "cwd": W, "turn_id": T, "prompt": "p"})
#   time.sleep(0.02)
#   open(W + "/notes/release-notes.md", "w").write("# Release notes\n\nGenerated by a Codex turn.\n")
#   open(W + "/some-repo/main.py", "w").write("SECRET='never uploaded'\n")   # inside a .git checkout
#   emit.observe_writes({"session_id": S, "hook_event_name": "Stop", "turn_id": T,
#                        "stop_hook_active": False, "last_assistant_message": "done"})
#   EOF
#
# Only the session ``cwd`` was rewritten to this file's fixture root. The
# ``refused: {"repository_subtrees_refused": 1}`` in it is the producer's own
# count of the checkout it refused to enter.
