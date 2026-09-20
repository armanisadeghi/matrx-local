"""A HELD operation says it is waiting for a choice — never a generic refusal.

THE RULING (Arman, 2026-09-19). With nothing set on this device the work is
HELD, the user SETS an organization, and the work resumes. It never fails with
"no default organization", and — this file's subject — it never *reads* like a
failure either:

    "Nothing fails silently. Every stand-in announces itself with a remedy; a
    screen is absent or honest — never dead, disabled-looking, or lying."

Every sidecar transport used to map ``OrganizationNotResolvedError`` onto its
own generic refusal, so six lanes said six different things about one question
nobody had been asked yet. These tests FAIL if any of them drifts back:

  * each lane's refusal carries the ONE held sentence and the
    ``choose_organization`` action a screen renders as one button;
  * a genuinely blocked case (no membership at all) keeps its own words and
    offers NO button, because there is nothing for the person to click;
  * the file-sync transport lets the TYPED error through instead of flattening
    it into a bare ``RuntimeError`` — the flattening that made the artifacts
    lane's one-click blocker unreachable and burned an upload attempt per tick;
  * the publisher recognises the refusal BY TYPE, not by matching its sentence.
"""

from __future__ import annotations

import pytest

from app.services.aidream.client import AIDreamError
from app.services.aidream.organization import (
    CHOOSE_ORGANIZATION_ACTION,
    ORGANIZATION_BLOCKED_CODE,
    ORGANIZATION_HELD_CODE,
    OrganizationNotResolvedError,
    organization_refusal,
    organization_refusal_text,
)

HELD = OrganizationNotResolvedError(
    "This Mac has no organization chosen yet.",
    remedy="Choose your organization in Matrx Local, then this will continue.",
    held=True,
)
BLOCKED = OrganizationNotResolvedError(
    "Your account has no active organization membership.",
    remedy="You need to be added to an organization before this will work.",
    held=False,
)


def _says_waiting(text: str) -> bool:
    return "waiting for you to choose an organization" in text.lower()


# ── the one refusal shape ────────────────────────────────────────────────


def test_a_held_refusal_says_it_is_waiting_and_names_the_one_click():
    refusal = organization_refusal(HELD)
    assert refusal["code"] == ORGANIZATION_HELD_CODE
    assert refusal["held"] is True
    assert refusal["action"] == CHOOSE_ORGANIZATION_ACTION
    assert _says_waiting(refusal["message"])
    assert "choose your organization" in refusal["remedy"].lower()
    assert "default" not in (refusal["message"] + refusal["remedy"]).lower()


def test_a_blocked_refusal_keeps_its_own_words_and_offers_no_button():
    """There is nothing to click when the account has no membership at all.
    A screen that offers the picker anyway sends the person to an empty list."""
    refusal = organization_refusal(BLOCKED)
    assert refusal["code"] == ORGANIZATION_BLOCKED_CODE
    assert refusal["held"] is False
    assert refusal["action"] is None
    assert refusal["message"] == str(BLOCKED)
    assert not _says_waiting(refusal["message"])


def test_the_typed_error_is_a_runtime_error():
    """Transports flattened it to ``RuntimeError`` to keep their callers'
    handlers working, and flattening threw away ``held`` and ``remedy``.
    Sharing the base class is what lets the type travel to the surface."""
    assert issubclass(OrganizationNotResolvedError, RuntimeError)


# ── the six lanes ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_file_sync_lets_the_typed_refusal_through(monkeypatch):
    """It used to raise a bare ``RuntimeError`` with its own sentence, which
    made ``coding_sessions/artifacts.py``'s ``except
    OrganizationNotResolvedError`` unreachable: the held upload fell into the
    generic handler, charged an attempt toward the stop, and set no blocker."""
    from app.services.aidream import organization as org_module
    from app.services.file_sync.client import MatrxFilesClient

    async def _held(_jwt):
        raise HELD

    monkeypatch.setattr(org_module, "resolve_active_organization_id", _held)

    client = MatrxFilesClient()
    client.set_jwt("jwt-value")
    with pytest.raises(OrganizationNotResolvedError) as excinfo:
        await client.auth_header()
    assert excinfo.value.held is True


