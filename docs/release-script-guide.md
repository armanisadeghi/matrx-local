# Release Script Engineering Guide

A reference for agents and engineers building automated version-bump and release scripts across any technology stack.

---

## The Goal

A release script has one job: **take the codebase from a clean state at version N to a published state at version N+1 with zero manual steps and zero lingering dirty files.**

That means:
1. Determine the new version (from the current, not from memory or a hardcoded number)
2. Write that version into every file that declares it
3. Commit those writes atomically
4. Tag the commit
5. Push the commit and the tag
6. Any downstream publish (PyPI, npm, GitHub Releases, etc.) is triggered by the tag — not by the script itself

If the script exits and `git status` shows anything modified, the script is broken.

---

## Principle 1: Single Source of Truth

Every project must designate **exactly one file** as the authoritative version. The script reads from it and writes to all others. It never reads from a secondary file to avoid split-brain.

Common choices by ecosystem:

| Ecosystem | Source of truth file | Version field |
|---|---|---|
| Python (PEP 517/518) | `pyproject.toml` | `version = "X.Y.Z"` under `[project]` |
| Node / npm | `package.json` | `"version": "X.Y.Z"` |
| Rust | `Cargo.toml` | `version = "X.Y.Z"` under `[package]` |
| Go | `cmd/root.go` or `version.go` | `const Version = "X.Y.Z"` |
| Tauri (desktop) | `tauri.conf.json` | `"version": "X.Y.Z"` at root |

For polyglot projects (e.g., a Python backend + Rust/Tauri frontend), pick one and make all others followers. `pyproject.toml` is a good choice for Python-primary projects; `package.json` for JS-primary.

---

## Principle 2: Enumerate Every Version Declaration — Before Writing the Script

Run a search across the entire repo before writing a single line of sed. Every file that contains a version string must either:

- **Be updated by the script**, or
- **Be changed to read the version dynamically at runtime** (preferred when possible), or
- **Have the version removed** if it isn't actually used

Missing even one file creates drift and confusion.

### How to audit

```bash
# Find all semantic version strings (adjust the exclude list for your project)
rg -n '"version":\s*"[0-9]+\.[0-9]+\.[0-9]+"' \
    --glob '!node_modules' --glob '!.git' --glob '!*.lock'

rg -n '^version\s*=\s*"[0-9]+\.[0-9]+\.[0-9]+"' \
    --glob '!*.lock'

# Also check markdown (badges, pinned versions in docs)
rg -n 'v[0-9]+\.[0-9]+\.[0-9]+' --glob '*.md'

# Check CI/CD workflow files
rg -n 'version' --glob '*.yml' --glob '*.yaml'
```

Do this audit after every major refactor, not just when writing the initial script.

---

## Principle 3: Static Declarations vs. Dynamic Reads

Some files must declare the version statically (manifests, build configs). Others can read it at runtime. The distinction matters:

**Must be static:**
- `pyproject.toml` — build tooling reads this before the package is installed
- `Cargo.toml` — Rust compiler reads this at build time
- `tauri.conf.json` — Tauri reads this before the app binary exists
- `package.json` — npm reads this directly; it can't execute code

**Should be dynamic (read at runtime, never hardcode):**
- API health/version endpoints (`GET /version`, `GET /health`)
- Discovery files written to disk at startup (e.g., `~/.myapp/local.json`)
- Log output, user-facing version strings in UI
- Any place in application code that reports its own version

### How to read the version dynamically in each language

**Python — reading from `pyproject.toml` directly (works without package install):**
```python
import re
from pathlib import Path

def _read_version() -> str:
    try:
        text = (Path(__file__).parent / "pyproject.toml").read_text()
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        return m.group(1) if m else "0.0.0"
    except Exception:
        return "0.0.0"

APP_VERSION = _read_version()
```

