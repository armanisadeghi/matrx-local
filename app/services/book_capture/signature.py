"""Page signatures — how the loop knows the reader actually turned the page.

**Why not a classic dHash.** The obvious instrument for "is this the same
frame" is a 64-bit difference hash, and it does not work on book pages.  A
page is a nearly-white field carrying a little ink; reduced to 8x9 cells its
gradient signs are dominated by paper, not glyphs.  Measured on rendered
prose pages, consecutive *different* pages came out 0–2 bits apart out of 64,
while a 3% brightness shift on the *same* page moved up to 6 bits at higher
grids — the noise floor sat above the signal.  Raising the grid to 32x32 did
not fix it.  Ink-coverage bit hashing failed the same way and was, in
addition, destroyed by a one-pixel scroll (96 bits of 2304).

What separates the two cases cleanly is how much of the page changed.  So a
signature here is a 64x64 mean-pooled greyscale fingerprint, and two frames
are compared by the **fraction of cells that moved more than a tone of
noise**.  Measured on the same rendered pages (see
``tests/unit/test_book_capture.py``):

| case                                       | fraction changed |
|--------------------------------------------|------------------|
| identical frame                            | 0.000000         |
| 2% brightness shift, same page             | 0.000000         |
| a blinking text caret, same page           | 0.000488         |
| new page, sparse near-identical text       | 0.001465 and up  |
| new page, ordinary prose                   | 0.109619 and up  |

The default threshold sits at 0.001 — between the caret and the sparsest page
that must still count.  Be honest about that band: it is a factor of two
either way, because *one changed glyph and one blinking caret are the same
number of pixels*, and no pixel metric separates them.  Ordinary prose sits a
hundred times clear of both, so the default is comfortable for real books and
tight only for a page of verse or an index.

Two things keep the tightness from failing silently.  The threshold is a knob
(``CaptureSettings.page_change_threshold``), and the end-of-book message
reports the largest change actually observed while the loop was deciding the
book had ended — so a run that stopped early says the number that made it
stop, and the remedy is arithmetic rather than guesswork.

A mean-absolute-difference metric was measured against this one and rejected:
it ranked a 2% brightness shift (0.000296) *above* a blinking caret
(0.000065), i.e. it made display gamma look more like a page turn than ink
did.  Counting cells past a tone tolerance puts brightness at exactly zero.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

#: Side of the mean-pooled grid. 64 was the smallest that held the separation
#: above; 32 halved the margin, 96 bought nothing for four times the work.
GRID = 64

#: Per-cell greyscale tolerance (0-255). Absorbs display gamma and JPEG-ish
#: rounding without absorbing ink.
TONE_TOLERANCE = 4

#: Default fraction of the page that must move to call it a new page.
DEFAULT_PAGE_CHANGE_THRESHOLD = 0.001


@dataclass(frozen=True)
class PageSignature:
    """A fixed-size perceptual fingerprint of one frame."""

    cells: tuple[int, ...]

    def __len__(self) -> int:
        return len(self.cells)


def signature(image: Image.Image) -> PageSignature:
    """Mean-pool ``image`` to a greyscale fingerprint."""
    pooled = image.convert("L").resize((GRID, GRID), Image.Resampling.BOX)
    return PageSignature(tuple(pooled.getdata()))


def changed_fraction(left: PageSignature, right: PageSignature) -> float:
    """Fraction of cells that differ by more than the tone tolerance."""
    if len(left) != len(right):
        raise ValueError("Signatures of different sizes cannot be compared.")
    moved = sum(
        1
        for a, b in zip(left.cells, right.cells)
        if abs(a - b) > TONE_TOLERANCE
    )
    return moved / len(left.cells)


def same_page(
    left: PageSignature,
    right: PageSignature,
    *,
    threshold: float = DEFAULT_PAGE_CHANGE_THRESHOLD,
) -> bool:
    """True when too little of the frame moved to call it a new page."""
    return changed_fraction(left, right) < threshold
