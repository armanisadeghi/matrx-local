"""Notes account boundaries, using migrated temporary SQLite and no cloud writes."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.documents import supabase_client
from app.services.documents.sync_engine import SyncEngine
from app.services.local_db.database import LocalDatabase
from app.services.local_db.repositories import NotesRepo
from tests.characterization.test_documents_sync_characterization import FakeFileManager
from tests.characterization.test_notes_duplication_regression import FakeSupabaseFull


def test_real_sqlite_preserves_owner_and_scopes_pending(tmp_path: Path) -> None:
    async def check() -> None:
        db = LocalDatabase(path=tmp_path / 'notes.db')
        await db.connect()
        try:
            repo = NotesRepo(db)
            await repo.upsert({'id': 'owned', 'user_id': 'account-a', 'content': 'keep', 'sync_status': 'failed'})
            for owner in ('account-b', ''):
                with pytest.raises(PermissionError):
                    await repo.upsert({'id': 'owned', 'user_id': owner, 'content': 'wrong'})
            row = await repo.get('owned')
            assert row['user_id'] == 'account-a' and row['content'] == 'keep'
            assert await repo.list_pending_push('account-b') == []
            assert [n['id'] for n in await repo.list_pending_push('account-a')] == ['owned']
            await repo.upsert({**row, 'content': 'own edit'})
            assert (await repo.get('owned'))['content'] == 'own edit'
            await repo.upsert({'id': 'legacy', 'user_id': '', 'content': 'legacy'})
            with pytest.raises(PermissionError):
                await repo.upsert({'id': 'legacy', 'user_id': 'account-a', 'content': 'adopted'})
            assert (await repo.get('legacy'))['content'] == 'legacy'
        finally:
            await db.close()
    asyncio.run(check())


def test_foreign_retry_and_direct_push_preserve_real_row_and_file(tmp_path: Path) -> None:
    async def check() -> None:
        db = LocalDatabase(path=tmp_path / 'notes.db')
        await db.connect()
        try:
            repo = NotesRepo(db)
            await repo.upsert({'id': 'owned', 'user_id': 'account-a', 'content': 'keep', 'file_path': 'General/note.md', 'sync_status': 'failed'})
            before = await repo.get('owned')
            fm = FakeFileManager(tmp_path)
            fm.notes['General/note.md'] = 'keep'
            sb = FakeSupabaseFull()
            engine = SyncEngine(fm=fm, sb=sb)
            engine._device_id = 'isolated-test-device'
            engine._get_notes_repo = lambda: repo
            engine.configure('account-b', 'test-token-b')
            sb.calls.clear()
            result = await engine.push_all()
            assert result['pushed'] == 0
            result = await engine.push_note('owned', 'note', 'wrong', file_path='General/note.md')
            assert result['_deferred_account'] and not result['_synced_to_cloud']
            assert sb.calls == []
            assert fm.notes == {'General/note.md': 'keep'}
            assert await repo.get('owned') == before
            engine.configure('account-a', 'test-token-a')
            result = await engine.push_note('owned', 'note', 'own edit', file_path='General/note.md')
            assert result['_synced_to_cloud']
            assert (await repo.get('owned'))['sync_status'] == 'synced'
            assert fm.notes['General/note.md'] == 'own edit'
        finally:
            await db.close()
    asyncio.run(check())


def test_task_switch_keeps_note_owner_and_transport_token_paired(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(supabase_client, '_REST_BASE', 'http://127.0.0.1:1')
    async def check() -> None:
        db = LocalDatabase(path=tmp_path / 'notes.db')
        await db.connect()
        try:
            repo = NotesRepo(db)
            await repo.upsert({'id': 'owned', 'user_id': 'account-a', 'file_path': 'General/note.md'})
            client = supabase_client.SupabaseDocClient()
            engine = SyncEngine(fm=FakeFileManager(tmp_path), sb=client)
            engine._get_notes_repo = lambda: repo
            engine._device_id = 'isolated-test-device'
            waiting, resume = asyncio.Event(), asyncio.Event()
            seen = []
            async def folders(user_id):
                waiting.set()
                await resume.wait()
                seen.append(('folder', user_id, client._headers()['Authorization']))
                return []
            async def upsert(**kwargs):
                seen.append(('push', kwargs['user_id'], client._headers()['Authorization']))
                return {'id': kwargs['note_id'], 'sync_version': 1}
            monkeypatch.setattr(client, 'list_folders', folders)
            monkeypatch.setattr(client, 'upsert_note', upsert)
            engine.configure('account-a', 'test-token-a')
            task = asyncio.create_task(engine.push_note('owned', 'note', 'body', file_path='General/note.md'))
            await waiting.wait()
            engine.configure('account-b', 'test-token-b')
            resume.set()
            result = await task
            assert result['_synced_to_cloud']
            assert seen == [('folder', 'account-a', 'Bearer test-token-a'), ('push', 'account-a', 'Bearer test-token-a')]
            assert engine._user_id == 'account-b'
            assert client._headers()['Authorization'] == 'Bearer test-token-b'
        finally:
            await db.close()
    asyncio.run(check())


def test_account_state_does_not_adopt_legacy_or_other_account(tmp_path: Path) -> None:
    fm = FakeFileManager(tmp_path)
    fm.state = {'note_hashes': {'legacy.md': 'old'}, 'last_pull_at': 'old', 'delete_breaker': {'tripped': True}}
    engine = SyncEngine(fm=fm, sb=FakeSupabaseFull())
    engine.configure('account-a', 'test-token-a')
    assert engine._load_sync_state()['note_hashes'] == {}
    engine._save_sync_state({'note_hashes': {'a.md': 'a'}, 'last_pull_at': 'cursor-a'})
    engine.configure('account-b', 'test-token-b')
    assert engine._load_sync_state()['note_hashes'] == {}
    engine._save_sync_state({'note_hashes': {'b.md': 'b'}, 'last_pull_at': 'cursor-b'})
    engine.configure('account-a', 'test-token-a')
    assert engine._load_sync_state()['last_pull_at'] == 'cursor-a'
    assert fm.state['note_hashes'] == {'legacy.md': 'old'}
    assert fm.state['delete_breaker'] == {'tripped': True}


def test_foreign_path_pull_and_signed_out_watcher_cannot_mutate(tmp_path: Path, monkeypatch) -> None:
    from app.services.local_db.repositories import TokenRepo
    async def check() -> None:
        db = LocalDatabase(path=tmp_path / 'notes.db')
        await db.connect()
        try:
            repo = NotesRepo(db)
            await repo.upsert({'id': 'a-note', 'user_id': 'account-a', 'content': 'keep', 'file_path': 'General/note.md'})
            before = await repo.get('a-note')
            fm = FakeFileManager(tmp_path)
            fm.notes['General/note.md'] = 'keep'
            sb = FakeSupabaseFull()
            engine = SyncEngine(fm=fm, sb=sb)
            engine._get_notes_repo = lambda: repo
            engine._device_id = 'isolated-test-device'
            engine.configure('account-b', 'test-token-b')
            sb.notes['b-note'] = {'id': 'b-note', 'created_by': 'account-b', 'content': 'remote', 'file_path': 'General/note.md'}
            result = await engine.pull_note('b-note')
            assert result['_deferred_account']
            assert fm.notes == {'General/note.md': 'keep'}
            assert await repo.get('a-note') == before
            assert await repo.get('b-note') is None
            async def signed_out(_self):
                return None
            monkeypatch.setattr(TokenRepo, 'get', signed_out)
            engine.configure('account-a', 'stale-inherited-token')
            sb.calls.clear()
            await engine._handle_external_change('General/note.md')
            assert await repo.get('a-note') == before
            assert sb.calls == []
        finally:
            await db.close()
    asyncio.run(check())


def test_foreign_route_update_refuses_before_file_write(tmp_path: Path, monkeypatch) -> None:
    from starlette.requests import Request
    from fastapi import HTTPException
    from app.api import document_routes
    async def check() -> None:
        db = LocalDatabase(path=tmp_path / 'notes.db')
        await db.connect()
        try:
            repo = NotesRepo(db)
            await repo.upsert({'id': 'a-note', 'user_id': 'account-a', 'content': 'keep', 'file_path': 'General/note.md'})
            before = await repo.get('a-note')
            fm = FakeFileManager(tmp_path)
            fm.notes['General/note.md'] = 'keep'
            monkeypatch.setattr(document_routes, '_get_notes_repo', lambda: repo)
            monkeypatch.setattr(document_routes, 'file_manager', fm)
            monkeypatch.setattr(document_routes, '_configure_sync', lambda request: None)
            request = Request({'type': 'http', 'headers': [(b'x-user-id', b'account-b')]})
            with pytest.raises(HTTPException) as exc:
                await document_routes.update_note('a-note', document_routes.UpdateNoteRequest(content='wrong'), request)
            assert exc.value.status_code == 404
            assert fm.notes == {'General/note.md': 'keep'}
            assert await repo.get('a-note') == before
        finally:
            await db.close()
    asyncio.run(check())


def test_push_uses_account_cas_hash_instead_of_legacy_hash(tmp_path: Path) -> None:
    from app.services.documents.file_manager import content_hash
    from tests.characterization.test_documents_sync_characterization import FakeNotesRepo
    async def check() -> None:
        repo = FakeNotesRepo()
        repo.rows['owned'] = {'id': 'owned', 'user_id': 'account-a', 'file_path': 'General/note.md'}
        fm = FakeFileManager(tmp_path)
        fm.state = {'note_hashes': {'General/note.md': 'wrong-legacy'}, 'accounts': {'account-a': {'note_hashes': {'General/note.md': content_hash('old')}}}}
        sb = FakeSupabaseFull()
        sb.notes['owned'] = {'id': 'owned', 'created_by': 'account-a', 'content': 'old', 'content_hash': content_hash('old')}
        engine = SyncEngine(fm=fm, sb=sb)
        engine._get_notes_repo = lambda: repo
        engine._device_id = 'isolated-test-device'
        engine.configure('account-a', 'test-token-a')
        result = await engine.push_note('owned', 'note', 'new', file_path='General/note.md')
        assert result['_synced_to_cloud']
        assert [kind for kind, _ in sb.calls if kind != 'set_jwt'] == ['update_note_if_unchanged']
        assert fm.state['note_hashes']['General/note.md'] == 'wrong-legacy'
    asyncio.run(check())


@pytest.mark.parametrize('action', ['rename', 'delete'])
def test_unknown_folder_contents_block_before_mutation(tmp_path: Path, monkeypatch, action: str) -> None:
    from fastapi import HTTPException
    from starlette.requests import Request
    from app.api import document_routes
    from tests.characterization.test_documents_sync_characterization import FakeNotesRepo
    fm = FakeFileManager(tmp_path)
    fm.notes['General/unindexed.md'] = 'keep'
    fm.list_folders = lambda: ['General']
    mutations = []
    fm.rename_folder = lambda *args: mutations.append(('rename', args))
    fm.delete_folder = lambda *args: mutations.append(('delete', args))
    repo = FakeNotesRepo()
    monkeypatch.setattr(document_routes, 'file_manager', fm)
    monkeypatch.setattr(document_routes, '_get_notes_repo', lambda: repo)
    monkeypatch.setattr(document_routes, '_configure_sync', lambda request: None)
    engine = SyncEngine(fm=fm, sb=FakeSupabaseFull())
    engine._device_id = 'isolated-test-device'
    monkeypatch.setattr(document_routes, 'sync_engine', engine)
    request = Request({'type': 'http', 'headers': [(b'x-user-id', b'account-b')]})
    folder_id = document_routes._folder_id_for_name('General')
    async def check() -> None:
        with pytest.raises(HTTPException) as exc:
            if action == 'rename':
                await document_routes.update_folder(folder_id, document_routes.UpdateFolderRequest(name='Moved'), request)
            else:
                await document_routes.delete_folder(folder_id, request)
        assert exc.value.status_code == 404
        assert mutations == []
        assert repo.soft_deleted == []
        assert fm.notes['General/unindexed.md'] == 'keep'
    asyncio.run(check())


def test_sync_status_route_scopes_real_queue(tmp_path: Path, monkeypatch) -> None:
    from starlette.requests import Request
    from app.api import document_routes
    async def check() -> None:
        db = LocalDatabase(path=tmp_path / 'notes.db')
        await db.connect()
        try:
            repo = NotesRepo(db)
            await repo.upsert({'id': 'a', 'user_id': 'account-a', 'sync_status': 'failed'})
            await repo.upsert({'id': 'b', 'user_id': 'account-b', 'sync_status': 'pending_push'})
            engine = SyncEngine(fm=FakeFileManager(tmp_path), sb=FakeSupabaseFull())
            engine._device_id = 'isolated-test-device'
            engine.configure('account-b', 'test-token-b')
            monkeypatch.setattr(document_routes, 'sync_engine', engine)
            monkeypatch.setattr(document_routes, '_get_notes_repo', lambda: repo)
            monkeypatch.setattr(document_routes, '_configure_sync', lambda request: None)
            request = Request({'type': 'http', 'headers': [(b'x-user-id', b'account-b')]})
            result = await document_routes.sync_status(request)
            assert result['pending_push_count'] == 1
            assert result['excluded_count'] == 0
        finally:
            await db.close()
    asyncio.run(check())


def test_successful_pull_persists_only_account_hash(tmp_path: Path) -> None:
    from app.services.documents.file_manager import content_hash
    from tests.characterization.test_documents_sync_characterization import FakeNotesRepo
    async def check() -> None:
        fm = FakeFileManager(tmp_path)
        fm.state = {'note_hashes': {'legacy.md': 'legacy'}, 'accounts': {'account-b': {'note_hashes': {'b.md': 'b'}}}}
        sb = FakeSupabaseFull()
        sb.notes['a'] = {'id': 'a', 'created_by': 'account-a', 'file_path': 'General/a.md', 'content': 'body', 'content_hash': content_hash('body')}
        repo = FakeNotesRepo()
        engine = SyncEngine(fm=fm, sb=sb)
        engine._get_notes_repo = lambda: repo
        engine._device_id = 'isolated-test-device'
        engine.configure('account-a', 'test-token-a')
        result = await engine.pull_note('a')
        assert result and not result.get('_deferred_account')
        assert fm.state['accounts']['account-a']['note_hashes'] == {'General/a.md': content_hash('body')}
        assert 'accounts' not in fm.state['accounts']['account-a']
        assert fm.state['accounts']['account-b'] == {'note_hashes': {'b.md': 'b'}}
        assert fm.state['note_hashes'] == {'legacy.md': 'legacy'}
    asyncio.run(check())


def test_breaker_reset_preserves_all_account_checkpoints(tmp_path: Path) -> None:
    fm = FakeFileManager(tmp_path)
    accounts = {'account-a': {'note_hashes': {'a': 'a'}}, 'account-b': {'note_hashes': {'b': 'b'}}}
    fm.state = {'accounts': accounts, 'note_hashes': {'legacy': 'legacy'}, 'delete_breaker': {'reason': 'test'}, 'delete_window': {'n': 1}, 'remote_live_count': 100}
    engine = SyncEngine(fm=fm, sb=FakeSupabaseFull())
    engine.configure('account-a', 'test-token-a')
    assert engine.reset_delete_breaker()['cleared'] == {'reason': 'test'}
    assert fm.state['accounts'] == accounts
    assert fm.state['note_hashes'] == {'legacy': 'legacy'}
    assert fm.state['remote_live_count'] == 100
    assert fm.state['delete_window'] == {}
    assert not engine.delete_breaker_tripped


@pytest.mark.parametrize('account_recent,legacy_recent,expected_full', [(True, False, 0), (False, True, 1)])
def test_auto_sync_full_reconcile_uses_account_timestamp(tmp_path: Path, monkeypatch, account_recent, legacy_recent, expected_full) -> None:
    import time
    from app.services.local_db.repositories import TokenRepo
    from unittest.mock import AsyncMock
    now = time.time()
    fm = FakeFileManager(tmp_path)
    fm.state = {'note_hashes': {}, 'last_full_sync': now if legacy_recent else 0, 'accounts': {'account-a': {'note_hashes': {}, 'last_full_sync': now if account_recent else 0}}}
    engine = SyncEngine(fm=fm, sb=FakeSupabaseFull())
    engine._device_id = 'isolated-test-device'
    monkeypatch.setattr(TokenRepo, 'get', AsyncMock(return_value={'user_id': 'account-a', 'access_token': 'test-token-a'}))
    monkeypatch.setattr(TokenRepo, 'is_expired', lambda *args: False)
    monkeypatch.setattr(engine, 'start_watcher', AsyncMock())
    monkeypatch.setattr(engine, 'pull_changes', AsyncMock(return_value={}))
    monkeypatch.setattr(engine, 'push_all', AsyncMock(return_value={}))
    full = AsyncMock(return_value={})
    monkeypatch.setattr(engine, 'full_sync', full)
    asyncio.run(engine._auto_sync_tick())
    assert full.await_count == expected_full
