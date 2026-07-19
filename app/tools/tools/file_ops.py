"""File operation tools — Read, Write, Edit, Glob, Grep, Move, Copy, Delete,
Rename, Mkdir."""

from __future__ import annotations

import asyncio
import base64
import errno
import logging
import mimetypes
import os
import re
import shutil
import uuid
from pathlib import Path

from app.common.platform_ctx import CAPABILITIES
from app.services.access_health import Capability, get_access_health
from app.services.action_needed import filesystem_access_needed
from app.tools.session import ToolSession
from app.tools.types import ImageData, ToolResult, ToolResultType

logger = logging.getLogger(__name__)


def _note_io(path: str, capability: Capability, exc: BaseException | None = None) -> None:
    """Feed tool I/O outcomes into the canonical access-health evidence.

    A no-op for paths outside registered resource roots, and never raises.
    This is what keeps the health view honest: an agent actively writing the
    notes tree IS proof of access, and a permission failure there is evidence
    the banner can show — the two can no longer contradict each other.
    """
    try:
        get_access_health().note_external_io(path, capability, exc=exc)
    except Exception:
        logger.debug("access-health tool hook failed", exc_info=True)

MAX_READ_SIZE = 256_000
MAX_INLINE_OUTPUT = 60_000


def _io_error_result(
    *, path: str, operation: str, prefix: str, exc: OSError, feature: str = "Files"
) -> ToolResult:
    """Return an evidence-only access request for actual EACCES/EPERM errors."""

    denied = isinstance(exc, PermissionError) or exc.errno in (errno.EACCES, errno.EPERM)
    return ToolResult(
        type=ToolResultType.ERROR,
        output=f"{prefix}: {exc}",
        action_needed=(
            filesystem_access_needed(
                feature=feature,
                path=path,
                operation=operation,
                source="tool.file_ops",
            )
            if denied
            else None
        ),
    )


async def tool_filesystem_places(session: ToolSession) -> ToolResult:
    """Return semantic roots on the user's real local filesystem."""
    from app.services.filesystem import get_filesystem_service

    places = await get_filesystem_service().places()
    lines = [f"{place['label']}: {place['path']}" for place in places]
    return ToolResult(
        output="Local filesystem places:\n" + "\n".join(lines),
        metadata={"kind": "filesystem.places", "namespace": "host", "places": places},
    )


