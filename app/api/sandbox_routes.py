"""Orchestrator-compatible sandbox dispatch routes for matrx-local.

This module exposes the same HTTP surface that the matrx-sandbox orchestrator
exposes for ``{base_url}/fs/...`` and ``{base_url}/exec`` — so the existing
matrx-ai ``_sandbox_proxy`` (which knows how to talk to an orchestrated
sandbox) works against the user's local PC unchanged.

Wire path: matrx-ai tool → aidream ``/api/local-proxy/{instance}/{path}``
reverse-proxy → this engine's ``/sandbox/{path}`` route.

Auth: every request must carry a Supabase JWT in ``Authorization: Bearer …``;
aidream's reverse-proxy puts the user's session token there before
forwarding. We validate via the existing ``extension_auth.validate_extension_principal``
dependency — same posture as the extension surface (JWKS when available,
loopback-presence fallback for HS256 / no-JWKS configurations).

Why we implement these directly instead of calling the existing tools:
the tools in ``app/tools/`` are shaped for AI consumption (human-readable
``output: str``). The orchestrator contract is structured RPC
({entries: [...]}, {exit_code, stdout, stderr, cwd}). Translating string
output back into structured shape is fragile; using ``os`` / ``subprocess``
directly gives us a clean 1:1 match with the orchestrator's wire shape.
"""

from __future__ import annotations

import asyncio
import base64
import os
import shutil
import stat as stat_module
import subprocess
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.extension_auth import ExtensionPrincipal, validate_extension_principal
from app.common.system_logger import get_logger

logger = get_logger()

router = APIRouter()

# ── Response shapes ────────────────────────────────────────────────────────


class FsEntry(BaseModel):
    """One entry in a directory listing — mirrors the orchestrator's
    ``matrx_agent`` daemon shape consumed by ``_sandbox_proxy.fs_list``."""

    model_config = ConfigDict(extra="forbid")

    name: str
    path: str
    kind: Literal["file", "dir", "symlink", "other"]
    size: int = 0
    mtime: float | None = None
    mode: int | None = None
    target: str | None = None


class FsListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[FsEntry]


class FsStatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    kind: Literal["file", "dir", "symlink", "other"]
    size: int = 0
    mtime: float | None = None
    mode: int | None = None
    target: str | None = None
    exists: bool = True


class FsWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    content: str
    encoding: Literal["utf8", "base64"] = "utf8"
    create_parents: bool = True
    mode: int | None = None


class FsWriteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    bytes_written: int


class FsMkdirRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    parents: bool = True


class FsMkdirResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    created: bool


class FsPatchEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    old_text: str
    new_text: str
    replace_all: bool = False


class FsPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    edits: list[FsPatchEdit]
    create_if_missing: bool = False


class FsPatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    applied: int
    """Number of edits that matched and were applied."""


class SearchPathsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str
    cwd: str
    max_results: int = 100


class SearchPathsHit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    kind: Literal["file", "dir", "symlink", "other"]


class SearchPathsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[SearchPathsHit]


class SearchContentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    cwd: str
    regex: bool = True
    case_sensitive: bool = False
    max_results: int = 100


class SearchContentHit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    line: int
    column: int = 0
    snippet: str


class SearchContentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[SearchContentHit]


class ExecRequest(BaseModel):
    model_config = ConfigDict(extra="allow")  # cwd/env/stdin/timeout/user
    command: str
    cwd: str | None = None
    env: dict[str, str] | None = None
    stdin: str | None = None
    timeout: int = 60
    user: str | None = "agent"  # ignored on a user PC — already running as user


class ExecResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exit_code: int
    stdout: str
    stderr: str
    cwd: str


# ── Helpers ────────────────────────────────────────────────────────────────


def _kind_for(path: Path) -> Literal["file", "dir", "symlink", "other"]:
    try:
        st = path.lstat()
    except FileNotFoundError:
        return "other"
    if stat_module.S_ISLNK(st.st_mode):
        return "symlink"
    if stat_module.S_ISDIR(st.st_mode):
        return "dir"
    if stat_module.S_ISREG(st.st_mode):
        return "file"
    return "other"


def _entry_from_path(path: Path) -> FsEntry:
    try:
        st = path.lstat()
        size = st.st_size if stat_module.S_ISREG(st.st_mode) else 0
        mtime = st.st_mtime
        mode = st.st_mode
    except FileNotFoundError:
        size = 0
        mtime = None
        mode = None
    target: str | None = None
    kind = _kind_for(path)
    if kind == "symlink":
        try:
            target = os.readlink(path)
        except OSError:
            target = None
    return FsEntry(
        name=path.name,
        path=str(path),
        kind=kind,
        size=size,
        mtime=mtime,
        mode=mode,
        target=target,
    )


def _resolve_path(raw: str) -> Path:
    """Expand and normalize a path. Allows ``~`` and resolves to absolute.
    No sandbox-style chroot — the agent has full access to the user's PC
    by design (the user picked their own machine as the target)."""
    expanded = os.path.expanduser(raw)
    return Path(expanded).resolve()


# ── /sandbox/fs/* ──────────────────────────────────────────────────────────