> **Why not `importlib.metadata.version()`?**  
> It requires the package to be installed (`pip install -e .` or equivalent). If your entry point is run directly from source (e.g., `python run.py`, `uv run run.py`) without an install step, the `.dist-info` directory doesn't exist and `PackageNotFoundError` is raised. Reading `pyproject.toml` directly works in all execution contexts: bare interpreter, virtualenv, frozen binary (PyInstaller bundles the file), and sidecar processes.

**Python — `importlib.metadata` is fine when the package is always installed:**
```python
from importlib.metadata import version, PackageNotFoundError

try:
    APP_VERSION = version("my-package")
except PackageNotFoundError:
    APP_VERSION = "0.0.0"
```
Use this form (with the try/except fallback) if you always run via `pip install -e .` and know metadata is present.

**Node.js / TypeScript:**
```typescript
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { version } = require("../../package.json");
```
Or at build time via Vite/webpack environment injection:
```typescript
// vite.config.ts
import pkg from "./package.json";
define: { __APP_VERSION__: JSON.stringify(pkg.version) }
```

**Rust:**
```rust
const VERSION: &str = env!("CARGO_PKG_VERSION");
```
`CARGO_PKG_VERSION` is injected at compile time from `Cargo.toml` — always in sync, zero runtime cost.

**Go:**
```go
// Set by the linker at build time via -ldflags
var Version = "dev"
// In CI: go build -ldflags="-X main.Version=$(git describe --tags)"
```

---

## Principle 4: In-Place `sed` Is Not Portable

`sed -i` behaves differently across operating systems:

| Platform | Correct syntax | Notes |
|---|---|---|
| Linux / WSL / Git Bash | `sed -i 's/old/new/' file` | GNU sed; `-i` takes no argument |
| macOS (BSD sed) | `sed -i '' 's/old/new/' file` | BSD sed; `-i` **requires** an extension argument, even if empty |
| Windows (native) | Neither — `sed` not available | Use PowerShell or Git Bash/WSL |

**The crash you will see on macOS if you use GNU-style `sed -i`:**
```
sed: 1: "filename
": extra characters at the end of p command
```

### Portable wrapper (Bash):

```bash
sedi() {
    if sed --version 2>/dev/null | grep -q GNU; then
        sed -i "$@"
    else
        sed -i '' "$@"
    fi
}

# Usage — identical on all platforms:
sedi "s/^version = \"[^\"]*\"/version = \"$VERSION\"/" pyproject.toml
```

Put this function near the top of every release script that uses in-place sed. It costs nothing and eliminates an entire class of CI failures.

### Alternative: use `perl -i` instead

Perl ships on macOS and all Linux distros and has consistent `-i` behavior:
```bash
perl -i -pe 's/^version = "[^"]*"/version = "'"$VERSION"'"/' pyproject.toml
```
This is a valid alternative if you prefer not to carry the `sedi` wrapper.

---

## Principle 5: Package Managers That Touch Multiple Files

Some version commands update more than one file. You must commit **all** of them, or the next `git status` will show modified files — creating an endless dirty-state loop.

| Command | Files modified |
|---|---|
| `npm version X.Y.Z --no-git-tag-version` | `package.json` **and** `package-lock.json` |
| `pnpm version X.Y.Z --no-git-tag-version` | `package.json` only (lockfile untouched) |
| `cargo set-version X.Y.Z` (cargo-edit) | `Cargo.toml` **and** `Cargo.lock` |
| `poetry version X.Y.Z` | `pyproject.toml` only |

Always check what a version command actually writes before finalizing the `git add` list.

```bash
# Safe pattern: stage everything the version bump touched before committing
git add \
    pyproject.toml \
    desktop/package.json \
    desktop/src-tauri/Cargo.toml \
    desktop/src-tauri/tauri.conf.json
```

---

## Principle 6: Version Incrementing Logic

Read from the source of truth, parse, increment — never hardcode the "current" version in the script.

