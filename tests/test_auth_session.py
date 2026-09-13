"""Race tests for the local host credential fence.

These exercise the actual FastAPI routers with an ASGI transport; no network or
local database is used.  The deferred verifier models an older POST completing
after logout has already rotated the process-local generation.
"""
from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from app.api import cloud_sync_routes, token_routes
from app.services import auth_session
from app.services.auth_session import AuthSessionCoordinator, SessionFenceError


class Repo:
    def __init__(self) -> None:
        self.row: dict | None = None

    async def get(self):
        return self.row.copy() if self.row else None

    async def save(self, **kwargs):
        self.row = {
            "access_token": kwargs["access_token"],
            "user_id": kwargs["user_id"],
            "refresh_token": kwargs["refresh_token"],
            "expires_at": kwargs["expires_at"],
        }

    async def clear(self):
        self.row = None


class Registry:
    async def reconcile_operation(self, *_args):
        return None


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(token_routes.router)
    app.include_router(cloud_sync_routes.router)
    return app


def _install_test_seams(monkeypatch, repo: Repo) -> AuthSessionCoordinator:
    coordinator = AuthSessionCoordinator(repo)
    monkeypatch.setattr(auth_session, "_coordinator", coordinator)
    monkeypatch.setattr(token_routes, "get_action_needed_registry", lambda: Registry())
    monkeypatch.setattr(token_routes, "_broadcast_enabled", lambda: False)
    monkeypatch.setattr(token_routes, "set_jwt_cache", lambda _token: None)
    monkeypatch.setattr(token_routes, "clear_jwt_cache", lambda: None)
    monkeypatch.setattr(token_routes, "invalidate_token", lambda _token: None)

    def no_background(coro, **_kwargs):
        coro.close()

    monkeypatch.setattr(token_routes, "fire_and_forget", no_background)
    return coordinator


def test_deferred_asgi_post_cannot_commit_after_delete(monkeypatch) -> None:
    async def scenario() -> None:
        repo = Repo()
        coordinator = _install_test_seams(monkeypatch, repo)
        started, release = asyncio.Event(), asyncio.Event()

        async def verify(token: str):
            if token == "old":
                started.set()
                await release.wait()
            return SimpleNamespace(
                status="verified", user=SimpleNamespace(user_id="actor-a")
            )

        monkeypatch.setattr(token_routes, "verify_supabase_token_result", verify)
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            initial = await client.post(
                "/auth/token",
                json={
                    "access_token": "current", "user_id": "actor-a",
                    "expected_generation": (await coordinator.snapshot()).generation,
                    "expected_credential_revision": 0,
                },
            )
            assert initial.status_code == 200
            receipt = await coordinator.snapshot()
            old_post = asyncio.create_task(
                client.post(
                    "/auth/token",
                    json={
                        "access_token": "old", "user_id": "actor-a",
                        "expected_generation": receipt.generation,
                        "expected_credential_revision": receipt.credential_revision,
                    },
                )
            )
            await started.wait()
            deleted = await client.delete(
                "/auth/token",
                params={
                    "expected_generation": receipt.generation,
                    "expected_credential_revision": receipt.credential_revision,
                },
            )
            assert deleted.status_code == 200
            release.set()
            assert (await old_post).status_code == 409
            assert repo.row is None
            stale_delete = await client.delete(
                "/auth/token",
                params={
                    "expected_generation": receipt.generation,
                    "expected_credential_revision": receipt.credential_revision,
                },
            )
            assert stale_delete.status_code == 409

    asyncio.run(scenario())


def test_rejected_old_verification_cannot_clear_current_session(monkeypatch) -> None:
    async def scenario() -> None:
        repo = Repo()
        coordinator = _install_test_seams(monkeypatch, repo)

        async def verify(token: str):
            if token == "rejected":
                return SimpleNamespace(status="invalid", user=None)
            return SimpleNamespace(status="verified", user=SimpleNamespace(user_id="actor-a"))

        monkeypatch.setattr(token_routes, "verify_supabase_token_result", verify)
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            state = await coordinator.snapshot()
            accepted = await client.post("/auth/token", json={"access_token":"current", "user_id":"actor-a", "expected_generation":state.generation, "expected_credential_revision":0})
            assert accepted.status_code == 200
            state = await coordinator.snapshot()
            rejected = await client.post("/auth/token", json={"access_token":"rejected", "user_id":"actor-a", "expected_generation":state.generation, "expected_credential_revision":state.credential_revision})
            assert rejected.status_code == 401
            assert repo.row and repo.row["access_token"] == "current"

    asyncio.run(scenario())


