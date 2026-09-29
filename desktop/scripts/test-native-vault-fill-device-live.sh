#!/usr/bin/env bash
# LIVE proof of the password provider's T-30 device key and T-30c passkey approval
# against a LOCAL aidream (never production): `PORT=8731 uv run python run.py` in
# aidream first. Signs in as admin@admin.com only; every device it registers is
# turned off at the end. Usage: test-native-vault-fill-device-live.sh [origin] [org] [domain]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export AIDREAM="${AIDREAM_DIR:-$ROOT/../../aidream}"
WORKDIR="$(mktemp -d)"; trap 'rm -rf "$WORKDIR"' EXIT
export FILL_LIVE_ORIGIN="${1:-http://127.0.0.1:8731}"
export FILL_LIVE_ORG="${2:-884d1ce8-7b49-4fba-a2f3-0f7dd7c83d4f}"   # admin's Workspace
export FILL_LIVE_DOMAIN="${3:-app.amplitude.com}"
export FILL_LIVE_APPROVE="$ROOT/scripts/native-vault-passkey-approval-fixture.py"
MINT="$ROOT/scripts/native-vault-mint-test-session.py"
(cd "$AIDREAM" && for n in 1 2 3; do uv run python "$MINT" "$WORKDIR/g$n.json" >/dev/null; done \
  && uv run python "$MINT" "$WORKDIR/web.json" --web >/dev/null)
export FILL_LIVE_GRANT1="$WORKDIR/g1.json" FILL_LIVE_GRANT2="$WORKDIR/g2.json" FILL_LIVE_GRANT3="$WORKDIR/g3.json" FILL_LIVE_WEB="$WORKDIR/web.json"
FILL_LIVE_PASSWORD="$(cd "$AIDREAM" && uv run python -c 'from dotenv import dotenv_values; print(dotenv_values(".env")["AI_ADMIN_PASSWORD"])')"
export FILL_LIVE_PASSWORD
SWIFTC="$(xcrun --find swiftc)"; SDK="$(xcrun --sdk macosx --show-sdk-path)"
ARCH="$(uname -m)"; BRIDGE="$($ROOT/scripts/build-native-vault-bridge.sh "$ARCH")"; RUST_ARCH="${ARCH/arm64/aarch64}"
S="$ROOT/native-vault-provider"
"$SWIFTC" -parse-as-library -sdk "$SDK" -target "$ARCH-apple-macosx15.0" \
  -I "$BRIDGE" -Xcc "-fmodule-map-file=$BRIDGE/native_vault_coreFFI.modulemap" \
  -L "$S/core/target/$RUST_ARCH-apple-darwin/release" -lnative_vault_core "$BRIDGE/native_vault_core.swift" \
  "$S/NativeVaultCodec.swift" "$S/NativeVaultEnrollmentLifecycle.swift" "$S/NativeVaultState.swift" "$S/NativeVaultTransport.swift" \
  "$S/NativeVaultPrivateSession.swift" "$S/NativeVaultSessionAccess.swift" "$S/NativeVaultPassword.swift" "$S/NativeVaultFillDevice.swift" \
  "$S/NativeVaultPasskeyCodec.swift" "$S/NativeVaultPasskey.swift" "$S/NativeVaultIdentity.swift" "$S/CredentialProviderViewController.swift" \
  "$S/tests/NativeVaultFillDeviceLive.swift" -o "$WORKDIR/live" 2>"$WORKDIR/build.log" || { grep error: "$WORKDIR/build.log"; exit 1; }
"$WORKDIR/live"
