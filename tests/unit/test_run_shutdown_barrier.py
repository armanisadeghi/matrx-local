from __future__ import annotations

import threading

import pytest

import run


@pytest.fixture(autouse=True)
def _restore_shutdown_state():
    previous_server = run._uvicorn_server
    run._server_stopped_event.clear()
    run._shutdown_event.clear()
    yield
    run._uvicorn_server = previous_server
    run._server_stopped_event.clear()
    run._shutdown_event.clear()


def test_server_completion_barriers_publish_before_final_log(monkeypatch) -> None:
    """A blocked log sink cannot strand the frozen launcher at shutdown."""
    log_entered = threading.Event()
    release_log = threading.Event()

    class FakeConfig:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class FakeServer:
        def __init__(self, config: object) -> None:
            self.config = config

        def run(self) -> None:
            return None

    def blocked_info(message: str, *args, **kwargs) -> None:
        if message == "[shutdown] uvicorn server thread exited":
            log_entered.set()
            release_log.wait(timeout=2)

    monkeypatch.setattr(run.uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(run.uvicorn, "Server", FakeServer)
    monkeypatch.setattr(run.logger, "info", blocked_info)

    worker = threading.Thread(target=run.start_server, args=(22399,), daemon=True)
    worker.start()
    try:
        assert log_entered.wait(timeout=1)
        assert worker.is_alive(), "the regression requires logger.info to be blocked"
        assert run._server_stopped_event.is_set()
        assert run._shutdown_event.is_set()
    finally:
        release_log.set()
        worker.join(timeout=1)

    assert not worker.is_alive()


def test_clean_completion_reaches_exit_without_final_log(monkeypatch) -> None:
    """The main thread performs no blocking I/O around the clean barrier."""
    exit_codes: list[int] = []

    class CleanExit(Exception):
        pass

    class FakeServer:
        should_exit = False

    def unexpected_info(message: str, *args, **kwargs) -> None:
        raise AssertionError(f"clean completion attempted to log: {message}")

    def unexpected_discovery_io() -> None:
        raise AssertionError("clean completion attempted discovery-file I/O")

    def exit_now(code: int) -> None:
        exit_codes.append(code)
        raise CleanExit

    monkeypatch.setattr(run, "_uvicorn_server", FakeServer())
    monkeypatch.setattr(run, "_server_thread", object())
    monkeypatch.setattr(run, "remove_discovery_file", unexpected_discovery_io)
    monkeypatch.setattr(run.logger, "info", unexpected_info)
    monkeypatch.setattr(run.os, "_exit", exit_now)
    run._shutdown_event.set()
    run._server_stopped_event.set()

    with pytest.raises(CleanExit):
        run._wait_forever()

    assert exit_codes == [0]
