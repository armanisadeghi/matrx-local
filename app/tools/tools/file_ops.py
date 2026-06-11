"""File operation tools — Read, Write, Edit, Glob, Grep."""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import os
import re
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
    if count > 1:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=f"old_string found {count} times — must be unique. Add more context.",
        )

    new_text = text.replace(old_string, new_string, 1)

    try:
        Path(resolved).write_text(new_text, encoding="utf-8")
    except OSError as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"Cannot write file: {e}")

    return ToolResult(output=f"Edited {resolved}")


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
