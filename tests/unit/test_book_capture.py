"""Guards for book capture, driven entirely by synthetic frames.

Not one test may focus, capture or keystroke a real window: the owner's screen
is his (Arman, 2026-09-17, on agents stealing his focus). The fixtures below
render genuine multi-page PDFs to images and feed them to
``SyntheticReaderDriver``, so the loop under test is the shipping loop.
"""

from __future__ import annotations

import asyncio
import io
import random

import pytest
from PIL import Image, ImageDraw, ImageEnhance

from app.services import book_capture as bc
from app.services.book_capture import assembly as assembly_mod
from app.services.book_capture.drivers import ReaderUnavailable

_WORDS = (
    "the quick brown fox jumps over a lazy dog while morning light fell across "
    "the harbour and nobody spoke of what had happened between them that winter "
    "in the long cold house by the river"
).split()


# ── fixtures: real books, rendered to real frames ────────────────────────


def _prose(page_number: int) -> str:
    rng = random.Random(page_number * 13)
    return " ".join(rng.choice(_WORDS) for _ in range(190))


def _prose_book(page_count: int) -> bytes:
    """An ordinary book: every page a different block of text."""
    import fitz

    doc = fitz.open()
    for number in range(1, page_count + 1):
        page = doc.new_page(width=612, height=792)
        page.insert_textbox(
            fitz.Rect(72, 90, 540, 720), _prose(number), fontsize=11, fontname="helv"
        )
        page.insert_text((300, 750), str(number), fontsize=9, fontname="helv")
    data = doc.tobytes()
    doc.close()
    return data


def _sparse_book(page_count: int) -> bytes:
    """The adversarial book: pages that differ by only a few glyphs.

    An index, a page of verse, a table of contents. If the detector needs a
    whole paragraph to change, this book ends three pages early.
    """
    import fitz

    doc = fitz.open()
    for number in range(1, page_count + 1):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 140), f"Chapter {number}", fontsize=34, fontname="helv")
        page.insert_text(
            (72, 220),
            f"This is page {number} of the captured book. Marker ZQX{number:03d}",
            fontsize=18,
            fontname="helv",
        )
        page.insert_text((300, 740), str(number), fontsize=12, fontname="helv")
    data = doc.tobytes()
    doc.close()
    return data


def _render(pdf_bytes: bytes, *, chrome: bool = False) -> list[bytes]:
    import fitz

    frames: list[bytes] = []
    doc = fitz.open("pdf", pdf_bytes)
    try:
        for index, page in enumerate(doc, start=1):
            image = Image.open(
                io.BytesIO(page.get_pixmap(dpi=110).tobytes("png"))
            ).convert("RGB")
            if chrome:
                image = _add_chrome(image, index, doc.page_count)
            frames.append(_png(image))
    finally:
        doc.close()
    return frames


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _add_chrome(page: Image.Image, index: int, total: int) -> Image.Image:
    """Wrap a page in a toolbar and a progress bar that move on every page.

    This is what a real reader looks like, and it is why cropping has to
    happen before the frames are compared.
    """
    canvas = Image.new("RGB", (page.width, page.height + CHROME_BAR * 2), (28, 28, 32))
    canvas.paste(page, (0, CHROME_BAR))
    draw = ImageDraw.Draw(canvas)
    draw.text((14, 30), f"My Library — page {index} of {total}", fill=(235, 235, 235))
    _draw_scrubber(canvas, index / total)
    return canvas


#: Height of the simulated reader's toolbar and footer, in frame pixels.
CHROME_BAR = 80


def _draw_scrubber(canvas: Image.Image, position: float) -> None:
    """The footer page-slider: a track with a thumb that slides along it."""
    draw = ImageDraw.Draw(canvas)
    top = canvas.height - CHROME_BAR + 20
    draw.rectangle([(14, top + 16), (canvas.width - 14, top + 24)], fill=(70, 70, 78))
    left = 14 + int((canvas.width - 88) * position)
    draw.rectangle([(left, top), (left + 60, top + 40)], fill=(90, 160, 240))


@pytest.fixture
def prose_frames() -> list[bytes]:
    return _render(_prose_book(6))


