"""Book capture — read a book you own off the screen, page by page.

The mechanism is four separable parts: a :mod:`drivers` seam that is the only
thing allowed to touch the screen, a :mod:`signature` detector that decides
whether the reader actually advanced, a :mod:`session` loop that turns those
two into pages, and :mod:`assembly`, which produces one PDF.  Nothing here
knows about the tool surface, so the same mechanism serves any caller.
"""

from .assembly import assemble, build_pdf, ocr_unavailable_reason, provenance_line
from .drivers import (
    CaptureFailed,
    MacReaderDriver,
    ReaderDriver,
    ReaderUnavailable,
    SyntheticReaderDriver,
    build_driver,
    supported_keys,
)
from .models import (
    AssembledBook,
    CaptureOutcome,
    CaptureRun,
    CaptureSettings,
    CapturedPage,
    CropInsets,
)
from .session import capture_book, crop_frame
from .signature import (
    DEFAULT_PAGE_CHANGE_THRESHOLD,
    PageSignature,
    changed_fraction,
    same_page,
    signature,
)

__all__ = [
    "AssembledBook",
    "CaptureFailed",
    "CaptureOutcome",
    "CaptureRun",
    "CaptureSettings",
    "CapturedPage",
    "CropInsets",
    "DEFAULT_PAGE_CHANGE_THRESHOLD",
    "MacReaderDriver",
    "PageSignature",
    "ReaderDriver",
    "ReaderUnavailable",
    "SyntheticReaderDriver",
    "assemble",
    "build_driver",
    "build_pdf",
    "capture_book",
    "changed_fraction",
    "crop_frame",
    "ocr_unavailable_reason",
    "provenance_line",
    "same_page",
    "signature",
    "supported_keys",
]
