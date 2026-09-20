#!/usr/bin/env python3
"""Retained source census for native Vault command and private-custody boundaries.

This guards reviewed source locations, not arbitrary code execution inside the
trusted containing process. Signing/library validation enforce its outer boundary.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = {
    'native_vault_provider_status', 'request_native_vault_provider_enable',
    'open_native_vault_provider_settings', 'invalidate_native_vault_host_actor',
    'reconcile_native_vault_host_actor', 'native_vault_exchange',
}
SECURITY = {
    'SecItemCopyMatching': 'desktop/native-vault-provider/NativeVaultPrivateSession.swift',
    'SecItemAdd': 'desktop/native-vault-provider/NativeVaultPrivateSession.swift',
    'SecItemDelete': 'desktop/native-vault-provider/NativeVaultPrivateSession.swift',
    'SecAccessControlCreateWithFlags': 'desktop/native-vault-provider/NativeVaultPrivateSession.swift',
    'SecRandomCopyBytes': 'desktop/native-vault-provider/CredentialProviderViewController.swift',
    'SecTaskCreateFromSelf': 'desktop/src-tauri/src/native_vault_settings.rs',
    'SecTaskCopyValueForEntitlement': 'desktop/src-tauri/src/native_vault_settings.rs',
}
EXCLUDED = {'.wt', 'target', 'build', 'vendor', 'node_modules', 'tests', '__tests__', '.venv'}


def census(sources: dict[str, str]) -> list[str]:
    errors = []
    commands = set()
    for path, text in sources.items():
        for symbol in set(re.findall(r'\bSec(?:Item|AccessControl|Random|Task)[A-Za-z0-9_]+\b', text)) if path.endswith((".swift", ".rs", ".c", ".h", ".m", ".mm", ".cc", ".cpp", ".hpp")) else set():
            if SECURITY.get(symbol) != path:
                errors.append(f'{path}: unreviewed Security symbol {symbol}')
        for symbol in re.findall(r'#\[tauri::command\]\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)', text):
            if 'native_vault' in symbol:
                commands.add(symbol)
                expected = 'desktop/src-tauri/src/native_vault_exchange.rs' if symbol == 'native_vault_exchange' else 'desktop/src-tauri/src/lib.rs'
                if symbol not in COMMANDS or path != expected:
                    errors.append(f'{path}: unreviewed native command {symbol}')
        for symbol in re.findall(r'@_cdecl\("([^"]+)"\)', text):
            if symbol != 'matrx_vault_exchange_dispatch' or path != 'desktop/native-vault-provider/NativeVaultExchangeHost.swift':
                errors.append(f'{path}: unreviewed native C export {symbol}')
        for symbol in set(re.findall(r'\bmatrx_vault_\w+\b', text)):
            if symbol != 'matrx_vault_exchange_dispatch' or path not in {'desktop/native-vault-provider/NativeVaultExchangeHost.swift', 'desktop/src-tauri/src/native_vault_exchange.rs'}:
                errors.append(f'{path}: unreviewed native C symbol {symbol}')
        if not path.startswith('desktop/native-vault-provider/'):
            for symbol in set(re.findall(r'\b(?:NativeVaultPrivateSession|PrivateSessionSecurity|nativeExportSourcePkcs8|NativeExportSourcePkcs8)\b', text)):
                errors.append(f'{path}: private native type escaped: {symbol}')
    if commands != COMMANDS:
        errors.append(f'native command inventory changed: {sorted(commands ^ COMMANDS)}')
    exchange = sources.get('desktop/src-tauri/src/native_vault_exchange.rs', '')
    expected_variants = {'BeginExport', 'Status', 'Cancel', 'Confirm'}
    expected_fields = {'item_ids', 'organization_id', 'operation_id', 'phase', 'total', 'eligible', 'unsupported', 'handed_off', 'message'}
    for name, body in re.findall(r'pub (?:struct|enum) (Request|Status)\s*\{(.*?)\n\}', exchange, re.S):
        if name == 'Request':
            variants = set(re.findall(r'^\s*([A-Z]\w*)\s*(?:[{},(]|$)', body, re.M))
            if variants != expected_variants:
                errors.append(f'Request variants changed: {sorted(variants ^ expected_variants)}')
        fields = set(re.findall(r'\b(\w+)\s*:\s*\w', body))
        if fields - expected_fields:
            errors.append(f'{name}: unexpected public fields {sorted(fields - expected_fields)}')
    host = sources.get('desktop/native-vault-provider/NativeVaultExchangeHost.swift', '')
    actions = set(re.findall(r'action == "(\w+)"', host))
    if actions != {'begin_export', 'status', 'cancel', 'confirm', 'invalidate'}:
        errors.append(f'Native host actions changed: {sorted(actions)}')
    if 'CStr::from_ptr' in exchange or 'matrx_vault_exchange_free' in exchange:
        errors.append('exchange response must use caller-owned bounded storage')
    return errors


def collect(root: Path) -> dict[str, str]:
    result = {}
    for base in ('desktop/src', 'desktop/src-tauri/src', 'desktop/native-vault-provider', 'app'):
        for path in (root / base).rglob('*'):
            relative = path.relative_to(root)
            if any(part in EXCLUDED for part in relative.parts) or not path.is_file():
                continue
            if path.suffix not in {'.rs', '.swift', '.ts', '.tsx', '.py', '.c', '.h', '.m', '.mm', '.cc', '.cpp', '.hpp'} or '.test.' in path.name or '.spec.' in path.name:
                continue
            result[relative.as_posix()] = path.read_text()
    return result


if __name__ == '__main__':
    failures = census(collect(ROOT))
    if failures:
        raise SystemExit('\n'.join(failures))
    print('PASS native Vault source custody and command census')