@pytest.fixture
def sparse_frames() -> list[bytes]:
    return _render(_sparse_book(6))


@pytest.fixture
def chrome_frames() -> list[bytes]:
    return _render(_prose_book(6), chrome=True)


def _settings(**overrides) -> bc.CaptureSettings:
    base = {
        "app_name": "Synthetic Reader",
        "max_pages": 50,
        "page_delay_seconds": 0.0,
    }
    base.update(overrides)
    return bc.CaptureSettings(**base)


async def _noop_sleep(_seconds: float) -> None:
    return None


def _run(frames, settings, out_dir, driver_cls=bc.SyntheticReaderDriver):
    return asyncio.run(
        bc.capture_book(driver_cls(frames), settings, out_dir, sleep=_noop_sleep)
    )


def _sig(frame: bytes) -> bc.PageSignature:
    return bc.signature(Image.open(io.BytesIO(frame)))


# ── GUARD 0 — the detector separates pages from noise ────────────────────


def test_the_detector_separates_a_page_turn_from_screen_noise(prose_frames, sparse_frames):
    """The measured margins the default threshold is set from.

    This is the guard that fails if anyone swaps the signature back to a
    64-bit dHash: under dHash consecutive prose pages measured 0-2 bits of 64
    apart while a brightness shift on one page moved up to 6 — the noise sat
    above the signal, and the loop declared every book one page long.
    """
    threshold = bc.DEFAULT_PAGE_CHANGE_THRESHOLD

    prose = [_sig(f) for f in prose_frames]
    prose_turns = [bc.changed_fraction(a, b) for a, b in zip(prose, prose[1:])]
    assert min(prose_turns) > threshold * 10, prose_turns

    sparse = [_sig(f) for f in sparse_frames]
    sparse_turns = [bc.changed_fraction(a, b) for a, b in zip(sparse, sparse[1:])]
    assert min(sparse_turns) > threshold, (
        f"a page that differs by a few glyphs must still register: {sparse_turns}"
    )

    page = Image.open(io.BytesIO(prose_frames[0]))
    identical = bc.changed_fraction(bc.signature(page), bc.signature(page))
    brighter = bc.changed_fraction(
        bc.signature(page), bc.signature(ImageEnhance.Brightness(page).enhance(1.02))
    )
    blinking = page.copy()
    ImageDraw.Draw(blinking).rectangle([(300, 400), (303, 418)], fill=(0, 0, 0))
    cursor = bc.changed_fraction(bc.signature(page), bc.signature(blinking))

    assert identical == 0.0
    assert brighter < threshold
    assert cursor < threshold, f"a blinking cursor must not look like a page turn: {cursor}"


# ── GUARD 1 — end-of-book detection ──────────────────────────────────────


def test_capture_stops_at_the_end_of_the_book(tmp_path, prose_frames):
    """The loop stops because the page stopped changing, not because it was told."""
    run = _run(prose_frames, _settings(), tmp_path)

    assert run.outcome.reason == "end_of_book", run.outcome.detail
    assert run.page_count == 6
    assert "stopped changing" in run.outcome.detail
    assert run.frames_captured == 6 + run.settings.end_repeat_threshold
    assert [p.index for p in run.pages] == [1, 2, 3, 4, 5, 6]


def test_a_sparse_book_is_not_cut_short(tmp_path, sparse_frames):
    """Pages that differ by a few glyphs are still pages."""
    run = _run(sparse_frames, _settings(), tmp_path)

    assert run.outcome.reason == "end_of_book"
    assert run.page_count == 6, run.outcome.detail


def test_end_detection_needs_more_than_one_slow_render(tmp_path, prose_frames):
    """A single repeated frame is a slow page turn, never the end of the book."""
    frames = prose_frames[:3] + [prose_frames[2]] + prose_frames[3:]
    run = _run(frames, _settings(), tmp_path)

    assert run.outcome.reason == "end_of_book"
    assert run.page_count == 6


def test_end_threshold_below_two_is_refused():
    with pytest.raises(ValueError, match="slow render"):
        _settings(end_repeat_threshold=1)


