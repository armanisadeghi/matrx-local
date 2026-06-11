"""Cloudflare Tunnel manager.

Manages a `cloudflared` subprocess that exposes the local engine
(127.0.0.1:{port}) to the internet via a Cloudflare Tunnel URL.

Two modes:
  - Quick tunnel (no token, default for all users): assigns a random
    *.trycloudflare.com URL per instance. URL changes on each restart.
    Zero config — no Cloudflare account, no token, no setup required.
    The URL is automatically stored in Supabase so remote devices can
    discover it via the /api/local-instances lookup.
  - Named tunnel (CLOUDFLARE_TUNNEL_TOKEN set): uses a pre-provisioned
    tunnel with a stable subdomain. URL survives restarts. Optional — for
    power users who want a permanent address.

The cloudflared binary is bundled with the installer. If not found, it is
downloaded on first use and cached at ~/.matrx/bin/cloudflared.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import stat
import sys
import time
from collections import deque
from pathlib import Path
from typing import Optional
from urllib.request import urlretrieve

from app.common.platform_ctx import CAPABILITIES, PLATFORM
from app.config import MATRX_HOME_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Binary download config
# ---------------------------------------------------------------------------

_CLOUDFLARED_VERSION = "2025.1.0"  # pin to a known-good release

_DOWNLOAD_URLS: dict[tuple[str, str], str] = {
    ("Darwin",  "arm64"):  f"https://github.com/cloudflare/cloudflared/releases/download/{_CLOUDFLARED_VERSION}/cloudflared-darwin-arm64",
    ("Darwin",  "x86_64"): f"https://github.com/cloudflare/cloudflared/releases/download/{_CLOUDFLARED_VERSION}/cloudflared-darwin-amd64",
    ("Linux",   "aarch64"):f"https://github.com/cloudflare/cloudflared/releases/download/{_CLOUDFLARED_VERSION}/cloudflared-linux-arm64",
    ("Linux",   "x86_64"): f"https://github.com/cloudflare/cloudflared/releases/download/{_CLOUDFLARED_VERSION}/cloudflared-linux-amd64",
    ("Windows", "AMD64"):  f"https://github.com/cloudflare/cloudflared/releases/download/{_CLOUDFLARED_VERSION}/cloudflared-windows-amd64.exe",
    ("Windows", "ARM64"):  f"https://github.com/cloudflare/cloudflared/releases/download/{_CLOUDFLARED_VERSION}/cloudflared-windows-arm64.exe",
}

# cloudflared prints the tunnel URL to stdout/stderr during startup.
#
# Quick tunnel log line looks like:
#   INF +----------------------------+
#   INF |  https://abc-def.trycloudflare.com  |
#   INF +----------------------------+
#
# Named tunnel log line looks like:
#   INF Registered tunnel connection ... url=https://your-subdomain.example.com
#
# _TUNNEL_URL_RE: matches the boxed URL line cloudflared prints for quick tunnels.
_TUNNEL_URL_RE = re.compile(
    r"\|\s*(https://[a-zA-Z0-9._-]+\.trycloudflare\.com[^\s|]*)"
)
# _NAMED_URL_RE: matches the url= field cloudflared logs for named tunnels.
_NAMED_URL_RE = re.compile(
    r'url=(https://[a-zA-Z0-9._-]+\.[a-zA-Z]{2,}[^\s"]*)'
)
# _FALLBACK_URL_RE: broad fallback — any https URL on a line mentioning the tunnel.
# Catches any format cloudflare might use across versions.
_FALLBACK_URL_RE = re.compile(
    # Negative lookahead: api.trycloudflare.com appears in cloudflared's own
    # QuickTunnel ERROR lines ("Error unmarshaling QuickTunnel response" from
    # https://api.trycloudflare.com/...) — matching it published a bogus
    # tunnel URL and marked the tunnel READY.
    r"(https://(?!api\.)[a-zA-Z0-9._-]+\.trycloudflare\.com[^\s]*)"
)


def _bin_dir() -> Path:
    return MATRX_HOME_DIR / "bin"


def _bin_path() -> Path:
    name = "cloudflared.exe" if PLATFORM["is_windows"] else "cloudflared"
    return _bin_dir() / name


def _get_download_url() -> str:
    system = PLATFORM["system"]
    machine = PLATFORM["machine"]
    # Normalise machine name to match _DOWNLOAD_URLS keys per platform
    if machine.lower() in ("arm64", "aarch64"):
        if system == "Linux":
            machine = "aarch64"
        elif system == "Windows":
            machine = "ARM64"
        else:
            machine = "arm64"
    if machine.lower() == "amd64":
        machine = "AMD64" if system == "Windows" else "x86_64"
    key = (system, machine)
    url = _DOWNLOAD_URLS.get(key)
    if not url:
        raise RuntimeError(
            f"No cloudflared binary available for {system}/{machine}. "
            "Download manually from https://github.com/cloudflare/cloudflared/releases "
            f"and place it at {_bin_path()}"
        )
    return url


def _finalize_binary(tmp: Path, dest: Path) -> None:
    """Atomically move tmp → dest and make executable on non-Windows.

    Uses Path.replace() which maps to MoveFileExW(MOVEFILE_REPLACE_EXISTING)
    on Windows — safe even if dest already exists.  On POSIX this is an
    atomic rename(2) call.

    On macOS, strips the com.apple.quarantine xattr so Gatekeeper does not
    block execution of the downloaded binary.
    """
    tmp.replace(dest)
    if not PLATFORM["is_windows"]:
        dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    if PLATFORM["system"] == "Darwin":
        try:
            import subprocess as _sp
            _sp.run(["xattr", "-d", "com.apple.quarantine", str(dest)],
                    capture_output=True, timeout=5)
        except Exception:
            pass


def _find_preinstalled_cloudflared() -> Path | None:
    """Look for cloudflared in:
    1. Tauri resource directory (bundled in the .app / installer)
    2. ~/.matrx/bin/ (previously downloaded by us)
    3. System PATH / well-known locations (Homebrew, package manager)

    Returns the first found binary, or None if not available anywhere.
    This function intentionally does NOT download — that's _ensure_binary().
    """
    # 1. Tauri resource dir: the frozen Python sidecar runs from a Resources/ folder.
    #    When we bundle cloudflared as a Tauri resource it lands next to the engine binary.
    resource_candidates = [
        Path(sys.executable).parent / "cloudflared",            # macOS/Linux sidecar
        Path(sys.executable).parent / "cloudflared.exe",        # Windows sidecar
        Path(sys.executable).with_name("cloudflared"),          # same dir as binary
        Path(sys.executable).with_name("cloudflared.exe"),
        # macOS .app bundle layout: sidecar is at Contents/MacOS/, Tauri resources
        # land at Contents/Resources/ — one level up from the executable parent.
        Path(sys.executable).parent.parent / "Resources" / "cloudflared",
    ]
    for p in resource_candidates:
        if p.exists() and p.is_file():
            logger.info("Found bundled cloudflared at %s", p)
            return p

    # 2. Our own cached download
    cached = _bin_path()
    if cached.exists():
        return cached

    # 3. System install (Homebrew, apt, MSI installer, etc.)
    system_candidates = [
        "/opt/homebrew/bin/cloudflared",          # macOS Apple Silicon (Homebrew)
        "/usr/local/bin/cloudflared",              # macOS Intel (Homebrew) / Linux
        "/usr/bin/cloudflared",                    # Linux package manager
        "C:\\Program Files\\cloudflared\\cloudflared.exe",  # Windows MSI
    ]
    for path_str in system_candidates:
        p = Path(path_str)
        if p.exists() and p.is_file():
            logger.info("Found system cloudflared at %s", p)
            return p

    # Also check PATH (already probed at startup)
    path_on_path = CAPABILITIES["cloudflared_path"]
    if path_on_path:
        return Path(path_on_path)

    return None


def _ensure_binary() -> Path:
    """Return path to a working cloudflared binary.

    Priority:
    1. Bundled (Tauri resource) or system-installed binary — no download needed.
    2. Previously cached download in ~/.matrx/bin/.
    3. Fresh download from GitHub Releases (first-run only, ~18 MB).
    """
    existing = _find_preinstalled_cloudflared()
    if existing:
        return existing

    # Nothing found locally — download once and cache it.
    dest = _bin_path()
    url = _get_download_url()
    logger.info("cloudflared not found locally — downloading from %s → %s", url, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    tmp = dest.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    try:
        urlretrieve(url, tmp)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to download cloudflared: {exc}") from exc

    _finalize_binary(tmp, dest)

    logger.info("cloudflared downloaded ✓ (%s)", dest)
    return dest


# ---------------------------------------------------------------------------
# TunnelManager
# ---------------------------------------------------------------------------


class TunnelManager:
    """Manages the cloudflared subprocess for inbound tunnel access."""

    def __init__(self) -> None:
        self._process: Optional[asyncio.subprocess.Process] = None
        self._url: Optional[str] = None
        self._ws_url: Optional[str] = None
        self._started_at: Optional[float] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._url_ready: asyncio.Event = asyncio.Event()
        self._token: str = os.getenv("CLOUDFLARE_TUNNEL_TOKEN", "")
        self._port: int = 0
        # Ring buffer of the most recent cloudflared output lines. We keep
        # this regardless of URL capture so when the process dies with a
        # non-zero exit code we can dump the last few seconds of output and
        # actually see WHY (DNS timeout? auth failure? port collision?).
        # 200 lines covers the typical "headline + boilerplate + error
        # block" cloudflared emits on failure.
        self._recent_output: deque[str] = deque(maxlen=200)
        self._last_exit_code: Optional[int] = None

    # ── public API ──────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def url(self) -> Optional[str]:
        return self._url

    @property
    def ws_url(self) -> Optional[str]:
        return self._ws_url

    @property
    def uptime_seconds(self) -> float:
        if self._started_at is None:
            return 0.0
        return round(time.time() - self._started_at, 1)

    @property
    def recent_output(self) -> list[str]:
        """Return a copy of the most recent cloudflared output lines.

        Used by the launcher's diagnostic dump to capture the actual reason
        cloudflared exited (e.g. exit code 1 with no obvious error in the
        engine's log). Safe to call at any time, including after stop().
        """
        return list(self._recent_output)

    @property
    def last_exit_code(self) -> Optional[int]:
        """Return cloudflared's exit code from the most recent run, or None."""
        return self._last_exit_code

    async def start(self, port: int) -> Optional[str]:
        """Start the tunnel subprocess. Returns the public URL when ready.

        Retries once on transient trycloudflare.com server flakes (the 1101 /
        "Error unmarshaling QuickTunnel response" failure where Cloudflare's
        edge returns a 500 with an HTML body instead of JSON). The retry
        applies only to that specific signature; every other failure mode
        (DNS, port-already-in-use, binary missing, network down) returns
        immediately so the operator sees the real cause.
        """
        if self.running:
            logger.warning("TunnelManager.start() called while already running")
            return self._url

        self._port = port

        try:
            bin_path = await asyncio.get_event_loop().run_in_executor(
                None, _ensure_binary
            )
            logger.info("cloudflared binary resolved: %s (exists=%s)", bin_path, bin_path.exists())
        except Exception as exc:
            logger.error("Failed to acquire cloudflared binary: %s", exc)
            return None

        # First attempt.
        url = await self._spawn_and_wait_for_url(bin_path, port, attempt=1)
        if url:
            return url

        # Retry only for the specific transient signature: cloudflared
        # exited with code 1 and its tail mentions either "error code: 1101"
        # (Cloudflare Worker exception) or "Error unmarshaling QuickTunnel
        # response" (HTML 500 returned where JSON was expected). Every
        # other failure returns immediately with the existing error log.
        if self._last_exit_code == 1 and self._is_transient_quicktunnel_flake():
            logger.warning(
                "cloudflared hit the trycloudflare.com QuickTunnel 1101 flake; "
                "retrying once after 3s backoff (this is a Cloudflare-side "
                "intermittent — not a local config issue)"
            )
            await asyncio.sleep(3.0)
            url = await self._spawn_and_wait_for_url(bin_path, port, attempt=2)

        if self._url:
            logger.info("Tunnel active: %s", self._url)
        return self._url

    def _is_transient_quicktunnel_flake(self) -> bool:
        """True iff the recent cloudflared output matches the known QuickTunnel
        500/1101 transient. Used to gate the one-shot retry."""
        markers = (
            "error code: 1101",
            "error unmarshaling quicktunnel",
            "500 internal server error",
        )
        for line in self._recent_output:
            ll = line.lower()
            if any(m in ll for m in markers):
                return True
        return False

    async def _spawn_and_wait_for_url(
        self, bin_path: Path, port: int, *, attempt: int
    ) -> Optional[str]:
        """Single spawn + 30s URL-wait cycle. Returns the public URL on
        success or None on any failure (timeout, early exit, exception).
        Resets per-attempt state so the retry path doesn't carry stale
        readers, output, or url events from the first attempt."""
        # Reset per-attempt state so a retry starts clean.
        self._url = None
        self._ws_url = None
        self._url_ready.clear()
        self._recent_output.clear()
        self._last_exit_code = None
        self._process = None
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        self._reader_task = None

        cmd = self._build_command(bin_path, port)
        logger.info(
            "Starting cloudflared (attempt %d): %s",
            attempt,
            " ".join(str(c) for c in cmd),
        )

        kwargs: dict = dict(
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        if PLATFORM["is_windows"]:
            import subprocess as _sp
            kwargs["creationflags"] = (
                _sp.CREATE_NO_WINDOW | _sp.CREATE_NEW_PROCESS_GROUP
            )

        try:
            self._process = await asyncio.create_subprocess_exec(*cmd, **kwargs)
        except Exception as exc:
            logger.error("Failed to launch cloudflared: %s", exc)
            return None

        self._started_at = time.time()
        self._reader_task = asyncio.create_task(self._read_output())

        try:
            exit_task = asyncio.create_task(self._process.wait())
            url_task = asyncio.create_task(self._url_ready.wait())
            done, pending = await asyncio.wait(
                [exit_task, url_task],
                timeout=30.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()

            if url_task in done:
                return self._url  # URL captured
            if exit_task in done:
                rc = exit_task.result()
                self._last_exit_code = rc
                tail = list(self._recent_output)[-30:]
                logger.error(
                    "cloudflared (attempt %d) exited with code %s before "
                    "emitting a tunnel URL — last %d lines:\n%s",
                    attempt,
                    rc,
                    len(tail),
                    "\n".join(f"  | {line}" for line in tail) or "  | <no output captured>",
                )
                return None
            logger.warning(
                "cloudflared (attempt %d) did not emit a tunnel URL within "
                "30s — last 10 lines:\n%s",
                attempt,
                "\n".join(f"  | {line}" for line in list(self._recent_output)[-10:]) or "  | <no output captured>",
            )
            return None
        except Exception as exc:
            logger.error("Error waiting for cloudflared URL (attempt %d): %s", attempt, exc)
            return None

    async def stop(self) -> None:
        """Stop the tunnel subprocess.

        Order matters: terminate the process FIRST while the stdout reader is
        still draining the pipe (it exits naturally on EOF). Cancelling the
        reader first left cloudflared's shutdown output unconsumed — a full
        pipe buffer would block it inside the SIGTERM window and force the
        SIGKILL path.
        """
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
                self._last_exit_code = self._process.returncode
            except asyncio.TimeoutError:
                logger.warning(
                    "cloudflared did not exit within 5s of SIGTERM — escalating to SIGKILL"
                )
                self._process.kill()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    logger.error("cloudflared survived SIGKILL — process is wedged")
                self._last_exit_code = self._process.returncode
            except Exception as exc:
                logger.debug("cloudflared stop error: %s", exc)
        elif self._process:
            # Process already dead before we got here. Capture whatever exit
            # code we have so the registry can report it.
            self._last_exit_code = self._process.returncode

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
        self._url = None
        self._ws_url = None
        self._started_at = None
        logger.info(
            "Tunnel stopped (cloudflared exit code: %s)",
            self._last_exit_code if self._last_exit_code is not None else "n/a",
        )

    def get_status(self) -> dict:
        return {
            "running": self.running,
            "url": self._url,
            "ws_url": self._ws_url,
            "uptime_seconds": self.uptime_seconds,
            "port": self._port,
            "mode": "named" if self._token else "quick",
        }

    # ── internal helpers ────────────────────────────────────────────────────

    def _build_command(self, bin_path: Path, port: int) -> list[str]:
        if self._token:
            # Named tunnel mode — token was pre-provisioned, URL is stable.
            return [str(bin_path), "tunnel", "--no-autoupdate", "run", "--token", self._token]
        else:
            # Quick tunnel mode — random URL, changes on restart.
            return [
                str(bin_path), "tunnel", "--no-autoupdate",
                "--url", f"http://127.0.0.1:{port}",
            ]

    async def _read_output(self) -> None:
        """Read cloudflared stdout/stderr and extract the tunnel URL."""
        if not self._process or not self._process.stdout:
            return

        line_count = 0
        try:
            async for raw_line in self._process.stdout:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue

                line_count += 1
                # Always retain in the ring buffer regardless of log level —
                # diagnostic dumps need the full tail of recent output even
                # when log level is INFO+ and DEBUG lines are dropped.
                self._recent_output.append(line)

                if line_count <= 30 or not self._url:
                    logger.info("[cloudflared] %s", line)
                else:
                    logger.debug("[cloudflared] %s", line)

                match = _TUNNEL_URL_RE.search(line) or _NAMED_URL_RE.search(line) or _FALLBACK_URL_RE.search(line)
                if match and not self._url:
                    url = match.group(1).rstrip("/")
                    self._url = url
                    self._ws_url = url.replace("https://", "wss://") + "/ws"
                    self._url_ready.set()
                    logger.info("Tunnel URL captured: %s", url)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("cloudflared output reader error: %s", exc)

        rc = self._process.returncode if self._process else "N/A"
        logger.info(
            "[cloudflared] output reader finished — %d lines read, exit code: %s, url found: %s",
            line_count, rc, bool(self._url),
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_tunnel_manager: Optional[TunnelManager] = None


def get_tunnel_manager() -> TunnelManager:
    global _tunnel_manager
    if _tunnel_manager is None:
        _tunnel_manager = TunnelManager()
    return _tunnel_manager
