"""Browser-runtime status and the one-click install that fixes it.

GET  /browser-runtime/status   → is browser rendering available, and if not, why
POST /browser-runtime/install  → SSE stream that downloads Chromium and brings
                                 the RUNNING engine's browser pool up

The install reuses ``setup_routes._install_playwright_browsers`` — there is one
installer in this repo and this is not a second one. What this adds is the two
things the first-run wizard cannot do for an app that is already running:
install into THIS world's browsers path (``MATRX_HOME_DIR`` — Hard Rule 9), and
restart the scraper's browser pool afterwards so the user does not have to
relaunch the app.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.common.system_logger import get_logger
from app.services.scraper import browser_runtime

logger = get_logger()
router = APIRouter(prefix="/browser-runtime", tags=["browser-runtime"])

# One install at a time. A second concurrent `playwright install` into the same
# directory races on the same extraction target.
_install_lock = asyncio.Lock()


@router.get("/status")
async def browser_runtime_status() -> dict[str, object]:
    """Freshly-probed browser availability for this world."""
    status = browser_runtime.status()
    item = browser_runtime.browser_action_needed()
    return {
        **status.to_dict(),
        "action_needed": item.model_dump(mode="json", exclude_none=True) if item else None,
    }


@router.post("/install")
async def install_browser_runtime() -> StreamingResponse:
    """Download the browser, then restart the pool — progress streamed as SSE."""

    async def _stream():
        from app.api.setup_routes import _install_playwright_browsers, _sse_event

        if _install_lock.locked() or browser_runtime.install_in_progress():
            yield await _sse_event(
                "progress",
                {
                    "component": "browser_engine",
                    "status": "installing",
                    "message": "A browser install is already running.",
                    "percent": browser_runtime.status().install_percent or 0,
                },
            )
            yield await _sse_event("complete", {"message": "Install already in progress"})
            return

        async with _install_lock:
            browser_runtime.install_started()
            await browser_runtime.publish_action_needed()
            failed = False
            try:
                path = str(browser_runtime.browsers_path())
                logger.info(
                    "[browser_runtime_routes] Installing Playwright browser into %s", path
                )
                # The headless shell is exactly what the scrape lane launches
                # (chrome-headless-shell) — a fraction of the full Chromium
                # download the first-run wizard fetches.
                async for event in _install_playwright_browsers(
                    path, browser=browser_runtime.INSTALL_BROWSER
                ):
                    # Mirror the installer's own progress into the shared state so
                    # every surface (not just the tab that clicked Install) can
                    # show it.
                    _percent, _message, _status = _parse_progress(event)
                    browser_runtime.install_progress(_percent, _message)
                    if _status == "error":
                        failed = True
                    yield event
            finally:
                browser_runtime.install_finished()

            if failed or not browser_runtime.browser_binary_present():
                await browser_runtime.publish_action_needed()
                yield await _sse_event(
                    "complete",
                    {"message": "Browser install did not complete", "installed": False},
                )
                return

            # The binary is on disk. Bring the pool up in the running engine so
            # the very next scrape can use it — no app restart.
            from app.services.scraper.engine import get_scraper_engine

            engine = get_scraper_engine()
            started = False
            if engine.is_ready:
                yield await _sse_event(
                    "progress",
                    {
                        "component": "browser_engine",
                        "status": "installing",
                        "message": "Starting the browser…",
                        "percent": 98,
                    },
                )
                started = await engine.ensure_browser_pool()
            else:
                # No running engine to attach to; the binary is still installed
                # and the next engine start will pick it up.
                started = browser_runtime.browser_binary_present()

            status = browser_runtime.sync_service_registry()
            await browser_runtime.publish_action_needed()

            yield await _sse_event(
                "progress",
                {
                    "component": "browser_engine",
                    "status": "ready" if started else "error",
                    "message": (
                        "Browser ready — pages that need JavaScript will now load."
                        if started
                        else "Browser installed, but it would not start. Restart the app and try again."
                    ),
                    "percent": 100,
                },
            )
            yield await _sse_event(
                "complete",
                {
                    "message": "Browser install finished",
                    "installed": status.available,
                },
            )

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _parse_progress(event: str) -> tuple[int | None, str | None, str | None]:
    """Pull (percent, message, status) out of one already-formatted SSE chunk."""
    import json

    for line in event.splitlines():
        if line.startswith("data: "):
            try:
                data = json.loads(line[6:])
            except ValueError:
                return None, None, None
            percent = data.get("percent")
            return (
                percent if isinstance(percent, int) else None,
                data.get("message"),
                data.get("status"),
            )
    return None, None, None
