"""Value types for a book-capture run.

Every behavioural choice a run makes is a field on :class:`CaptureSettings`
with a named default — never a constant buried in the loop (the
limits-are-knobs law).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .signature import DEFAULT_PAGE_CHANGE_THRESHOLD, PageSignature


@dataclass(frozen=True)
class CropInsets:
    """Fractions of the captured frame to discard on each edge.

    Fractions, not pixels: a reader window captured on a Retina display yields
    twice the pixels of the same window on an external monitor, so a pixel
    inset that trims the toolbar on one screen eats the text on the other.
    """

    top: float = 0.0
    bottom: float = 0.0
    left: float = 0.0
    right: float = 0.0

    def __post_init__(self) -> None:
        for name in ("top", "bottom", "left", "right"):
            value = getattr(self, name)
            if not 0.0 <= value < 0.5:
                raise ValueError(
                    f"Crop inset {name}={value} is out of range: each inset is a "
                    "fraction of the frame between 0.0 and 0.5."
                )
        if self.top + self.bottom > 0.8 or self.left + self.right > 0.8:
            raise ValueError(
                "Crop insets would leave less than a fifth of the frame — that "
                "is a sliver, not a book page."
            )

    @property
    def is_noop(self) -> bool:
        return not (self.top or self.bottom or self.left or self.right)


@dataclass(frozen=True)
class CaptureSettings:
    """Every knob of one capture run."""

    #: Reader application to drive, e.g. "Books", "Kindle", "Preview".
    app_name: str
    #: Hard ceiling on captured frames. Always finite: an unattended loop that
    #: cannot end is a defect, not a feature.
    max_pages: int = 400
    #: Key sent to turn the page. Any name in the driver's key table.
    next_page_key: str = "right"
    #: Seconds to wait after the key before capturing, so the reader can
    #: finish its page-turn animation and re-render text.
    page_delay_seconds: float = 0.9
    #: Consecutive unchanged frames that mean "the reader stopped advancing".
    #: Two can fire on one slow render; three is the honest end-of-book signal.
    end_repeat_threshold: int = 3
    #: Fraction of the frame that must change to count as a new page.
    #: Why this and not a bit-distance: ``signature`` module header.
    page_change_threshold: float = DEFAULT_PAGE_CHANGE_THRESHOLD
    crop: CropInsets = field(default_factory=CropInsets)
    #: Add a searchable text layer when an OCR engine is present.
    ocr: bool = True

    def __post_init__(self) -> None:
        if not self.app_name.strip():
            raise ValueError("app_name is required — name the reader to capture.")
        if self.max_pages < 1:
            raise ValueError("max_pages must be at least 1.")
        if self.end_repeat_threshold < 2:
            raise ValueError(
                "end_repeat_threshold must be at least 2 — a single repeated "
                "frame is a slow render, not the end of the book."
            )
        if self.page_delay_seconds < 0:
            raise ValueError("page_delay_seconds cannot be negative.")
        if not 0.0 < self.page_change_threshold < 1.0:
            raise ValueError(
                "page_change_threshold is a fraction of the page between 0 and 1."
            )


@dataclass
class CapturedPage:
    """One kept frame."""

    index: int
    path: Path
    signature: PageSignature
    width: int
    height: int


@dataclass
class CaptureOutcome:
    """Why the loop stopped. Never silent, never guessed."""

    #: "end_of_book" | "max_pages" | "capture_failed"
    reason: str
    detail: str


@dataclass
class CaptureRun:
    """The full result of a run, before assembly."""

    settings: CaptureSettings
    pages: list[CapturedPage]
    outcome: CaptureOutcome
    frames_captured: int
    frames_deduped: int
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def page_count(self) -> int:
        return len(self.pages)


@dataclass
class AssembledBook:
    """What the run hands to the platform."""

    pdf_path: Path
    image_paths: list[Path]
    page_count: int
    #: True when a real OCR text layer was written into the PDF.
    searchable: bool
    #: Present whenever ``searchable`` is False: why, and what to do about it.
    ocr_unavailable_reason: str | None
    provenance: str