async def tool_find_paths(
    session: ToolSession,
    query: str,
    path: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> ToolResult:
    """Find local paths by name/path using the progressive metadata index."""
    from app.services.filesystem import get_filesystem_service

    root = session.resolve_path(path) if path else None
    try:
        page = await get_filesystem_service().find(
            query, root=root, cursor=cursor, limit=limit
        )
    except PermissionError as exc:
        return _io_error_result(
            path=str(root or "."),
            operation="search",
            prefix="Path search failed",
            exc=exc,
        )
    except (ValueError, asyncio.TimeoutError) as exc:
        return ToolResult(type=ToolResultType.ERROR, output=f"Path search failed: {exc}")
    data = page.to_dict()
    entries = data["entries"]
    if not entries:
        summary = f"No local paths found for {query!r}."
    else:
        summary = "\n".join(
            f"{entry['path']}{os.sep if entry['kind'] == 'dir' else ''}"
            for entry in entries
        )
    if data["next_cursor"]:
        summary += f"\nMore results available (cursor={data['next_cursor']})."
    if data["truncated"]:
        summary += (
            "\nThe bounded disk search stopped before every folder was examined; "
            "narrow the path or query to search more precisely."
        )
    if not data["index_complete"]:
        summary += (
            "\nThe local index is incomplete; indexing may be paused or some folders "
            "may be unavailable."
        )
    return ToolResult(output=summary, metadata=data)


async def tool_semantic_find_paths(
    session: ToolSession,
    query: str,
    limit: int = 20,
) -> ToolResult:
    """Find local files by meaning when optional semantic indexing is enabled."""
    from app.services.filesystem import get_filesystem_service

    try:
        data = await get_filesystem_service().semantic_find(query, limit=limit)
    except (ValueError, RuntimeError) as exc:
        return ToolResult(type=ToolResultType.ERROR, output=str(exc))
    results = data["results"]
    if not results:
        output = f"No semantic matches found for {query!r}."
    else:
        output = "\n".join(
            f"{result['score']:.3f}  {result['entry']['path']}" for result in results
        )
    return ToolResult(output=output, metadata=data)


async def tool_read(
    session: ToolSession,
    file_path: str,
    offset: int | None = None,
    limit: int | None = None,
) -> ToolResult:
    resolved = session.resolve_path(file_path)

    if not os.path.exists(resolved):
        return ToolResult(type=ToolResultType.ERROR, output=f"File not found: {resolved}")

    if os.path.isdir(resolved):
        return ToolResult(type=ToolResultType.ERROR, output=f"Path is a directory: {resolved}")

    # Pointer files under the Files root hydrate transparently (file sync
    # virtual mapping) — an empty placeholder must never read as content.
    from app.services.file_sync.hydration import ensure_hydrated

    hydrate_error = await ensure_hydrated(resolved)
    if hydrate_error:
        return ToolResult(type=ToolResultType.ERROR, output=hydrate_error)

    mime, _ = mimetypes.guess_type(resolved)
    if mime and mime.startswith("image/"):
        return _read_image(session, resolved, mime)

    # Office documents (.docx / .pptx / .xlsx) are OpenXML zips — reading them
    # as UTF-8 returns zip garbage. Route them through the canonical matrx-files
    # Office codec, which converts the bytes to AI-facing markdown (the same
    # converged shape a PDF produces). Mirrors tool_pdf_extract's bytes→codec.
    from matrx_files.specific_handlers.office import classify_office

    if classify_office(mime, resolved) is not None:
        return _read_office(session, resolved, mime)

    try:
        text = Path(resolved).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        _note_io(resolved, Capability.READ, e)
        return _io_error_result(path=resolved, operation="read", prefix="Cannot read file", exc=e)
    _note_io(resolved, Capability.READ)

    lines = text.splitlines(keepends=True)
    total = len(lines)

    start = (offset - 1) if offset and offset > 0 else 0
    end = (start + limit) if limit else total

    selected = lines[start:end]
    numbered = "".join(f"{start + i + 1:6d}|{line}" for i, line in enumerate(selected))

    if len(numbered) > MAX_READ_SIZE:
        numbered = numbered[:MAX_READ_SIZE] + "\n... [truncated]"

    session.mark_file_read(resolved)
    return ToolResult(output=numbered, metadata={"path": resolved, "total_lines": total})


def _read_office(session: ToolSession, path: str, mime: str | None) -> ToolResult:
    """Read a Microsoft Office document (.docx/.pptx/.xlsx) as markdown via the
    canonical matrx-files Office codec. Legacy binary formats (.doc/.ppt/.xls)
    raise a clear error unless LibreOffice is available on the host."""
    from matrx_files.specific_handlers.office import OfficeExtractionError, extract_office

    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        return _io_error_result(path=path, operation="read", prefix="Cannot read file", exc=e)

    try:
        extraction = extract_office(raw, mime_type=mime, file_name=path)
    except OfficeExtractionError as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"Cannot read Office document: {e}")

    markdown = extraction.markdown or ""
    if len(markdown) > MAX_READ_SIZE:
        markdown = markdown[:MAX_READ_SIZE] + "\n... [truncated]"

    session.mark_file_read(path)
    return ToolResult(
        output=markdown,
        metadata={
            "path": path,
            "office_kind": extraction.office_kind.value,
            "portions": len(extraction.portions),
            "warnings": extraction.warnings,
        },
    )


def _read_image(session: ToolSession, path: str, mime: str) -> ToolResult:
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        return _io_error_result(path=path, operation="read", prefix="Cannot read image", exc=e)

    session.mark_file_read(path)
    return ToolResult(
        output=f"Image: {path} ({len(data)} bytes)",
        image=ImageData(media_type=mime, base64_data=base64.b64encode(data).decode()),
    )


