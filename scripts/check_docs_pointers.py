#!/usr/bin/env python3
"""Docs pointer guard — scans repo .md files for broken pointers.

Catches two classes of drift that compile cleanly and fail silently:

  (a) Relative links to in-repo files that don't exist (renamed/moved/deleted
      docs, typo'd paths).
  (b) Cross-repo pointers of the form:
        /Users/armanisadeghi/code/<repo>/<path>
        common-docs/<path>
      whose `common-docs/` path does not start with one of the five bundle
      roots: systems/, projects/, policies/, skills/, meta/.

The 2026-07-22 common-docs bundle restructure moved every doc under those five
roots and broke 33 flat pointers (e.g. `common-docs/app-config/FEATURE.md`
instead of `common-docs/systems/app-config/FEATURE.md`) with no build error,
no runtime error, and no signal until an agent went looking and found a 404
against a doc that used to exist. This script cannot verify a sibling repo's
filesystem (it isn't checked out here) or a common-docs file's real existence
(same reason) — it verifies SHAPE only: does the common-docs path start with a
known bundle root. That's exactly the check that would have caught the
incident, offline, with zero network access.

Usage:
    python scripts/check_docs_pointers.py            # advisory: exit 0, loud report
    python scripts/check_docs_pointers.py --strict    # exit 1 on any violation

Scope: docs/, root *.md, .matrx/, app/**/FEATURE.md (per the task; extend
SCAN_ROOTS if more areas should be covered). docs/official/** is scanned and
reported like everything else, but this script never edits it — official docs
are Arman-only; violations there are surfaced, not auto-fixed.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCAN_ROOTS = [
    REPO_ROOT / "docs",
    REPO_ROOT / ".matrx",
]
# Root-level *.md files (not recursive — avoid re-walking docs/.matrx via '.').
ROOT_MD_GLOB = "*.md"

# app/**/FEATURE.md specifically (per task scope), not all of app/**/*.md.
APP_FEATURE_GLOB = "app/**/FEATURE.md"

ALLOWED_COMMON_DOCS_ROOTS = (
    "systems/",
    "projects/",
    "policies/",
    "operations/",
    "inbox/",
    "meta/",
    "skills/",
    "workspace-root/",
)

# Markdown link pattern: [text](target) — we only care about the target.
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")

# Bare cross-repo path mentions outside markdown link syntax, e.g. inside
# backticks or plain prose: /Users/armanisadeghi/code/<repo>/<path>
ABS_CROSS_REPO_RE = re.compile(r"/Users/armanisadeghi/code/([A-Za-z0-9_.-]+)/([A-Za-z0-9_./-]+)")

# Bare `common-docs/<path>` mentions (backticked or plain prose, not inside a
# markdown link — those are caught via MD_LINK_RE targets too, this is the
# catch-all for prose mentions like `common-docs/systems/x/FEATURE.md`).
BARE_COMMON_DOCS_RE = re.compile(r"(?<![\w/-])common-docs/([A-Za-z0-9_./-]+)")


@dataclass
class Violation:
    file: Path
    line_no: int
    kind: str  # "broken_relative_link" | "flat_common_docs_pointer"
    detail: str
    is_official: bool = field(default=False)


def iter_scan_files() -> list[Path]:
    files: set[Path] = set()
    for root in SCAN_ROOTS:
        if root.exists():
            files.update(root.rglob("*.md"))
    files.update(REPO_ROOT.glob(ROOT_MD_GLOB))
    files.update(REPO_ROOT.glob(APP_FEATURE_GLOB))
    return sorted(files)


def is_official(path: Path) -> bool:
    parts = path.relative_to(REPO_ROOT).parts
    return "official" in parts


def is_relative_link(target: str) -> bool:
    if not target:
        return False
    if target.startswith(("http://", "https://", "mailto:", "file://", "#")):
        return False
    if target.startswith("/Users/") or target.startswith("/home/"):
        return False
    if target.startswith("common-docs/"):
        return False
    # Windows/unix absolute paths outside the repo — skip, not a relative link.
    if target.startswith("/") and not (REPO_ROOT / target.lstrip("/")).exists() is None:
        pass
    return True


def strip_anchor(target: str) -> str:
    return target.split("#", 1)[0].strip()


def check_relative_link(md_file: Path, target: str) -> str | None:
    """Return an error detail string if the relative link target is missing."""
    clean = strip_anchor(target)
    if not clean:
        return None  # pure anchor link within the same doc
    # Skip absolute-repo paths like /Users/... or common-docs/... (handled elsewhere).
    if clean.startswith(("/Users/", "/home/", "common-docs/")):
        return None
    if clean.startswith("http"):
        return None
    resolved = (md_file.parent / clean).resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError:
        # Points outside the repo entirely (e.g. sibling repo via ../../other-repo) —
        # can't verify a sibling repo's filesystem from here; skip.
        return None
    if not resolved.exists():
        return f"relative link target does not exist: {clean} -> {resolved}"
    return None


def check_common_docs_shape(common_docs_path: str) -> str | None:
    normalized = common_docs_path.lstrip("/")
    if not normalized.startswith(ALLOWED_COMMON_DOCS_ROOTS):
        return (
            f"common-docs pointer does not start with a known bundle root "
            f"({'/'.join(r.rstrip('/') for r in ALLOWED_COMMON_DOCS_ROOTS)}): "
            f"common-docs/{normalized}"
        )
    return None


def scan_file(md_file: Path) -> list[Violation]:
    violations: list[Violation] = []
    official = is_official(md_file)
    try:
        text = md_file.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return violations

    for line_no, line in enumerate(text.splitlines(), start=1):
        # (a) markdown-link relative targets
        for _label, target in MD_LINK_RE.findall(line):
            target = target.strip()
            if is_relative_link(target):
                detail = check_relative_link(md_file, target)
                if detail:
                    violations.append(
                        Violation(md_file, line_no, "broken_relative_link", detail, official)
                    )

        # (b) cross-repo pointers: /Users/armanisadeghi/code/<repo>/common-docs/...
        #     or common-docs/... directly, in link targets OR bare prose/backticks.
        for m in ABS_CROSS_REPO_RE.finditer(line):
            repo, rest = m.group(1), m.group(2)
            if repo == "common-docs":
                detail = check_common_docs_shape(rest)
                if detail:
                    violations.append(
                        Violation(md_file, line_no, "flat_common_docs_pointer", detail, official)
                    )
            elif rest.startswith("common-docs/"):
                sub = rest[len("common-docs/"):]
                detail = check_common_docs_shape(sub)
                if detail:
                    violations.append(
                        Violation(md_file, line_no, "flat_common_docs_pointer", detail, official)
                    )

        for m in BARE_COMMON_DOCS_RE.finditer(line):
            sub = m.group(1)
            detail = check_common_docs_shape(sub)
            if detail:
                violations.append(
                    Violation(md_file, line_no, "flat_common_docs_pointer", detail, official)
                )

    return violations


def main() -> int:
    strict = "--strict" in sys.argv[1:]

    all_violations: list[Violation] = []
    for md_file in iter_scan_files():
        all_violations.extend(scan_file(md_file))

    # De-duplicate (bare-prose regex + link regex can both catch the same line).
    seen: set[tuple[Path, int, str, str]] = set()
    deduped: list[Violation] = []
    for v in all_violations:
        key = (v.file, v.line_no, v.kind, v.detail)
        if key not in seen:
            seen.add(key)
            deduped.append(v)
    all_violations = deduped

    if not all_violations:
        print("[check_docs_pointers] OK — no broken relative links or flat common-docs pointers found.")
        return 0

    fixable = [v for v in all_violations if not v.is_official]
    official_only = [v for v in all_violations if v.is_official]

    print("=" * 78)
    print(f"[check_docs_pointers] {len(all_violations)} pointer issue(s) found")
    print("=" * 78)

    if fixable:
        print(f"\n-- {len(fixable)} in agent-fixable docs --\n")
        for v in fixable:
            rel = v.file.relative_to(REPO_ROOT)
            print(f"  {rel}:{v.line_no}  [{v.kind}]")
            print(f"    {v.detail}")

    if official_only:
        print(f"\n-- {len(official_only)} in docs/official/** (Arman-only — report, do not auto-fix) --\n")
        for v in official_only:
            rel = v.file.relative_to(REPO_ROOT)
            print(f"  {rel}:{v.line_no}  [{v.kind}]")
            print(f"    {v.detail}")

    print()
    if strict:
        print("[check_docs_pointers] --strict: failing.")
        return 1

    print("[check_docs_pointers] advisory mode: not failing the build. Re-run with --strict to enforce.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