@router.get("/fs/list", response_model=FsListResponse)
async def fs_list(
    path: str = Query(...),
    recursive: bool = Query(False),
    depth: int = Query(1, ge=1, le=10),
    _: ExtensionPrincipal = Depends(validate_extension_principal),
) -> FsListResponse:
    root = _resolve_path(path)
    if not root.exists():
        raise HTTPException(status_code=404, detail="path_not_found")
    if not root.is_dir():
        raise HTTPException(status_code=400, detail="not_a_directory")

    entries: list[FsEntry] = []

    def _walk(d: Path, remaining: int) -> None:
        try:
            children = sorted(d.iterdir())
        except PermissionError:
            return
        for child in children:
            entries.append(_entry_from_path(child))
            if recursive and remaining > 1 and child.is_dir() and not child.is_symlink():
                _walk(child, remaining - 1)

    _walk(root, depth)
    return FsListResponse(entries=entries)


@router.get("/fs/stat", response_model=FsStatResponse)
async def fs_stat(
    path: str = Query(...),
    _: ExtensionPrincipal = Depends(validate_extension_principal),
) -> FsStatResponse:
    p = _resolve_path(path)
    if not p.exists() and not p.is_symlink():
        return FsStatResponse(
            path=str(p), kind="other", exists=False, size=0,
        )
    entry = _entry_from_path(p)
    return FsStatResponse(
        path=entry.path,
        kind=entry.kind,
        size=entry.size,
        mtime=entry.mtime,
        mode=entry.mode,
        target=entry.target,
        exists=True,
    )


@router.get("/fs/read")
async def fs_read(
    path: str = Query(...),
    encoding: Literal["utf8", "base64"] = Query("utf8"),
    _: ExtensionPrincipal = Depends(validate_extension_principal),
) -> Response:
    """Return the file's contents as the response body — matches the
    orchestrator's plain-text/base64 return shape (matrx-ai's
    ``_sandbox_proxy.fs_read`` reads ``resp.text``)."""
    p = _resolve_path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="file_not_found")
    if not p.is_file():
        raise HTTPException(status_code=400, detail="not_a_file")
    # Off-thread: reading + base64-encoding an arbitrary-size file blocked
    # every other engine request for the duration.
    data = await asyncio.to_thread(p.read_bytes)
    if encoding == "base64":
        body = base64.b64encode(data).decode("ascii")
        return Response(content=body, media_type="text/plain")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=415,
            detail="binary_file_use_base64",
        ) from exc
    return Response(content=text, media_type="text/plain")


@router.put("/fs/write", response_model=FsWriteResponse)
async def fs_write(
    payload: FsWriteRequest,
    _: ExtensionPrincipal = Depends(validate_extension_principal),
) -> FsWriteResponse:
    p = _resolve_path(payload.path)
    if payload.create_parents:
        p.parent.mkdir(parents=True, exist_ok=True)
    if payload.encoding == "base64":
        data = base64.b64decode(payload.content)
    else:
        data = payload.content.encode("utf-8")
    p.write_bytes(data)
    if payload.mode is not None:
        try:
            p.chmod(payload.mode)
        except PermissionError:
            pass
    return FsWriteResponse(path=str(p), bytes_written=len(data))


@router.post("/fs/mkdir", response_model=FsMkdirResponse)
async def fs_mkdir(
    payload: FsMkdirRequest,
    _: ExtensionPrincipal = Depends(validate_extension_principal),
) -> FsMkdirResponse:
    p = _resolve_path(payload.path)
    created = not p.exists()
    p.mkdir(parents=payload.parents, exist_ok=True)
    return FsMkdirResponse(path=str(p), created=created)


@router.post("/fs/patch", response_model=FsPatchResponse)
async def fs_patch(
    payload: FsPatchRequest,
    _: ExtensionPrincipal = Depends(validate_extension_principal),
) -> FsPatchResponse:
    p = _resolve_path(payload.path)
    if not p.exists():
        if not payload.create_if_missing:
            raise HTTPException(status_code=404, detail="file_not_found")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    text = p.read_text(encoding="utf-8")
    applied = 0
    for edit in payload.edits:
        if edit.old_text not in text:
            continue
        if edit.replace_all:
            count = text.count(edit.old_text)
            text = text.replace(edit.old_text, edit.new_text)
            applied += count
        else:
            text = text.replace(edit.old_text, edit.new_text, 1)
            applied += 1
    p.write_text(text, encoding="utf-8")
    return FsPatchResponse(path=str(p), applied=applied)


# ── /sandbox/search/* ──────────────────────────────────────────────────────


@router.post("/search/paths", response_model=SearchPathsResponse)
async def search_paths(
    payload: SearchPathsRequest,
    _: ExtensionPrincipal = Depends(validate_extension_principal),
) -> SearchPathsResponse:
    root = _resolve_path(payload.cwd)
    if not root.exists() or not root.is_dir():
        return SearchPathsResponse(results=[])
    def _walk() -> list[SearchPathsHit]:
        found: list[SearchPathsHit] = []
        for match in root.rglob(payload.pattern):
            if len(found) >= payload.max_results:
                break
            found.append(SearchPathsHit(path=str(match), kind=_kind_for(match)))
        return found

    # Off-thread: rglob over e.g. the user's home dir froze the event loop.
    hits = await asyncio.to_thread(_walk)
    return SearchPathsResponse(results=hits)


