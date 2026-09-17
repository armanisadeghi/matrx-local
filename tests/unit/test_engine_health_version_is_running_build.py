"""`/health` must report the build that is SERVING, never the one on disk.

An auto-update replaces the app bundle underneath a running engine. Measured
2026-09-17: the updater landed 1.4.147 in `/Applications/AI Matrx.app` at 03:30
while the engine process spawned at 01:28 kept serving — and correctly kept
reporting — 1.4.145. The desktop compares that number against its own build to
decide whether a restart is pending, so if `/health` ever started reflecting the
files on disk instead of the running code, the restart prompt would silently
switch off at exactly the moment it is needed.

The contract these tests pin:

  * the version is resolved ONCE, at import, into ``_APP_VERSION``;
  * mutating (or deleting) the on-disk ``pyproject.toml`` afterwards cannot
    change what `/health` reports;
  * every engine surface that carries a version reads that one snapshot.

The guard is forcing: it re-runs the real resolver against a real mutated file
and fails on a live re-read. Proven failing against a deliberately re-reading
resolver before it was kept (a resolver that calls ``_read_version()`` per
request returns the mutated value and trips
``test_a_live_re_read_of_the_version_file_is_a_defect``).
"""

from __future__ import annotations

import re
from pathlib import Path

from app.api import routes


def _pyproject_path() -> Path:
    path = Path(routes.__file__).parent.parent.parent / "pyproject.toml"
    assert path.exists(), "the source-tree pyproject.toml is the release authority"
    return path


def test_health_reports_the_import_time_snapshot():
    """The module constant, not a fresh read, is what `/health` carries."""
    assert routes._APP_VERSION, "a blank version is never a valid answer"
    assert re.match(r"^\d+\.\d+\.\d+", routes._APP_VERSION), routes._APP_VERSION


def test_a_live_re_read_of_the_version_file_is_a_defect(monkeypatch):
    """Swap the version file under us; the reported version must not move.

    This is the update, reproduced. The resolver genuinely depends on a file
    the updater can replace — the first assertion proves that, so the test is
    not vacuous — and the snapshot must be immune to it anyway.
    """
    pyproject = _pyproject_path()
    original = pyproject.read_text()
    assert 'version = "' in original

    # A version nothing in the tree could legitimately be built from.
    bumped = re.sub(
        r'^version\s*=\s*"[^"]+"',
        'version = "99.99.99"',
        original,
        count=1,
        flags=re.MULTILINE,
    )
    assert bumped != original

    snapshot_before = routes._APP_VERSION
    try:
        pyproject.write_text(bumped)

        # Force the disk lane (the lane a frozen bundle actually uses: there is
        # no installed dist-info inside the app bundle) and show it really does
        # follow the file. Without this the snapshot assertion below could pass
        # for the wrong reason.
        import importlib.metadata as _md

        def _not_installed(_name):
            raise _md.PackageNotFoundError("matrx-local")

        monkeypatch.setattr(_md, "version", _not_installed)
        assert routes._read_version() == "99.99.99", (
            "the resolver must genuinely read the replaceable file — otherwise "
            "this guard proves nothing about surviving an update"
        )

        # The running process has already imported; nothing may re-resolve.
        assert routes._APP_VERSION == snapshot_before, (
            "the engine's reported version moved when the file on disk moved — "
            "that is the 'running build' lie: an updated bundle would make a "
            "still-old engine claim the new build"
        )
        # And the version a request would serve is that same constant.
        from app.services.cloud_sync.instance_manager import current_app_version

        assert current_app_version() == snapshot_before
    finally:
        pyproject.write_text(original)

    assert pyproject.read_text() == original


def test_health_payload_carries_the_one_snapshot():
    """The liveness probe's own literal, so a second resolver cannot creep in."""
    source = Path(routes.__file__).read_text()
    # `/health`, `/` and `/version` all read the module constant.
    assert source.count('"version": _APP_VERSION') >= 2, (
        "every version-bearing engine response reads the ONE snapshot"
    )
    calls = [
        line
        for line in source.splitlines()
        if "_read_version()" in line
        and not line.lstrip().startswith(("#", '"""', "def "))
    ]
    assert calls == ["_APP_VERSION = _read_version()"], (
        "_read_version must be called exactly once, at import — a second call "
        f"site is a runtime re-read of replaceable files; found: {calls}"
    )
