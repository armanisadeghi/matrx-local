"""Bounded, privacy-minimal discovery of local coding workspaces.

The desktop folder picker supplies a parent directory.  This module never
reads project files: it inspects directory entry names only, labels likely
projects from Git metadata and common manifests, and returns a directory tree
for the local UI.  Symlinked directories are deliberately excluded so a scan
cannot escape the root the user selected.
"""

from __future__ import annotations

import os
from collections import deque
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


MAX_DISCOVERY_DEPTH = 8
MAX_DISCOVERY_DIRECTORIES = 5_000
MAX_DISCOVERY_ENTRIES = 20_000

_SKIPPED_DIRECTORY_NAMES = frozenset(
    {
        "__pycache__",
        "build",
        "coverage",
        "deriveddata",
        "dist",
        "env",
        "node_modules",
        "out",
        "pods",
        "target",
        "temp",
        "tmp",
        "vendor",
        "venv",
    }
)

_EXACT_MANIFEST_KINDS: dict[str, str] = {
    "CMakeLists.txt": "cmake",
    "Cargo.toml": "rust",
    "Gemfile": "ruby",
    "Makefile": "make",
    "Package.swift": "swift",
    "build.gradle": "java",
    "build.gradle.kts": "kotlin",
    "composer.json": "php",
    "deno.json": "javascript",
    "deno.jsonc": "javascript",
    "go.mod": "go",
    "mix.exs": "elixir",
    "package.json": "javascript",
    "pnpm-workspace.yaml": "javascript",
    "pom.xml": "java",
    "pubspec.yaml": "dart",
    "pyproject.toml": "python",
    "setup.cfg": "python",
    "setup.py": "python",
}

_SUFFIX_MANIFEST_KINDS: tuple[tuple[str, str], ...] = (
    (".csproj", "dotnet"),
    (".fsproj", "dotnet"),
    (".sln", "dotnet"),
)


class WorkspaceDiscoveryNode(BaseModel):
    """One directory in the local-only workspace tree."""

    model_config = ConfigDict(extra="forbid")

    path: str
    name: str
    kind: Literal["directory", "git_repository", "project"] = "directory"
    project_kinds: list[str] = Field(default_factory=list)
    children: list["WorkspaceDiscoveryNode"] = Field(default_factory=list)
    truncated: bool = False


