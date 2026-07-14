"""File operation tools — Read, Write, Edit, Glob, Grep, Move, Copy, Delete,
Rename, Mkdir."""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import os
import re
import shutil
from pathlib import Path

from app.common.platform_ctx import CAPABILITIES
from app.tools.session import ToolSession
from app.tools.types import ImageData, ToolResult, ToolResultType

logger = logging.getLogger(__name__)

MAX_READ_SIZE = 256_000
MAX_INLINE_OUTPUT = 60_000


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

    mime, _ = mimetypes.guess_type(resolved)
    if mime and mime.startswith("image/"):
        return _read_image(session, resolved, mime)

    try:
        text = Path(resolved).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"Cannot read file: {e}")

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


def _read_image(session: ToolSession, path: str, mime: str) -> ToolResult:
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"Cannot read image: {e}")

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
        return ToolResult(type=ToolResultType.ERROR, output=f"Cannot write file: {e}")

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
        return ToolResult(type=ToolResultType.ERROR, output=f"Cannot read file: {e}")

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
        return ToolResult(type=ToolResultType.ERROR, output=f"Cannot write file: {e}")

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


# ── File management: Move / Copy / Delete / Rename / Mkdir ───────────────────
#
# These are the elementary verbs an agent needs to actually MANAGE files.
# The dispatcher shipped for months with read/write/search only — an agent
# could not relocate, duplicate, remove, or rename anything, which is why
# "basic file management" felt broken. Delete is trash-first by design:
# a remote agent's mistake must be recoverable from the OS trash.


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
    # Moving a file INTO an existing directory keeps its basename.
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
        # overwrite means REPLACE, not merge/nest: without this, moving a
        # directory onto an existing directory would nest it inside
        # (shutil.move semantics), silently doing the wrong thing.
        _remove_existing(dst)

    try:
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        shutil.move(src, dst)
    except OSError as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"Cannot move: {e}")

    return ToolResult(output=f"Moved {src} → {dst}", metadata={"source": src, "destination": dst})


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
        # overwrite means REPLACE, not merge: copytree(dirs_exist_ok=True)
        # would merge into the existing tree and leave stale files behind,
        # and dir-over-file would raise. Clear the destination first.
        _remove_existing(dst)

    try:
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    except OSError as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"Cannot copy: {e}")

    return ToolResult(output=f"Copied {src} → {dst}", metadata={"source": src, "destination": dst})


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
        return ToolResult(type=ToolResultType.ERROR, output=f"Cannot delete: {e}")

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
        return ToolResult(type=ToolResultType.ERROR, output=f"Cannot rename: {e}")

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
        return ToolResult(type=ToolResultType.ERROR, output=f"Cannot create directory: {e}")

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
