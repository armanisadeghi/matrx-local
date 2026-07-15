"""Media processing tools — OCR, image manipulation, PDF extraction, archives."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
import uuid
import zipfile
import tarfile
from pathlib import Path
from typing import Any

from app.config import TEMP_DIR
from app.tools.session import ToolSession
from app.tools.types import ImageData, ToolResult, ToolResultType

logger = logging.getLogger(__name__)


async def tool_image_ocr(
    session: ToolSession,
    file_path: str,
    language: str = "eng",
) -> ToolResult:
    """Extract text from an image using OCR (Optical Character Recognition).

    Requires tesseract to be installed on the system.
    Languages: eng, fra, deu, spa, ita, por, chi_sim, chi_tra, jpn, kor, etc.
    """
    resolved = session.resolve_path(file_path)
    if not os.path.isfile(resolved):
        return ToolResult(
            type=ToolResultType.ERROR, output=f"File not found: {resolved}"
        )

    try:
        import pytesseract
        from PIL import Image

        img = Image.open(resolved)
        text = pytesseract.image_to_string(img, lang=language)

        return ToolResult(
            output=f"OCR result ({resolved}):\n\n{text.strip()}",
            metadata={
                "text": text.strip(),
                "source": resolved,
                "language": language,
                "image_size": f"{img.width}x{img.height}",
            },
        )
    except ImportError:
        # Try CLI fallback
        try:
            proc = await asyncio.create_subprocess_exec(
                "tesseract",
                resolved,
                "stdout",
                "-l",
                language,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode != 0:
                return ToolResult(
                    type=ToolResultType.ERROR,
                    output=f"Tesseract error: {stderr.decode()}. Install: brew install tesseract (macOS) or apt install tesseract-ocr (Linux)",
                )
            text = stdout.decode().strip()
            return ToolResult(
                output=f"OCR result ({resolved}):\n\n{text}",
                metadata={"text": text, "source": resolved},
            )
        except FileNotFoundError:
            return ToolResult(
                type=ToolResultType.ERROR,
                output="Tesseract OCR not installed. Install: brew install tesseract (macOS), apt install tesseract-ocr (Linux), or download from github.com/tesseract-ocr",
            )
    except Exception as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"OCR failed: {e}")


async def tool_image_resize(
    session: ToolSession,
    file_path: str,
    width: int | None = None,
    height: int | None = None,
    scale: float | None = None,
    output_format: str | None = None,
    quality: int = 85,
) -> ToolResult:
    """Resize or convert an image. Specify width/height or scale factor.

    If only width or height is given, maintains aspect ratio.
    output_format: png, jpg, webp, bmp, etc.
    """
    resolved = session.resolve_path(file_path)
    if not os.path.isfile(resolved):
        return ToolResult(
            type=ToolResultType.ERROR, output=f"File not found: {resolved}"
        )

    try:
        from PIL import Image

        img = Image.open(resolved)
        original_size = img.size

        if scale:
            new_width = int(img.width * scale)
            new_height = int(img.height * scale)
        elif width and height:
            new_width, new_height = width, height
        elif width:
            ratio = width / img.width
            new_width = width
            new_height = int(img.height * ratio)
        elif height:
            ratio = height / img.height
            new_width = int(img.width * ratio)
            new_height = height
        else:
            return ToolResult(
                type=ToolResultType.ERROR,
                output="Provide width/height or scale factor.",
            )

        img_resized = img.resize((new_width, new_height), Image.LANCZOS)

        # Determine output path
        ext = output_format or Path(resolved).suffix[1:]
        out_dir = TEMP_DIR / "images"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"resized_{uuid.uuid4().hex[:8]}.{ext}"

        save_kwargs = {}
        if ext.lower() in ("jpg", "jpeg"):
            if img_resized.mode == "RGBA":
                img_resized = img_resized.convert("RGB")
            save_kwargs["quality"] = quality
        elif ext.lower() == "webp":
            save_kwargs["quality"] = quality

        img_resized.save(str(output_path), **save_kwargs)

        file_size = output_path.stat().st_size

        return ToolResult(
            output=f"Resized: {original_size[0]}x{original_size[1]} → {new_width}x{new_height}\nSaved to: {output_path} ({file_size} bytes)",
            metadata={
                "path": str(output_path),
                "original_size": list(original_size),
                "new_size": [new_width, new_height],
                "format": ext,
                "size_bytes": file_size,
            },
        )

    except ImportError:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=(
                "Image processing library (Pillow) is not installed.\n"
                "Developer info: pip install Pillow"
            ),
        )
    except Exception as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"Image resize failed: {e}")


async def tool_pdf_extract(
    session: ToolSession,
    file_path: str,
    pages: str | None = None,
    extract_images: bool = False,
) -> ToolResult:
    """Extract text (and optionally images) from a PDF file.

    pages: page range like '1-5', '3', or '1,3,5'. None = all pages.
    """
    resolved = session.resolve_path(file_path)
    if not os.path.isfile(resolved):
        return ToolResult(
            type=ToolResultType.ERROR, output=f"File not found: {resolved}"
        )

    try:
        import fitz  # PyMuPDF

        doc = fitz.open(resolved)
        total_pages = len(doc)

        # Parse page range
        if pages:
            page_list = _parse_page_range(pages, total_pages)
        else:
            page_list = list(range(total_pages))

        text_parts = []
        images_extracted = 0

        for page_num in page_list:
            if page_num >= total_pages:
                continue
            page = doc[page_num]
            text = page.get_text()
            text_parts.append(f"--- Page {page_num + 1} ---\n{text}")

            if extract_images:
                images = page.get_images()
                for img_idx, img in enumerate(images):
                    try:
                        xref = img[0]
                        base_image = doc.extract_image(xref)
                        img_dir = TEMP_DIR / "pdf_images"
                        img_dir.mkdir(parents=True, exist_ok=True)
                        img_path = (
                            img_dir
                            / f"page{page_num + 1}_img{img_idx}.{base_image['ext']}"
                        )
                        img_path.write_bytes(base_image["image"])
                        images_extracted += 1
                    except Exception:
                        continue

        doc.close()

        full_text = "\n\n".join(text_parts)
        if len(full_text) > 50000:
            full_text = full_text[:50000] + "\n\n... [text truncated at 50000 chars]"

        output = f"PDF: {resolved} ({total_pages} pages, extracted {len(page_list)} pages)\n\n{full_text}"
        if extract_images and images_extracted > 0:
            output += (
                f"\n\nExtracted {images_extracted} images to {TEMP_DIR}/pdf_images/"
            )

        return ToolResult(
            output=output,
            metadata={
                "source": resolved,
                "total_pages": total_pages,
                "extracted_pages": len(page_list),
                "images_extracted": images_extracted,
            },
        )

    except ImportError:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=(
                "PDF Extraction is not installed. "
                "Go to Settings → Capabilities to install it, or open the Devices & Permissions page.\n"
                "Developer info: pip install PyMuPDF"
            ),
            metadata={"fix_capability_id": "pdf_extraction"},
        )
    except Exception as e:
        return ToolResult(
            type=ToolResultType.ERROR, output=f"PDF extraction failed: {e}"
        )


async def tool_office_generate(
    session: ToolSession,
    format: str,
    spec: dict[str, Any],
    path: str,
) -> ToolResult:
    """Generate a Microsoft Office document (.docx / .pptx / .xlsx) from a
    structured spec and write it to `path`.

    format: 'docx' | 'pptx' | 'xlsx'.
    spec: the structured document description, matching the codec spec for the
      chosen format:
        docx -> DocumentSpec  {title?, blocks: [{type, text, level?, rows?, header?}]}
        pptx -> PresentationSpec {title?, subtitle?, slides: [{title?, bullets?, body?, notes?, layout?}]}
        xlsx -> SpreadsheetSpec {sheets: [{name, columns?, rows, freeze_header?}]}
    path: destination file path (parent directories are created).
    """
    from pydantic import ValidationError

    from matrx_files.specific_handlers.office import (
        DocumentSpec,
        PresentationSpec,
        SpreadsheetSpec,
        generate_office,
    )

    spec_classes = {
        "docx": DocumentSpec,
        "pptx": PresentationSpec,
        "xlsx": SpreadsheetSpec,
    }
    fmt = (format or "").lower().lstrip(".")
    spec_cls = spec_classes.get(fmt)
    if spec_cls is None:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=f"Unknown format: {format!r}. Use one of: docx, pptx, xlsx.",
        )
    if not isinstance(spec, dict):
        return ToolResult(
            type=ToolResultType.ERROR,
            output=f"spec must be an object matching the {fmt} spec, got {type(spec).__name__}.",
        )

    try:
        spec_model = spec_cls.model_validate(spec)
    except ValidationError as e:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=f"Invalid {fmt} spec: {e}",
        )

    try:
        result = generate_office(spec_model)
    except Exception as e:
        return ToolResult(
            type=ToolResultType.ERROR, output=f"Office generation failed: {e}"
        )

    data = base64.b64decode(result.base64_data)
    resolved = session.resolve_path(path)
    try:
        os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
        Path(resolved).write_bytes(data)
    except OSError as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"Cannot write file: {e}")

    session.mark_file_read(resolved)
    return ToolResult(
        output=f"Wrote {fmt} ({result.byte_size} bytes) to {resolved}",
        metadata={
            "path": resolved,
            "format": fmt,
            "office_kind": result.office_kind.value,
            "mime_type": result.mime_type,
            "size_bytes": result.byte_size,
        },
    )


def _parse_page_range(pages: str, total: int) -> list[int]:
    """Parse page range string into list of 0-indexed page numbers."""
    result = []
    for part in pages.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            start_idx = int(start) - 1
            end_idx = int(end)
            result.extend(range(max(0, start_idx), min(total, end_idx)))
        else:
            idx = int(part) - 1
            if 0 <= idx < total:
                result.append(idx)
    return sorted(set(result))


async def tool_archive_create(
    session: ToolSession,
    source_paths: list[str],
    output_path: str | None = None,
    format: str = "zip",
    compression: str = "deflate",
) -> ToolResult:
    """Create an archive (zip or tar.gz) from files/directories.

    format: zip, tar, tar.gz, tar.bz2
    compression (zip only): deflate, stored
    """
    resolved_sources = [session.resolve_path(p) for p in source_paths]

    # Validate sources
    for src in resolved_sources:
        if not os.path.exists(src):
            return ToolResult(type=ToolResultType.ERROR, output=f"Not found: {src}")

    if not output_path:
        ext = {
            "zip": ".zip",
            "tar": ".tar",
            "tar.gz": ".tar.gz",
            "tar.bz2": ".tar.bz2",
        }.get(format, ".zip")
        out_dir = TEMP_DIR / "archives"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir / f"archive_{uuid.uuid4().hex[:8]}{ext}")
    else:
        output_path = session.resolve_path(output_path)

    try:
        if format == "zip":
            comp = (
                zipfile.ZIP_DEFLATED if compression == "deflate" else zipfile.ZIP_STORED
            )
            with zipfile.ZipFile(output_path, "w", comp) as zf:
                for src in resolved_sources:
                    if os.path.isfile(src):
                        zf.write(src, os.path.basename(src))
                    elif os.path.isdir(src):
                        base = os.path.basename(src)
                        for root, dirs, files in os.walk(src):
                            for f in files:
                                full = os.path.join(root, f)
                                arcname = os.path.join(base, os.path.relpath(full, src))
                                zf.write(full, arcname)

        elif format in ("tar", "tar.gz", "tar.bz2"):
            mode_map = {"tar": "w", "tar.gz": "w:gz", "tar.bz2": "w:bz2"}
            with tarfile.open(output_path, mode_map[format]) as tf:
                for src in resolved_sources:
                    tf.add(src, arcname=os.path.basename(src))
        else:
            return ToolResult(
                type=ToolResultType.ERROR, output=f"Unknown format: {format}"
            )

        file_size = os.path.getsize(output_path)
        return ToolResult(
            output=f"Archive created: {output_path} ({file_size} bytes, {format})",
            metadata={"path": output_path, "size_bytes": file_size, "format": format},
        )

    except Exception as e:
        return ToolResult(
            type=ToolResultType.ERROR, output=f"Archive creation failed: {e}"
        )


async def tool_archive_extract(
    session: ToolSession,
    file_path: str,
    output_dir: str | None = None,
) -> ToolResult:
    """Extract an archive (zip, tar, tar.gz, tar.bz2, 7z)."""
    resolved = session.resolve_path(file_path)
    if not os.path.isfile(resolved):
        return ToolResult(
            type=ToolResultType.ERROR, output=f"File not found: {resolved}"
        )

    if not output_dir:
        out = TEMP_DIR / "extracted" / uuid.uuid4().hex[:8]
    else:
        out = Path(session.resolve_path(output_dir))

    out.mkdir(parents=True, exist_ok=True)

    try:
        extracted_count = 0

        if zipfile.is_zipfile(resolved):
            with zipfile.ZipFile(resolved, "r") as zf:
                zf.extractall(str(out))
                extracted_count = len(zf.namelist())

        elif tarfile.is_tarfile(resolved):
            with tarfile.open(resolved) as tf:
                tf.extractall(str(out), filter="data")
                extracted_count = len(tf.getnames())

        else:
            # Try 7z via command line
            proc = await asyncio.create_subprocess_exec(
                "7z",
                "x",
                resolved,
                f"-o{out}",
                "-y",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode != 0:
                return ToolResult(
                    type=ToolResultType.ERROR,
                    output=f"Unsupported archive format or extraction failed: {stderr.decode()}",
                )
            extracted_count = len(list(out.rglob("*")))

        return ToolResult(
            output=f"Extracted {extracted_count} items to {out}",
            metadata={"output_dir": str(out), "count": extracted_count},
        )

    except Exception as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"Extraction failed: {e}")
