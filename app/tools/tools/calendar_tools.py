"""Calendar and Reminders tools — read/write via EventKit (macOS only).

Requires:
  - TCC grants:
      Calendars: System Settings → Privacy & Security → Calendars
      Reminders: System Settings → Privacy & Security → Reminders
  - Entitlements:
      com.apple.security.personal-information.calendars
      com.apple.security.personal-information.reminders
  - pyobjc-framework-EventKit
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from app.common.platform_ctx import PLATFORM
from app.services.action_needed import os_permission_needed
from app.tools.session import ToolSession
from app.tools.types import ToolResult, ToolResultType

logger = logging.getLogger(__name__)

_CALENDAR_HINT = (
    "Calendar access is required. "
    "Grant it in System Settings → Privacy & Security → Calendars, then restart the app."
)
_REMINDERS_HINT = (
    "Reminders access is required. "
    "Grant it in System Settings → Privacy & Security → Reminders, then restart the app."
)

# EKEntityType constants
_EK_ENTITY_EVENT = 0
_EK_ENTITY_REMINDER = 1


def _get_authorized_store(entity_type: int, *, write: bool = False) -> Any:
    """Return an authorized EKEventStore or raise PermissionError."""
    import EventKit  # type: ignore[import]

    store = EventKit.EKEventStore.alloc().init()
    status = EventKit.EKEventStore.authorizationStatusForEntityType_(entity_type)
    # 0=notDetermined, 1=restricted, 2=denied, 3=fullAccess, 4=writeOnly
    if status == 3 or (write and status == 4):
        return store
    if status == 0:
        raise PermissionError("EKEventStore access has not been requested.")
    raise PermissionError(
        f"EKEventStore authorization status={status}. "
        + (_CALENDAR_HINT if entity_type == _EK_ENTITY_EVENT else _REMINDERS_HINT)
    )


def _ek_date_to_iso(ns_date: Any | None) -> str | None:
    """Convert NSDate to ISO 8601 string."""
    if ns_date is None:
        return None
    try:
        ts = ns_date.timeIntervalSince1970()
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:
        return None


def _event_to_dict(event: Any) -> dict[str, Any]:
    return {
        "identifier": str(event.eventIdentifier() or ""),
        "title": str(event.title() or ""),
        "notes": str(event.notes() or ""),
        "location": str(event.location() or ""),
        "start_date": _ek_date_to_iso(event.startDate()),
        "end_date": _ek_date_to_iso(event.endDate()),
        "all_day": bool(event.isAllDay()),
        "calendar": str(event.calendar().title() if event.calendar() else ""),
        "url": str(event.URL() or ""),
    }


def _reminder_to_dict(reminder: Any) -> dict[str, Any]:
    return {
        "identifier": str(reminder.calendarItemIdentifier() or ""),
        "title": str(reminder.title() or ""),
        "notes": str(reminder.notes() or ""),
        "completed": bool(reminder.isCompleted()),
        "completion_date": _ek_date_to_iso(reminder.completionDate()),
        "due_date": _ek_date_to_iso(reminder.dueDateComponents().date() if reminder.dueDateComponents() else None),
        "priority": int(reminder.priority()),
        "list": str(reminder.calendar().title() if reminder.calendar() else ""),
    }


def _list_events_sync(
    days_ahead: int,
    calendar_names: list[str] | None,
    limit: int,
) -> list[dict[str, Any]]:
    import Foundation  # type: ignore[import]

    store = _get_authorized_store(_EK_ENTITY_EVENT)

    now = Foundation.NSDate.date()
    end_ts = now.timeIntervalSinceReferenceDate() + days_ahead * 86400
    end_date = Foundation.NSDate.dateWithTimeIntervalSinceReferenceDate_(end_ts)

    calendars = None
    if calendar_names:
        all_cals = store.calendarsForEntityType_(_EK_ENTITY_EVENT)
        calendars = [c for c in all_cals if str(c.title()) in calendar_names] or None

    predicate = store.predicateForEventsWithStartDate_endDate_calendars_(now, end_date, calendars)
    events = store.eventsMatchingPredicate_(predicate) or []
    sorted_events = sorted(events, key=lambda e: e.startDate().timeIntervalSince1970())
    return [_event_to_dict(e) for e in sorted_events[:limit]]


async def tool_list_events(
    session: ToolSession,
    days_ahead: int = 7,
    calendar_names: list[str] | None = None,
    limit: int = 50,
) -> ToolResult:
    """List upcoming calendar events.

    Args:
        days_ahead: How many days into the future to look (default 7, max 365).
        calendar_names: Filter to specific calendar names. Omit for all calendars.
        limit: Maximum events to return (default 50, max 500).
    """
    if not PLATFORM["is_mac"]:
        return ToolResult(output="Calendar tool is only available on macOS.", type=ToolResultType.ERROR)

    days_ahead = max(1, min(days_ahead, 365))
    limit = max(1, min(limit, 500))

    try:
        events = await asyncio.get_event_loop().run_in_executor(
            None, _list_events_sync, days_ahead, calendar_names, limit
        )
    except PermissionError as exc:
        return ToolResult(output=f"Calendar permission denied. {_CALENDAR_HINT}\nDetail: {exc}", type=ToolResultType.ERROR, action_needed=os_permission_needed(feature="Calendar events", permission_key="calendar", source="tool.calendar"))
    except Exception as exc:
        logger.exception("tool_list_events failed")
        return ToolResult(output=f"Failed to list events: {exc}", type=ToolResultType.ERROR)

    return ToolResult(
        output=f"Found {len(events)} event(s) in the next {days_ahead} day(s).",
        metadata={"events": events, "count": len(events)},
        type=ToolResultType.SUCCESS,
    )


def _create_event_sync(
    title: str,
    start_iso: str,
    end_iso: str,
    notes: str | None,
    calendar_name: str | None,
    all_day: bool,
) -> dict[str, Any]:
    import EventKit  # type: ignore[import]
    import Foundation  # type: ignore[import]

    store = _get_authorized_store(_EK_ENTITY_EVENT, write=True)

    start_dt = datetime.fromisoformat(start_iso).astimezone(timezone.utc)
    end_dt = datetime.fromisoformat(end_iso).astimezone(timezone.utc)
    start_ns = Foundation.NSDate.dateWithTimeIntervalSince1970_(start_dt.timestamp())
    end_ns = Foundation.NSDate.dateWithTimeIntervalSince1970_(end_dt.timestamp())

    event = EventKit.EKEvent.eventWithEventStore_(store)
    event.setTitle_(title)
    event.setStartDate_(start_ns)
    event.setEndDate_(end_ns)
    event.setAllDay_(all_day)
    if notes:
        event.setNotes_(notes)

    if calendar_name:
        cals = store.calendarsForEntityType_(_EK_ENTITY_EVENT)
        cal = next((c for c in cals if str(c.title()) == calendar_name), None)
        if cal:
            event.setCalendar_(cal)
    if event.calendar() is None:
        event.setCalendar_(store.defaultCalendarForNewEvents())

    success, error = store.saveEvent_span_commit_error_(event, EventKit.EKSpanThisEvent, True, None)
    if not success:
        raise RuntimeError(f"Failed to save event: {error.localizedDescription() if error else 'unknown'}")
    return _event_to_dict(event)


async def tool_create_event(
    session: ToolSession,
    title: str,
    start: str,
    end: str,
    notes: str | None = None,
    calendar: str | None = None,
    all_day: bool = False,
) -> ToolResult:
    """Create a new calendar event.

    Args:
        title: Event title.
        start: Start datetime in ISO 8601 format (e.g. "2026-03-15T10:00:00").
        end: End datetime in ISO 8601 format.
        notes: Optional notes/description.
        calendar: Calendar name to add the event to. Defaults to the default calendar.
        all_day: Whether this is an all-day event.
    """
    if not PLATFORM["is_mac"]:
        return ToolResult(output="Calendar tool is only available on macOS.", type=ToolResultType.ERROR)

    try:
        event = await asyncio.get_event_loop().run_in_executor(
            None, _create_event_sync, title, start, end, notes, calendar, all_day
        )
    except PermissionError as exc:
        return ToolResult(output=f"Calendar permission denied. {_CALENDAR_HINT}\nDetail: {exc}", type=ToolResultType.ERROR, action_needed=os_permission_needed(feature="Calendar events", permission_key="calendar", source="tool.calendar"))
    except Exception as exc:
        logger.exception("tool_create_event failed")
        return ToolResult(output=f"Failed to create event: {exc}", type=ToolResultType.ERROR)

    return ToolResult(
        output=f"Created event: {event['title']} on {event['start_date']}",
        metadata=event,
        type=ToolResultType.SUCCESS,
    )


def _list_reminders_sync(list_names: list[str] | None, include_completed: bool, limit: int) -> list[dict[str, Any]]:
    store = _get_authorized_store(_EK_ENTITY_REMINDER)

    calendars = None
    if list_names:
        all_cals = store.calendarsForEntityType_(_EK_ENTITY_REMINDER)
        calendars = [c for c in all_cals if str(c.title()) in list_names] or None

    predicate = store.predicateForRemindersInCalendars_(calendars)

    result: list[dict[str, Any]] = []
    done = threading.Event()

    def handler(reminders: Any) -> None:
        if reminders:
            for r in reminders:
                if include_completed or not r.isCompleted():
                    result.append(_reminder_to_dict(r))
                if len(result) >= limit:
                    break
        done.set()

    store.fetchRemindersMatchingPredicate_completion_(predicate, handler)
    done.wait(timeout=10.0)
    return result[:limit]


async def tool_list_reminders(
    session: ToolSession,
    list_names: list[str] | None = None,
    include_completed: bool = False,
    limit: int = 50,
) -> ToolResult:
    """List reminders from macOS Reminders.

    Args:
        list_names: Filter to specific Reminders list names. Omit for all lists.
        include_completed: Include completed reminders (default False).
        limit: Maximum reminders to return (default 50, max 500).
    """
    if not PLATFORM["is_mac"]:
        return ToolResult(output="Reminders tool is only available on macOS.", type=ToolResultType.ERROR)

    limit = max(1, min(limit, 500))

    try:
        reminders = await asyncio.get_event_loop().run_in_executor(
            None, _list_reminders_sync, list_names, include_completed, limit
        )
    except PermissionError as exc:
        return ToolResult(output=f"Reminders permission denied. {_REMINDERS_HINT}\nDetail: {exc}", type=ToolResultType.ERROR, action_needed=os_permission_needed(feature="Reminders", permission_key="reminders", source="tool.reminders"))
    except Exception as exc:
        logger.exception("tool_list_reminders failed")
        return ToolResult(output=f"Failed to list reminders: {exc}", type=ToolResultType.ERROR)

    return ToolResult(
        output=f"Found {len(reminders)} reminder(s).",
        metadata={"reminders": reminders, "count": len(reminders)},
        type=ToolResultType.SUCCESS,
    )


def _create_reminder_sync(
    title: str,
    notes: str | None,
    due_iso: str | None,
    list_name: str | None,
) -> dict[str, Any]:
    import EventKit  # type: ignore[import]
    import Foundation  # type: ignore[import]

    store = _get_authorized_store(_EK_ENTITY_REMINDER, write=True)

    reminder = EventKit.EKReminder.reminderWithEventStore_(store)
    reminder.setTitle_(title)
    if notes:
        reminder.setNotes_(notes)

    if due_iso:
        due_dt = datetime.fromisoformat(due_iso).astimezone(timezone.utc)
        due_ns = Foundation.NSDate.dateWithTimeIntervalSince1970_(due_dt.timestamp())
        components = Foundation.NSCalendar.currentCalendar().components_fromDate_(
            Foundation.NSCalendarUnitYear
            | Foundation.NSCalendarUnitMonth
            | Foundation.NSCalendarUnitDay
            | Foundation.NSCalendarUnitHour
            | Foundation.NSCalendarUnitMinute,
            due_ns,
        )
        reminder.setDueDateComponents_(components)

    if list_name:
        cals = store.calendarsForEntityType_(_EK_ENTITY_REMINDER)
        cal = next((c for c in cals if str(c.title()) == list_name), None)
        if cal:
            reminder.setCalendar_(cal)
    if reminder.calendar() is None:
        reminder.setCalendar_(store.defaultCalendarForNewReminders())

    success, error = store.saveReminder_commit_error_(reminder, True, None)
    if not success:
        raise RuntimeError(f"Failed to save reminder: {error.localizedDescription() if error else 'unknown'}")
    return _reminder_to_dict(reminder)


async def tool_create_reminder(
    session: ToolSession,
    title: str,
    notes: str | None = None,
    due: str | None = None,
    list_name: str | None = None,
) -> ToolResult:
    """Create a new reminder in macOS Reminders.

    Args:
        title: Reminder title.
        notes: Optional notes.
        due: Due date/time in ISO 8601 format (e.g. "2026-03-20T09:00:00").
        list_name: Reminders list to add it to. Defaults to the default list.
    """
    if not PLATFORM["is_mac"]:
        return ToolResult(output="Reminders tool is only available on macOS.", type=ToolResultType.ERROR)

    try:
        reminder = await asyncio.get_event_loop().run_in_executor(
            None, _create_reminder_sync, title, notes, due, list_name
        )
    except PermissionError as exc:
        return ToolResult(output=f"Reminders permission denied. {_REMINDERS_HINT}\nDetail: {exc}", type=ToolResultType.ERROR, action_needed=os_permission_needed(feature="Reminders", permission_key="reminders", source="tool.reminders"))
    except Exception as exc:
        logger.exception("tool_create_reminder failed")
        return ToolResult(output=f"Failed to create reminder: {exc}", type=ToolResultType.ERROR)

    return ToolResult(
        output=f"Created reminder: {reminder['title']}",
        metadata=reminder,
        type=ToolResultType.SUCCESS,
    )