def test_max_pages_ceiling_is_reported_as_truncation(tmp_path, prose_frames):
    """Hitting the ceiling says the book is unfinished — it never claims success."""
    run = _run(prose_frames, _settings(max_pages=3), tmp_path)

    assert run.outcome.reason == "max_pages"
    assert run.page_count == 3
    assert "Raise max_pages" in run.outcome.detail


# ── GUARD 2 — dedupe ─────────────────────────────────────────────────────


def test_repeated_frames_are_never_written_twice(tmp_path, prose_frames):
    """Every frame after the last page is dropped, not saved as a page."""
    run = _run(prose_frames, _settings(), tmp_path)

    written = sorted(p.name for p in tmp_path.glob("page-*.png"))
    assert written == [f"page-{i:04d}.png" for i in range(1, 7)]
    assert run.frames_deduped == run.settings.end_repeat_threshold


def test_two_genuinely_blank_pages_both_survive(tmp_path, prose_frames):
    """Dedupe is adjacent-only.

    A book with a blank page in two places has two blank pages; collapsing
    them by signature equality alone would silently lose one.
    """
    blank = _png(Image.new("RGB", (674, 872), "white"))
    frames = [prose_frames[0], blank, prose_frames[1], blank, prose_frames[2]]
    run = _run(frames, _settings(), tmp_path)

    assert run.page_count == 5
    blank_sig = _sig(blank)
    kept_blanks = [
        p for p in run.pages
        if bc.changed_fraction(p.signature, blank_sig) < bc.DEFAULT_PAGE_CHANGE_THRESHOLD
    ]
    assert len(kept_blanks) == 2


# ── GUARD 3 — crop ───────────────────────────────────────────────────────


def test_crop_removes_the_reader_chrome(chrome_frames):
    framed = Image.open(io.BytesIO(chrome_frames[0]))
    insets = bc.CropInsets(top=CHROME_BAR / framed.height, bottom=CHROME_BAR / framed.height)
    cropped = bc.crop_frame(framed, insets)

    assert cropped.height == framed.height - CHROME_BAR * 2
    corner = cropped.convert("RGB").getpixel((2, 2))
    assert min(corner) > 200, f"top-left pixel {corner} is still chrome"


def test_uncropped_chrome_defeats_end_detection_and_cropping_rescues_it(
    tmp_path, chrome_frames
):
    """The failing-then-passing proof that cropping must precede comparison.

    The reader is parked on the last page while its progress bar keeps
    animating. Uncropped, the frame never stops changing, so the book never
    ends and the run burns to the ceiling. Cropped, it ends correctly.
    """
    parked = []
    for step in range(6):
        image = Image.open(io.BytesIO(chrome_frames[-1])).convert("RGB")
        _draw_scrubber(image, 0.3 + step * 0.1)
        parked.append(_png(image))

    frames = chrome_frames + parked
    height = Image.open(io.BytesIO(frames[0])).height
    inset = bc.CropInsets(top=CHROME_BAR / height, bottom=CHROME_BAR / height)

    uncropped = _run(frames, _settings(max_pages=12), tmp_path / "uncropped")
    assert uncropped.outcome.reason == "max_pages"
    assert uncropped.page_count > 6, "chrome noise was not counted as new pages"

    cropped = _run(
        frames, _settings(max_pages=12, crop=inset), tmp_path / "cropped"
    )
    assert cropped.outcome.reason == "end_of_book"
    assert cropped.page_count == 6


def test_impossible_crop_insets_are_refused_by_name():
    with pytest.raises(ValueError, match="out of range"):
        bc.CropInsets(top=0.8)
    with pytest.raises(ValueError, match="sliver"):
        bc.CropInsets(top=0.45, bottom=0.45)


# ── GUARD 4 — assembly ───────────────────────────────────────────────────


def test_assembly_produces_one_pdf_with_a_page_per_capture(tmp_path, prose_frames):
    import fitz

    driver = bc.SyntheticReaderDriver(prose_frames, name="Synthetic Reader")
    run = asyncio.run(
        bc.capture_book(driver, _settings(), tmp_path, sleep=_noop_sleep)
    )
    book = bc.assemble(run, tmp_path, source_name=driver.describes)

    assert book.pdf_path.exists() and book.pdf_path.stat().st_size > 0
    with fitz.open(str(book.pdf_path)) as doc:
        assert doc.page_count == 6
    assert book.page_count == 6
    assert "Captured from Synthetic Reader, 6 pages," in book.provenance


