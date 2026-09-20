#!/usr/bin/env python3
"""Retained source census for native Vault command and private-custody boundaries.

This guards reviewed source locations, not arbitrary code execution inside the
trusted containing process. Signing/library validation enforce its outer boundary.
"""
from __future__ import annotations

import re
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = {
    'native_vault_provider_status', 'request_native_vault_provider_enable',
    'open_native_vault_provider_settings', 'invalidate_native_vault_host_actor',
    'reconcile_native_vault_host_actor', 'native_vault_exchange',
    'native_vault_file_import',
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
    registrations = {}
    invoke_handlers = {}
    for path, text in sources.items():
        if not path.endswith('.rs'):
            continue
        text = re.sub(r'//[^\n]*', '', text)
        invocations = list(re.finditer(r'\b(?:r#)?invoke_handler\s*\(', text))
        if invocations:
            invoke_handlers[path] = len(invocations)
        for invocation in invocations:
            if not re.match(r'\s*tauri::generate_handler!\s*\[', text[invocation.end():]):
                errors.append(f'{path}: direct or aliased invoke handler bypasses reviewed registration inventory')
        entries = []
        for body in re.findall(r'(?:tauri::)?generate_handler!\s*\[([^\]]*)\]', text, re.S):
            entries.extend(entry.strip() for entry in body.split(',') if entry.strip())
        if entries:
            registrations[path] = sorted(entries)
    if invoke_handlers != {'desktop/src-tauri/src/lib.rs': 1}:
        errors.append('invoke handler count or location changed; review containing-process entry points')
    reviewed = json.loads((ROOT / 'scripts/native-vault-command-inventory.json').read_text())
    if registrations != reviewed:
        errors.append('shipped command registration inventory changed; review containing-process custody before updating inventory')
    for path, text in sources.items():
        for symbol in set(re.findall(r'\bSec(?:Item|AccessControl|Random|Task)[A-Za-z0-9_]+\b', text)) if path.endswith((".swift", ".rs", ".c", ".h", ".m", ".mm", ".cc", ".cpp", ".hpp")) else set():
            if SECURITY.get(symbol) != path:
                errors.append(f'{path}: unreviewed Security symbol {symbol}')
        for symbol in re.findall(r'#\[tauri::command\]\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)', text):
            if 'native_vault' in symbol:
                commands.add(symbol)
                expected = {
                    'native_vault_exchange': 'desktop/src-tauri/src/native_vault_exchange.rs',
                    'native_vault_file_import': 'desktop/src-tauri/src/native_vault_file_import.rs',
                }.get(symbol, 'desktop/src-tauri/src/lib.rs')
                if symbol not in COMMANDS or path != expected:
                    errors.append(f'{path}: unreviewed native command {symbol}')
        for symbol in re.findall(r'@_cdecl\("([^"]+)"\)', text):
            if symbol != 'matrx_vault_exchange_dispatch' or path != 'desktop/native-vault-provider/NativeVaultExchangeHost.swift':
                errors.append(f'{path}: unreviewed native C export {symbol}')
        for symbol in set(re.findall(r'\bmatrx_vault_\w+\b', text)):
            if symbol != 'matrx_vault_exchange_dispatch' or path not in {'desktop/native-vault-provider/NativeVaultExchangeHost.swift', 'desktop/src-tauri/src/native_vault_exchange.rs', 'desktop/src-tauri/src/native_vault_file_import.rs'}:
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
    phases = {'Idle', 'Authorizing', 'Preflighting', 'AwaitingConfirmation', 'ChoosingDestination',
              'Exporting', 'HandedToDestination', 'Cancelled', 'Failed', 'Unavailable'}
    phase_bodies = re.findall(r'pub enum Phase\s*\{(.*?)\n\}', exchange, re.S)
    if len(phase_bodies) != 1 or set(re.findall(r'^\s*([A-Z]\w*)\s*(?:[{},(]|$)', phase_bodies[0], re.M)) != phases:
        errors.append('Phase variants changed; review the public status contract')
    typescript = sources.get('desktop/src/lib/native-vault-exchange.ts', '')
    status_bodies = re.findall(r'export interface NativeVaultExchangeStatus\s*\{(.*?)\n\}', typescript, re.S)
    # Request object members contain semicolons, so end at the declaration newline.
    request_bodies = re.findall(r'export type NativeVaultExchangeRequest\s*=(.*?);\s*\n\n', typescript, re.S)
    expected_status = {'operation_id', 'phase', 'total', 'eligible', 'unsupported', 'handed_off', 'message'}
    if len(status_bodies) != 1 or set(re.findall(r'\b(\w+)\??\s*:', status_bodies[0])) != expected_status:
        errors.append('TypeScript status fields changed; private values must not cross the public bridge')
    snake_phases = {re.sub(r'(?<!^)(?=[A-Z])', '_', value).lower() for value in phases}
    if len(status_bodies) != 1 or set(re.findall(r'"([a-z_]+)"', status_bodies[0])) != snake_phases:
        errors.append('TypeScript status phases changed')
    expected_requests = [({'action', 'item_ids', 'organization_id'}, {'begin_export'}),
                         ({'action', 'operation_id'}, {'status', 'cancel', 'confirm'})]
    observed_requests = []
    if len(request_bodies) == 1:
        for body in re.findall(r'\{([^{}]*)\}', request_bodies[0]):
            observed_requests.append((set(re.findall(r'\b(\w+)\??\s*:', body)), set(re.findall(r'"([a-z_]+)"', body))))
    if observed_requests != expected_requests:
        errors.append('TypeScript request shapes changed; native selection and operation IDs only')
    host = sources.get('desktop/native-vault-provider/NativeVaultExchangeHost.swift', '')
    actions = set(re.findall(r'action == "(\w+)"', host))
    if actions != {'begin_export', 'status', 'cancel', 'confirm', 'invalidate', 'import_begin', 'import_status', 'import_preview', 'import_choose_scope', 'import_confirm', 'import_cancel', 'import_recover'}:
        errors.append(f'Native host actions changed: {sorted(actions)}')
    if 'CStr::from_ptr' in exchange or 'matrx_vault_exchange_free' in exchange:
        errors.append('exchange response must use caller-owned bounded storage')
    file_import = sources.get('desktop/src-tauri/src/native_vault_file_import.rs', '')
    import_request_bodies = re.findall(r'pub enum Request\s*\{(.*?)\n\}', file_import, re.S)
    import_variants = {'BeginFileImport', 'Status', 'Preview', 'ChooseScope', 'Confirm', 'Cancel', 'Recover'}
    if len(import_request_bodies) != 1 or set(re.findall(r'^\s*([A-Z]\w*)\s*(?:[{},(]|$)', import_request_bodies[0], re.M)) != import_variants:
        errors.append('native file import request variants changed; review public command contract')
    for forbidden in ('source', 'path', 'token', 'authorization', 'url'):
        if re.search(rf'pub struct (?:Status|Preview|Slot)\s*\{{[^}}]*\b{forbidden}\b', file_import, re.S):
            errors.append(f'native file import public response contains forbidden {forbidden} field')
    import_typescript = sources.get('desktop/src/lib/native-vault-file-import.ts', '')
    expected_import_actions = {'begin_file_import', 'recover', 'status', 'cancel', 'preview', 'choose_scope', 'confirm'}
    if set(re.findall(r'"([a-z_]+)"', import_typescript.split('export type NativeVaultFileImportRequest', 1)[-1].split('export type NativeVaultImportPhase', 1)[0])) != expected_import_actions:
        errors.append('TypeScript native file import actions changed; metadata-only request contract required')
    if re.search(r'\b(source|path|token|authorization|url)\b', import_typescript, re.I):
        errors.append('TypeScript native file import contract contains private material')
    return errors


def collect(root: Path) -> dict[str, str]:
    result = {}
    for base in ('desktop/src', 'desktop/src-tauri/src', 'desktop/native-vault-provider', 'app'):
        for directory, directories, files in os.walk(root / base):
            directories[:] = [name for name in directories if name not in EXCLUDED]
            for name in files:
                path = Path(directory) / name
                if path.suffix not in {'.rs', '.swift', '.ts', '.tsx', '.py', '.c', '.h', '.m', '.mm', '.cc', '.cpp', '.hpp'} or '.test.' in name or '.spec.' in name:
                    continue
                result[path.relative_to(root).as_posix()] = path.read_text()
    return result


if __name__ == '__main__':
    failures = census(collect(ROOT))
    if failures:
        raise SystemExit('\n'.join(failures))
    print('PASS native Vault source custody and command census')
