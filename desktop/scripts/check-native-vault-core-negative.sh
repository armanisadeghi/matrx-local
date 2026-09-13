#!/usr/bin/env bash
# Self-test: each actual temporary weakening must cause the production guard to fail.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work="$(mktemp -d "${TMPDIR:-/tmp}/native-vault-core-negative.XXXXXX")"
trap 'rm -rf "$work"' EXIT

prepare() {
  copy="$work/$1"
  mkdir "$copy"
  mkdir -p "$copy/desktop/scripts" "$copy/desktop/native-vault-provider"
  cp "$ROOT/desktop/scripts/check-native-vault-core.sh" "$copy/desktop/scripts/"
  rsync -a --exclude target "$ROOT/desktop/native-vault-provider/core/" "$copy/desktop/native-vault-provider/core/"
}

expect_failure() {
  local name="$1"
  if MATRX_LOCAL_ROOT="$copy" "$copy/desktop/scripts/check-native-vault-core.sh" >"$work/$name.log" 2>&1; then
    echo "negative probe unexpectedly passed: $name" >&2
    exit 1
  fi
}

# A second patch entry was the Sol-proven manifest-parser bypass.
prepare extra_patch
cat >> "$copy/desktop/native-vault-provider/core/Cargo.toml" <<'TOML'
[patch.crates-io.extra]
other = { path = "vendor/other" }
TOML
expect_failure extra_patch

# This changes a real semantic assertion in the adapter; the exact 1-pass/3-fail oracle must reject it.
prepare false_state_green
python3 - "$copy/desktop/native-vault-provider/core/provenance/semantic_adapter.rs" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
old = 'assert_exact_backup_flags(false, false, Flags::empty()).await;'
new = 'assert_exact_backup_flags(false, false, Flags::BE | Flags::BS).await;'
assert s.count(old) == 1
p.write_text(s.replace(old, new))
PY
expect_failure false_state_green
printf 'PASS native-vault CI guard negative probes\n'