@pytest.mark.skipif(
    bc.ocr_unavailable_reason() is not None,
    reason="no OCR engine on this machine",
)
def test_searchable_pdf_really_contains_the_page_text(tmp_path, sparse_frames):
    """Searchable means searchable: the marker from page 4 is findable."""
    import fitz

    run = _run(sparse_frames, _settings(), tmp_path)
    book = bc.assemble(run, tmp_path, source_name="Synthetic Reader")

    assert book.searchable is True
    assert book.ocr_unavailable_reason is None
    with fitz.open(str(book.pdf_path)) as doc:
        text = "".join(page.get_text() for page in doc)
    assert "ZQX004" in text.replace(" ", ""), text[:400]


def test_image_only_pdf_announces_that_it_is_not_searchable(tmp_path, prose_frames):
    """OCR off must never yield a file that merely looks searchable."""
    import fitz

    run = _run(prose_frames, _settings(ocr=False), tmp_path)
    book = bc.assemble(run, tmp_path, source_name="Synthetic Reader")

    assert book.searchable is False
    assert book.ocr_unavailable_reason
    with fitz.open(str(book.pdf_path)) as doc:
        assert doc.page_count == 6
        assert "".join(page.get_text() for page in doc).strip() == ""


def test_missing_ocr_engine_degrades_instead_of_failing(
    tmp_path, prose_frames, monkeypatch
):
    """No Tesseract on the machine is a stated limitation, not a lost capture."""
    monkeypatch.setattr(assembly_mod.shutil, "which", lambda _name: None)

    run = _run(prose_frames, _settings(), tmp_path)
    book = assembly_mod.assemble(run, tmp_path, source_name="Synthetic Reader")

    assert book.page_count == 6
    assert book.searchable is False
    assert "Install Tesseract" in (book.ocr_unavailable_reason or "")


def test_assembling_nothing_is_refused(tmp_path):
    with pytest.raises(ValueError, match="no captured pages"):
        bc.build_pdf([], tmp_path / "empty.pdf")


# ── GUARD 5 — the loop never lies about a failure ────────────────────────


def test_a_broken_capture_keeps_what_it_got_and_says_why(tmp_path, prose_frames):
    class DyingDriver(bc.SyntheticReaderDriver):
        async def capture(self) -> bytes:
            if len(self.key_presses) >= 3:
                raise OSError("screencapture produced an empty file")
            return await super().capture()

    run = _run(prose_frames, _settings(), tmp_path, driver_cls=DyingDriver)

    assert run.outcome.reason == "capture_failed"
    assert run.page_count == 3
    assert "empty file" in run.outcome.detail
    assert len(list(tmp_path.glob("page-*.png"))) == 3


def test_a_failed_page_turn_stops_the_run_and_says_why(tmp_path, prose_frames):
    class StuckDriver(bc.SyntheticReaderDriver):
        async def send_next_page(self, key: str) -> None:
            if len(self.key_presses) >= 2:
                raise ReaderUnavailable("Allow Accessibility for AI Matrx")
            await super().send_next_page(key)

    run = _run(prose_frames, _settings(), tmp_path, driver_cls=StuckDriver)

    assert run.outcome.reason == "capture_failed"
    assert "Page turn failed" in run.outcome.detail
    assert "Accessibility" in run.outcome.detail
    assert run.page_count == 3


def test_an_unknown_page_turn_key_names_the_ones_that_work():
    driver = bc.MacReaderDriver("Books")
    with pytest.raises(ReaderUnavailable, match="page-turn key"):
        asyncio.run(driver.send_next_page("banana"))
    assert "right" in bc.supported_keys()


def test_settings_refuse_an_unnamed_reader():
    with pytest.raises(ValueError, match="app_name is required"):
        bc.CaptureSettings(app_name="  ")
