"""Test-only harness (admin@admin.com, never a real person): mint a native password-provider OAuth session for admin@admin.com
(the same PKCE + consent the provider's Connect button drives), write the grant
to a 0600 file. Never prints a token or password."""
import base64, hashlib, json, os, secrets, sys, urllib.parse
import httpx
from dotenv import dotenv_values

env = dotenv_values(".env")  # run with cwd = aidream
URL = env["SUPABASE_MATRIX_URL"].rstrip("/") + "/auth/v1"
KEY = env["SUPABASE_MATRIX_PUBLISHABLE_KEY"]
CLIENT = "d8a02629-f5f2-4064-ab26-31da07f082fc"
CALLBACK = "matrx-vault-provider://oauth/callback"
out = sys.argv[1]
WEB_ONLY = "--web" in sys.argv  # write the plain web sign-in token (the attacker's starting point)

with httpx.Client(timeout=20, follow_redirects=False) as c:
    h = {"apikey": KEY}
    r = c.post(f"{URL}/token?grant_type=password", headers=h, json={"email": env["AI_ADMIN_USERNAME"], "password": env["AI_ADMIN_PASSWORD"]})
    r.raise_for_status(); web = r.json()["access_token"]
    if WEB_ONLY:
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"access_token": web}, f)
        print(json.dumps({"minted": True, "web": True}))
        sys.exit(0)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    q = {"response_type": "code", "client_id": CLIENT, "redirect_uri": CALLBACK, "state": state,
         "code_challenge": challenge, "code_challenge_method": "S256", "scope": "openid email offline_access"}
    r = c.get(f"{URL}/oauth/authorize?" + urllib.parse.urlencode(q), headers=h)
    loc = r.headers.get("location", "")
    auth_id = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query).get("authorization_id", [None])[0]
    assert auth_id, f"authorize: {r.status_code} no authorization_id"
    bh = {**h, "authorization": f"Bearer {web}"}
    r = c.get(f"{URL}/oauth/authorizations/{auth_id}", headers=bh); r.raise_for_status()
    detail = r.json()
    redirect = detail.get("redirect_url") or detail.get("redirect_to")
    if not redirect:
        r = c.post(f"{URL}/oauth/authorizations/{auth_id}/consent", headers=bh, json={"action": "approve"}); r.raise_for_status()
        redirect = r.json().get("redirect_url") or r.json().get("redirect_to")
    params = urllib.parse.parse_qs(urllib.parse.urlparse(redirect).query)
    assert params.get("state", [None])[0] == state, "state mismatch"
    code = params["code"][0]
    r = c.post(f"{URL}/oauth/token", headers={**h, "content-type": "application/x-www-form-urlencoded"},
               data={"grant_type": "authorization_code", "client_id": CLIENT, "redirect_uri": CALLBACK, "code": code, "code_verifier": verifier})
    r.raise_for_status(); tok = r.json()
    c.post(f"{URL}/logout?scope=local", headers=bh)
    claims = json.loads(base64.urlsafe_b64decode(tok["access_token"].split(".")[1] + "=="))
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"access_token": tok["access_token"], "user_id": claims["sub"], "session_id": claims.get("session_id"), "client_id": claims.get("client_id")}, f)
    print(json.dumps({"minted": True, "client_id": claims.get("client_id"), "session_id": claims.get("session_id"), "user_id": claims["sub"]}))