async def tool_write(
    session: ToolSession,
    file_path: str,
    content: str,
    create_directories: bool = True,
) -> ToolResult:
    resolved = session.resolve_path(file_path)

    if create_directories:
        os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)

    try:
        Path(resolved).write_text(content, encoding="utf-8")
    except OSError as e:
        _note_io(resolved, Capability.WRITE, e)
        return _io_error_result(path=resolved, operation="write", prefix="Cannot write file", exc=e)
    _note_io(resolved, Capability.WRITE)

    session.mark_file_read(resolved)
    return ToolResult(output=f"Wrote {len(content)} bytes to {resolved}")


async def tool_edit(
    session: ToolSession,
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> ToolResult:
    resolved = session.resolve_path(file_path)

    if not os.path.exists(resolved):
        return ToolResult(type=ToolResultType.ERROR, output=f"File not found: {resolved}")

    try:
        text = Path(resolved).read_text(encoding="utf-8")
    except OSError as e:
        return _io_error_result(path=resolved, operation="read", prefix="Cannot read file", exc=e)

    count = text.count(old_string)
    if count == 0:
        return ToolResult(type=ToolResultType.ERROR, output="old_string not found in file.")
    if count > 1 and not replace_all:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=(
                f"old_string found {count} times — must be unique. Add more "
                "context, or pass replace_all=true to replace every occurrence."
            ),
        )

    if replace_all:
        new_text = text.replace(old_string, new_string)
        replaced = count
    else:
        new_text = text.replace(old_string, new_string, 1)
        replaced = 1

    try:
        Path(resolved).write_text(new_text, encoding="utf-8")
    except OSError as e:
        _note_io(resolved, Capability.WRITE, e)
        return _io_error_result(path=resolved, operation="write", prefix="Cannot write file", exc=e)
    _note_io(resolved, Capability.WRITE)

    return ToolResult(
        output=f"Edited {resolved} ({replaced} replacement{'s' if replaced != 1 else ''})"
    )


def _remove_existing(path: str) -> None:
    """Clear a destination ahead of an overwrite-mode Move/Copy.

    Raises OSError on failure — callers surface it as the operation's error.
    """
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def _copy_path(source: str, destination: str) -> None:
    if os.path.isdir(source) and not os.path.islink(source):
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination, follow_symlinks=False)


def _replace_from_staged_copy(source: str, destination: str, *, remove_source: bool) -> str | None:
    """Copy fully to a sibling stage, then atomically exchange the destination.

    The existing destination is renamed to a backup before the stage is
    committed. Any commit failure restores it. This ensures overwrite never
    destroys the user's destination merely because the subsequent copy/move
    failed.
    """
    parent = os.path.dirname(destination) or "."
    name = os.path.basename(destination)
    token = uuid.uuid4().hex
    stage = os.path.join(parent, f".{name}.matrx-stage-{token}")
    backup = os.path.join(parent, f".{name}.matrx-backup-{token}")
    os.makedirs(parent, exist_ok=True)
    try:
        _copy_path(source, stage)
        os.replace(destination, backup)
        try:
            os.replace(stage, destination)
        except BaseException:
            os.replace(backup, destination)
            raise
        cleanup_warning: str | None = None
        try:
            _remove_existing(backup)
        except OSError as exc:
            cleanup_warning = f"Replacement succeeded, but backup cleanup failed at {backup}: {exc}"
        if remove_source:
            try:
                _remove_existing(source)
            except OSError as exc:
                cleanup_warning = (
                    f"Destination was committed safely, but the original remains at {source}: {exc}"
                )
        return cleanup_warning
    except BaseException:
        if os.path.lexists(stage):
            try:
                _remove_existing(stage)
            except OSError:
                pass
        raise


# ── File management: Move / Copy / Delete / Rename / Mkdir ───────────────────
#
# These are the elementary verbs an agent needs to actually MANAGE files.
# The dispatcher shipped for months with read/write/search only — an agent
# could not relocate, duplicate, remove, or rename anything, which is why
# "basic file management" felt broken. Delete is trash-first by design:
# a remote agent's mistake must be recoverable from the OS trash.