def test_concurrent_refresh_first_revision_wins(monkeypatch) -> None:
    async def scenario() -> None:
        repo = Repo()
        coordinator = _install_test_seams(monkeypatch, repo)

        async def verify(_token: str):
            await asyncio.sleep(0)
            return SimpleNamespace(status="verified", user=SimpleNamespace(user_id="actor-a"))

        monkeypatch.setattr(token_routes, "verify_supabase_token_result", verify)
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            state = await coordinator.snapshot()
            initial = await client.post("/auth/token", json={"access_token":"first", "user_id":"actor-a", "expected_generation":state.generation, "expected_credential_revision":0})
            assert initial.status_code == 200
            state = await coordinator.snapshot()
            one, two = await asyncio.gather(
                client.post("/auth/token", json={"access_token":"two", "user_id":"actor-a", "expected_generation":state.generation, "expected_credential_revision":state.credential_revision}),
                client.post("/auth/token", json={"access_token":"three", "user_id":"actor-a", "expected_generation":state.generation, "expected_credential_revision":state.credential_revision}),
            )
            assert sorted([one.status_code, two.status_code]) == [200, 409]
            assert repo.row and repo.row["access_token"] in {"two", "three"}

    asyncio.run(scenario())


def test_cloud_requires_exact_current_stored_token(monkeypatch) -> None:
    class Sync:
        is_orphan = False
        configured: list[tuple] = []
        def configure(self, **kwargs): self.configured.append((kwargs["jwt"], kwargs["user_id"]))
        async def register_instance(self, _registration): return None
        async def sync(self): return {"status": "in_sync"}
        def get_debug_state(self): return {"last_error": None}

    async def scenario() -> None:
        repo = Repo()
        coordinator = _install_test_seams(monkeypatch, repo)
        receipt = await coordinator.install(generation=(await coordinator.snapshot()).generation, revision=0, subject="actor-a", access_token="current", refresh_token=None, expires_at=None, allow_initialize=True)
        sync = Sync()
        monkeypatch.setattr(cloud_sync_routes, "get_settings_sync", lambda: sync)
        monkeypatch.setattr(cloud_sync_routes, "get_instance_manager", lambda: SimpleNamespace(instance_id="instance", get_registration_payload=lambda: {}))
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            wrong = await client.post("/cloud/configure", json={"jwt":"other", "user_id":"actor-a", "expected_generation":receipt.generation, "expected_credential_revision":receipt.credential_revision})
            assert wrong.status_code == 409 and not sync.configured
            good = await client.post("/cloud/configure", json={"jwt":"current", "user_id":"actor-a", "expected_generation":receipt.generation, "expected_credential_revision":receipt.credential_revision})
            assert good.status_code == 200 and sync.configured == [("current", "actor-a")]

    asyncio.run(scenario())


def test_internal_adoption_cannot_initialize_or_replace(monkeypatch) -> None:
    from app.api import ai_routes

    async def scenario() -> None:
        repo = Repo()
        coordinator = _install_test_seams(monkeypatch, repo)
        monkeypatch.setitem(sys.modules, "jwt", SimpleNamespace(decode=lambda *_args, **_kwargs: {"sub":"actor-a", "exp": 4_000_000_000}))
        seen: list[str] = []
        monkeypatch.setattr("app.services.ai.engine.set_jwt_cache", seen.append)
        await ai_routes._adopt_request_jwt("token-a", "actor-a")
        assert repo.row is None and not seen
        receipt = await coordinator.install(generation=(await coordinator.snapshot()).generation, revision=0, subject="actor-a", access_token="current", refresh_token=None, expires_at=None, allow_initialize=True)
        await ai_routes._adopt_request_jwt("token-a", "actor-a")
        assert repo.row and repo.row["access_token"] == "token-a" and seen == ["token-a"]
        before = await coordinator.snapshot()
        with __import__("pytest").raises(SessionFenceError):
            await coordinator.install(generation=receipt.generation, revision=receipt.credential_revision, subject="actor-b", access_token="bad", refresh_token=None, expires_at=None, allow_initialize=False)
        assert (await coordinator.snapshot()) == before

    asyncio.run(scenario())

