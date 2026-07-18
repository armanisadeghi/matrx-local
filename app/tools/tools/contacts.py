"""Contacts tools — read macOS Contacts via CNContactStore (macOS only).

Requires:
  - TCC grant: Contacts (System Settings → Privacy & Security → Contacts)
  - Entitlement: com.apple.security.personal-information.addressbook
  - pyobjc-framework-Contacts
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.common.platform_ctx import PLATFORM
from app.services.action_needed import os_permission_needed
from app.tools.session import ToolSession
from app.tools.types import ToolResult, ToolResultType

logger = logging.getLogger(__name__)

_PERMISSION_HINT = (
    "Contacts access is required. "
    "Grant it in System Settings → Privacy & Security → Contacts, then restart the app."
)


def _contact_to_dict(contact: Any) -> dict[str, Any]:
    """Convert a CNContact object to a plain dict."""
    phones = []
    for phone_value in contact.phoneNumbers():
        phones.append({
            "label": str(phone_value.label() or ""),
            "number": str(phone_value.value().stringValue()),
        })

    emails: list[dict[str, str]] = []
    for email_value in contact.emailAddresses():
        emails.append({
            "label": str(email_value.label() or ""),
            "address": str(email_value.value()),
        })

    postal: list[dict[str, str]] = []
    for addr_value in contact.postalAddresses():
        a = addr_value.value()
        postal.append({
            "label": str(addr_value.label() or ""),
            "street": str(a.street() or ""),
            "city": str(a.city() or ""),
            "state": str(a.state() or ""),
            "postalCode": str(a.postalCode() or ""),
            "country": str(a.country() or ""),
        })

    birthday = None
    if contact.birthday() is not None:
        try:
            bday = contact.birthday()
            birthday = f"{bday.year()}-{bday.month():02d}-{bday.day():02d}"
        except Exception:
            pass

    org = str(contact.organizationName() or "")
    title = str(contact.jobTitle() or "")

    return {
        "identifier": str(contact.identifier()),
        "given_name": str(contact.givenName() or ""),
        "family_name": str(contact.familyName() or ""),
        "full_name": f"{contact.givenName() or ''} {contact.familyName() or ''}".strip(),
        "organization": org,
        "job_title": title,
        "phones": phones,
        "emails": emails,
        "postal_addresses": postal,
        "birthday": birthday,
        "note": str(contact.note() or ""),
    }


def _fetch_contacts_sync(query: str | None, limit: int) -> list[dict[str, Any]]:
    """Blocking contacts fetch — run in thread pool."""
    import Contacts  # type: ignore[import]

    store = Contacts.CNContactStore.alloc().init()

    keys_to_fetch = [
        Contacts.CNContactIdentifierKey,
        Contacts.CNContactGivenNameKey,
        Contacts.CNContactFamilyNameKey,
        Contacts.CNContactOrganizationNameKey,
        Contacts.CNContactJobTitleKey,
        Contacts.CNContactPhoneNumbersKey,
        Contacts.CNContactEmailAddressesKey,
        Contacts.CNContactPostalAddressesKey,
        Contacts.CNContactBirthdayKey,
        Contacts.CNContactNoteKey,
    ]

    if query:
        predicate = Contacts.CNContact.predicateForContactsMatchingName_(query)
    else:
        predicate = Contacts.CNContact.predicateForContactsInContainerWithIdentifier_(
            store.defaultContainerIdentifier()
        )

    contacts, error = store.unifiedContactsMatchingPredicate_keysToFetch_error_(
        predicate, keys_to_fetch, None
    )

    if error is not None:
        raise PermissionError(f"CNContactStore error: {error.localizedDescription()}")
    if contacts is None:
        return []

    results = []
    for contact in contacts:
        try:
            results.append(_contact_to_dict(contact))
        except Exception as exc:
            logger.warning("Skipping contact due to error: %s", exc)
        if len(results) >= limit:
            break
    return results


async def tool_search_contacts(
    session: ToolSession,
    query: str | None = None,
    limit: int = 25,
) -> ToolResult:
    """Search contacts by name. Returns up to `limit` matching contacts.

    Args:
        query: Name to search for. If omitted, returns up to `limit` contacts
               from the default container.
        limit: Maximum number of contacts to return (default 25, max 200).
    """
    if not PLATFORM["is_mac"]:
        return ToolResult(
            output="Contacts tool is only available on macOS.",
            type=ToolResultType.ERROR,
        )

    limit = max(1, min(limit, 200))

    try:
        contacts = await asyncio.get_event_loop().run_in_executor(
            None, _fetch_contacts_sync, query, limit
        )
    except PermissionError as exc:
        return ToolResult(
            output=f"Contacts permission denied. {_PERMISSION_HINT}\nDetail: {exc}",
            type=ToolResultType.ERROR,
            action_needed=os_permission_needed(feature="Contacts", permission_key="contacts", source="tool.contacts"),
        )
    except Exception as exc:
        logger.exception("tool_search_contacts failed")
        return ToolResult(
            output=f"Failed to search contacts: {exc}",
            type=ToolResultType.ERROR,
        )

    label = f"Found {len(contacts)} contact(s)" + (f" matching '{query}'" if query else "")
    return ToolResult(
        output=label,
        metadata={"contacts": contacts, "count": len(contacts), "query": query},
        type=ToolResultType.SUCCESS,
    )


def _get_contact_sync(identifier: str) -> dict[str, Any] | None:
    import Contacts  # type: ignore[import]

    store = Contacts.CNContactStore.alloc().init()
    keys_to_fetch = [
        Contacts.CNContactIdentifierKey,
        Contacts.CNContactGivenNameKey,
        Contacts.CNContactFamilyNameKey,
        Contacts.CNContactOrganizationNameKey,
        Contacts.CNContactJobTitleKey,
        Contacts.CNContactPhoneNumbersKey,
        Contacts.CNContactEmailAddressesKey,
        Contacts.CNContactPostalAddressesKey,
        Contacts.CNContactBirthdayKey,
        Contacts.CNContactNoteKey,
    ]
    contact, error = store.unifiedContactWithIdentifier_keysToFetch_error_(
        identifier, keys_to_fetch, None
    )
    if error is not None:
        raise PermissionError(f"CNContactStore error: {error.localizedDescription()}")
    if contact is None:
        return None
    return _contact_to_dict(contact)


async def tool_get_contact(
    session: ToolSession,
    identifier: str,
) -> ToolResult:
    """Get a single contact by its unique CNContact identifier.

    Args:
        identifier: The CNContact identifier string (from search_contacts results).
    """
    if not PLATFORM["is_mac"]:
        return ToolResult(
            output="Contacts tool is only available on macOS.",
            type=ToolResultType.ERROR,
        )

    try:
        contact = await asyncio.get_event_loop().run_in_executor(
            None, _get_contact_sync, identifier
        )
    except PermissionError as exc:
        return ToolResult(
            output=f"Contacts permission denied. {_PERMISSION_HINT}\nDetail: {exc}",
            type=ToolResultType.ERROR,
            action_needed=os_permission_needed(feature="Contacts", permission_key="contacts", source="tool.contacts"),
        )
    except Exception as exc:
        logger.exception("tool_get_contact failed")
        return ToolResult(
            output=f"Failed to get contact: {exc}",
            type=ToolResultType.ERROR,
        )

    if contact is None:
        return ToolResult(
            output=f"No contact found with identifier: {identifier}",
            type=ToolResultType.ERROR,
        )

    return ToolResult(
        output=f"Contact: {contact['full_name']}",
        metadata=contact,
        type=ToolResultType.SUCCESS,
    )
