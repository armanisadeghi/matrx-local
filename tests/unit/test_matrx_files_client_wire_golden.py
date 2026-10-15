"""Golden: MatrxFilesClient puts exactly these identity headers, method, path, query and body on the wire.

Pinned against the hand-built httpx transport BEFORE it moved onto ``matrx_api`` and kept green after, so the switch
is provably a no-op on the wire. X-Request-Id is the transport's own and never an identity (except when the caller
supplies one for an upload - that value must be the one sent).
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.matrx_files.client import MatrxFilesClient, MatrxFilesHTTPError

ORG = "3f2b8c1e-0a4d-4c55-9e0f-1a2b3c4d5e6f"
FILE_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
IDENTITY = {"authorization", "x-organization-id"}


@pytest.fixture
def wire(monkeypatch):
    from app.services.aidream import organization as org_module

    async def _org(_jwt):
        return ORG

    monkeypatch.setattr(org_module, "resolve_active_organization_id", _org)
    seen: list[httpx.Request] = []
    state = {"status": 200, "json": {"ok": True}}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(state["status"], json=state["json"])

    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = MatrxFilesClient(base_url="https://files.example/")
    client.set_jwt("user-jwt")
    return client, seen, state


def _identity(request: httpx.Request) -> dict[str, str]:
    return {k: v for k, v in request.headers.items() if k in IDENTITY}


@pytest.mark.anyio
async def test_sync_changes_get_with_query_and_identity(wire):
    client, seen, _ = wire
    out = await client.sync_changes(cursor="c1", limit=50)
    req = seen[0]
    assert out == {"ok": True}
    assert req.method == "GET"
    assert req.url.scheme + "://" + req.url.host + req.url.path == "https://files.example/files/sync/changes"
    assert dict(req.url.params) == {"limit": "50", "cursor": "c1"}
    assert _identity(req) == {"authorization": "Bearer user-jwt", "x-organization-id": ORG}


@pytest.mark.anyio
async def test_record_patch_delete_restore_routes(wire):
    client, seen, _ = wire
    await client.get_record(FILE_ID)
    await client.patch(FILE_ID, name="n", folder="f")
    await client.soft_delete(FILE_ID)
    await client.restore(FILE_ID)
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", f"/files/{FILE_ID}"),
        ("PATCH", f"/files/{FILE_ID}"),
        ("DELETE", f"/files/{FILE_ID}"),
        ("POST", f"/files/{FILE_ID}/restore"),
    ]
    assert json.loads(seen[1].content) == {"name": "n", "folder": "f"}
    for r in seen:
        assert _identity(r) == {"authorization": "Bearer user-jwt", "x-organization-id": ORG}


@pytest.mark.anyio
async def test_upload_is_multipart_with_caller_request_id_and_idempotency_key(wire):
    client, seen, _ = wire
    await client.upload(
        file_path="a/b.txt", content=b"hello", filename="b.txt", mime_type="text/plain",
        metadata={"k": 1}, request_id="req-123", idempotency_key="idem-9", intent="force_new_copy", reason="r",
    )
    req = seen[0]
    assert (req.method, req.url.path) == ("POST", "/files/upload")
    assert req.headers["content-type"].startswith("multipart/form-data")
    body = req.content
    for needle in (b'name="file_path"', b"a/b.txt", b'name="intent"', b"force_new_copy", b'name="reason"',
                   b'name="metadata_json"', b'{"k":1}', b'filename="b.txt"', b"text/plain", b"hello"):
        assert needle in body
    assert req.headers["x-request-id"] == "req-123"
    assert req.headers["x-idempotency-key"] == "idem-9"
    assert _identity(req) == {"authorization": "Bearer user-jwt", "x-organization-id": ORG}


@pytest.mark.anyio
async def test_empty_204_is_an_empty_dict_and_http_error_carries_status_and_body(wire):
    client, seen, state = wire
    state["status"], state["json"] = 409, {"detail": "conflict"}
    with pytest.raises(MatrxFilesHTTPError) as excinfo:
        await client.get_record(FILE_ID)
    assert excinfo.value.status_code == 409
    assert "conflict" in str(excinfo.value)


@pytest.mark.anyio
async def test_no_jwt_refuses_before_sending(wire):
    client, seen, _ = wire
    client.set_jwt(None)
    with pytest.raises(RuntimeError):
        await client.get_record(FILE_ID)
    assert seen == []
