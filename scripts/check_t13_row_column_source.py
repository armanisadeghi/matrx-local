#!/usr/bin/env python3
"""T-13 source ratchet — no new code reference to the retiring row column (access ladder).

Access ladder T-13 (common-docs/projects/access-ladder/t13/PLAN.md §2.5c) retires the row column
into ``shown_to`` (a list filter) and ``published_to_web`` (the only anonymous lane). Every code
reference to the old column is a reader or writer phase 5 converts; this guard makes sure the set
only shrinks while that happens. The same guard, same rule and same baseline shape live in
aidream, matrx-frontend, matrx-extend and matrx-local.

WHAT IT COUNTS: per file, the lines naming the column as a whole word (lower case — the column,
the ``platform.<word>`` enum, a ``.eq("<word>", …)`` filter, a model field, a SQL predicate), in
tracked and untracked-but-not-ignored code files (py, ts, tsx, js, jsx, mjs, cjs, sql, rs, svelte,
vue). NOT counted, because they are a different word: CSS/DOM visibility (``style.<word>``,
``<word>: hidden|visible|collapse…``), the SEO ``ai_<word>`` feature, ``document.<word>…``.
Skipped paths: migrations (the database guard ``t13_no_new_row_column_reader`` owns what lands in
the database), generated types and build output.

It FAILS (exit 1) when any file holds more such lines than ``scripts/t13_row_column_source_baseline.json``
allows (a new file's allowance is 0), naming every line. The baseline was taken 2026-09-28 from
this detector over the tree and cross-checked against the T-13 census
(common-docs/projects/access-ladder/t13/census/). It only shrinks: ``--shrink-baseline`` lowers
counts to what is live and drops converted files; it can never raise one.

WHAT IT CANNOT SEE: a reference spelled without the word (a constant holding it, a dynamic key).
Green means no new literal reference, not that no new reader exists.

``--self-test`` proves it red-then-green in memory, writing nothing: a planted new file with a
filter on the column fails, one more line in a baselined file fails, planted CSS/DOM lines do not
fire, and the real tree without plants passes.

Usage:
    uv run --frozen python scripts/check_t13_row_column_source.py
    uv run --frozen python scripts/check_t13_row_column_source.py --shrink-baseline
    uv run --frozen python scripts/check_t13_row_column_source.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scripts" / "t13_row_column_source_baseline.json"

WORD = "visi" + "bility"
ROW = re.compile(r"(?<![A-Za-z0-9_\-])" + WORD + r"(?![A-Za-z0-9_\-])")
NOISE = [re.compile(p) for p in (
    WORD + r"""\s*[:=]\s*["'`]?(hidden|visible|collapse|inherit|initial|unset|revert)\b""",
    r"""style(\.|\[["'])""" + WORD,
    WORD + r"\s*:\s*\$\{",
    r"transition[^;\n]*" + WORD,
    r"ai[_-]" + WORD,
    r"document\." + WORD,
)]
CODE = re.compile(r"\.(py|ts|tsx|js|jsx|mjs|cjs|sql|rs|svelte|vue)$")
SKIP = re.compile(
    r"(^|/)(migrations|node_modules|dist|build|\.next|\.output|vendor|__pycache__|target)/"
    r"|database\.types\.ts$|generated|api-types\.ts$|\.d\.ts$|\.min\.js$"
)


def _files() -> list[str]:
    out: set[str] = set()
    for args in (["ls-files"], ["ls-files", "--others", "--exclude-standard"]):
        res = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True)
        out.update(p for p in res.stdout.split("\n") if p)
    return sorted(p for p in out if CODE.search(p) and not SKIP.search(p))


def matching_lines(text: str) -> list[tuple[int, str]]:
    return [(i, line.strip()) for i, line in enumerate(text.splitlines(), 1)
            if ROW.search(line) and not any(n.search(line) for n in NOISE)]


def scan(overrides: dict[str, str] | None = None) -> dict[str, list[tuple[int, str]]]:
    overrides = overrides or {}
    found: dict[str, list[tuple[int, str]]] = {}
    for rel in sorted(set(_files()) | set(overrides)):
        if rel in overrides:
            text = overrides[rel]
        else:
            path = ROOT / rel
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        hits = matching_lines(text)
        if hits:
            found[rel] = hits
    return found


