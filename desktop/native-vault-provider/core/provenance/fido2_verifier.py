import base64
import copy
import json
import subprocess
import sys

from fido2.cose import ES256
from fido2.server import Fido2Server
from fido2.webauthn import (
    AttestationConveyancePreference,
    AuthenticatorData,
    PublicKeyCredentialParameters,
    PublicKeyCredentialRpEntity,
    PublicKeyCredentialType,
    PublicKeyCredentialUserEntity,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

BINARY = sys.argv[1]
ORIGIN = "https://example.com"


def b64decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def b64encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def mutate_client_data(credential, key, value):
    changed = copy.deepcopy(credential)
    data = json.loads(b64decode(changed["response"]["clientDataJSON"]))
    data[key] = value
    changed["response"]["clientDataJSON"] = b64encode(
        json.dumps(data, separators=(",", ":")).encode()
    )
    return changed


def expect_refused(call, label):
    try:
        call()
    except Exception:
        return
    raise AssertionError(f"{label} unexpectedly accepted")


def ceremony(credentials):
    server = Fido2Server(
        PublicKeyCredentialRpEntity(id="example.com", name="Example"),
        attestation=AttestationConveyancePreference.NONE,
    )
    server.allowed_algorithms = [
        PublicKeyCredentialParameters(
            type=PublicKeyCredentialType.PUBLIC_KEY, alg=ES256.ALGORITHM
        )
    ]
    user = PublicKeyCredentialUserEntity(
        id=b"review-user", name="review-user", display_name="Review User"
    )
    creation, register_state = server.register_begin(
        user,
        resident_key_requirement=ResidentKeyRequirement.REQUIRED,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    request, authentication_state = server.authenticate_begin(
        credentials=credentials,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    create_options = dict(creation.public_key)
    get_options = dict(request.public_key)
    frames = [
        {
            "command": "register",
            "id": "register-review",
            "options": create_options,
            "origin": ORIGIN,
            "uv": True,
            "persistence": "success",
        },
        {
            "command": "authenticate",
            "id": "authenticate-review",
            "options": get_options,
            "origin": ORIGIN,
            "uv": True,
        },
    ]
    payload = "".join(json.dumps(frame, separators=(",", ":")) + "\n" for frame in frames)
    result = subprocess.run(
        [BINARY], input=payload, text=True, capture_output=True, check=True, timeout=20
    )
    assert result.stderr == ""
    output = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(output) == 2 and all(row["ok"] for row in output)
    registration = output[0]["credential"]
    assertion = output[1]["credential"]
    assertion_auth_data = AuthenticatorData(
        b64decode(assertion["response"]["authenticatorData"])
    )
    assert assertion_auth_data.is_user_present()
    assert assertion_auth_data.is_user_verified()
    assert assertion_auth_data.is_backup_eligible()
    assert assertion_auth_data.is_backed_up()
    assert assertion_auth_data.counter == 0

    expect_refused(
        lambda: server.register_complete(
            register_state,
            mutate_client_data(registration, "origin", "https://wrong.example"),
        ),
        "registration wrong origin",
    )
    expect_refused(
        lambda: server.register_complete(
            register_state,
            mutate_client_data(registration, "challenge", b64encode(b"wrong-challenge")),
        ),
        "registration wrong challenge",
    )
    auth_data = server.register_complete(register_state, registration)
    assert auth_data.is_user_present() and auth_data.is_user_verified()
    assert auth_data.is_backup_eligible() and auth_data.is_backed_up()
    assert auth_data.counter == 0 and auth_data.credential_data is not None

    expect_refused(
        lambda: server.authenticate_complete(
            authentication_state,
            [auth_data.credential_data],
            mutate_client_data(assertion, "origin", "https://wrong.example"),
        ),
        "authentication wrong origin",
    )
    expect_refused(
        lambda: server.authenticate_complete(
            authentication_state,
            [auth_data.credential_data],
            mutate_client_data(assertion, "challenge", b64encode(b"wrong-challenge")),
        ),
        "authentication wrong challenge",
    )
    selected = server.authenticate_complete(
        authentication_state, [auth_data.credential_data], assertion
    )
    assert selected.credential_id == auth_data.credential_data.credential_id
    return "omitted" if credentials is None else "empty"


assert ceremony(None) == "omitted"
assert ceremony([]) == "empty"
print("PASS python-fido2=2.2.1 register/authenticate omitted+empty allowCredentials; registration+assertion UP/UV/BE/BS and counter=0; wrong origin/challenge refused")
