#!/usr/bin/env bash
# Builds the provider-private UniFFI bridge and regenerates its Swift C ABI
# surface.  The generated files stay under build/ and are compiled immediately.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CORE="$ROOT/native-vault-provider/core"
case "${1:-${TAURI_ENV_ARCH:-$(uname -m)}}" in
  arm64|aarch64|aarch64-apple-darwin) TARGET="aarch64-apple-darwin" ;;
  x86_64|x86_64-apple-darwin) TARGET="x86_64-apple-darwin" ;;
  *) echo "ERROR: expected arm64 or x86_64 native Vault bridge architecture" >&2; exit 1 ;;
esac
if ! rustup target list --toolchain 1.93.1 --installed | grep -Fx "$TARGET" >/dev/null; then
  rustup target add --toolchain 1.93.1 "$TARGET"
fi
(
  cd "$CORE"
  cargo +1.93.1 build --release --locked --features native-bridge --target "$TARGET"
)
LIBDIR="$CORE/target/$TARGET/release"
test -f "$LIBDIR/libnative_vault_core.a"
test -f "$LIBDIR/libnative_vault_core.dylib"
# Every caller owns its generated bindings until its compilation has finished.
# Never remove the shared architecture directory underneath another compiler.
PARENT="$ROOT/native-vault-provider/build/native-vault-bridge/$TARGET"
mkdir -p "$PARENT"
OUT="$(mktemp -d "$PARENT/bindings.XXXXXXXX")"
(
  cd "$CORE"
  cargo +1.93.1 run --release --locked --features native-bridge --bin native-vault-bindgen -- "$LIBDIR/libnative_vault_core.dylib" "$OUT"
)
test -s "$OUT/native_vault_core.swift"
test -s "$OUT/native_vault_coreFFI.h"
test -s "$OUT/native_vault_coreFFI.modulemap"
printf '%s\n' "$OUT"
