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


def test_ci_ignores_every_release_only_version_file() -> None:
    script = RELEASE_SCRIPT.read_text(encoding="utf-8")
    ci_workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    version_files_match = re.search(
        r"(?ms)^VERSION_FILES=\(\n(?P<body>.*?)^\)", script
    )
    assert version_files_match is not None
    version_files = {
        line.strip()
        for line in version_files_match.group("body").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    ignored_paths_match = re.search(
        r"(?m)^    paths-ignore:\n(?P<body>(?:      - .+\n)+)", ci_workflow
    )
    assert ignored_paths_match is not None
    ignored_paths = {
        line.removeprefix("      - ").strip()
        for line in ignored_paths_match.group("body").splitlines()
    }

    assert version_files <= ignored_paths