@router.post("/search/content", response_model=SearchContentResponse)
async def search_content(
    payload: SearchContentRequest,
    _: ExtensionPrincipal = Depends(validate_extension_principal),
) -> SearchContentResponse:
    # Prefer ripgrep when available; fall back to grep, then Python.
    rg = shutil.which("rg")
    grep = shutil.which("grep")
    root = _resolve_path(payload.cwd)
    if not root.exists() or not root.is_dir():
        return SearchContentResponse(results=[])

    if rg is not None:
        cmd = [rg, "--no-heading", "--line-number", "--column", "--color", "never"]
        if not payload.case_sensitive:
            cmd.append("-i")
        if not payload.regex:
            cmd.append("-F")
        cmd.extend(["--max-count", str(payload.max_results), payload.query, str(root)])
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, _stderr_b = await proc.communicate()
        except OSError:
            stdout_b = b""
        hits: list[SearchContentHit] = []
        for raw_line in stdout_b.decode("utf-8", errors="replace").splitlines():
            if len(hits) >= payload.max_results:
                break
            # rg format: path:line:col:snippet
            parts = raw_line.split(":", 3)
            if len(parts) < 4:
                continue
            try:
                hits.append(
                    SearchContentHit(
                        path=parts[0],
                        line=int(parts[1]),
                        column=int(parts[2]),
                        snippet=parts[3],
                    )
                )
            except ValueError:
                continue
        return SearchContentResponse(results=hits)

    if grep is not None:
        flags = ["-rn"]
        if not payload.case_sensitive:
            flags.append("-i")
        if not payload.regex:
            flags.append("-F")
        try:
            proc = await asyncio.create_subprocess_exec(
                grep, *flags, payload.query, str(root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout_b, _ = await proc.communicate()
        except OSError:
            stdout_b = b""
        hits = []
        for raw_line in stdout_b.decode("utf-8", errors="replace").splitlines():
            if len(hits) >= payload.max_results:
                break
            parts = raw_line.split(":", 2)
            if len(parts) < 3:
                continue
            try:
                hits.append(
                    SearchContentHit(
                        path=parts[0],
                        line=int(parts[1]),
                        column=0,
                        snippet=parts[2],
                    )
                )
            except ValueError:
                continue
        return SearchContentResponse(results=hits)

    # Pure-Python fallback (slow but always works).
    import re

    pattern = payload.query if payload.regex else re.escape(payload.query)
    flags_re = 0 if payload.case_sensitive else re.IGNORECASE
    try:
        regex = re.compile(pattern, flags_re)
    except re.error:
        return SearchContentResponse(results=[])
    def _scan() -> list[SearchContentHit]:
        found: list[SearchContentHit] = []
        for filepath in root.rglob("*"):
            if len(found) >= payload.max_results:
                break
            if not filepath.is_file():
                continue
            try:
                for i, line in enumerate(
                    filepath.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                ):
                    m = regex.search(line)
                    if m:
                        found.append(
                            SearchContentHit(
                                path=str(filepath),
                                line=i,
                                column=m.start(),
                                snippet=line,
                            )
                        )
                        if len(found) >= payload.max_results:
                            break
            except (OSError, UnicodeDecodeError):
                continue
        return found

    # Off-thread: the pure-Python content scan walks whole trees.
    hits = await asyncio.to_thread(_scan)
    return SearchContentResponse(results=hits)


# ── /sandbox/exec ──────────────────────────────────────────────────────────


@router.post("/exec", response_model=ExecResponse)
async def exec_command(
    payload: ExecRequest,
    _: ExtensionPrincipal = Depends(validate_extension_principal),
) -> ExecResponse:
    cwd = _resolve_path(payload.cwd) if payload.cwd else Path.cwd()
    if not cwd.exists():
        raise HTTPException(status_code=400, detail="cwd_not_found")

    env = os.environ.copy()
    if payload.env:
        env.update(payload.env)

    timeout_seconds = max(1, min(payload.timeout, 600))

    try:
        # asyncio's subprocess gives us a non-blocking wait + timeout.
        proc = await asyncio.create_subprocess_shell(
            payload.command,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if payload.stdin is not None else None,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(
                    input=payload.stdin.encode("utf-8") if payload.stdin else None
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            stdout_b, stderr_b = await proc.communicate()
            return ExecResponse(
                exit_code=124,
                stdout=stdout_b.decode("utf-8", errors="replace"),
                stderr=stderr_b.decode("utf-8", errors="replace")
                + f"\n[matrx-local] command timed out after {timeout_seconds}s",
                cwd=str(cwd),
            )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"exec_failed: {exc}") from exc

    return ExecResponse(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout_b.decode("utf-8", errors="replace"),
        stderr=stderr_b.decode("utf-8", errors="replace"),
        cwd=str(cwd),
    )


__all__ = ["router"]
