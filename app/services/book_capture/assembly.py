"""Turn kept frames into one PDF the platform can ingest.

A searchable PDF when an OCR engine is present; an image-only PDF, saying so
in words, when it is not.  Never a silently text-less file that looks
searchable until someone searches it (the nothing-fails-silently law).
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .models import AssembledBook, CaptureRun

logger = logging.getLogger(__name__)

_NO_ENGINE = (
    "No OCR engine is installed, so the pages are images without searchable "
    "text. Install Tesseract to get a text layer; the page images are complete "
    "either way."
)


def ocr_unavailable_reason() -> str | None:
    """Return why OCR cannot run, or ``None`` when it can."""
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return _NO_ENGINE
    if shutil.which("tesseract") is None:
        return _NO_ENGINE
    return None


def _page_pdf_with_text(image_path: Path) -> bytes:
    """One searchable single-page PDF for ``image_path``."""
    import pytesseract
    from PIL import Image

    with Image.open(image_path) as image:
        return pytesseract.image_to_pdf_or_hocr(image, extension="pdf")


def build_pdf(
    image_paths: list[Path],
    destination: Path,
    *,
    ocr: bool = True,
) -> tuple[bool, str | None]:
    """Write ``image_paths`` into one PDF at ``destination``.

    Returns ``(searchable, reason_it_is_not)``.
    """
    import fitz  # PyMuPDF

    if not image_paths:
        raise ValueError("There are no captured pages to assemble.")

    reason = None if ocr else "OCR was turned off for this capture."
    if ocr:
        reason = ocr_unavailable_reason()

    destination.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    searchable = reason is None
    try:
        if searchable:
            try:
                for path in image_paths:
                    with fitz.open("pdf", _page_pdf_with_text(path)) as page_pdf:
                        doc.insert_pdf(page_pdf)
            except Exception as exc:  # engine present but unusable on this file
                logger.warning("book_capture: OCR failed, falling back: %s", exc)
                searchable = False
                reason = (
                    f"The OCR engine failed on these pages ({exc}), so the PDF "
                    "holds the page images without searchable text."
                )
                doc.close()
                doc = fitz.open()

        if not searchable:
            for path in image_paths:
                rect = fitz.Pixmap(str(path))
                page = doc.new_page(width=rect.width, height=rect.height)
                page.insert_image(page.rect, filename=str(path))

        doc.save(str(destination), garbage=3, deflate=True)
    finally:
        doc.close()

    return searchable, reason


def provenance_line(run: CaptureRun, *, source_name: str, when: datetime | None = None) -> str:
    """The sentence that travels with the Source so its origin is never lost."""
    stamp = (when or datetime.now(timezone.utc)).date().isoformat()
    return (
        f"Captured from {source_name}, {run.page_count} pages, {stamp} "
        f"({run.outcome.reason.replace('_', ' ')})"
    )


def assemble(
    run: CaptureRun,
    output_dir: Path,
    *,
    source_name: str,
    when: datetime | None = None,
) -> AssembledBook:
    """Assemble a finished run into the artefact handed to the platform."""
    image_paths = [page.path for page in run.pages]
    pdf_path = output_dir / "book.pdf"
    searchable, reason = build_pdf(image_paths, pdf_path, ocr=run.settings.ocr)
    return AssembledBook(
        pdf_path=pdf_path,
        image_paths=image_paths,
        page_count=len(image_paths),
        searchable=searchable,
        ocr_unavailable_reason=reason,
        provenance=provenance_line(run, source_name=source_name, when=when),
    )
