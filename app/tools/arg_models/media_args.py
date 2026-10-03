from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ImageOcrArgs(BaseModel):
    file_path: str = Field(description="Input file: image (ocr, resize), PDF (pdf_extract), or archive (archive_extract: zip, tar, tar.gz, tar.bz2, 7z).")
    language: str = Field(
        default="eng",
        description="Tesseract language code(s), '+'-joined, e.g. 'eng+fra'.",
    )


class ImageResizeArgs(BaseModel):
    file_path: str = Field(description="Input file: image (ocr, resize), PDF (pdf_extract), or archive (archive_extract: zip, tar, tar.gz, tar.bz2, 7z).")
    width: int | None = Field(
        default=None,
        ge=1,
        description="Pixels; alone, keeps the aspect ratio.",
    )
    height: int | None = Field(
        default=None,
        ge=1,
        description="Pixels; alone, keeps the aspect ratio.",
    )
    scale: float | None = Field(
        default=None,
        gt=0.0,
        description="Factor, e.g. 0.5; overrides width/height.",
    )
    output_format: Literal["png", "jpeg", "webp", "gif", "bmp"] | None = Field(
        default=None,
        description="Default: the source's format.",
    )


class PdfExtractArgs(BaseModel):
    file_path: str = Field(description="Input file: image (ocr, resize), PDF (pdf_extract), or archive (archive_extract: zip, tar, tar.gz, tar.bz2, 7z).")
    pages: str | None = Field(
        default=None,
        description="1-based: '3', '1-5' or '1,3,5-7'. Default: all.",
    )
    extract_images: bool = Field(
        default=False,
        description="Also save embedded images to the app temp dir (pdf_images/).",
    )


class OfficeGenerateArgs(BaseModel):
    format: Literal["docx", "pptx", "xlsx"] = Field(
        description="archive_create: archive type. office_generate: document type.",
    )
    spec: dict[str, Any] = Field(
        description=(
            "Per format. docx: {title?, blocks: [{type: heading|paragraph|bullet|numbered|"
            "quote|table|page_break, text?, level?, rows?, header?}]}. pptx: {title?, "
            "subtitle?, slides: [{title?, bullets?, body?, notes?, layout?: title|"
            "title_content|section|blank}]}. xlsx: {sheets: [{name, columns?, rows, "
            "freeze_header?}]}."
        ),
    )
    path: str = Field(
        description="Destination file; parent dirs created, an existing file overwritten.",
    )


class ArchiveCreateArgs(BaseModel):
    source_paths: list[str] = Field(
        description="Files and directories to include.",
        min_length=1,
    )
    output_path: str | None = Field(
        default=None,
        description="Default: a new file in the app temp dir (archives/).",
    )
    format: Literal["zip", "tar", "tar.gz", "tar.bz2"] = Field(
        default="zip",
        description="archive_create: archive type. office_generate: document type.",
    )
    compression: Literal["deflate", "store", "bzip2", "lzma"] = Field(
        default="deflate",
        description="zip only: deflate compresses; any other value stores uncompressed.",
    )


class ArchiveExtractArgs(BaseModel):
    file_path: str = Field(
        description="Input file: image (ocr, resize), PDF (pdf_extract), or archive (archive_extract: zip, tar, tar.gz, tar.bz2, 7z)."
    )
    output_dir: str | None = Field(
        default=None,
        description="Default: a new dir in the app temp dir (extracted/).",
    )