@pytest.mark.anyio
async def test_delegation_refusal_says_waiting(monkeypatch):
    from app.services.aidream import organization as org_module
    from app.services.delegation.client import DelegationApiClient, DelegationApiError

    async def _held(_jwt):
        raise HELD

    monkeypatch.setattr(org_module, "resolve_active_organization_id", _held)

    with pytest.raises(DelegationApiError) as excinfo:
        await DelegationApiClient("https://example.invalid")._headers("jwt-value")
    assert _says_waiting(str(excinfo.value))


@pytest.mark.anyio
async def test_scraper_refusal_carries_the_action(monkeypatch):
    from app.services.aidream import organization as org_module
    from app.services.scraper.remote_client import (
        RemoteScraperClient,
        RemoteScraperOrganizationError,
    )

    async def _held(_jwt):
        raise HELD

    monkeypatch.setattr(org_module, "resolve_active_organization_id", _held)

    with pytest.raises(RemoteScraperOrganizationError) as excinfo:
        await RemoteScraperClient()._auth_headers("jwt-value")
    error = excinfo.value
    assert error.held is True
    assert error.action == CHOOSE_ORGANIZATION_ACTION
    assert error.code == ORGANIZATION_HELD_CODE
    assert _says_waiting(str(error))


@pytest.mark.anyio
async def test_vault_held_state_is_not_the_blocked_state(monkeypatch):
    from app.services.aidream import organization as org_module
    from app.services.credential_vault.client import VaultUnavailable, _organization_id

    async def _held(_jwt):
        raise HELD

    monkeypatch.setattr(org_module, "resolve_active_organization_id", _held)
    with pytest.raises(VaultUnavailable) as excinfo:
        await _organization_id("jwt-value")
    assert excinfo.value.state == ORGANIZATION_HELD_CODE
    assert _says_waiting(excinfo.value.message)

    async def _blocked(_jwt):
        raise BLOCKED

    monkeypatch.setattr(org_module, "resolve_active_organization_id", _blocked)
    with pytest.raises(VaultUnavailable) as excinfo:
        await _organization_id("jwt-value")
    assert excinfo.value.state == ORGANIZATION_BLOCKED_CODE


@pytest.mark.anyio
async def test_the_transport_tags_its_synthetic_400_with_the_refusal(monkeypatch):
    """The publisher must be able to tell "waiting on one click" from "the
    server refused you" WITHOUT reading the sentence."""
    from app.services.aidream import client as client_module
    from app.services.aidream import organization as org_module

    async def _held(_jwt):
        raise HELD

    monkeypatch.setattr(org_module, "resolve_active_organization_id", _held)

    api = client_module.AIDreamClient(base_url="https://example.invalid")
    with pytest.raises(AIDreamError) as excinfo:
        await api._build_headers({}, "jwt-value", None, path="/ai/anything")
    refusal = excinfo.value.organization_refusal
    assert refusal is not None and refusal["action"] == CHOOSE_ORGANIZATION_ACTION
    assert _says_waiting(str(excinfo.value))


def test_the_publisher_recognises_the_refusal_by_type_not_by_sentence():
    from app.services.coding_sessions.service import (
        _is_terminal_rejection,
        _organization_refusal_of,
    )

    refusal = organization_refusal(HELD)
    tagged = AIDreamError(400, "anything at all", organization_refusal=refusal)
    assert _organization_refusal_of(tagged) == refusal
    # A local refusal is NEVER terminal: the server was never asked, so no
    # number of attempts may quarantine the row.
    assert _is_terminal_rejection(tagged, attempts=999) is False
    # The TEXT BACKSTOP, never the primary path: an envelope queued by an older
    # build carries only the sentence, and a row that is not recognised here is
    # a row that gets quarantined for a refusal the server never made.
    from_text = _organization_refusal_of(AIDreamError(400, organization_refusal_text(HELD)))
    assert from_text is not None and from_text["held"] is True
    legacy = _organization_refusal_of(
        AIDreamError(400, "[aidream_client] Cannot name an organization for this request: x")
    )
    assert legacy is not None and legacy["action"] == CHOOSE_ORGANIZATION_ACTION
    # An ordinary server refusal is NOT one, whatever its status.
    assert _organization_refusal_of(AIDreamError(409, '{"error":"entry_mutated"}')) is None


def test_the_artifacts_lane_blocker_is_the_shared_refusal():
    """The one-click button is rendered off ``action``; a blocker that carries
    only a code string leaves the next lane's held refusal with no way out."""
    refusal = organization_refusal(HELD)
    assert set(refusal) == {"code", "message", "remedy", "action", "held"}
