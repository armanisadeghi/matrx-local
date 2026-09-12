# Protected-purpose desktop adoption
- Adoption commit: `3a887f8fd`.
- `matrx-orm` floor and lock: 3.1.141; wheel hash matches the approved PyPI proof.
- Required resolver compatibility: `matrx-utils` 2.0.8 -> 2.0.38.
- Regenerated frozen-runtime manifests and target locks; the Linux image lock also selects `cuda-pathfinder` 1.8.1 through its existing resolver.
- Checks: isolated frozen sync; package protected-purpose imports; `app.main` import; Vault remains HTTP-only.
- Checks: `tests/unit/test_credential_vault_provider_keys.py` (13 passed); manifest/runtime-lock/UV lock checks; diff check.
- Gaps: no PyInstaller/Tauri artifact build, packaged smoke, release, or live Vault protected-row refusal canary was run.
