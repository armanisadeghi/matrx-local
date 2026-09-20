"""Mutations must reopen the native custody boundary instead of passing green."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location('custody_census', ROOT / 'scripts/check-native-vault-custody.py')
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_real_shipped_sources_satisfy_custody_contract():
    assert module.census(module.collect(ROOT)) == []


@pytest.mark.parametrize(('path', 'addition'), [
    ('desktop/src-tauri/src/lib.rs', '\n#[tauri::command]\nasync fn native_vault_reveal() {}'),
    ('desktop/src-tauri/src/native_vault_exchange.rs', '\nfn matrx_vault_read_secret();'),
    ('desktop/native-vault-provider/NativeVaultExchangeHost.swift', '\n@_cdecl("matrx_vault_secret")\nfunc extra() {}'),
    ('desktop/native-vault-provider/NativeVaultTransport.swift', '\nlet reader = Security.SecItemCopyMatching'),
    ('desktop/src-tauri/src/native_vault_exchange.rs', '\nuse Security::SecItemCopyMatching as hidden;'),
    ('desktop/src/lib/native-vault-exchange.ts', '\nconst value: NativeExportSourcePkcs8 = {}'),
])
def test_new_private_or_command_reference_fails_even_through_alias(path, addition):
    sources = module.collect(ROOT)
    sources[path] += addition
    assert module.census(sources)


def test_new_response_field_fails():
    sources = module.collect(ROOT)
    path = 'desktop/src-tauri/src/native_vault_exchange.rs'
    sources[path] = sources[path].replace('pub struct Status {', 'pub struct Status {\n    source: Vec<u8>,')
    assert module.census(sources)


def test_descendant_worktree_and_generated_test_sources_are_not_shipped(tmp_path):
    for folder in ('desktop/native-vault-provider/build', 'desktop/src/.wt/nested', 'desktop/src/tests'):
        target = tmp_path / folder / 'ignored.swift'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('SecItemCopyMatching')
    assert module.collect(tmp_path) == {}


def test_unit_request_variant_fails():
    sources = module.collect(ROOT)
    path = 'desktop/src-tauri/src/native_vault_exchange.rs'
    sources[path] = sources[path].replace('pub enum Request {', 'pub enum Request {\n    RevealSource,')
    assert module.census(sources)


@pytest.mark.parametrize('suffix', ['.c', '.h', '.m', '.mm', '.cc', '.cpp', '.hpp'])
def test_new_native_source_language_is_scanned(tmp_path, suffix):
    target = tmp_path / 'desktop/src-tauri/src' / ('leak' + suffix)
    target.parent.mkdir(parents=True)
    target.write_text('auto alias = SecItemCopyMatching;')
    sources = module.collect(ROOT)
    sources.update(module.collect(tmp_path))
    assert module.census(sources)


def test_unprefixed_swift_c_export_fails():
    sources = module.collect(ROOT)
    path = 'desktop/native-vault-provider/NativeVaultExchangeHost.swift'
    sources[path] += '\n@_cdecl("read_private")\nfunc leak() {}'
    assert module.census(sources)
