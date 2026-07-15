from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ImageOcrArgs(BaseModel):
    file_path: str = Field(description="Path to the image file (PNG, JPEG, TIFF, BMP, etc.).")
    language: str = Field(
        default="eng",
        description=(
            "Tesseract language code (e.g. 'eng', 'fra', 'deu', 'spa'). "
            "Multiple languages can be combined with '+' (e.g. 'eng+fra')."
        ),
    )


class ImageResizeArgs(BaseModel):
    file_path: str = Field(description="Path to the source image.")
    width: int | None = Field(
        default=None,
        ge=1,
        description="Target width in pixels. If only width is set, height is scaled proportionally.",
    )
    height: int | None = Field(
        default=None,
        ge=1,
        description="Target height in pixels. If only height is set, width is scaled proportionally.",
    )
    scale: float | None = Field(
        default=None,
        gt=0.0,
        description="Scale factor (e.g. 0.5 for half size, 2.0 for double). Overrides width/height.",
    )
    output_format: Literal["png", "jpeg", "webp", "gif", "bmp"] | None = Field(
        default=None,
        description="Output image format. Defaults to the same format as the source.",
    )


class PdfExtractArgs(BaseModel):
    file_path: str = Field(description="Path to the PDF file.")
    pages: str | None = Field(
        default=None,
        description=(
            "Pages to extract. Accepts a single page ('3'), a range ('1-5'), "
            "or comma-separated values ('1,3,5-7'). Defaults to all pages."
        ),
    )
    extract_images: bool = Field(
        default=False,
        description="If true, also extract embedded images (saves to same directory as PDF).",
    )


class OfficeGenerateArgs(BaseModel):
    format: Literal["docx", "pptx", "xlsx"] = Field(
        description="Office document format to generate.",
    )
    spec: dict[str, Any] = Field(
        description=(
            "Structured document description matching the chosen format:\n"
            "- docx (DocumentSpec): {title?, blocks: [{type: heading|paragraph|"
            "bullet|numbered|quote|table|page_break, text?, level?, rows?, header?}]}\n"
            "- pptx (PresentationSpec): {title?, subtitle?, slides: [{title?, "
            "bullets?, body?, notes?, layout?}]}\n"
            "- xlsx (SpreadsheetSpec): {sheets: [{name, columns?, rows, freeze_header?}]}"
        ),
    )
    path: str = Field(
        description="Destination file path for the generated document (parent directories are created).",
    )


class ArchiveCreateArgs(BaseModel):
    source_paths: list[str] = Field(
        description="List of file or directory paths to include in the archive.",
        min_length=1,
    )
    output_path: str | None = Field(
        default=None,
        description="Output archive path. Auto-generated in the working directory if omitted.",
    )
    format: Literal["zip", "tar", "tar.gz", "tar.bz2"] = Field(
        default="zip",
        description="Archive format.",
    )
    compression: Literal["deflate", "store", "bzip2", "lzma"] = Field(
        default="deflate",
        description="Compression algorithm (only used for 'zip' format).",
    )


class ArchiveExtractArgs(BaseModel):
    file_path: str = Field(
        description="Path to the archive to extract (zip, tar, tar.gz, tar.bz2, 7z)."
    )
    output_dir: str | None = Field(
        default=None,
        description="Directory to extract into. Defaults to the archive's directory.",
    )