class WorkspaceDiscoveryResponse(BaseModel):
    """Stable response contract consumed by the desktop tree picker."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    parent: str | None
    workspace_roots: list[str]
    roots: list[WorkspaceDiscoveryNode]
    approved_folders: list[str]
    project_count: int = Field(ge=0)
    directory_count: int = Field(ge=0)
    scanned_entries: int = Field(ge=0)
    truncated: bool
    skipped: int = Field(ge=0)


def _manifest_kind(name: str) -> str | None:
    exact = _EXACT_MANIFEST_KINDS.get(name)
    if exact is not None:
        return exact
    folded = name.casefold()
    for suffix, kind in _SUFFIX_MANIFEST_KINDS:
        if folded.endswith(suffix):
            return kind
    return None


def _skip_directory(name: str) -> bool:
    return name.startswith(".") or name.casefold() in _SKIPPED_DIRECTORY_NAMES


def _prune_non_project_branches(node: WorkspaceDiscoveryNode, *, root: bool) -> None:
    """Keep project ancestry and every first-level choice, not source clutter."""
    kept: list[WorkspaceDiscoveryNode] = []
    for child in node.children:
        _prune_non_project_branches(child, root=False)
        if root or child.kind != "directory" or child.children or child.truncated:
            kept.append(child)
    node.children = kept


def discover_workspace_tree(
    roots: list[Path],
    *,
    parent: str | None,
    workspace_roots: list[str],
    approved_folders: list[str],
    initial_skipped: int = 0,
) -> WorkspaceDiscoveryResponse:
    """Inspect directory names breadth-first and return a bounded tree.

    ``roots`` and ``parent`` must already be resolved and security-validated
    by ``LocalClaudeRuntime``.  Breadth-first traversal is intentional: a huge
    monorepo cannot consume the entire budget before its sibling repositories
    are visible.
    """

    nodes = [
        WorkspaceDiscoveryNode(path=str(root), name=root.name or str(root))
        for root in roots
    ]
    queue: deque[tuple[Path, WorkspaceDiscoveryNode, int]] = deque(
        (root, node, 0) for root, node in zip(roots, nodes, strict=True)
    )
    directory_count = len(nodes)
    project_count = 0
    scanned_entries = 0
    skipped = initial_skipped
    truncated = False

    while queue:
        path, node, depth = queue.popleft()
        remaining_entries = MAX_DISCOVERY_ENTRIES - scanned_entries
        if remaining_entries <= 0:
            node.truncated = True
            truncated = True
            for root_node in nodes:
                root_node.truncated = True
            break

        entries: list[os.DirEntry[str]] = []
        hit_entry_limit = False
        try:
            with os.scandir(path) as iterator:
                for entry in iterator:
                    if len(entries) >= remaining_entries:
                        hit_entry_limit = True
                        break
                    entries.append(entry)
        except OSError:
            skipped += 1
            continue

        scanned_entries += len(entries)
        if hit_entry_limit:
            node.truncated = True
            truncated = True

        project_kinds: set[str] = set()
        child_paths: list[Path] = []
        for entry in entries:
            try:
                if entry.is_symlink():
                    skipped += 1
                    continue
                if entry.name == ".git" and (
                    entry.is_dir(follow_symlinks=False)
                    or entry.is_file(follow_symlinks=False)
                ):
                    project_kinds.add("git")
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if _skip_directory(entry.name):
                        skipped += 1
                    else:
                        child_paths.append(Path(entry.path))
                    continue
                if entry.is_file(follow_symlinks=False):
                    kind = _manifest_kind(entry.name)
                    if kind is not None:
                        project_kinds.add(kind)
            except OSError:
                # A concurrently removed or permission-changed entry is a
                # skipped observation, never a reason to fail the whole tree.
                skipped += 1

        node.project_kinds = sorted(project_kinds)
        if "git" in project_kinds:
            node.kind = "git_repository"
        elif project_kinds:
            node.kind = "project"
        if project_kinds:
            project_count += 1

        child_paths.sort(key=lambda item: (item.name.casefold(), item.name))
        if depth >= MAX_DISCOVERY_DEPTH:
            if child_paths:
                node.truncated = True
                truncated = True
                skipped += len(child_paths)
            continue

        for child_path in child_paths:
            if directory_count >= MAX_DISCOVERY_DIRECTORIES:
                node.truncated = True
                truncated = True
                skipped += 1
                continue
            # The DirEntry was non-symlinked. Resolve again before publishing
            # the path to close the rename/symlink race between scandir and
            # traversal. A changed or escaping path is simply omitted.
            try:
                resolved_child = child_path.resolve(strict=True)
                resolved_child.relative_to(path.resolve(strict=True))
            except (OSError, ValueError):
                skipped += 1
                continue
            child = WorkspaceDiscoveryNode(
                path=str(resolved_child), name=resolved_child.name
            )
            node.children.append(child)
            directory_count += 1
            queue.append((resolved_child, child, depth + 1))

    # A project chooser needs the hierarchy leading to discovered projects,
    # not thousands of source/package directories. Preserve every first-level
    # folder so the user can still approve a conventional top-level project
    # that has no recognized manifest; below that, retain project-bearing or
    # explicitly truncated branches only. The native picker remains the door
    # for any arbitrary directory.
    for node in nodes:
        _prune_non_project_branches(node, root=True)

    return WorkspaceDiscoveryResponse(
        parent=parent,
        workspace_roots=workspace_roots,
        roots=nodes,
        approved_folders=approved_folders,
        project_count=project_count,
        directory_count=directory_count,
        scanned_entries=scanned_entries,
        truncated=truncated,
        skipped=skipped,
    )


__all__ = [
    "WorkspaceDiscoveryNode",
    "WorkspaceDiscoveryResponse",
    "discover_workspace_tree",
]
