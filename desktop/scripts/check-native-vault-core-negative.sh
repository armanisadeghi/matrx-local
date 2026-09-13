#!/usr/bin/env bash
# Self-test each provenance predicate against a real temporary guard copy.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GUARD="${NATIVE_VAULT_GUARD:-$ROOT/desktop/scripts/check-native-vault-core.sh}"
work="$(mktemp -d "${TMPDIR:-/tmp}/native-vault-core-negative.XXXXXX")"
trap 'rm -rf "$work"' EXIT

prepare() {
  copy="$work/$1"
  mkdir -p "$copy/desktop/scripts" "$copy/desktop/native-vault-provider"
  cp "$GUARD" "$copy/desktop/scripts/check-native-vault-core.sh"
  chmod +x "$copy/desktop/scripts/check-native-vault-core.sh"
  rsync -a --exclude target "$ROOT/desktop/native-vault-provider/core/" "$copy/desktop/native-vault-provider/core/"
}

must_refuse() {
  local name="$1" marker="$2"
  if MATRX_LOCAL_ROOT="$copy" NATIVE_VAULT_TEST_PRISTINE_MUTATION="${NATIVE_VAULT_TEST_PRISTINE_MUTATION:-}" \
      "$copy/desktop/scripts/check-native-vault-core.sh" >"$work/$name.log" 2>&1; then
    echo "native-vault negative probe accepted: $name" >&2
    exit 1
  fi
  grep -F "$marker" "$work/$name.log" >/dev/null || {
    echo "native-vault negative probe failed before its predicate: $name" >&2
    exit 1
  }
}

semantic_refusal() {
  prepare pristine_semantic
  NATIVE_VAULT_TEST_PRISTINE_MUTATION=false_state_green must_refuse pristine_semantic \
    'native-vault pristine semantic matrix mismatch: test device_bound_backup_flags_are_absent_on_make_and_get ... FAILED'
}

patch_entry_refusal() {
  prepare extra_registry_patch
  python3 - "$copy/desktop/native-vault-provider/core/Cargo.toml" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
needle = 'passkey-authenticator = { path = "vendor/passkey-authenticator" }\n'
assert s.count(needle) == 1
p.write_text(s.replace(needle, needle + 'extra-registry-source = "0.5.0"\n'))
PY
  must_refuse extra_registry_patch 'manifest patch table is not exactly the approved authenticator path patch'
}

path_census_refusal() {
  prepare stray_path
  python3 - "$copy/desktop/native-vault-provider/core/Cargo.toml" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
needle = '[dev-dependencies]\n'
assert s.count(needle) == 1
p.write_text(s.replace(needle, needle + 'stray-local-source = { path = "../stray-local-source" }\n'))
PY
  must_refuse stray_path 'manifest contains an unapproved path dependency'
}

if [ "${NATIVE_VAULT_NEGATIVE_CHILD:-}" = 1 ]; then
  semantic_refusal
  exit 0
fi

semantic_refusal
patch_entry_refusal
path_census_refusal

# A weakened name-only/no-aggregate semantic gate accepts the fork-only false/false
# regression. The inner negative test must therefore fail; otherwise this self-test
# is not causally tied to the semantic predicate it claims to cover.
weak="$work/weakened-guard.sh"
cp "$ROOT/desktop/scripts/check-native-vault-core.sh" "$weak"
python3 - "$weak" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
start = s.index('    for line in \\\n      \'test device_bound_backup_flags_are_absent_on_make_and_get ... FAILED\'')
end = s.index('\n  else\n    cargo test --locked', start)
weak = '''    grep -F 'device_bound_backup_flags_are_absent_on_make_and_get' "$work/pristine.log" >/dev/null
    grep -F 'eligible_not_backed_up_has_only_be_on_make_and_get' "$work/pristine.log" >/dev/null
    grep -F 'cross_user_handle_exclusion_requires_credential_excluded' "$work/pristine.log" >/dev/null
    grep -F 'eligible_backed_up_is_intentional_control' "$work/pristine.log" >/dev/null'''
p.write_text(s[:start] + weak + s[end:])
PY
if NATIVE_VAULT_GUARD="$weak" NATIVE_VAULT_NEGATIVE_CHILD=1 NATIVE_VAULT_TEST_STOP_AFTER_SEMANTIC=1 "$0" >"$work/weakened.log" 2>&1; then
  echo 'native-vault negative self-test did not fail against weakened semantic gate' >&2
  exit 1
fi
grep -F 'native-vault negative probe accepted: pristine_semantic' "$work/weakened.log" >/dev/null
printf 'PASS native-vault CI guard negative probes\n'
