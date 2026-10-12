"""Regression guards for the single-workflow release path."""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_SCRIPT = REPO_ROOT / "scripts" / "release.sh"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


def test_release_script_skips_push_runs_then_dispatches_one_release() -> None:
    script = RELEASE_SCRIPT.read_text(encoding="utf-8")
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert 'COMMIT_MSG="${COMMIT_MSG} [skip actions]"' in script
    assert "gh workflow run release.yml" in script
    assert '--ref "$NEW_TAG"' in script
    assert "workflow_dispatch:" in workflow

    # The explicitly dispatched workflow must retain its own verification gate;
    # otherwise suppressing the ordinary CI workflow would weaken releases.
    assert re.search(r"(?m)^  verify:$", workflow)
    assert re.search(r"(?m)^    needs: verify$", workflow)


WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _triggers(text: str) -> dict[str, str]:
    """Top-level `on:` keys of a workflow and their nested text."""
    block = re.search(r"(?m)^on:\n(?P<body>(?:(?:  .*)?\n)+)", text)
    assert block is not None, "workflow has an on: block"
    keys: dict[str, str] = {}
    current = None
    for line in block.group("body").splitlines():
        m = re.match(r"^  ([a-z_]+):", line)
        if m:
            current = m.group(1)
            keys[current] = ""
        elif current:
            keys[current] += line + "\n"
    return keys


def test_nothing_builds_automatically() -> None:
    # Release cadence (Arman, 2026-10-11): Matrx Local builds only on a deliberate release — a
    # workflow_dispatch or a manually pushed tag. Never a branch push, pull request or schedule
    # (October 2026: CI on every push plus ~20 releases a day).
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        triggers = _triggers(workflow.read_text(encoding="utf-8"))
        assert set(triggers) <= {"workflow_dispatch", "push", "workflow_call"}, (workflow.name, set(triggers))
        if "push" in triggers:
            body = triggers["push"]
            assert "tags:" in body and "branches" not in body and "paths" not in body, (workflow.name, body)
    assert set(_triggers(CI_WORKFLOW.read_text(encoding="utf-8"))) == {"workflow_dispatch"}