```bash
# Read
CURRENT=$(grep -m1 '^version' pyproject.toml | sed 's/.*"\(.*\)".*/\1/')

# Parse
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"

# Increment
PATCH=$((PATCH + 1))           # patch bump: 1.0.5 → 1.0.6
# MINOR=$((MINOR + 1)); PATCH=0  # minor bump: 1.0.5 → 1.1.0
# MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0  # major bump (keep manual)

VERSION="$MAJOR.$MINOR.$PATCH"
```

**Why keep major bumps manual:**  
Major version increments signal breaking changes. Automating them creates the risk of accidentally publishing `2.0.0` from a patch-style CI run. Require a human to pass an explicit version argument (`./release.sh 2.0.0`) for major changes.

---

## Principle 7: Guard Against Dirty Working Trees

The script must refuse to run if there are uncommitted changes. This enforces the workflow: commit your work first, then release.

```bash
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "ERROR: Uncommitted changes detected. Commit them first."
    exit 1
fi
```

Without this guard, the release commit will silently include unrelated in-progress changes alongside the version bump, making history messy and potentially publishing broken code.

---

## Principle 8: Tag Management

Git tags are what trigger CI (GitHub Actions `on: push: tags: "v*"`). Handle them carefully.

```bash
TAG="v$VERSION"

# Don't assume the tag doesn't exist — handle it gracefully
if git tag --list "$TAG" | grep -q "$TAG"; then
    echo "Tag $TAG already exists — removing and recreating"
    git tag -d "$TAG"
    git push origin ":refs/tags/$TAG" 2>/dev/null || true
fi

git tag "$TAG"

# Push commit and tag separately — both are required
git push origin main
git push origin "$TAG"
```

Always push the **commit** before the **tag**. Pushing the tag first can cause the CI runner to check out a ref that the remote doesn't have yet.

---

## Principle 9: Let CI Do the Publishing

The release script's job ends at `git push origin "$TAG"`. It should not:
- Build distribution packages (`python -m build`, `npm pack`)
- Upload to PyPI, npm, crates.io
- Create GitHub Releases directly

All of that belongs in CI (GitHub Actions, GitLab CI, etc.), triggered by the tag push. This separation means:
- Publishing happens in a clean, reproducible environment
- Secrets (PyPI tokens, npm tokens, signing keys) stay in CI, never on developer machines
- The same CI pipeline is used whether the release comes from the script or a manual tag

**Typical GitHub Actions trigger:**
```yaml
on:
  push:
    tags:
      - "v*"
```

---

## Principle 10: Regex Anchoring in Sed Patterns

Unanchored version patterns will corrupt files with multiple version-like strings.

```bash
# DANGEROUS — matches any "version" key anywhere in the file
sedi 's/"version": "[^"]*"/"version": "'"$VERSION"'"/' package.json

# SAFER — anchored to the start of line, specific indentation
sedi 's/^  "version": "[^"]*"/  "version": "'"$VERSION"'"/' package.json

# SAFEST — use the package manager's own tool
pnpm version "$VERSION" --no-git-tag-version  # handles JSON correctly
```

For JSON files, prefer using the package manager CLI over sed whenever one is available. For TOML and plain text, anchor your patterns to `^` (start of line) and use `[^"]*` (no quote in version string) rather than `.*` (greedy, will match too much).

---

## Principle 11: Frozen / Packaged Binary Contexts

If your application is ever compiled into a standalone binary (PyInstaller, Tauri sidecar, Electron, Go build), runtime version lookups need extra care:

- **`importlib.metadata`** — works if the `.dist-info` is included in the bundle (PyInstaller does this when the package is installed before freezing; not guaranteed otherwise)
- **Reading `pyproject.toml` at runtime** — works only if the file is bundled. PyInstaller includes it if referenced in `datas`; Tauri sidecars can bundle arbitrary files
- **Compile-time injection** — most reliable for frozen binaries. Inject at build time so the binary carries the version as a constant, not a file path
  - Rust: `env!("CARGO_PKG_VERSION")` — always correct, zero overhead
  - Python + PyInstaller: set a `__version__` constant in your package `__init__.py` from `pyproject.toml` at build time, or use a `_version.py` generated by `setuptools-scm`
  - Vite/webpack: `define: { __APP_VERSION__: JSON.stringify(pkg.version) }`