def _move_hydrated(src: str, dst: str, overwrite: bool) -> ToolResult:
    if os.path.isdir(dst) and not os.path.isdir(src):
        dst = os.path.join(dst, os.path.basename(src))
    if os.path.abspath(dst) == os.path.abspath(src):
        return ToolResult(type=ToolResultType.ERROR, output="Source and destination are the same path.")
    if os.path.exists(dst):
        if not overwrite:
            return ToolResult(
                type=ToolResultType.ERROR,
                output=f"Destination already exists: {dst}. Pass overwrite=true to replace it.",
            )
        try:
            warning = _replace_from_staged_copy(src, dst, remove_source=True)
        except OSError as exc:
            _note_io(dst, Capability.REPLACE, exc)
            return _io_error_result(
                path=dst, operation="replace", prefix="Cannot move", exc=exc
            )
        _note_io(dst, Capability.REPLACE)
        output = f"Moved {src} → {dst}"
        if warning:
            output += f"\nWarning: {warning}"
        return ToolResult(
            output=output,
            metadata={"source": src, "destination": dst, "warning": warning},
        )
    try:
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        shutil.move(src, dst)
    except OSError as exc:
        _note_io(dst, Capability.REPLACE, exc)
        return _io_error_result(
            path=dst, operation="move", prefix="Cannot move", exc=exc
        )
    _note_io(dst, Capability.REPLACE)
    return ToolResult(
        output=f"Moved {src} → {dst}",
        metadata={"source": src, "destination": dst},
    )


def _copy_hydrated(src: str, dst: str, overwrite: bool) -> ToolResult:
    if os.path.isdir(dst) and not os.path.isdir(src):
        dst = os.path.join(dst, os.path.basename(src))
    if os.path.abspath(dst) == os.path.abspath(src):
        return ToolResult(type=ToolResultType.ERROR, output="Source and destination are the same path.")
    if os.path.exists(dst):
        if not overwrite:
            return ToolResult(
                type=ToolResultType.ERROR,
                output=f"Destination already exists: {dst}. Pass overwrite=true to replace it.",
            )
        try:
            warning = _replace_from_staged_copy(src, dst, remove_source=False)
        except OSError as exc:
            _note_io(dst, Capability.REPLACE, exc)
            return _io_error_result(
                path=dst, operation="replace", prefix="Cannot copy", exc=exc
            )
        _note_io(dst, Capability.REPLACE)
        output = f"Copied {src} → {dst}"
        if warning:
            output += f"\nWarning: {warning}"
        return ToolResult(
            output=output,
            metadata={"source": src, "destination": dst, "warning": warning},
        )
    try:
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    except OSError as exc:
        _note_io(dst, Capability.WRITE, exc)
        return _io_error_result(
            path=dst, operation="write", prefix="Cannot copy", exc=exc
        )
    _note_io(dst, Capability.WRITE)
    return ToolResult(
        output=f"Copied {src} → {dst}",
        metadata={"source": src, "destination": dst},
    )


async def tool_move(
    session: ToolSession,
    source: str,
    destination: str,
    overwrite: bool = False,
) -> ToolResult:
    src = session.resolve_path(source)
    dst = session.resolve_path(destination)

    if not os.path.exists(src):
        return ToolResult(type=ToolResultType.ERROR, output=f"Source not found: {src}")

    from app.services.file_sync.hydration import run_tree_operation_hydrated

    result, hydrate_error = await run_tree_operation_hydrated(
        src, lambda: _move_hydrated(src, dst, overwrite)
    )
    if hydrate_error:
        return ToolResult(type=ToolResultType.ERROR, output=hydrate_error)
    assert result is not None
    return result


async def tool_copy(
    session: ToolSession,
    source: str,
    destination: str,
    overwrite: bool = False,
) -> ToolResult:
    src = session.resolve_path(source)
    dst = session.resolve_path(destination)

    if not os.path.exists(src):
        return ToolResult(type=ToolResultType.ERROR, output=f"Source not found: {src}")

    from app.services.file_sync.hydration import run_tree_operation_hydrated

    result, hydrate_error = await run_tree_operation_hydrated(
        src, lambda: _copy_hydrated(src, dst, overwrite)
    )
    if hydrate_error:
        return ToolResult(type=ToolResultType.ERROR, output=hydrate_error)
    assert result is not None
    return result


