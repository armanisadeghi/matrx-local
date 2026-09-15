"""One rate-limited rejection log for EVERY auth surface, not just /extension/*.

WHY (SR-05, 2026-09-11 → 2026-09-14): the throttle existed and worked, and
`app/api/auth.py` — the middleware in front of everything except
/extension/* — did not use it. A signed-out desktop poller therefore produced
one unthrottled WARNING per rejected request:

    /prompt-matrix/paths 12,377 · /prompt-matrix/templates 12,225 ·
    /prompt-matrix/library 12,225 · /cloud/debug 1,187 · /access/health 1,057 ·
    /downloads/stream 1,009 · /filesystem/status 868 · /scrapes/sync-status 307

~37,000 lines from the three /prompt-matrix routes alone, in 72 hours, all
saying the same thing — the largest class in the engine log, burying
everything real in it.
"""

from __future__ import annotations

import pytest

import app.api.auth_rejection_log as rejection_log


class _Recorder:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.infos: list[str] = []
        self.debugs: list[str] = []

    def warning(self, t: str, *a: object) -> None:
        self.warnings.append(t % a if a else t)

    def info(self, t: str, *a: object) -> None:
        self.infos.append(t % a if a else t)

    def debug(self, t: str, *a: object) -> None:
        self.debugs.append(t % a if a else t)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rejection_log.reset_rejection_log_state()
    rec = _Recorder()
    monkeypatch.setattr(rejection_log, "logger", rec)
    yield rec
    rejection_log.reset_rejection_log_state()


def test_a_signed_out_poller_warns_once_not_twelve_thousand_times(
    recorder: _Recorder,
) -> None:
    for _ in range(12_377):
        rejection_log.log_rejection(
            "auth", "http", "/prompt-matrix/paths", "missing_bearer_token", method="GET"
        )

    assert len(recorder.warnings) == 1
    assert "/prompt-matrix/paths" in recorder.warnings[0]
    assert "[auth]" in recorder.warnings[0]
    assert len(recorder.debugs) == 12_376


def test_the_main_middleware_actually_uses_the_shared_throttle() -> None:
    """The whole defect was that one surface did not. Read the wiring, not
    just the helper: a WARNING left behind in the reject path is the bug."""
    import inspect

    import app.api.auth as auth_module

    source = inspect.getsource(auth_module.AuthMiddleware)
    assert "log_rejection(" in source
    assert "logger.warning(" not in source, (
        "an unthrottled rejection WARNING is back in the auth middleware"
    )


def test_extension_surface_still_goes_through_the_same_place() -> None:
    import inspect

    import app.api.extension_auth as extension_auth

    source = inspect.getsource(extension_auth._log_rejection)
    assert "log_rejection(" in source


def test_two_surfaces_never_mask_each_other(recorder: _Recorder) -> None:
    rejection_log.log_rejection("auth", "http", "/x", "missing_bearer_token")
    rejection_log.log_rejection("extension_auth", "http", "/x", "missing_bearer_token")

    assert len(recorder.warnings) == 2


def test_a_different_path_or_reason_still_surfaces_immediately(
    recorder: _Recorder,
) -> None:
    rejection_log.log_rejection("auth", "http", "/a", "missing_bearer_token")
    rejection_log.log_rejection("auth", "http", "/b", "missing_bearer_token")
    rejection_log.log_rejection("auth", "http", "/a", "not_instance_owner")

    assert len(recorder.warnings) == 3


def test_a_resumed_stream_is_news_again(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = {"t": 1_000.0}
    monkeypatch.setattr(rejection_log.time, "monotonic", lambda: clock["t"])

    rejection_log.log_rejection("auth", "http", "/a", "missing_bearer_token")
    rejection_log.log_rejection("auth", "http", "/a", "missing_bearer_token")
    assert len(recorder.warnings) == 1

    clock["t"] += rejection_log.REJECT_LOG_WINDOW_SECONDS + 1
    rejection_log.log_rejection("auth", "http", "/a", "missing_bearer_token")
    assert len(recorder.warnings) == 2


def test_a_continuing_stream_reports_its_rate_once_a_minute(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Suppressed must never mean invisible: 'still rejecting' has to be
    sayable without the flood."""
    clock = {"t": 1_000.0}
    monkeypatch.setattr(rejection_log.time, "monotonic", lambda: clock["t"])

    for _ in range(40):
        clock["t"] += 2.0
        rejection_log.log_rejection("auth", "http", "/a", "missing_bearer_token")

    assert len(recorder.warnings) == 1
    assert len(recorder.infos) == 1
    assert "rejections in last" in recorder.infos[0]


def test_the_tracked_key_set_is_bounded(recorder: _Recorder) -> None:
    """A port scanner must not be able to grow this dict forever."""
    for i in range(rejection_log.REJECT_STATE_MAX_KEYS * 3):
        rejection_log.log_rejection("auth", "http", f"/scan/{i}", "missing_bearer_token")

    assert len(rejection_log.rejection_log_state()) <= rejection_log.REJECT_STATE_MAX_KEYS