---

## Principle 12: CI Environment Differences

Your script will run in:
- Developer machine (interactive shell, credentials via keychain/SSH agent)
- GitHub Actions runner (credentials via `GITHUB_TOKEN`, tools may differ by runner OS)
- WSL on Windows (GNU tools but Windows filesystem paths)
- Docker container (minimal tooling)

Write defensively:
- Use `#!/usr/bin/env bash` not `#!/bin/bash` (path varies)
- Use `set -euo pipefail` — fail fast, treat unset variables as errors, propagate pipe failures
- Don't assume `npm`, `pnpm`, `cargo`, etc. are on PATH without checking
- Use `2>/dev/null || true` on non-critical cleanup commands (like deleting a remote tag that may not exist)

---

## Complete Script Template

```bash
#!/usr/bin/env bash
# Release script — <project name>
# Source of truth: <path to source of truth file>
#
# Usage:
#   ./scripts/release.sh            # patch bump
#   ./scripts/release.sh --major    # minor bump
#   ./scripts/release.sh X.Y.Z      # exact version
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

# Portable in-place sed (BSD macOS vs GNU Linux/WSL)
sedi() {
    if sed --version 2>/dev/null | grep -q GNU; then
        sed -i "$@"
    else
        sed -i '' "$@"
    fi
}

# 1. Read current version from source of truth
CURRENT=$(grep -m1 '^version' pyproject.toml | sed 's/.*"\(.*\)".*/\1/')
[[ -z "$CURRENT" ]] && { echo "ERROR: version not found"; exit 1; }
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"

# 2. Determine new version
case "${1:-patch}" in
    patch)   PATCH=$((PATCH + 1)); VERSION="$MAJOR.$MINOR.$PATCH" ;;
    --major) MINOR=$((MINOR + 1)); PATCH=0; VERSION="$MAJOR.$MINOR.$PATCH" ;;
    [0-9]*)  VERSION="$1" ;;
    *)       echo "Usage: $0 [--major|X.Y.Z]"; exit 1 ;;
esac
TAG="v$VERSION"
echo "=== Releasing $TAG (was $CURRENT) ==="

# 3. Require clean tree
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "ERROR: Uncommitted changes. Commit first."; exit 1
fi

# 4. Update all version declarations
echo "  → Syncing version to $VERSION..."
sedi "s/^version = \"[^\"]*\"/version = \"$VERSION\"/" pyproject.toml
# ... add other files here ...

# 5. Commit
git add pyproject.toml  # ... all modified files ...
git commit -m "release: $TAG"

# 6. Tag
git tag --list "$TAG" | grep -q "$TAG" && {
    git tag -d "$TAG"
    git push origin ":refs/tags/$TAG" 2>/dev/null || true
}
git tag "$TAG"

# 7. Push (triggers CI → publish)
git push origin main
git push origin "$TAG"

echo "=== Released $TAG ==="
echo "  Monitor: https://github.com/<org>/<repo>/actions"
```

---

## Checklist for New Repos

- [ ] Identified the single source of truth file
- [ ] Audited every version string in the repo with `rg`
- [ ] Decided: static update via script, or dynamic read at runtime, for each location
- [ ] Using `sedi()` wrapper or `perl -i` for cross-platform in-place edits
- [ ] All files modified by package manager version commands (lock files, etc.) are in `git add`
- [ ] Script reads the current version dynamically — no hardcoded "was" version
- [ ] Major version bump is manual-only (requires explicit argument)
- [ ] Clean working tree is enforced before any writes
- [ ] Commit is pushed before tag
- [ ] CI (not the script) handles package publishing
- [ ] Tested on target platforms (macOS dev machine + Linux CI runner minimum)
