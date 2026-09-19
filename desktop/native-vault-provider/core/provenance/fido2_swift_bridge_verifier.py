import base64
import hashlib
import json
import subprocess
import sys

from fido2.cose import ES256
from fido2.server import Fido2Server
from fido2.webauthn import (
    AttestationConveyancePreference, AuthenticatorData, PublicKeyCredentialParameters,
    PublicKeyCredentialRpEntity, PublicKeyCredentialType, PublicKeyCredentialUserEntity,
    ResidentKeyRequirement, UserVerificationRequirement,
)

binary = sys.argv[1]
def b64(data): return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
def raw(value): return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
def client_data(kind, challenge):
    return json.dumps({"type": kind, "challenge": b64(challenge), "origin": "https://example.com", "crossOrigin": False}, separators=(",", ":")).encode()

server = Fido2Server(PublicKeyCredentialRpEntity(id="example.com", name="Example"), attestation=AttestationConveyancePreference.NONE)
server.allowed_algorithms = [PublicKeyCredentialParameters(type=PublicKeyCredentialType.PUBLIC_KEY, alg=ES256.ALGORITHM)]
user = PublicKeyCredentialUserEntity(id=b"swift-rp-user", name="swift-rp-user", display_name="Swift RP User")
creation, register_state = server.register_begin(user, resident_key_requirement=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED)
request, authentication_state = server.authenticate_begin(credentials=[], user_verification=UserVerificationRequirement.REQUIRED)
result = subprocess.run([binary, b64(creation.public_key.challenge), b64(request.public_key.challenge)], capture_output=True, check=True, timeout=20)
payload = json.loads(result.stdout)
registration = {"id": payload["credentialId"], "rawId": payload["credentialId"], "type": "public-key", "response": {"clientDataJSON": b64(client_data("webauthn.create", creation.public_key.challenge)), "attestationObject": payload["attestationObject"]}}
auth_data = server.register_complete(register_state, registration)
assertion = {"id": payload["assertionCredentialId"], "rawId": payload["assertionCredentialId"], "type": "public-key", "response": {"clientDataJSON": b64(client_data("webauthn.get", request.public_key.challenge)), "authenticatorData": payload["authenticatorData"], "signature": payload["signature"], "userHandle": payload["userHandle"]}}
selected = server.authenticate_complete(authentication_state, [auth_data.credential_data], assertion)
flags = AuthenticatorData(raw(payload["authenticatorData"]))
assert selected.credential_id == auth_data.credential_data.credential_id
assert flags.is_user_present() and flags.is_user_verified() and flags.is_backup_eligible() and flags.is_backed_up()
tampered = dict(assertion)
tampered_response = dict(assertion["response"])
tampered_authenticator_data = bytearray(raw(payload["authenticatorData"]))
tampered_authenticator_data[:32] = hashlib.sha256(b"wrong.example").digest()
tampered_response["authenticatorData"] = b64(bytes(tampered_authenticator_data))
tampered["response"] = tampered_response
try:
    server.authenticate_complete(authentication_state, [auth_data.credential_data], tampered)
except ValueError:
    pass
else:
    raise AssertionError("RP-ID-hash tampering must be rejected by python-fido2")
print("PASS Swift UniFFI bridge registration/assertion accepted by python-fido2=2.2.1")
