"""Supervisor for the ``matrx-egress`` helper child process.

What this is
------------
The user may lend their own computer's internet connection to AI Matrx, used
ONLY when a site blocks our datacenter address. The device side of that is one
Rust binary, ``matrx-egress`` (crate ``matrx-local/crates/matrx-egress``) — the
ONLY device-side implementation there is (contract rule 6). This module is the
engine's supervisor for it: resolve the binary, register this computer with the
aidream gateway, spawn the helper as a CHILD of the engine, read its status
file, and stop it again.

Lifecycle ownership (Hard Rule 0)
---------------------------------
Exactly the discipline ``app/services/tunnel/manager.py`` uses for cloudflared:

* the child is spawned by the engine and by nobody else;
* its PID + OS creation time + executable path are persisted under the
  discovery key ``residential_egress`` so ``app/preflight.py`` can reclaim an
  orphan with the same three-way identity proof cloudflared gets;
* the launcher registry service ``"residential_egress"`` carries the state;
* ``stop()`` cascades (terminate → wait → SIGKILL) before reporting done.

The token
---------
``POST /egress/devices`` mints a bearer that appears exactly once. It is handed
to the child on **stdin** and nowhere else: never written to disk, never put in
argv (argv is world-readable via ``ps``), never logged. stdin then STAYS OPEN —
EOF on it is the helper's own shutdown signal, so closing it after the write
would stop the helper the instant it started.

Binary resolution order (contract § "The desktop app (matrx-local)")
--------------------------------------------------------------------
1. ``MATRX_EGRESS_BINARY`` — a developer-only path VALUE (never a toggle); it
   names WHICH binary to run, it does not turn anything on or off.
2. the bundled sidecar next to the frozen engine (same probe shape as
   ``tunnel/manager.py::_find_preinstalled_cloudflared``);
3. ``<matrx home>/bin/matrx-egress``;
4. the dev workspace build, ``desktop/src-tauri/target/{debug,release}/``.

No download path: unlike cloudflared this binary is ours and ships with the
app. A missing binary is an honest STATE (``not_installed``) with a remedy —
never a spinner, never silence.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import psutil

from app.common.platform_ctx import PLATFORM, host_bundle_macos_dir
from app.config import MATRX_HOME_DIR

logger = logging.getLogger(__name__)

# The one sentence the user sees when the helper is not part of this build.
NOT_INSTALLED_MESSAGE = "The connection helper is not installed with this build"
NOT_INSTALLED_REMEDY = (
    "Update AI Matrx to a build that ships the home-connection helper, or "
    "install it from the AI Matrx website and restart the app."
)

# Developer-only override: the PATH of a binary, not a switch.
_BINARY_ENV_VAR = "MATRX_EGRESS_BINARY"

_BINARY_NAME = "matrx-egress.exe" if PLATFORM["is_windows"] else "matrx-egress"

# How long to wait for the helper's first status write before calling the
# start degraded. The helper writes on every change and every 5s.
_FIRST_STATUS_TIMEOUT = 10.0

_REGISTER_TIMEOUT = 20.0


def _egress_home() -> Path:
    return MATRX_HOME_DIR / "egress"


def status_file_path() -> Path:
    return _egress_home() / "status.json"


def find_binary() -> Path | None:
    """Resolve the ``matrx-egress`` binary, or None when this build has none."""
    override = os.environ.get(_BINARY_ENV_VAR, "").strip()
    if override:
        candidate = Path(override).expanduser()
        if candidate.exists() and candidate.is_file():
            return candidate
        logger.warning(
            "[residential_egress] %s points at %s, which does not exist — "
            "falling through to the normal resolution order",
            _BINARY_ENV_VAR,
            candidate,
        )

    # 2. Bundled sidecar, next to the frozen engine binary.
    exe_dir = Path(sys.executable).parent
    bundled = [
        exe_dir / _BINARY_NAME,
        exe_dir.parent / "Resources" / _BINARY_NAME,
    ]
    # macOS: the engine runs from the NESTED Matrx Engine.app, so the helper that
    # ships beside the HOST executable is three directories up, not beside us.
    host_macos = host_bundle_macos_dir()
    if host_macos is not None:
        bundled.append(host_macos / _BINARY_NAME)
    for candidate in bundled:
        if candidate.exists() and candidate.is_file():
            return candidate

    # 3. Our own bin dir (where a standalone installer drops it too).
    cached = MATRX_HOME_DIR / "bin" / _BINARY_NAME
    if cached.exists() and cached.is_file():
        return cached

    # 4. Dev workspace build.
    repo_root = Path(__file__).resolve().parents[3]
    for profile in ("debug", "release"):
        candidate = (
            repo_root / "desktop" / "src-tauri" / "target" / profile / _BINARY_NAME
        )
        if candidate.exists() and candidate.is_file():
            return candidate

    return None


async def _helper_version(binary: Path) -> str | None:
    """``matrx-egress --version``, or None when it cannot be read."""
    try:
        proc = await asyncio.create_subprocess_exec(
            str(binary),
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
    except Exception:
        logger.debug("[residential_egress] --version failed", exc_info=True)
        return None
    text = (out or b"").decode("utf-8", errors="replace").strip()
    return text or None


class EgressRegistrationError(Exception):
    """Registration with the aidream gateway did not produce a device token."""


class ResidentialEgressSupervisor:
    """Owns the ``matrx-egress`` child for the lifetime of the engine."""

    def __init__(self) -> None:
        self._process: Optional[asyncio.subprocess.Process] = None
        self._process_started_at: Optional[float] = None
        self._process_executable: Optional[str] = None
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._started_at: Optional[float] = None
        self._device_id: Optional[str] = None
        self._display_name: Optional[str] = None
        self._helper_version: Optional[str] = None
        self._last_error: Optional[str] = None
        self._last_remedy: Optional[str] = None
        self._last_exit_code: Optional[int] = None
        self._stopping = False
        # Why the helper is not running although the user asked for it. Set by
        # the reconciler when it declines to start. Without this, "enabled but
        # signed out" read as a bare "stopped" with no error and no remedy —
        # the user flips the switch and the screen says nothing.
        self._blocked_reason: Optional[str] = None
        # start/stop mutate one subprocess handle and one discovery identity.
        self._lifecycle_lock = asyncio.Lock()

    # ── public state ────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def device_id(self) -> str | None:
        return self._device_id

    @property
    def process_identity(self) -> dict[str, int | float | str] | None:
        """PID + creation time + executable — the proof preflight demands.

        PID alone is unsafe (the OS reuses them); creation time closes that
        race and the executable path stops an unrelated process satisfying a
        broad command match. Identical rule to the cloudflared child.
        """
        if (
            self._process is None
            or self._process_started_at is None
            or not self._process_executable
        ):
            return None
        return {
            "pid": self._process.pid,
            "process_started_at": self._process_started_at,
            "executable": self._process_executable,
        }

    # ── start / stop ────────────────────────────────────────────────────

    async def start(self) -> bool:
        async with self._lifecycle_lock:
            return await self._start_locked()

    async def _start_locked(self) -> bool:
        if self.running:
            logger.debug("[residential_egress] start() while already running")
            return True

        self._last_error = None
        self._last_remedy = None

        binary = find_binary()
        if binary is None:
            self._last_error = NOT_INSTALLED_MESSAGE
            self._last_remedy = NOT_INSTALLED_REMEDY
            logger.warning("[residential_egress] %s", NOT_INSTALLED_MESSAGE)
            return False

        self._helper_version = await _helper_version(binary)

        try:
            registration = await self._register_device(binary)
        except EgressRegistrationError as exc:
            self._last_error = str(exc)
            self._last_remedy = (
                "Sign in to AI Matrx on this computer and try the switch again."
            )
            logger.warning("[residential_egress] registration failed: %s", exc)
            return False

        token = registration["device_token"]
        self._device_id = registration.get("device_id")
        self._display_name = registration.get("display_name")

        status_file = status_file_path()
        status_file.parent.mkdir(parents=True, exist_ok=True)
        # A stale file from a previous run would read as live status for the
        # first seconds of this one.
        status_file.unlink(missing_ok=True)

        cmd = self._build_command(binary, status_file)
        kwargs: dict[str, Any] = dict(
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        if PLATFORM["is_windows"]:
            import subprocess as _sp

            kwargs["creationflags"] = (
                _sp.CREATE_NO_WINDOW | _sp.CREATE_NEW_PROCESS_GROUP
            )

        logger.info(
            "[residential_egress] starting helper (executable=%s, version=%s)",
            binary,
            self._helper_version or "unknown",
        )
        try:
            self._process = await asyncio.create_subprocess_exec(*cmd, **kwargs)
        except Exception as exc:
            self._process = None
            self._last_error = f"The connection helper could not start: {exc}"
            self._last_remedy = "Restart AI Matrx; if it keeps failing, reinstall the app."
            logger.error("[residential_egress] spawn failed: %s", exc)
            return False

        self._started_at = time.time()
        self._stopping = False
        self._last_exit_code = None

        # The token goes in on stdin as one line. It never touches disk, argv,
        # or a log line.
        #
        # stdin then STAYS OPEN for the child's lifetime: EOF on stdin is the
        # helper's shutdown signal (contract: "exits 0 on stdin EOF or
        # SIGTERM"), so closing the pipe here would stop the helper the instant
        # it started. ``_terminate_child`` closes it as the graceful stop.
        try:
            assert self._process.stdin is not None
            self._process.stdin.write((token + "\n").encode("utf-8"))
            await self._process.stdin.drain()
        except Exception as exc:
            logger.error("[residential_egress] could not hand the token to the helper: %s", exc)
            await self._terminate_child()
            self._last_error = "The connection helper did not accept its credential."
            self._last_remedy = "Turn the switch off and on again."
            return False
        finally:
            del token

        try:
            spawned = psutil.Process(self._process.pid)
            self._process_started_at = spawned.create_time()
            self._process_executable = spawned.exe()
        except Exception as exc:
            logger.warning(
                "[residential_egress] could not capture process identity; orphan "
                "cleanup will fail closed for pid %s: %s",
                self._process.pid,
                exc,
            )

        identity = self.process_identity
        if identity:
            try:
                from app.preflight import update_discovery_service

                update_discovery_service("residential_egress", identity)
            except Exception:
                logger.debug(
                    "[residential_egress] could not persist process identity",
                    exc_info=True,
                )

        self._reader_task = asyncio.create_task(
            self._read_output(self._process), name="residential-egress-reader"
        )

        # Give the helper a moment to write its first status. A helper that
        # exits immediately is a failure, not a silent no-op.
        deadline = time.monotonic() + _FIRST_STATUS_TIMEOUT
        while time.monotonic() < deadline:
            if self._process.returncode is not None:
                self._last_exit_code = self._process.returncode
                self._last_error = (
                    f"The connection helper stopped straight away (exit code "
                    f"{self._process.returncode})."
                )
                self._last_remedy = "Turn the switch off and on again."
                return False
            if status_file.exists():
                return True
            await asyncio.sleep(0.2)

        # Running but silent — honest degraded, not a lie in either direction.
        logger.warning(
            "[residential_egress] helper is running but wrote no status within %.0fs",
            _FIRST_STATUS_TIMEOUT,
        )
        return True

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        self._stopping = True
        await self._terminate_child()

        if self._reader_task and not self._reader_task.done():
            try:
                await asyncio.wait_for(self._reader_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except (asyncio.CancelledError, Exception):
                    pass
        self._reader_task = None

        self._process = None
        self._process_started_at = None
        self._process_executable = None
        self._started_at = None
        self._clear_discovery()
        status_file_path().unlink(missing_ok=True)
        self._stopping = False
        logger.info(
            "[residential_egress] helper stopped (exit code: %s)",
            self._last_exit_code if self._last_exit_code is not None else "n/a",
        )

    async def _terminate_child(self) -> None:
        """Terminate → wait → SIGKILL. Cascade before reporting done."""
        proc = self._process
        if proc is None:
            return
        if proc.returncode is not None:
            self._last_exit_code = proc.returncode
            return
        # Graceful first: EOF on stdin is the helper's own "stop now" signal,
        # so it gets to close its sockets and write a final status. SIGTERM
        # then SIGKILL are the escalations behind it.
        try:
            if proc.stdin is not None and not proc.stdin.is_closing():
                proc.stdin.close()
            await asyncio.wait_for(proc.wait(), timeout=3.0)
            self._last_exit_code = proc.returncode
            return
        except asyncio.TimeoutError:
            logger.debug(
                "[residential_egress] helper still alive 3s after stdin EOF — "
                "sending SIGTERM"
            )
        except Exception:
            logger.debug("[residential_egress] stdin close error", exc_info=True)

        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning(
                "[residential_egress] helper did not exit within 5s of SIGTERM — "
                "escalating to SIGKILL"
            )
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.error("[residential_egress] helper survived SIGKILL — wedged")
            except Exception:
                logger.debug("[residential_egress] kill error", exc_info=True)
        except Exception as exc:
            logger.debug("[residential_egress] terminate error: %s", exc)
        self._last_exit_code = proc.returncode

    def _clear_discovery(self) -> None:
        try:
            from app.preflight import update_discovery_service

            update_discovery_service("residential_egress", None)
        except Exception:
            logger.debug(
                "[residential_egress] could not clear discovery state", exc_info=True
            )

    # ── status ──────────────────────────────────────────────────────────

    def read_status_file(self) -> dict[str, Any] | None:
        path = status_file_path()
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def get_status(self, *, enabled: bool) -> dict[str, Any]:
        """The one honest answer for ``GET /egress/status``.

        Shape: the helper's own status JSON (contract § "The helper binary"),
        plus ``installed`` / ``enabled`` / ``remedy``. Every state names itself;
        none of them is a spinner.
        """
        binary = find_binary()
        installed = binary is not None

        base: dict[str, Any] = {
            "state": "stopped",
            "since": None,
            "device_name": self._display_name,
            "server": None,
            "streams_active": 0,
            "streams_total": 0,
            "bytes_relayed": 0,
            "last_error": self._last_error,
            "remedy": self._last_remedy,
            "helper_version": self._helper_version,
        }

        if not installed:
            base.update(
                {
                    "state": "not_installed",
                    "last_error": NOT_INSTALLED_MESSAGE,
                    "remedy": NOT_INSTALLED_REMEDY,
                }
            )
        elif not enabled:
            base["state"] = "disabled"
            base["last_error"] = None
            base["remedy"] = None
        elif self._blocked_reason == "signed_out":
            base.update(
                {
                    "state": "signed_out",
                    "last_error": "Nobody is signed in to AI Matrx on this computer.",
                    "remedy": "Sign in, and the home connection starts on its own.",
                }
            )
        elif self.running:
            helper = self.read_status_file()
            if isinstance(helper, dict):
                base.update(helper)
                base.setdefault("remedy", None)
            else:
                base["state"] = "connecting"
        elif self._last_error:
            base["state"] = "error"

        base["installed"] = installed
        base["enabled"] = bool(enabled)
        base["running"] = self.running
        base["device_id"] = self._device_id
        base["uptime_seconds"] = (
            round(time.time() - self._started_at, 1) if self._started_at else 0.0
        )
        return base

    # ── internals ───────────────────────────────────────────────────────

    def _build_command(self, binary: Path, status_file: Path) -> list[str]:
        from app.services.app_config import get_aidream_server_url
        from app.services.cloud_sync.instance_manager import get_instance_manager

        return [
            str(binary),
            "run",
            "--token-stdin",
            "--server",
            (get_aidream_server_url() or "").rstrip("/"),
            "--status-file",
            str(status_file),
            "--no-tray",
            "--name",
            get_instance_manager().instance_name,
        ]

    async def _register_device(self, binary: Path) -> dict[str, Any]:
        """``POST {aidream}/egress/devices`` with the daemon's access grant.

        The route is NOT under ``/api`` (the gateway's router prefix is
        ``/egress``), so this is a direct call rather than ``AIDreamClient``.
        """
        import httpx

        from app.services.app_config import get_aidream_server_url
        from app.services.cloud_sync.instance_manager import get_instance_manager
        from app.services.sync_client import get_sync_client

        base_url = (get_aidream_server_url() or "").rstrip("/")
        if not base_url:
            raise EgressRegistrationError(
                "No AI Matrx server address is configured on this computer."
            )

        grant = await get_sync_client().access_grant()
        if grant is None:
            raise EgressRegistrationError("Nobody is signed in on this computer.")
        jwt, _user_id = grant

        registration = get_instance_manager().get_registration_payload()
        registration["client_kind"] = "desktop_app"
        registration["helper_version"] = self._helper_version

        try:
            async with httpx.AsyncClient(timeout=_REGISTER_TIMEOUT) as http:
                response = await http.post(
                    f"{base_url}/egress/devices",
                    headers={
                        "Authorization": f"Bearer {jwt}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json={"registration": registration},
                )
        except httpx.HTTPError as exc:
            raise EgressRegistrationError(
                f"Could not reach AI Matrx to register this computer: {exc}"
            ) from exc

        if not response.is_success:
            raise EgressRegistrationError(
                f"AI Matrx refused to register this computer "
                f"(HTTP {response.status_code})."
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise EgressRegistrationError(
                "AI Matrx returned an unreadable answer when registering this computer."
            ) from exc

        if not isinstance(body, dict) or not body.get("device_token"):
            raise EgressRegistrationError(
                "AI Matrx registered this computer but issued no credential."
            )
        return body

    async def _read_output(self, process: asyncio.subprocess.Process) -> None:
        """Drain the helper's stdout (one JSON line per state change).

        The helper never prints its token; these lines are state, not secrets.
        """
        if process.stdout is None:
            return
        try:
            async for raw in process.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    logger.info("[matrx-egress] %s", line)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("[residential_egress] output reader error", exc_info=True)

        if process.returncode is None:
            try:
                await process.wait()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug(
                    "[residential_egress] wait after output EOF failed", exc_info=True
                )

        if process.returncode is not None and not self._stopping:
            # A spontaneous exit has no stop route to repair state. Say so
            # rather than leaving a dead child reading as connected.
            self._last_exit_code = process.returncode
            self._last_error = (
                f"The connection helper stopped on its own (exit code "
                f"{process.returncode})."
            )
            self._last_remedy = "Turn the switch off and on again."
            self._clear_discovery()
            status_file_path().unlink(missing_ok=True)
            try:
                from app.launcher import get_registry

                get_registry().failed("residential_egress", self._last_error)
            except Exception:
                logger.debug(
                    "[residential_egress] could not record the child's exit",
                    exc_info=True,
                )
            logger.warning("[residential_egress] %s", self._last_error)


_supervisor: Optional[ResidentialEgressSupervisor] = None


def get_egress_supervisor() -> ResidentialEgressSupervisor:
    global _supervisor
    if _supervisor is None:
        _supervisor = ResidentialEgressSupervisor()
    return _supervisor


def egress_enabled() -> bool:
    """The user's intent, read fresh — default off (they are lending a connection)."""
    try:
        from app.services.cloud_sync.settings_sync import get_settings_sync

        return bool(get_settings_sync().get("residential_egress_enabled", False))
    except Exception:
        logger.warning(
            "[residential_egress] settings read failed — the home connection "
            "stays off (an opt-in never defaults on)",
            exc_info=True,
        )
        return False


