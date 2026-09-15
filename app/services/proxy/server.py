"""HTTP forward proxy server.

Runs as a lightweight HTTP proxy on a configurable port so that external
services (e.g. the cloud Python backend) can route requests through the
user's residential IP address.

Protocol: HTTP CONNECT (for HTTPS tunnelling) + plain HTTP forwarding.
No SOCKS5.  No caching.  Minimal overhead.

Usage from external Python code:
    proxies = {"http": "http://127.0.0.1:22180", "https": "http://127.0.0.1:22180"}
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Port-base offset +40 keeps the proxy inside its world's port block:
# live 22140 → 22180, dev 22240 → 22280 (MXL-D-043 dev/live isolation).
# Same formula as DEFAULT_SETTINGS["proxy_port"] in cloud_sync/settings_sync.py
# and DEFAULTS.proxyPort in desktop/src/lib/settings.ts.
PROXY_PORT_OFFSET = 40
DEFAULT_PROXY_PORT = int(os.environ.get("MATRX_PORT_BASE", "22140")) + PROXY_PORT_OFFSET
MAX_PORT_SCAN = 10


def derive_proxy_port(engine_port: int, configured: int | None = None) -> int:
    """The proxy port for the engine that actually bound ``engine_port``.

    🚨 THE OFFSET IS OFF THE BOUND PORT, NOT OFF THE PORT BASE (fixed
    2026-09-15). The engine's port is dynamic: it scans its world's block
    (dev 22240–22259) and takes the first free one, so a second dev engine
    lives on 22241. The proxy port was computed from the static PORT BASE
    instead, so every dev engine wanted 22280 and every engine after the first
    booted with ``failed: ["proxy"]`` — observed live on 2026-09-14 with two
    source engines running, the second reporting

        proxy → ✗ FAILED — port 22280 in use: [errno 48] address already in use

    while `_find_available_port` (which would have scanned) sat unused because
    Phase 4 passes an explicit port. Deriving from the bound port gives each
    engine its own proxy (22240→22280, 22241→22281) and keeps it inside the
    world's block, which is what the +40 was for.

    A port the user actually chose still wins: only a stored value equal to
    this world's static default is treated as "nobody picked this", because
    that is exactly what a shipped default looks like.
    """
    if configured is not None and configured != DEFAULT_PROXY_PORT:
        return configured
    if engine_port > 0:
        return engine_port + PROXY_PORT_OFFSET
    return DEFAULT_PROXY_PORT
BUFFER_SIZE = 65536
CONNECT_TIMEOUT = 15


class ProxyServer:
    """Async HTTP forward proxy using raw sockets."""

    def __init__(self) -> None:
        self._server: Optional[asyncio.AbstractServer] = None
        self._port: int = 0
        self._running = False
        self._request_count = 0
        self._bytes_forwarded = 0
        self._started_at: Optional[float] = None
        self._active_connections = 0

    # ── public API ──────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._running

    @property
    def port(self) -> int:
        return self._port

    @property
    def stats(self) -> dict:
        return {
            "running": self._running,
            "port": self._port,
            "request_count": self._request_count,
            "bytes_forwarded": self._bytes_forwarded,
            "active_connections": self._active_connections,
            "uptime_seconds": round(time.time() - self._started_at, 1) if self._started_at else 0,
        }

    async def start(self, port: int = 0) -> int:
        """Start the proxy server. Returns the port it bound to."""
        if self._running:
            logger.info(
                "[app/services/proxy/server.py] HTTP proxy already running on port %d", self._port
            )
            return self._port

        # 🚨 THE ONLY PORT THIS REPORTS IS ONE IT ACTUALLY BOUND.
        # A requested port can be taken between the caller deciding on it and
        # this bind — two engines racing the same engine port derive the same
        # proxy port, and a stale TIME_WAIT or a foreign listener does it too.
        # Before 2026-09-15 that was a hard failure and the engine simply ran
        # without a proxy for the session. A bind refusal is retried forward
        # through the same scan range as the unrequested case, loudly; only
        # exhausting the range is fatal.
        first_choice = port or self._find_available_port()
        chosen_port = 0
        last_exc: OSError | None = None
        for offset in range(MAX_PORT_SCAN):
            candidate = first_choice + offset
            if candidate > 65535:
                break
            logger.info(
                "[app/services/proxy/server.py] Binding HTTP proxy to 127.0.0.1:%d...",
                candidate,
            )
            try:
                self._server = await asyncio.start_server(
                    self._handle_client,
                    host="127.0.0.1",
                    port=candidate,
                )
            except OSError as exc:
                last_exc = exc
                logger.warning(
                    "[app/services/proxy/server.py] Port %d is taken (%s) — trying "
                    "%d. Whoever holds it: lsof -ti:%d",
                    candidate, exc, candidate + 1, candidate,
                )
                continue
            chosen_port = candidate
            if candidate != first_choice:
                logger.warning(
                    "[app/services/proxy/server.py] HTTP proxy moved to port %d "
                    "because %d was already taken — anything configured to reach "
                    "this proxy must use %d (the engine reports the bound port in "
                    "/admin/status and /proxy/status)",
                    candidate, first_choice, candidate,
                )
            break
        else:
            chosen_port = 0

        if not chosen_port:
            logger.error(
                "[app/services/proxy/server.py] Failed to bind the HTTP proxy "
                "anywhere in %d-%d — last error: %s. Free one of those ports "
                "(lsof -ti:%d) or set a different proxy port in Settings.",
                first_choice, first_choice + MAX_PORT_SCAN - 1, last_exc,
                first_choice,
            )
            raise last_exc if last_exc is not None else OSError(
                f"no free proxy port in {first_choice}-{first_choice + MAX_PORT_SCAN - 1}"
            )
        self._port = chosen_port
        self._running = True
        self._started_at = time.time()
        logger.info(
            "[app/services/proxy/server.py] HTTP proxy server started ✓ on 127.0.0.1:%d", chosen_port
        )
        return chosen_port

    async def stop(self) -> None:
        """Stop the proxy server.

        wait_closed() blocks until all active connection handlers exit. On
        Windows this can hang indefinitely if a handler is stuck on a slow
        upstream or a keep-alive connection (the OS does not forcibly close
        sockets on asyncio server.close() the way Unix does). We cap the
        wait at 4 seconds so shutdown never blocks the rest of teardown.
        """
        if not self._running:
            return
        self._running = False
        if self._server:
            self._server.close()
            try:
                import asyncio as _asyncio
                await _asyncio.wait_for(self._server.wait_closed(), timeout=4.0)
            except Exception:
                pass
            self._server = None
        logger.info("HTTP proxy server stopped")

    async def test_connectivity(self, test_url: str = "http://httpbin.org/ip") -> dict:
        """Test that the proxy can reach the internet. Returns result dict."""
        import httpx

        proxy_url = f"http://127.0.0.1:{self._port}"
        try:
            async with httpx.AsyncClient(proxy=proxy_url, timeout=10) as client:
                resp = await client.get(test_url)
                return {
                    "success": True,
                    "status_code": resp.status_code,
                    "body": resp.text[:500],
                    "proxy_url": proxy_url,
                }
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
                "proxy_url": proxy_url,
            }

    # ── connection handling ─────────────────────────────────────────────

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single proxy client connection."""
        self._active_connections += 1
        try:
            # Read the initial request line
            first_line = await asyncio.wait_for(reader.readline(), timeout=30)
            if not first_line:
                return

            request_line = first_line.decode("latin-1", errors="replace").strip()
            parts = request_line.split()
            if len(parts) < 3:
                writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                await writer.drain()
                return

            method = parts[0].upper()

            if method == "CONNECT":
                await self._handle_connect(parts[1], reader, writer)
            else:
                await self._handle_http(request_line, reader, writer)

            self._request_count += 1
        except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception:
            logger.warning("Proxy connection error", exc_info=True)
        finally:
            self._active_connections -= 1
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_connect(
        self,
        target: str,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """Handle CONNECT method (HTTPS tunneling)."""
        # Parse host:port
        if ":" in target:
            host, port_str = target.rsplit(":", 1)
            port = int(port_str)
        else:
            host = target
            port = 443

        # Read and discard remaining headers
        while True:
            line = await asyncio.wait_for(client_reader.readline(), timeout=10)
            if line in (b"\r\n", b"\n", b""):
                break

        # Connect to target
        try:
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=CONNECT_TIMEOUT,
            )
        except Exception as exc:
            client_writer.write(f"HTTP/1.1 502 Bad Gateway\r\n\r\n{exc}\r\n".encode())
            await client_writer.drain()
            return

        # Send 200 Connection Established
        client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await client_writer.drain()

        # Bidirectional tunnel
        await self._tunnel(client_reader, client_writer, remote_reader, remote_writer)

    async def _handle_http(
        self,
        request_line: str,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """Handle plain HTTP request forwarding."""
        parts = request_line.split()
        method = parts[0]
        url = parts[1]
        version = parts[2] if len(parts) > 2 else "HTTP/1.1"

        # Parse the URL to extract host and path
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.hostname or ""
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        # Read headers
        headers_raw = b""
        while True:
            line = await asyncio.wait_for(client_reader.readline(), timeout=10)
            headers_raw += line
            if line in (b"\r\n", b"\n", b""):
                break

        # Check for content-length to read body
        headers_text = headers_raw.decode("latin-1", errors="replace")
        content_length = 0
        for h in headers_text.split("\r\n"):
            if h.lower().startswith("content-length:"):
                content_length = int(h.split(":", 1)[1].strip())

        body = b""
        if content_length > 0:
            body = await asyncio.wait_for(
                client_reader.readexactly(content_length), timeout=30
            )

        # Connect to target
        try:
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=CONNECT_TIMEOUT,
            )
        except Exception as exc:
            client_writer.write(f"HTTP/1.1 502 Bad Gateway\r\n\r\n{exc}\r\n".encode())
            await client_writer.drain()
            return

        # Forward the request (rewrite URL to relative path)
        forward_line = f"{method} {path} {version}\r\n".encode()
        remote_writer.write(forward_line)
        remote_writer.write(headers_raw)
        if body:
            remote_writer.write(body)
        await remote_writer.drain()

        # Relay the response back
        try:
            while True:
                data = await asyncio.wait_for(remote_reader.read(BUFFER_SIZE), timeout=60)
                if not data:
                    break
                self._bytes_forwarded += len(data)
                client_writer.write(data)
                await client_writer.drain()
        except (asyncio.TimeoutError, ConnectionResetError):
            pass
        finally:
            try:
                remote_writer.close()
                await remote_writer.wait_closed()
            except Exception:
                pass

    async def _tunnel(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        remote_reader: asyncio.StreamReader,
        remote_writer: asyncio.StreamWriter,
    ) -> None:
        """Bidirectional tunnel between client and remote."""

        async def _pipe(
            src: asyncio.StreamReader, dst: asyncio.StreamWriter
        ) -> None:
            try:
                while True:
                    data = await src.read(BUFFER_SIZE)
                    if not data:
                        break
                    self._bytes_forwarded += len(data)
                    dst.write(data)
                    await dst.drain()
            except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                pass
            except Exception:
                # Catch-all so a _pipe task exception is always retrieved
                # (an unretrieved task exception logs a scary asyncio warning
                # at GC time with no context). Peer info makes it debuggable.
                try:
                    peer = dst.get_extra_info("peername")
                except Exception:
                    peer = None
                logger.debug("Proxy _pipe failed (peer=%s)", peer, exc_info=True)

        task1 = asyncio.create_task(_pipe(client_reader, remote_writer))
        task2 = asyncio.create_task(_pipe(remote_reader, client_writer))

        try:
            await asyncio.wait(
                [task1, task2], return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            task1.cancel()
            task2.cancel()
            try:
                remote_writer.close()
                await remote_writer.wait_closed()
            except Exception:
                pass

    # ── helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _find_available_port() -> int:
        """Find an available port starting from DEFAULT_PROXY_PORT."""
        for offset in range(MAX_PORT_SCAN):
            candidate = DEFAULT_PROXY_PORT + offset
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.1)
                try:
                    s.bind(("127.0.0.1", candidate))
                    return candidate
                except OSError:
                    continue
        raise RuntimeError(
            f"No available proxy port in range "
            f"{DEFAULT_PROXY_PORT}-{DEFAULT_PROXY_PORT + MAX_PORT_SCAN - 1}"
        )


# ── Module-level singleton ──────────────────────────────────────────────────

_proxy_server: Optional[ProxyServer] = None


def get_proxy_server() -> ProxyServer:
    """Get or create the singleton proxy server instance."""
    global _proxy_server
    if _proxy_server is None:
        _proxy_server = ProxyServer()
    return _proxy_server