def test_clear_failure_rotates_before_io_and_exact_retry_is_single_cycle() -> None:
    class FailingRepo(Repo):
        def __init__(self): super().__init__(); self.fail = True
        async def clear(self):
            if self.fail: raise OSError("disk")
            await super().clear()
    async def scenario() -> None:
        repo = FailingRepo(); repo.row = {"access_token":"a", "user_id":"actor-a"}
        coordinator = AuthSessionCoordinator(repo); before = await coordinator.snapshot()
        with __import__("pytest").raises(SessionFenceError):
            await coordinator.clear(generation=before.generation, revision=before.credential_revision)
        fenced = await coordinator.snapshot()
        assert fenced.generation != before.generation and fenced.credential_revision == 0 and fenced.subject is None
        with __import__("pytest").raises(SessionFenceError):
            await coordinator.clear(generation=before.generation, revision=before.credential_revision)
        with __import__("pytest").raises(SessionFenceError):
            await coordinator.clear(generation=fenced.generation, revision=0)
        await coordinator.finish_cleanup(retired_generation=before.generation, new_generation=fenced.generation, attempt_id=coordinator._cleanup_attempt or "", success=False)
        repo.fail = False
        retried = await coordinator.clear(generation=fenced.generation, revision=0)
        assert retried and retried.snapshot.generation == fenced.generation
        await coordinator.finish_cleanup(retired_generation=before.generation, new_generation=fenced.generation, attempt_id=retried.attempt_id, success=True)
        with __import__("pytest").raises(SessionFenceError):
            await coordinator.finish_cleanup(retired_generation=before.generation, new_generation=fenced.generation, attempt_id=retried.attempt_id, success=True)
    asyncio.run(scenario())

def test_real_sqlite_token_repo_clear_fence(tmp_path) -> None:
    from app.services.local_db.database import LocalDatabase
    from app.services.local_db.repositories import TokenRepo
    async def scenario() -> None:
        db = LocalDatabase(tmp_path / "isolated" / "matrx.db")
        await db.connect()
        try:
            repo = TokenRepo(db)
            await repo.save(access_token="test-current", refresh_token=None, user_id="actor-a", expires_at=None)
            coordinator = AuthSessionCoordinator(repo)
            before = await coordinator.snapshot()
            cleared = await coordinator.clear(generation=before.generation, revision=before.credential_revision, token="test-current")
            assert cleared is not None and await repo.get() is None
            assert cleared.snapshot.generation != before.generation
            with __import__("pytest").raises(SessionFenceError):
                await coordinator.install(generation=before.generation, revision=before.credential_revision, subject="actor-a", access_token="test-old", refresh_token=None, expires_at=None, allow_initialize=True)
            await coordinator.finish_cleanup(retired_generation=cleared.retired_generation, new_generation=cleared.snapshot.generation, attempt_id=cleared.attempt_id, success=True)
        finally:
            await db.close()
    asyncio.run(scenario())

def test_asgi_rejected_exact_stored_token_rotates_but_stale_rejection_does_not(monkeypatch) -> None:
    async def scenario() -> None:
        repo = Repo(); coordinator = _install_test_seams(monkeypatch, repo)
        async def verify(token: str):
            return SimpleNamespace(status="invalid", user=None) if token.startswith("bad") else SimpleNamespace(status="verified", user=SimpleNamespace(user_id="actor-a"))
        monkeypatch.setattr(token_routes, "verify_supabase_token_result", verify)
        cleaned: list[str] = []
        async def teardown(result):
            cleaned.append(result.retired_generation)
            await auth_session.get_auth_session().finish_cleanup(retired_generation=result.retired_generation, new_generation=result.snapshot.generation, attempt_id=result.attempt_id, success=True)
        monkeypatch.setattr(token_routes, "_teardown_cleared_session", teardown)
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            initial_state = await coordinator.snapshot()
            await coordinator.install(generation=initial_state.generation, revision=0, subject="actor-a", access_token="bad-current", refresh_token=None, expires_at=None, allow_initialize=True)
            state = await coordinator.snapshot()
            initial = await client.post("/auth/token", json={"access_token":"bad-current", "user_id":"actor-a", "expected_generation":state.generation, "expected_credential_revision":state.credential_revision})
            assert initial.status_code == 401
            assert repo.row is None and len(cleaned) == 1
            state = await coordinator.snapshot()
            # A delayed old rejection has a stale generation and cannot clear a later row.
            await coordinator.install(generation=state.generation, revision=0, subject="actor-a", access_token="current", refresh_token=None, expires_at=None, allow_initialize=True)
            stale = await client.post("/auth/token", json={"access_token":"bad-old", "user_id":"actor-a", "expected_generation":"11111111-1111-4111-8111-111111111111", "expected_credential_revision":0})
            assert stale.status_code == 401 and repo.row and repo.row["access_token"] == "current"
    asyncio.run(scenario())

