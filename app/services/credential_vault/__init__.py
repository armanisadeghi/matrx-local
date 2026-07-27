"""Credential Vault consumer — matrx-local reads the PLATFORM vault.

This is the desktop's client for the one platform credential system
(``users.credential_items`` + ``users.user_secrets``, served by aidream at
``{BACKEND}/api/vault/*``). It is a CONSUMER only: matrx-local never writes
to the vault and never holds a vault encryption key.

Not to be confused with ``app.services.media_vault`` — the local,
password-locked media store. Different system, different data, no overlap.

Entry points:
  * ``client``        — the four HTTP calls (list / get / reveal / resolve)
  * ``provider_keys`` — matching vault items to local AI providers

Resolution order for provider API keys is owned by
``app.services.ai.key_manager`` — read its module docstring before changing
anything here.
"""
