#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; WORK="$(mktemp -d)"
TEST_SERVER_PID=""
cleanup() { if [[ -n "$TEST_SERVER_PID" ]]; then kill "$TEST_SERVER_PID" 2>/dev/null || true; wait "$TEST_SERVER_PID" 2>/dev/null || true; fi; rm -rf "$WORK"; }
trap cleanup EXIT
python3 - "$WORK/port" <<'PY' &
import http.server, pathlib, sys, time
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/failure':
            self.connection.close(); return
        if self.path == '/hold': time.sleep(5)
        self.send_response(302 if self.path == '/redirect' else 200)
        if self.path == '/redirect': self.send_header('Location', '/success')
        self.end_headers()
        try: self.wfile.write(b'{"fixture":"synthetic response"}')
        except (BrokenPipeError, ConnectionResetError): pass
    def log_message(self, *_): pass
server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
pathlib.Path(sys.argv[1]).write_text(str(server.server_port))
server.serve_forever()
PY
TEST_SERVER_PID=$!
for _ in {1..100}; do [[ -s "$WORK/port" ]] && break; sleep 0.05; done
[[ -s "$WORK/port" ]] || { echo 'Test HTTP server failed to start' >&2; exit 1; }
export NATIVE_VAULT_TEST_HTTP_PORT="$(cat "$WORK/port")"
export NATIVE_VAULT_TEST_PROOF="$WORK/public-ceremony.json"
SWIFTC="$(xcrun --find swiftc)"; SDK="$(xcrun --sdk macosx --show-sdk-path)"; ARCH="$(uname -m)"; BRIDGE="$($ROOT/scripts/build-native-vault-bridge.sh "$ARCH")"; RUST_ARCH="${ARCH/arm64/aarch64}"
"$SWIFTC" -parse-as-library -sdk "$SDK" -target "$ARCH-apple-macosx15.0" -I "$BRIDGE" -Xcc "-fmodule-map-file=$BRIDGE/native_vault_coreFFI.modulemap" -L "$ROOT/native-vault-provider/core/target/$RUST_ARCH-apple-darwin/release" -lnative_vault_core "$BRIDGE/native_vault_core.swift" "$ROOT/native-vault-provider/NativeVaultCodec.swift" "$ROOT/native-vault-provider/NativeVaultEnrollmentLifecycle.swift" "$ROOT/native-vault-provider/NativeVaultState.swift" "$ROOT/native-vault-provider/NativeVaultTransport.swift" "$ROOT/native-vault-provider/NativeVaultPrivateSession.swift" "$ROOT/native-vault-provider/NativeVaultSessionAccess.swift" "$ROOT/native-vault-provider/NativeVaultPassword.swift" "$ROOT/native-vault-provider/NativeVaultPasskeyCodec.swift" "$ROOT/native-vault-provider/NativeVaultPasskey.swift" "$ROOT/native-vault-provider/NativeVaultIdentity.swift" "$ROOT/native-vault-provider/CredentialProviderViewController.swift" "$ROOT/native-vault-provider/tests/NativeVaultPasskeyControllerCorpus.swift" -o "$WORK/corpus"
"$WORK/corpus"
uv run --no-project --with 'fido2==2.2.1' python - "$NATIVE_VAULT_TEST_PROOF" <<'PY'
import base64, hashlib, json, pathlib, sys
from fido2.webauthn import AttestationObject, AuthenticatorData
proof = json.loads(pathlib.Path(sys.argv[1]).read_text())
decode = lambda key: base64.b64decode(proof[key], validate=True)
attestation = AttestationObject(decode('attestation'))
assertion = AuthenticatorData(decode('authenticator_data'))
credential = attestation.auth_data.credential_data
assert credential and credential.credential_id == decode('credential_id')
for auth in (attestation.auth_data, assertion):
    assert auth.rp_id_hash == hashlib.sha256(b'example.com').digest()
    assert auth.is_user_present() and auth.is_user_verified()
assert decode('client_data_hash') == bytes([9]) * 32
credential.public_key.verify(bytes(assertion) + decode('client_data_hash'), decode('signature'))
try:
    credential.public_key.verify(bytes(assertion) + bytes([8]) * 32, decode('signature'))
except Exception:
    pass
else:
    raise AssertionError('altered challenge hash accepted')
print('PASS independent fido2 verification of actual Apple-controller outputs')
PY
