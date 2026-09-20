"""The capture loop: focus, grab, compare, turn, stop.

The loop owns exactly three judgements — is this frame a new page, has the
book ended, and when must it give up — and it makes all three from frame
content, never from a page count the caller guessed.
"""

from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path

from PIL import Image

from .drivers import CaptureFailed, ReaderDriver, ReaderUnavailable
from .models import (
    CaptureOutcome,
    CaptureRun,
    CaptureSettings,
    CapturedPage,
    CropInsets,
)
from .signature import PageSignature, changed_fraction, signature

logger = logging.getLogger(__name__)


def crop_frame(image: Image.Image, insets: CropInsets) -> Image.Image:
    """Trim the reader's chrome off a frame.

    Insets are fractions, so the same settings hold on a Retina panel and an
    external display.
    """
    if insets.is_noop:
        return image
    width, height = image.size
    box = (
        int(round(width * insets.left)),
        int(round(height * insets.top)),
        width - int(round(width * insets.right)),
        height - int(round(height * insets.bottom)),
    )
    if box[2] - box[0] < 1 or box[3] - box[1] < 1:
        raise ValueError(
            f"Crop insets leave nothing of a {width}x{height} frame. "
            "Lower the insets and try again."
        )
    return image.crop(box)


async def capture_book(
    driver: ReaderDriver,
    settings: CaptureSettings,
    output_dir: Path,
    *,
    sleep=asyncio.sleep,
) -> CaptureRun:
    """Drive ``driver`` until the book ends, the ceiling is hit, or it breaks.

    ``sleep`` is injected so a test can run the loop's real timing logic
    without spending the wall-clock seconds a reader needs.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    pages: list[CapturedPage] = []
    frames_captured = 0
    frames_deduped = 0
    repeats = 0
    largest_repeat_change = 0.0
    previous_signature: PageSignature | None = None
    outcome: CaptureOutcome | None = None

    await driver.focus()

    while frames_captured < settings.max_pages:
        try:
            raw = await driver.capture()
            frame = crop_frame(Image.open(io.BytesIO(raw)), settings.crop)
        except (CaptureFailed, ReaderUnavailable, OSError, ValueError) as exc:
            outcome = CaptureOutcome(
                reason="capture_failed",
                detail=(
                    f"Capture stopped after {len(pages)} page(s): {exc}"
                ),
            )
            break

        frames_captured += 1
        frame_signature = signature(frame)

        moved = (
            1.0
            if previous_signature is None
            else changed_fraction(frame_signature, previous_signature)
        )

        if moved < settings.page_change_threshold:
            # The reader did not advance. Either it is still animating or the
            # book is over; the repeat count is what tells them apart.
            frames_deduped += 1
            repeats += 1
            largest_repeat_change = max(largest_repeat_change, moved)
            if repeats >= settings.end_repeat_threshold:
                outcome = CaptureOutcome(
                    reason="end_of_book",
                    detail=(
                        f"The page stopped changing after {len(pages)} page(s) — "
                        f"{repeats} frames in a row moved at most "
                        f"{largest_repeat_change:.4%} of the page, under the "
                        f"{settings.page_change_threshold:.4%} that counts as a "
                        "page turn. If the book was longer than this, lower "
                        "page_change_threshold and run it again."
                    ),
                )
                break
        else:
            repeats = 0
            largest_repeat_change = 0.0
            previous_signature = frame_signature
            page_path = output_dir / f"page-{len(pages) + 1:04d}.png"
            frame.save(page_path, format="PNG")
            pages.append(
                CapturedPage(
                    index=len(pages) + 1,
                    path=page_path,
                    signature=frame_signature,
                    width=frame.width,
                    height=frame.height,
                )
            )

        try:
            await driver.send_next_page(settings.next_page_key)
        except ReaderUnavailable as exc:
            outcome = CaptureOutcome(
                reason="capture_failed",
                detail=f"Page turn failed after {len(pages)} page(s): {exc}",
            )
            break
        if settings.page_delay_seconds:
            await sleep(settings.page_delay_seconds)

    if outcome is None:
        outcome = CaptureOutcome(
            reason="max_pages",
            detail=(
                f"Stopped at the {settings.max_pages}-page ceiling before the book "
                "ended. Raise max_pages and run again to capture the rest."
            ),
        )

    return CaptureRun(
        settings=settings,
        pages=pages,
        outcome=outcome,
        frames_captured=frames_captured,
        frames_deduped=frames_deduped,
    )
