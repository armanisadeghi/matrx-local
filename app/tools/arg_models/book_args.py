from __future__ import annotations

from pydantic import BaseModel, Field


class BookCaptureArgs(BaseModel):
    app_name: str = Field(
        description=(
            "The reader application holding the open book, e.g. 'Books', "
            "'Kindle', 'Preview'. The book must already be open on screen."
        ),
    )
    max_pages: int = Field(
        default=400,
        ge=1,
        le=5000,
        description=(
            "Hard ceiling on captured pages. The capture normally stops on its "
            "own when the page stops changing; this is the safety stop."
        ),
    )
    next_page_key: str = Field(
        default="right",
        description=(
            "Key that turns the page in this reader: right, left, down, up, "
            "space, page_down, page_up or return."
        ),
    )
    page_delay_seconds: float = Field(
        default=0.9,
        ge=0.0,
        le=30.0,
        description=(
            "Seconds to wait after each page turn before capturing, so the "
            "reader finishes animating and re-renders the text."
        ),
    )
    end_repeat_threshold: int = Field(
        default=3,
        ge=2,
        le=20,
        description=(
            "How many unchanged frames in a row mean the book has ended. Two "
            "can fire on one slow render, so three is the safe floor."
        ),
    )
    page_change_threshold: float = Field(
        default=0.001,
        gt=0.0,
        lt=1.0,
        description=(
            "Fraction of the page that must change to count as a new page. "
            "Lower it for books whose pages differ by only a few words, such "
            "as an index or a page of verse."
        ),
    )
    crop_top: float = Field(
        default=0.0, ge=0.0, lt=0.5,
        description="Fraction of the window height to trim off the top, to remove the reader's toolbar.",
    )
    crop_bottom: float = Field(
        default=0.0, ge=0.0, lt=0.5,
        description="Fraction of the window height to trim off the bottom, to remove the page slider.",
    )
    crop_left: float = Field(
        default=0.0, ge=0.0, lt=0.5,
        description="Fraction of the window width to trim off the left edge.",
    )
    crop_right: float = Field(
        default=0.0, ge=0.0, lt=0.5,
        description="Fraction of the window width to trim off the right edge.",
    )
    ocr: bool = Field(
        default=True,
        description=(
            "Add a searchable text layer when an OCR engine is available. The "
            "result always states whether the text layer was actually written."
        ),
    )
    window_title: str | None = Field(
        default=None,
        description=(
            "Optional text from the reader window's title, when the app has "
            "more than one window open and the largest is not the book."
        ),
    )
    send_to_library: bool = Field(
        default=True,
        description=(
            "Upload the assembled PDF to the user's AI Matrx library as one "
            "Source. When false the PDF is only written to this Mac."
        ),
    )