def load_baseline() -> dict[str, int]:
    return json.loads(BASELINE.read_text())["files"]


def verdict(found: dict[str, list[tuple[int, str]]], baseline: dict[str, int]) -> tuple[list[str], list[str]]:
    grew, shrinkable = [], []
    for rel, hits in sorted(found.items()):
        allowed = baseline.get(rel, 0)
        if len(hits) > allowed:
            head = f"{rel}: {len(hits)} reference(s), baseline allows {allowed}"
            grew.append(head + "".join(f"\n    {rel}:{n}: {line[:160]}" for n, line in hits))
    for rel, allowed in sorted(baseline.items()):
        if len(found.get(rel, ())) < allowed:
            shrinkable.append(f"{rel}: {len(found.get(rel, ()))} < {allowed}")
    return grew, shrinkable


def shrink(found: dict[str, list[tuple[int, str]]]) -> int:
    data = json.loads(BASELINE.read_text())
    old = data["files"]
    new = {rel: min(n, len(found.get(rel, ()))) for rel, n in old.items() if found.get(rel)}
    added = sorted(set(new) - set(old)) + sorted(r for r in new if new[r] > old.get(r, 0))
    if added:  # impossible by construction; kept as the ratchet's own refusal
        raise SystemExit(f"refused: shrinking would add {added}")
    data["files"] = dict(sorted(new.items()))
    BASELINE.write_text(json.dumps(data, indent=1) + "\n")
    print(f"[t13-row-column-source] baseline shrunk: {sum(old.values())} -> {sum(new.values())} lines, "
          f"{len(old)} -> {len(new)} files")
    return 0


def self_test() -> int:
    baseline = load_baseline()
    ok = True
    some_file = next(iter(baseline))
    real = (ROOT / some_file).read_text(encoding="utf-8", errors="ignore")
    plant_new = "app/__t13_selftest_plant__.py"
    cases = [
        ("real tree, no plant", {}, False),
        ("planted new file filtering on the column", {plant_new: f'rows = q.eq("{WORD}", "public")\n'}, True),
        ("one more reference in a baselined file", {some_file: real + f"\nx.{WORD} = 'public'\n"}, True),
        ("planted CSS/DOM lines only (must not fire)",
         {plant_new: f"el.style.{WORD} = 'hidden'\ncss = '{WORD}: hidden;'\nai_{WORD}_panel = 1\n"}, False),
    ]
    for name, overrides, want_fail in cases:
        grew, _ = verdict(scan(overrides), baseline)
        failed = bool(grew)
        named = all(any(k in g for g in grew) for k in overrides) if want_fail else True
        good = failed == want_fail and named
        ok &= good
        print(f"  {'ok  ' if good else 'FAIL'} {name}: {'red' if failed else 'green'}"
              + (f" — {grew[0].splitlines()[0]}" if grew else ""))
    print(f"[t13-row-column-source] self-test {'PASS' if ok else 'FAIL'} (in memory, nothing written)")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shrink-baseline", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    found = scan()
    if args.shrink_baseline:
        return shrink(found)
    grew, shrinkable = verdict(found, load_baseline())
    if shrinkable:
        print(f"[t13-row-column-source] {len(shrinkable)} file(s) converted below baseline — run --shrink-baseline")
    if grew:
        for g in grew:
            print(f"FAIL {g}")
        print(f"[t13-row-column-source] FAIL — {len(grew)} file(s) gained a reference to the row column access-ladder "
              "T-13 retires. Read published_to_web for the anonymous lane and shown_to for list narrowing "
              "(common-docs/projects/access-ladder/t13/PLAN.md); the baseline never grows.")
        return 1
    print(f"[t13-row-column-source] clean — {sum(len(v) for v in found.values())} baselined reference(s) in "
          f"{len(found)} file(s), none new")
    return 0


if __name__ == "__main__":
    sys.exit(main())
