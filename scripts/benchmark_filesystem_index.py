#!/usr/bin/env python3
"""Build a disposable warm filesystem index and enforce search latency budgets.

This fixture measures the production SQLite schema and ``FilesystemIndex.search``
path without creating hundreds of thousands of real files.  Rows are inserted in
bulk only to make fixture construction practical; the FTS triggers, query code,
path scoping, and result materialization are the real implementation.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Iterator

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from app.services.filesystem.index import FilesystemIndex  # noqa: E402
from app.services.filesystem.roots import normalize_path_key  # noqa: E402

_INSERT_ENTRY = """
INSERT INTO filesystem_entries(
    path,path_key,parent_path,parent_key,root_id,name,kind,size,
    modified_at,hidden,extension,indexed_at
) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
"""


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entries", type=int, default=500_000)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=5_000)
    parser.add_argument("--match-every", type=int, default=10_000)
    parser.add_argument("--p95-budget-ms", type=float, default=100.0)
    parser.add_argument(
        "--db-path",
        type=Path,
        help="Keep the fixture at this explicit path instead of using a temporary directory.",
    )
    args = parser.parse_args()
    for name in ("entries", "samples", "batch_size", "match_every"):
        if getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be at least 1")
    if args.p95_budget_ms <= 0:
        parser.error("--p95-budget-ms must be positive")
    return args


def _entry_rows(
    fixture_root: Path,
    *,
    entries: int,
    match_every: int,
    now: float,
) -> Iterator[tuple[object, ...]]:
    directory_count = (entries + 999) // 1_000
    root = str(fixture_root)
    root_key = normalize_path_key(root)
    parent = str(fixture_root.parent)
    parent_key = normalize_path_key(parent)
    yield (
        root,
        root_key,
        parent,
        parent_key,
        "fixture-root",
        fixture_root.name,
        "directory",
        0,
        now,
        0,
        None,
        now,
    )
    for directory_index in range(directory_count):
        directory = fixture_root / f"project-{directory_index:06d}"
        path = str(directory)
        path_key = normalize_path_key(path)
        yield (
            path,
            path_key,
            root,
            root_key,
            "fixture-root",
            directory.name,
            "directory",
            0,
            now,
            0,
            None,
            now,
        )
    for index in range(entries):
        directory = fixture_root / f"project-{index // 1_000:06d}"
        suffix = "-needle" if index % match_every == 0 else ""
        name = f"file-{index:09d}{suffix}.txt"
        path = str(directory / name)
        parent = str(directory)
        path_key = normalize_path_key(path)
        parent_key = normalize_path_key(parent)
        yield (
            path,
            path_key,
            parent,
            parent_key,
            "fixture-root",
            name,
            "file",
            128,
            now,
            0,
            ".txt",
            now,
        )


def _build_fixture(
    index: FilesystemIndex,
    fixture_root: Path,
    *,
    entries: int,
    match_every: int,
    batch_size: int,
) -> float:
    started = time.perf_counter()
    index.initialize()
    now = time.time()
    with sqlite3.connect(index.path) as db:
        db.execute(
            """
            INSERT INTO filesystem_roots(
                id,label,path,category,priority,available,configured,updated_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                "fixture-root",
                "Benchmark fixture",
                str(fixture_root),
                "benchmark",
                1,
                1,
                1,
                now,
            ),
        )
        batch: list[tuple[object, ...]] = []
        for row in _entry_rows(
            fixture_root,
            entries=entries,
            match_every=match_every,
            now=now,
        ):
            batch.append(row)
            if len(batch) >= batch_size:
                db.executemany(_INSERT_ENTRY, batch)
                batch.clear()
        if batch:
            db.executemany(_INSERT_ENTRY, batch)
    return time.perf_counter() - started


def _measure(
    index: FilesystemIndex,
    *,
    samples: int,
    root: str | None,
) -> dict[str, float | int]:
    durations: list[float] = []
    match_count = 0
    for _ in range(samples):
        started = time.perf_counter()
        results = index.search("needle", limit=50, offset=0, root=root)
        durations.append((time.perf_counter() - started) * 1_000)
        match_count = len(results)
    ordered = sorted(durations)
    p95_index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
    return {
        "matches": match_count,
        "p50_ms": round(statistics.median(durations), 3),
        "p95_ms": round(ordered[p95_index], 3),
        "max_ms": round(max(durations), 3),
    }


def main() -> int:
    args = _arguments()
    if args.db_path:
        db_path = args.db_path.expanduser().resolve()
        if db_path.exists():
            raise SystemExit(f"Refusing to overwrite existing benchmark database: {db_path}")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        context = nullcontext(db_path.parent)
    else:
        context = tempfile.TemporaryDirectory(prefix="matrx-filesystem-benchmark-")

    with context as workspace:
        workspace_path = Path(workspace)
        db_path = args.db_path.expanduser().resolve() if args.db_path else workspace_path / "index.sqlite3"
        fixture_root = workspace_path / "fixture"
        index = FilesystemIndex(db_path)
        build_seconds = _build_fixture(
            index,
            fixture_root,
            entries=args.entries,
            match_every=args.match_every,
            batch_size=args.batch_size,
        )
        global_result = _measure(index, samples=args.samples, root=None)
        scoped_result = _measure(index, samples=args.samples, root=str(fixture_root))
        report = {
            "entries": args.entries,
            "samples": args.samples,
            "build_seconds": round(build_seconds, 3),
            "database_mib": round(db_path.stat().st_size / (1024 * 1024), 3),
            "p95_budget_ms": args.p95_budget_ms,
            "global_search": global_result,
            "scoped_search": scoped_result,
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return (
            0
            if global_result["p95_ms"] <= args.p95_budget_ms
            and scoped_result["p95_ms"] <= args.p95_budget_ms
            else 1
        )


if __name__ == "__main__":
    raise SystemExit(main())
