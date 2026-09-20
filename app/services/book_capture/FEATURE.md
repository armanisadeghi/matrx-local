# FEATURE — Book capture (read a book off the screen, file it as one Source)

Capture a book the user already owns and already has open: focus the reader,
photograph its window page by page, stop when the book ends, and hand the
platform ONE searchable PDF Source with its provenance attached.

Tool surface: `BookCapture` → cloud `local_book_capture`
(`app/tools/tools/book_capture.py`, arg model `arg_models/book_args.py`,
`Screen` action group action `capture_book`). macOS only.

## The shape

| Part | File | Owns |
|---|---|---|
| Driver seam | `drivers.py` | The ONLY code that touches the screen |
| Change detector | `signature.py` | "Did the reader actually advance?" |
| Loop | `session.py` | Pages, dedupe, end-of-book, the stop reason |
| Assembly | `assembly.py` | One PDF, with or without a text layer |
| Handoff | `handoff.py` | Upload as one Source, with provenance |

## Rules

1. **Everything that touches the screen goes through `ReaderDriver`.** That is
   not test scaffolding — it is why the whole mechanism can be proven without
   an agent seizing the owner's display (Arman, 2026-09-17: agent browsers
   stole his focus). `SyntheticReaderDriver` replays pre-rendered frames and
   clamps at the last one exactly as a real reader does. **Every automated
   test uses it. A test that constructs `MacReaderDriver` and drives it is a
   defect.**
2. **Capture is window-scoped, never a screen grab.** `screencapture -l
   <CGWindowID>`: the owner's other windows, notifications and wallpaper are
   not ours to collect, and a window grab survives something overlapping the
   reader. The window id comes from Quartz `CGWindowListCopyWindowInfo` —
   `window_manager`'s AppleScript listing reports titles and bounds but no id,
   so it cannot serve here.
3. **Never a 64-bit dHash, and never "compare the raw frame".** Read the
   header of `signature.py` before touching the detector: it carries the
   measured numbers for every candidate that was tried and why each failed.
   The short version is that a book page is a nearly-white field carrying a
   little ink, so gradient hashing puts the noise floor above the signal.
4. **Crop before you compare.** Reader chrome — a page slider, a progress bar,
   a clock — keeps moving after the book has ended. Uncropped, the run never
   terminates and burns to the ceiling; that failure is pinned by
   `test_uncropped_chrome_defeats_end_detection_and_cropping_rescues_it`.
   Insets are FRACTIONS, because the same window yields twice the pixels on a
   Retina panel as on an external display.
5. **Dedupe is adjacent-only.** A book with a blank page in two places has two
   blank pages. Collapsing by signature equality across the whole run silently
   loses one.
6. **The run always says why it stopped**, and an early stop says the number
   that stopped it: `end_of_book` reports the largest change it saw while
   deciding, so a book cut short points straight at
   `page_change_threshold`. `max_pages` says the book is unfinished. A
   `capture_failed` run keeps the pages it got.
7. **A PDF never claims a text layer it does not have.** No OCR engine, OCR
   turned off, or an engine that failed mid-run all produce an image-only PDF
   plus `ocr_unavailable_reason` in plain words. Graceful degradation, Hard
   Rule 3 — Tesseract is never a hard dependency.
8. **One capture is one Source.** The assembled PDF crosses, not the loose
   page images: the own-files lane treats one document as one Source, so
   uploading 300 PNGs would make 300 Sources and no book. Upload goes through
   `MatrxFilesClient` with `intent="force_new_copy"` — two captures of the
   same book are two captures, and aliasing the second onto the first would
   record this run nowhere.
9. **Both OS grants are settled before anything is driven.** Screen Recording
   to see the window, Accessibility to send the key; each missing one comes
   back as an `os_permission_needed` action the app can act on. The guards
   assert the screen was never touched on a refusal path.

## Knobs

Every behavioural choice is a `CaptureSettings` field and a tool argument:
`max_pages`, `next_page_key`, `page_delay_seconds`, `end_repeat_threshold`,
`page_change_threshold`, the four crop insets, `ocr`, `window_title`,
`send_to_library`. No hardcoded taste (limits-are-knobs). When these grow an
org-level default, they bind through the unified settings layer — the tool
arguments stay the per-run override.

## Verification

```bash
uv run --frozen pytest tests/unit/test_book_capture.py tests/unit/test_book_capture_tool.py
```

Both files run on synthetic frames rendered from a real PDF and need no
screen, no network and no credentials. Twelve deliberate mutations — end-of-book
disabled, dedupe off, content-dedupe instead of adjacent, comparison before
crop, the dHash restored, a PDF lying about its text layer (twice), each
permission preflight skipped, a not-yet-asked permission treated as granted, a
failure reported as a normal finish, and the ceiling reported as the end —
were each confirmed to turn a guard red.

**Not yet verified by anyone: a real capture of a real book on a real screen.**
That run belongs to the owner, deliberately; the guided session is
`common-docs/operations/for-arman/2026-09-19/book-capture-first-run.md`, and
board row E1 stays `doing` until he has done it.
