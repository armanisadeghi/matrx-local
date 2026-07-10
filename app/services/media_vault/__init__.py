"""Private Media Vault — encrypted, password-locked store for library media.

Kept import-light on purpose: ``crypto`` and ``escrow`` have NO app-internal
imports so scripts/vault-recover.py can use the exact same code paths without
booting any engine machinery. Import the service via
``app.services.media_vault.service.get_vault_service()``.
"""
