#!/usr/bin/env bash
# Pure Rust core/provenance gate. No FFI, OS provider, release, or database work.
set -euo pipefail
ROOT="${MATRX_LOCAL_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
CORE="$ROOT/desktop/native-vault-provider/core"
PROVENANCE="$CORE/provenance"
ARCHIVE_SHA256='20bf5800e3f6287580da985fb88e678f37c078d62242cb536941c44d852ab37c'
PATCH_SHA256='af3acb38c587a39728627e234120df48c836c545eeb607c5986e7080a68f259c'
work="$(mktemp -d "${TMPDIR:-/tmp}/native-vault-core.XXXXXX")"
trap 'rm -rf "$work"' EXIT
check_sha256() {
  local expected="$1" file="$2" actual
  actual="$(shasum -a 256 "$file" | awk '{print $1}')"
  test "$actual" = "$expected"
}

archive="$work/passkey-authenticator-0.5.0.crate"
curl --fail --location --silent --show-error \
  'https://static.crates.io/crates/passkey-authenticator/passkey-authenticator-0.5.0.crate' -o "$archive"
check_sha256 "$ARCHIVE_SHA256" "$archive"
check_sha256 "$PATCH_SHA256" "$PROVENANCE/passkey-authenticator-0.5.0.patch"
mkdir "$work/upstream"
tar -xzf "$archive" -C "$work/upstream" --strip-components=1
# The release archive omits these notices; the vendored copy intentionally retains matching notices.
for license in LICENSE-APACHE LICENSE-MIT; do
  test -s "$CORE/vendor/passkey-authenticator/$license"
done
uv run --no-project --python 3.11 python - "$CORE/Cargo.toml" "$CORE/Cargo.lock" <<'PY'
from pathlib import Path
import re, sys, tomllib
manifest_text = Path(sys.argv[1]).read_text()
manifest = tomllib.loads(manifest_text)
patch = manifest.get('patch', {}).get('crates-io')
expected = {'passkey-authenticator': {'path': 'vendor/passkey-authenticator'}}
if patch != expected:
    raise SystemExit('manifest patch table is not exactly the approved authenticator path patch')
def paths(value, where=''):
    if isinstance(value, dict):
        for key, item in value.items():
            child = f'{where}.{key}' if where else key
            if key == 'path':
                yield child, item
            yield from paths(item, child)
    elif isinstance(value, list):
        for item in value:
            yield from paths(item, where)
allowed_paths = [
    ('bin.path', 'src/bin/protocol-harness.rs'),
    ('patch.crates-io.passkey-authenticator.path', 'vendor/passkey-authenticator'),
]
if sorted(paths(manifest)) != allowed_paths:
    raise SystemExit('manifest contains an unapproved path dependency')
lock = Path(sys.argv[2]).read_text()
if re.search(r'^source = "git\+', lock, re.M):
    raise SystemExit('git dependency found in locked graph')
entry = re.search(r'\[\[package\]\]\nname = "passkey-types".*?(?=\n\[\[package\]\]|\Z)', lock, re.S)
if entry is None or 'registry+https://github.com/rust-lang/crates.io-index' not in entry.group(0):
    raise SystemExit('passkey-types must resolve from the registry')
PY
cp -a "$work/upstream/." "$work/reproduced"
git -C "$work/reproduced" apply "$PROVENANCE/passkey-authenticator-0.5.0.patch"
diff -qr --exclude target "$work/reproduced" "$CORE/vendor/passkey-authenticator"
for tree in "$work/reproduced" "$CORE/vendor/passkey-authenticator"; do
  test "$(find "$tree" -type f ! -path '*/target/*' | wc -l | tr -d ' ')" = 25
done

# The one source file is copied unchanged into both temporary root-lock graphs.
for mode in pristine patched; do
  copy="$work/$mode"
  mkdir "$copy"
  rsync -a --exclude target "$CORE/" "$copy/"
  printf '// provenance adapter isolates the pinned authenticator dependency.\n' > "$copy/src/lib.rs"
  cp "$PROVENANCE/semantic_adapter.rs" "$copy/tests/semantic_adapter.rs"
  cmp "$PROVENANCE/semantic_adapter.rs" "$copy/tests/semantic_adapter.rs"
  cmp "$CORE/Cargo.lock" "$copy/Cargo.lock"
  # Test-only self-check seam: mutates only the already-forked pristine adapter.
  # CI never sets this variable and the patched adapter always remains byte-identical.
  if [ "$mode" = pristine ] && [ "${NATIVE_VAULT_TEST_PRISTINE_MUTATION:-}" = false_state_green ]; then
    python3 - "$copy/tests/semantic_adapter.rs" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
old = 'assert_exact_backup_flags(false, false, Flags::empty()).await;'
new = 'assert_exact_backup_flags(false, false, Flags::BE | Flags::BS).await;'
if s.count(old) != 1:
    raise SystemExit('pristine mutation target missing')
p.write_text(s.replace(old, new))
PY
  fi
  python3 - "$copy/Cargo.toml" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
needle = 'default = []\n'
if needle not in s:
    raise SystemExit('missing [features] default declaration')
p.write_text(s.replace(needle, needle + 'patched-adapter = []\n', 1))
PY
  if [ "$mode" = pristine ]; then
    rm -rf "$copy/vendor/passkey-authenticator"
    cp -a "$work/upstream/." "$copy/vendor/passkey-authenticator/"
    set +e
    cargo test --locked --manifest-path "$copy/Cargo.toml" --test semantic_adapter > "$work/pristine.log" 2>&1
    status=$?
    set -e
    test "$status" -ne 0
    for line in \
      'test device_bound_backup_flags_are_absent_on_make_and_get ... FAILED' \
      'test eligible_not_backed_up_has_only_be_on_make_and_get ... FAILED' \
      'test cross_user_handle_exclusion_requires_credential_excluded ... FAILED' \
      'test eligible_backed_up_is_intentional_control ... ok'; do
      if ! grep -Fx "$line" "$work/pristine.log" >/dev/null; then
        echo "native-vault pristine semantic matrix mismatch: $line" >&2
        exit 1
      fi
    done
    if ! grep -E '^test result: FAILED\. 1 passed; 3 failed; 0 ignored; 0 measured; 0 filtered out; finished in [0-9.]+s$' "$work/pristine.log" >/dev/null; then
      echo 'native-vault pristine semantic aggregate mismatch: expected 1 passed; 3 failed' >&2
      exit 1
    fi
  else
    cargo test --locked --manifest-path "$copy/Cargo.toml" --features patched-adapter --test semantic_adapter
  fi
done

# Test-only negative-suite boundary: normal CI never sets this. It allows the
# self-test to prove semantic-predicate causality without rerunning downstream RP work.
if [ "${NATIVE_VAULT_TEST_STOP_AFTER_SEMANTIC:-}" = 1 ]; then
  exit 0
fi

cd "$CORE"
cargo test --locked --features protocol-test-harness
cargo build --locked --features protocol-test-harness --bin protocol-harness
uv run --no-project --with 'fido2==2.2.1' python "$PROVENANCE/fido2_verifier.py" target/debug/protocol-harness