async def tool_delete(
    session: ToolSession,
    path: str,
    permanent: bool = False,
) -> ToolResult:
    resolved = session.resolve_path(path)

    if not os.path.exists(resolved):
        return ToolResult(type=ToolResultType.ERROR, output=f"Path not found: {resolved}")

    is_dir = os.path.isdir(resolved)
    kind = "directory" if is_dir else "file"

    if not permanent:
        try:
            from send2trash import send2trash

            send2trash(resolved)
            return ToolResult(
                output=f"Moved {kind} to trash: {resolved} (recoverable from the OS trash)",
                metadata={"path": resolved, "trashed": True},
            )
        except ImportError:
            return ToolResult(
                type=ToolResultType.ERROR,
                output=(
                    "Trash support unavailable on this system. Pass "
                    "permanent=true to delete permanently (NOT recoverable)."
                ),
            )
        except OSError as e:
            return ToolResult(
                type=ToolResultType.ERROR,
                output=(
                    f"Could not move to trash: {e}. Pass permanent=true to "
                    "delete permanently (NOT recoverable)."
                ),
            )

    try:
        if is_dir:
            shutil.rmtree(resolved)
        else:
            os.remove(resolved)
    except OSError as e:
        _note_io(resolved, Capability.DELETE, e)
        return _io_error_result(path=resolved, operation="delete", prefix="Cannot delete", exc=e)
    _note_io(resolved, Capability.DELETE)

    return ToolResult(
        output=f"Permanently deleted {kind}: {resolved}",
        metadata={"path": resolved, "trashed": False},
    )


async def tool_rename(
    session: ToolSession,
    path: str,
    new_name: str,
) -> ToolResult:
    resolved = session.resolve_path(path)

    if not os.path.exists(resolved):
        return ToolResult(type=ToolResultType.ERROR, output=f"Path not found: {resolved}")
    if os.sep in new_name or (os.altsep and os.altsep in new_name):
        return ToolResult(
            type=ToolResultType.ERROR,
            output="new_name must be a bare name, not a path. Use Move to relocate.",
        )
    if not new_name or new_name in (".", ".."):
        return ToolResult(type=ToolResultType.ERROR, output=f"Invalid new name: {new_name!r}")

    dst = os.path.join(os.path.dirname(resolved), new_name)
    # Allow pure case-changes on case-insensitive filesystems (macOS/Windows):
    # there os.path.exists(dst) is True because dst IS the source inode, yet
    # renaming File.txt → file.txt is the legitimate way to fix casing.
    same_inode = os.path.exists(dst) and os.path.samefile(resolved, dst)
    if os.path.exists(dst) and not same_inode:
        return ToolResult(type=ToolResultType.ERROR, output=f"A file or directory named {new_name!r} already exists here.")

    try:
        os.rename(resolved, dst)
    except OSError as e:
        _note_io(dst, Capability.REPLACE, e)
        return _io_error_result(path=dst, operation="rename", prefix="Cannot rename", exc=e)
    _note_io(dst, Capability.REPLACE)

    return ToolResult(output=f"Renamed {resolved} → {dst}", metadata={"source": resolved, "destination": dst})


async def tool_mkdir(
    session: ToolSession,
    path: str,
    parents: bool = True,
) -> ToolResult:
    resolved = session.resolve_path(path)

    if os.path.isdir(resolved):
        return ToolResult(output=f"Directory already exists: {resolved}", metadata={"path": resolved, "created": False})
    if os.path.exists(resolved):
        return ToolResult(type=ToolResultType.ERROR, output=f"A file already exists at: {resolved}")

    try:
        if parents:
            os.makedirs(resolved)
        else:
            os.mkdir(resolved)
    except OSError as e:
        _note_io(resolved, Capability.CREATE, e)
        return ToolResult(type=ToolResultType.ERROR, output=f"Cannot create directory: {e}")
    _note_io(resolved, Capability.CREATE)

    return ToolResult(output=f"Created directory: {resolved}", metadata={"path": resolved, "created": True})


