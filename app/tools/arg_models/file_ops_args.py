from __future__ import annotations

from pydantic import BaseModel, Field


class ReadArgs(BaseModel):
    file_path: str = Field(
        description="Absolute or workspace-relative path to the file to read."
    )
    offset: int | None = Field(
        default=None,
        ge=1,
        description="1-based line number to start reading from.",
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        description="Maximum number of lines to return.",
    )


class WriteArgs(BaseModel):
    file_path: str = Field(description="Absolute or workspace-relative path to write.")
    content: str = Field(description="Content to write to the file.")
    create_directories: bool = Field(
        default=True,
        description="Create parent directories if they don't exist.",
    )


class EditArgs(BaseModel):
    file_path: str = Field(description="Path to the file to edit.")
    old_string: str = Field(
        description=(
            "Exact string to find and replace. Must match exactly including "
            "whitespace. Must be unique in the file unless replace_all is true."
        )
    )
    new_string: str = Field(description="Replacement string.")
    replace_all: bool = Field(
        default=False,
        description="Replace every occurrence instead of requiring a unique match.",
    )


class MoveArgs(BaseModel):
    source: str = Field(description="Path of the file or directory to move.")
    destination: str = Field(
        description=(
            "Target path. Moving a file into an existing directory keeps the "
            "file's name."
        )
    )
    overwrite: bool = Field(
        default=False,
        description="Replace the destination if it already exists.",
    )


class CopyArgs(BaseModel):
    source: str = Field(description="Path of the file or directory to copy.")
    destination: str = Field(
        description=(
            "Target path. Copying a file into an existing directory keeps the "
            "file's name."
        )
    )
    overwrite: bool = Field(
        default=False,
        description="Replace the destination if it already exists.",
    )


class DeleteArgs(BaseModel):
    path: str = Field(description="Path of the file or directory to delete.")
    permanent: bool = Field(
        default=False,
        description=(
            "If false (default), move to the OS trash (recoverable). If true, "
            "delete permanently — NOT recoverable."
        ),
    )


class RenameArgs(BaseModel):
    path: str = Field(description="Path of the file or directory to rename.")
    new_name: str = Field(
        description="New bare name (no path separators). Use Move to relocate."
    )


class MkdirArgs(BaseModel):
    path: str = Field(description="Path of the directory to create.")
    parents: bool = Field(
        default=True,
        description="Create missing parent directories as needed.",
    )


class GlobArgs(BaseModel):
    pattern: str = Field(
        description="Glob pattern to match (e.g. '**/*.py', 'src/*.ts')."
    )
    path: str | None = Field(
        default=None,
        description="Base directory to search from. Defaults to working directory.",
    )


class GrepArgs(BaseModel):
    pattern: str = Field(description="Regular expression pattern to search for.")
    path: str | None = Field(
        default=None,
        description="File or directory path to search in. Defaults to working directory.",
    )
    include: str | None = Field(
        default=None,
        description="Glob pattern to restrict which files are searched (e.g. '*.py').",
    )
    max_results: int = Field(
        default=100,
        ge=1,
        le=2000,
        description="Maximum number of matching lines to return.",
    )


class ListDirectoryArgs(BaseModel):
    path: str | None = Field(
        default=None,
        description="Directory path to list. Defaults to working directory.",
    )
    show_hidden: bool = Field(
        default=False,
        description="If true, include hidden files and directories (names starting with '.').",
    )
