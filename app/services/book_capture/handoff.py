"""Hand a captured book to the platform as ONE Source.

The platform's own-files lane treats one document as one Source, so the
assembled PDF — not the loose page images — is what crosses.  That is also
why :mod:`assembly` exists at all: uploading 300 PNGs would produce 300
Sources and no book.

Uploads go through the one canonical primitive every other local feature
uses, :class:`MatrxFilesClient`, which resolves the active organization
itself and refuses rather than guessing one.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from app.services.file_sync.client import MatrxFilesClient

from .models import AssembledBook, CaptureRun

logger = logging.getLogger(__name__)


@dataclass
class PublishedBook:
    """What the platform gave back for the uploaded book."""

    file_id: str
    file_path: str
    size_bytes: int
    url: str | None


async def publish_book(
    book: AssembledBook,
    run: CaptureRun,
    *,
    jwt: str | None,
    capture_id: str,
    client: MatrxFilesClient | None = None,
    request_id: str | None = None,
) -> PublishedBook:
    """Upload the assembled PDF and return its platform identity."""
    if not jwt:
        raise RuntimeError(
            "Sending a captured book to your library needs you to be signed in "
            "to AI Matrx on this Mac."
        )

    content = book.pdf_path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    file_path = f"system-files/book-capture/{capture_id}.pdf"

    files = client or MatrxFilesClient()
    files.set_jwt(jwt)
    response = await files.upload(
        file_path=file_path,
        content=content,
        filename=f"{run.settings.app_name} capture {capture_id}.pdf",
        mime_type="application/pdf",
        visibility="private",
        metadata={
            "kind": "book_capture",
            "capture_id": capture_id,
            "provenance": book.provenance,
            "captured_from": run.settings.app_name,
            "page_count": book.page_count,
            "captured_at": run.started_at.isoformat(),
            "stopped_because": run.outcome.reason,
            "searchable": book.searchable,
            "ocr_unavailable_reason": book.ocr_unavailable_reason,
            "checksum": checksum,
        },
        request_id=request_id,
        # The placement is the point: two captures of the same book are two
        # captures, and aliasing the second onto the first would record this
        # run nowhere.
        intent="force_new_copy",
        reason="book capture",
        idempotency_key=f"book-capture:{capture_id}",
    )

    file_id = response.get("file_id")
    if not file_id:
        raise RuntimeError("matrx-files upload returned no file_id")
    return PublishedBook(
        file_id=str(file_id),
        file_path=file_path,
        size_bytes=int(response.get("size_bytes") or len(content)),
        url=response.get("url") or response.get("download_url"),
    )