TOOL_TIMEOUT_S = 15.0


async def tool_glob(
    session: ToolSession,
    pattern: str,
    path: str | None = None,
) -> ToolResult:
    root = session.resolve_path(path or ".")

    if not os.path.isdir(root):
        return ToolResult(type=ToolResultType.ERROR, output=f"Directory not found: {root}")

    try:
        if CAPABILITIES["has_fd"]:
            return await asyncio.wait_for(_glob_fd(root, pattern), timeout=TOOL_TIMEOUT_S)
        return await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, _glob_python, root, pattern),
            timeout=TOOL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return ToolResult(type=ToolResultType.ERROR, output=f"Glob timed out after {TOOL_TIMEOUT_S}s — try a more specific path.")


async def _glob_fd(root: str, pattern: str) -> ToolResult:
    proc = await asyncio.create_subprocess_exec(
        "fd", "--glob", pattern, "--type", "f", "--color", "never",
        cwd=root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    output = stdout.decode("utf-8", errors="replace").strip()
    if not output:
        return ToolResult(output="No matching files found.")
    lines = output.split("\n")
    return ToolResult(output="\n".join(lines), metadata={"count": len(lines)})


def _glob_python(root: str, pattern: str) -> ToolResult:
    if not pattern.startswith("**/"):
        pattern = f"**/{pattern}"

    matches = sorted(str(p.relative_to(root)) for p in Path(root).glob(pattern) if p.is_file())
    if not matches:
        return ToolResult(output="No matching files found.")
    return ToolResult(output="\n".join(matches), metadata={"count": len(matches)})


async def tool_grep(
    session: ToolSession,
    pattern: str,
    path: str | None = None,
    include: str | None = None,
    max_results: int = 100,
) -> ToolResult:
    root = session.resolve_path(path or ".")

    try:
        if CAPABILITIES["has_rg"]:
            return await asyncio.wait_for(_grep_rg(root, pattern, include, max_results), timeout=TOOL_TIMEOUT_S)
        return await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, _grep_python, root, pattern, include, max_results),
            timeout=TOOL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return ToolResult(type=ToolResultType.ERROR, output=f"Grep timed out after {TOOL_TIMEOUT_S}s — try a more specific path.")


async def _grep_rg(root: str, pattern: str, include: str | None, max_results: int) -> ToolResult:
    cmd = ["rg", "--no-heading", "--line-number", "--color", "never", "-m", str(max_results)]
    if include:
        cmd += ["--glob", include]
    cmd += [pattern, root]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    output = stdout.decode("utf-8", errors="replace").strip()

    if not output:
        return ToolResult(output="No matches found.")

    lines = output.split("\n")[:max_results]
    return ToolResult(output="\n".join(lines), metadata={"count": len(lines)})


def _grep_python(root: str, pattern: str, include: str | None, max_results: int) -> ToolResult:
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"Invalid regex: {e}")

    matches: list[str] = []
    root_path = Path(root)

    if root_path.is_file():
        # `path` may be a single file — Path.glob on a file yields nothing,
        # so the fallback always reported "No matches" where ripgrep matched.
        candidates = iter([root_path])
    else:
        glob_pattern = include or "**/*"
        # Recurse for bare patterns like "*.py" to match ripgrep's --glob
        # semantics (the old top-level-only glob diverged by environment).
        if include and "/" not in include and not include.startswith("**"):
            glob_pattern = f"**/{include}"
        candidates = root_path.glob(glob_pattern)

    for file_path in candidates:
        if not file_path.is_file():
            continue
        try:
            for i, line in enumerate(file_path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if regex.search(line):
                    rel = (
                        file_path.relative_to(root_path)
                        if root_path.is_dir()
                        else file_path.name
                    )
                    matches.append(f"{rel}:{i}:{line}")
                    if len(matches) >= max_results:
                        break
        except (OSError, UnicodeDecodeError):
            continue
        if len(matches) >= max_results:
            break

    if not matches:
        return ToolResult(output="No matches found.")
    return ToolResult(output="\n".join(matches), metadata={"count": len(matches)})
