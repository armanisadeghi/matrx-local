"""BookCapture — read a book you own off the screen and file it as one Source.

Two operating-system grants stand between this tool and the screen: Screen
Recording, to see the reader's window at all, and Accessibility, to send the
page-turn key.  Both are checked BEFORE anything is driven, and a missing one
comes back as a named requirement the app can act on — never a failed run
with a puzzled sentence (rule 4: nothing fails silently).
"""

from __future__ import annotations

import logging
import uuid

from app.common.platform_ctx import PLATFORM
from app.services.action_needed import os_permission_needed
from app.services.book_capture import (
    CaptureSettings,
    CropInsets,
    assemble,
    build_driver,
    capture_book,
    supported_keys,
)
from app.services.book_capture.drivers import ReaderUnavailable
from app.services.book_capture.handoff import publish_book
from app.services.paths import safe_dir
from app.tools.session import ToolSession
from app.tools.types import ToolResult, ToolResultType

logger = logging.getLogger(__name__)

_FEATURE = "Book capture"
_SOURCE = "tool.book_capture"


async def _permission_block(check, permission_key: str, why: str) -> ToolResult | None:
    """Return a refusal naming the grant, or ``None`` when it is granted."""
    from app.services.permissions.checker import PermissionStatus

    permission = await check()
    if permission.status == PermissionStatus.GRANTED:
        return None
    return ToolResult(
        type=ToolResultType.ERROR,
        output=(
            f"Book capture cannot start: {why} "
            + (permission.user_details or permission.details or "")
        ).strip(),
        action_needed=os_permission_needed(
            feature=_FEATURE,
            permission_key=permission_key,
            source=_SOURCE,
            message=why,
        ),
    )


async def tool_book_capture(
    session: ToolSession,
    app_name: str,
    max_pages: int = 400,
    next_page_key: str = "right",
    page_delay_seconds: float = 0.9,
    end_repeat_threshold: int = 3,
    page_change_threshold: float = 0.001,
    crop_top: float = 0.0,
    crop_bottom: float = 0.0,
    crop_left: float = 0.0,
    crop_right: float = 0.0,
    ocr: bool = True,
    window_title: str | None = None,
    send_to_library: bool = True,
) -> ToolResult:
    """Page through an open e-book on this Mac, capture every page, and file it.

    Focuses the named reader, captures its window, sends the page-turn key,
    and stops when the page stops changing. The pages are assembled into one
    searchable PDF and sent to the user's AI Matrx library as a single Source.
    """
    # Rule 5: platform gating in _META advertises; the handler enforces.
    if not PLATFORM["is_mac"]:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=(
                "Book capture drives a reader window with macOS screen capture "
                "and is only available on macOS."
            ),
        )

    from app.services.permissions.checker import (
        check_accessibility,
        check_screen_recording,
    )

    blocked = await _permission_block(
        check_screen_recording,
        "screen_recording",
        "capturing the reader's window needs Screen Recording access.",
    )
    if blocked is not None:
        return blocked

    blocked = await _permission_block(
        check_accessibility,
        "accessibility",
        "turning the pages needs Accessibility access.",
    )
    if blocked is not None:
        return blocked

    try:
        settings = CaptureSettings(
            app_name=app_name,
            max_pages=max_pages,
            next_page_key=next_page_key,
            page_delay_seconds=page_delay_seconds,
            end_repeat_threshold=end_repeat_threshold,
            page_change_threshold=page_change_threshold,
            crop=CropInsets(
                top=crop_top, bottom=crop_bottom, left=crop_left, right=crop_right
            ),
            ocr=ocr,
        )
    except ValueError as exc:
        return ToolResult(type=ToolResultType.ERROR, output=str(exc))

    if next_page_key not in supported_keys():
        return ToolResult(
            type=ToolResultType.ERROR,
            output=(
                f"“{next_page_key}” is not a page-turn key this tool knows. "
                f"Use one of: {', '.join(supported_keys())}."
            ),
        )

    capture_id = uuid.uuid4().hex[:12]
    output_dir = safe_dir("data") / "book-capture" / capture_id

    try:
        driver = build_driver(app_name, window_title=window_title)
        run = await capture_book(driver, settings, output_dir)
    except ReaderUnavailable as exc:
        return ToolResult(type=ToolResultType.ERROR, output=str(exc))

    if not run.pages:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=f"No pages were captured. {run.outcome.detail}",
        )

    book = assemble(run, output_dir, source_name=app_name, when=run.started_at)

    lines = [
        f"Captured {book.page_count} page(s) from {app_name}.",
        run.outcome.detail,
    ]
    if not book.searchable:
        lines.append(book.ocr_unavailable_reason or "")

    metadata: dict = {
        "capture_id": capture_id,
        "page_count": book.page_count,
        "stopped_because": run.outcome.reason,
        "searchable": book.searchable,
        "pdf_path": str(book.pdf_path),
        "frames_captured": run.frames_captured,
        "frames_deduped": run.frames_deduped,
        "provenance": book.provenance,
    }
    if not book.searchable:
        metadata["ocr_unavailable_reason"] = book.ocr_unavailable_reason

    if send_to_library:
        try:
            published = await publish_book(
                book,
                run,
                jwt=session.cloud_access_token,
                capture_id=capture_id,
                request_id=session.cloud_call_id,
            )
        except Exception as exc:
            # The capture succeeded; only the handoff failed. Saying so, with
            # the file still on disk, beats discarding a finished book.
            logger.warning("[book_capture] handoff failed for %s: %s", capture_id, exc)
            metadata["handoff_error"] = str(exc)
            lines.append(
                f"The pages are saved at {book.pdf_path}, but sending them to "
                f"your library failed: {exc}"
            )
        else:
            metadata["file_id"] = published.file_id
            metadata["file_path"] = published.file_path
            lines.append(
                f"Filed in your library as one Source ({book.provenance})."
            )
    else:
        lines.append(f"Saved to {book.pdf_path}; not sent to your library.")

    return ToolResult(
        output="\n".join(line for line in lines if line),
        metadata=metadata,
    )