def test_retry_retains_actor_context_rejects_overlap_and_stale_attempt_finisher() -> None:
    async def scenario() -> None:
        repo = Repo(); repo.row = {"access_token":"a", "user_id":"actor-a"}
        coordinator = AuthSessionCoordinator(repo); before = await coordinator.snapshot()
        first = await coordinator.clear(generation=before.generation, revision=0)
        assert first and first.row and first.row["user_id"] == "actor-a"
        await coordinator.finish_cleanup(retired_generation=first.retired_generation, new_generation=first.snapshot.generation, attempt_id=first.attempt_id, success=False)
        retried = await coordinator.clear(generation=first.snapshot.generation, revision=0)
        assert retried and retried.row and retried.row["user_id"] == "actor-a" and retried.attempt_id != first.attempt_id
        with __import__("pytest").raises(SessionFenceError):
            await coordinator.clear(generation=retried.snapshot.generation, revision=0)
        with __import__("pytest").raises(SessionFenceError):
            await coordinator.finish_cleanup(retired_generation=first.retired_generation, new_generation=first.snapshot.generation, attempt_id=first.attempt_id, success=True)
        await coordinator.finish_cleanup(retired_generation=retried.retired_generation, new_generation=retried.snapshot.generation, attempt_id=retried.attempt_id, success=True)
    asyncio.run(scenario())

def test_asgi_teardown_failure_retries_same_retired_broadcast_actor(monkeypatch) -> None:
    async def scenario() -> None:
        repo = Repo(); coordinator = _install_test_seams(monkeypatch, repo)
        initial = await coordinator.snapshot()
        installed = await coordinator.install(generation=initial.generation, revision=0, subject="actor-a", access_token="current", refresh_token=None, expires_at=None, allow_initialize=True)
        # Route composition imports these real modules; replace their local side effects,
        # leaving the actual token DELETE -> _teardown_cleared_session call intact.
        import app.services.ai.key_manager as key_manager
        import app.services.cloud_sync.settings_sync as settings_sync
        import app.services.coding_sessions as coding_sessions
        import app.api.extension_broadcast as extension_broadcast
        monkeypatch.setattr(key_manager, "clear_vault_keys", lambda: None)
        monkeypatch.setattr(settings_sync, "get_settings_sync", lambda: SimpleNamespace(clear_credentials=lambda: None))
        monkeypatch.setattr(coding_sessions, "get_coding_session_bridge_outbox", lambda: SimpleNamespace(credentials_changed=lambda: _ok()))
        calls: list[str] = []
        async def disconnect(subject: str) -> None:
            calls.append(subject)
            if len(calls) == 1: raise RuntimeError("disconnect failed")
        monkeypatch.setattr(extension_broadcast, "disconnect_broadcast", disconnect)
        monkeypatch.setattr(token_routes, "_broadcast_enabled", lambda: True)
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            failed = await client.delete("/auth/token", params={"expected_generation":installed.generation, "expected_credential_revision":installed.credential_revision})
            assert failed.status_code == 503 and calls == ["actor-a"]
            fence = await coordinator.snapshot()
            stale = await client.delete("/auth/token", params={"expected_generation":installed.generation, "expected_credential_revision":installed.credential_revision})
            assert stale.status_code == 409
            retried = await client.delete("/auth/token", params={"expected_generation":fence.generation, "expected_credential_revision":0})
            assert retried.status_code == 200 and calls == ["actor-a", "actor-a"]
    async def _ok() -> None: return None
    asyncio.run(scenario())

def test_asgi_rejected_exact_token_teardown_failure_is_fixed_503(monkeypatch) -> None:
    async def scenario() -> None:
        repo = Repo(); coordinator = _install_test_seams(monkeypatch, repo)
        state = await coordinator.snapshot()
        installed = await coordinator.install(generation=state.generation, revision=0, subject="actor-a", access_token="bad-current", refresh_token=None, expires_at=None, allow_initialize=True)
        async def verify(_token: str): return SimpleNamespace(status="invalid", user=None)
        async def fail(_result): raise RuntimeError("internal detail")
        monkeypatch.setattr(token_routes, "verify_supabase_token_result", verify)
        monkeypatch.setattr(token_routes, "_teardown_cleared_session", fail)
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/auth/token", json={"access_token":"bad-current", "user_id":"actor-a", "expected_generation":installed.generation, "expected_credential_revision":installed.credential_revision})
        assert response.status_code == 503
        assert response.json()["detail"] == {"code":"session_cleanup_failed", "message":"Engine credential cleanup did not finish. Retry sign-out."}
    asyncio.run(scenario())