async def _signed_in() -> bool:
    try:
        from app.services.sync_client import get_sync_client

        return await get_sync_client().user_id() is not None
    except Exception:
        return False


async def reconcile_residential_egress() -> None:
    """Start the helper when signed in AND enabled; stop it otherwise.

    The ONE decision point. Called from the startup phase, from the
    enable/disable routes, and from the daemon session reconciler on every
    sign-in / sign-out — so a sign-out takes the home connection down with it.
    """
    from app.launcher import get_registry

    registry = get_registry()
    supervisor = get_egress_supervisor()
    enabled = egress_enabled()
    signed_in = await _signed_in()
    want = enabled and signed_in
    # Say WHY, when the answer is "not now". A switch the user turned on that
    # quietly does nothing is the failure this records.
    supervisor._blocked_reason = (
        "signed_out" if (enabled and not signed_in) else None
    )

    if want and not supervisor.running:
        registry.starting("residential_egress")
        started = await supervisor.start()
        if started:
            registry.ready(
                "residential_egress",
                pid=supervisor._process.pid if supervisor._process else None,
                device_id=supervisor.device_id,
            )
        elif find_binary() is None:
            registry.degraded("residential_egress", NOT_INSTALLED_MESSAGE)
        else:
            registry.failed(
                "residential_egress",
                supervisor._last_error or "the home-connection helper did not start",
            )
    elif not want and supervisor.running:
        registry.stopping("residential_egress")
        await supervisor.stop()
        registry.stopped("residential_egress")
    elif enabled and not signed_in:
        # The user asked for it and we are declining. Say so in the registry
        # too, so /admin/status and a diagnostic dump carry the reason instead
        # of an empty slot that reads as "nobody wanted this".
        registry.degraded(
            "residential_egress",
            "nobody is signed in on this computer — it starts on sign-in",
        )
