"""Tool dispatcher — routes tool calls to the correct handler function."""

from __future__ import annotations

import inspect
import logging
import types
import errno
from typing import Any, Callable, Coroutine

from app.tools.session import ToolSession
from app.services.action_needed.models import filesystem_access_needed
from app.tools.tools.clipboard import tool_clipboard_read, tool_clipboard_write
from app.tools.tools.execution import tool_bash, tool_bash_output, tool_task_stop
from app.tools.tools.file_ops import (
    tool_copy,
    tool_delete,
    tool_edit,
    tool_glob,
    tool_grep,
    tool_filesystem_places,
    tool_find_paths,
    tool_semantic_find_paths,
    tool_mkdir,
    tool_move,
    tool_read,
    tool_rename,
    tool_write,
)
from app.tools.tools.network import (
    tool_fetch_url,
    tool_fetch_with_browser,
    tool_research,
    tool_scrape,
    tool_search,
)
from app.tools.tools.notify import tool_notify
from app.tools.tools.system import (
    tool_list_directory,
    tool_list_screens,
    tool_open_path,
    tool_open_url,
    tool_screenshot,
    tool_system_info,
)
from app.tools.tools.transfer import tool_download_file, tool_upload_file

# New tool modules
from app.tools.tools.process_manager import (
    tool_focus_app,
    tool_kill_process,
    tool_launch_app,
    tool_list_processes,
    tool_list_ports,
    tool_list_terminals,
    tool_tail_terminal,
)
from app.tools.tools.window_manager import (
    tool_focus_window,
    tool_list_windows,
    tool_minimize_window,
    tool_move_window,
)
from app.tools.tools.input_automation import (
    tool_hotkey,
    tool_mouse_click,
    tool_mouse_move,
    tool_type_text,
)
from app.tools.tools.audio import (
    tool_list_audio_devices,
    tool_play_audio,
    tool_record_audio,
    tool_transcribe_audio,
)
from app.tools.tools.browser_automation import (
    tool_browser_click,
    tool_browser_close,
    tool_browser_eval,
    tool_browser_extract,
    tool_browser_navigate,
    tool_browser_screenshot,
    tool_browser_tabs,
    tool_browser_type,
)
from app.tools.tools.network_discovery import (
    tool_mdns_discover,
    tool_network_info,
    tool_network_scan,
    tool_port_scan,
)
from app.tools.tools.system_monitor import (
    tool_battery_status,
    tool_disk_usage,
    tool_system_resources,
    tool_top_processes,
)
from app.tools.tools.file_watch import (
    tool_stop_watch,
    tool_watch_directory,
    tool_watch_events,
)
from app.tools.tools.app_integration import (
    tool_applescript,
    tool_get_installed_apps,
    tool_powershell_script,
)
from app.tools.tools.scheduler import (
    tool_cancel_scheduled,
    tool_heartbeat_status,
    tool_list_scheduled,
    tool_prevent_sleep,
    tool_schedule_task,
)
from app.tools.tools.media import (
    tool_archive_create,
    tool_archive_extract,
    tool_image_ocr,
    tool_image_resize,
    tool_office_generate,
    tool_pdf_extract,
)
from app.tools.tools.ner import tool_extract_entities, tool_extract_pii
from app.tools.tools.wifi_bluetooth import (
    tool_bluetooth_devices,
    tool_connected_devices,
    tool_wifi_networks,
)
from app.tools.tools.documents import (
    tool_list_documents,
    tool_list_document_folders,
    tool_read_document,
    tool_search_documents,
    tool_write_document,
)
from app.tools.tools.powershell_tools import (
    tool_ps_get_env,
    tool_ps_set_env,
    tool_registry_read,
    tool_registry_write,
    tool_service_list,
    tool_service_control,
    tool_event_log,
    tool_windows_features,
)
# macOS app-integration tools (Mail, Messages, Contacts, Calendar, Photos,
# Location, Speech). Registered on every platform — each handler self-gates
# at runtime via PLATFORM["is_mac"] and returns a clear "only available on
# macOS" error envelope elsewhere, matching AppleScript's behavior.
from app.tools.tools.mail import (
    tool_get_email_accounts,
    tool_list_emails,
    tool_send_email,
)
from app.tools.tools.messages import (
    tool_list_conversations,
    tool_list_messages,
    tool_send_message,
)
from app.tools.tools.contacts import (
    tool_get_contact,
    tool_search_contacts,
)
from app.tools.tools.calendar_tools import (
    tool_create_event,
    tool_create_reminder,
    tool_list_events,
    tool_list_reminders,
)
from app.tools.tools.photos import (
    tool_get_photo,
    tool_search_photos,
)
from app.tools.tools.location import tool_get_location
from app.tools.tools.speech_recognition_tools import (
    tool_list_speech_locales,
    tool_transcribe_with_speech,
)
from app.tools.types import ToolResult, ToolResultType

logger = logging.getLogger(__name__)

ToolHandler = Callable[..., Coroutine[Any, Any, ToolResult]]

TOOL_HANDLERS: dict[str, ToolHandler] = {
    # ── File Operations ──────────────────────────────────────────────
    "Read": tool_read,
    "Write": tool_write,
    "Edit": tool_edit,
    "Glob": tool_glob,
    "Grep": tool_grep,
    "FilesystemPlaces": tool_filesystem_places,
    "FindPaths": tool_find_paths,
    "SemanticFindPaths": tool_semantic_find_paths,
    "Move": tool_move,
    "Copy": tool_copy,
    "Delete": tool_delete,
    "Rename": tool_rename,
    "Mkdir": tool_mkdir,
    # ── Execution ────────────────────────────────────────────────────
    "Bash": tool_bash,
    "BashOutput": tool_bash_output,
    "TaskStop": tool_task_stop,
    # ── System ───────────────────────────────────────────────────────
    "SystemInfo": tool_system_info,
    "Screenshot": tool_screenshot,
    "ListScreens": tool_list_screens,
    "ListDirectory": tool_list_directory,
    "OpenUrl": tool_open_url,
    "OpenPath": tool_open_path,
    # ── Clipboard ────────────────────────────────────────────────────
    "ClipboardRead": tool_clipboard_read,
    "ClipboardWrite": tool_clipboard_write,
    # ── Notifications ────────────────────────────────────────────────
    "Notify": tool_notify,
    # ── Network / Scraping ───────────────────────────────────────────
    "FetchUrl": tool_fetch_url,
    "FetchWithBrowser": tool_fetch_with_browser,
    "Scrape": tool_scrape,
    "Search": tool_search,
    "Research": tool_research,
    # ── File Transfer ────────────────────────────────────────────────
    "DownloadFile": tool_download_file,
    "UploadFile": tool_upload_file,
    # ── Process Management ───────────────────────────────────────────
    "ListProcesses": tool_list_processes,
    "LaunchApp": tool_launch_app,
    "KillProcess": tool_kill_process,
    "FocusApp": tool_focus_app,
    "ListPorts": tool_list_ports,
    "ListTerminals": tool_list_terminals,
    "TailTerminal": tool_tail_terminal,
    # ── Window Management ────────────────────────────────────────────
    "ListWindows": tool_list_windows,
    "FocusWindow": tool_focus_window,
    "MoveWindow": tool_move_window,
    "MinimizeWindow": tool_minimize_window,
    # ── Input Automation ─────────────────────────────────────────────
    "TypeText": tool_type_text,
    "Hotkey": tool_hotkey,
    "MouseClick": tool_mouse_click,
    "MouseMove": tool_mouse_move,
    # ── Audio ────────────────────────────────────────────────────────
    "ListAudioDevices": tool_list_audio_devices,
    "RecordAudio": tool_record_audio,
    "PlayAudio": tool_play_audio,
    "TranscribeAudio": tool_transcribe_audio,
    # ── Browser Automation ───────────────────────────────────────────
    "BrowserNavigate": tool_browser_navigate,
    "BrowserClick": tool_browser_click,
    "BrowserType": tool_browser_type,
    "BrowserExtract": tool_browser_extract,
    "BrowserScreenshot": tool_browser_screenshot,
    "BrowserEval": tool_browser_eval,
    "BrowserTabs": tool_browser_tabs,
    "BrowserClose": tool_browser_close,
    # ── Network Discovery ────────────────────────────────────────────
    "NetworkInfo": tool_network_info,
    "NetworkScan": tool_network_scan,
    "PortScan": tool_port_scan,
    "MDNSDiscover": tool_mdns_discover,
    # ── System Monitoring ────────────────────────────────────────────
    "SystemResources": tool_system_resources,
    "BatteryStatus": tool_battery_status,
    "DiskUsage": tool_disk_usage,
    "TopProcesses": tool_top_processes,
    # ── File Watching ────────────────────────────────────────────────
    "WatchDirectory": tool_watch_directory,
    "WatchEvents": tool_watch_events,
    "StopWatch": tool_stop_watch,
    # ── OS App Integration ───────────────────────────────────────────
    "AppleScript": tool_applescript,
    "PowerShellScript": tool_powershell_script,
    "GetInstalledApps": tool_get_installed_apps,
    # ── Scheduler / Heartbeat ────────────────────────────────────────
    "ScheduleTask": tool_schedule_task,
    "ListScheduled": tool_list_scheduled,
    "CancelScheduled": tool_cancel_scheduled,
    "HeartbeatStatus": tool_heartbeat_status,
    "PreventSleep": tool_prevent_sleep,
    # ── Media Processing ─────────────────────────────────────────────
    "ImageOCR": tool_image_ocr,
    "ImageResize": tool_image_resize,
    "PdfExtract": tool_pdf_extract,
    "OfficeGenerate": tool_office_generate,
    "ArchiveCreate": tool_archive_create,
    "ArchiveExtract": tool_archive_extract,
    # ── Local NER ───────────────────────────────────────────────────────
    "ExtractEntities": tool_extract_entities,
    "ExtractPII": tool_extract_pii,
    # ── WiFi & Bluetooth ─────────────────────────────────────────────
    "WifiNetworks": tool_wifi_networks,
    "BluetoothDevices": tool_bluetooth_devices,
    "ConnectedDevices": tool_connected_devices,
    # ── Documents ──────────────────────────────────────────────────────────
    "ListDocuments": tool_list_documents,
    "ListDocumentFolders": tool_list_document_folders,
    "ReadDocument": tool_read_document,
    "WriteDocument": tool_write_document,
    "SearchDocuments": tool_search_documents,
    # ── PowerShell / System Integration ────────────────────────────────────
    "PSGetEnv": tool_ps_get_env,
    "PSSetEnv": tool_ps_set_env,
    "RegistryRead": tool_registry_read,
    "RegistryWrite": tool_registry_write,
    "ServiceList": tool_service_list,
    "ServiceControl": tool_service_control,
    "EventLog": tool_event_log,
    "WindowsFeatures": tool_windows_features,
    # ── Mail (macOS-only, runtime-gated) ───────────────────────────────────
    "ListEmails": tool_list_emails,
    "SendEmail": tool_send_email,
    "GetEmailAccounts": tool_get_email_accounts,
    # ── Messages (macOS-only, runtime-gated) ───────────────────────────────
    "ListMessages": tool_list_messages,
    "ListConversations": tool_list_conversations,
    "SendMessage": tool_send_message,
    # ── Contacts (macOS-only, runtime-gated) ───────────────────────────────
    "SearchContacts": tool_search_contacts,
    "GetContact": tool_get_contact,
    # ── Calendar & Reminders (macOS-only, runtime-gated) ───────────────────
    "ListEvents": tool_list_events,
    "CreateEvent": tool_create_event,
    "ListReminders": tool_list_reminders,
    "CreateReminder": tool_create_reminder,
    # ── Photos (macOS-only, runtime-gated) ─────────────────────────────────
    "SearchPhotos": tool_search_photos,
    "GetPhoto": tool_get_photo,
    # ── Location (macOS-only, runtime-gated) ───────────────────────────────
    "GetLocation": tool_get_location,
    # ── Speech Recognition (macOS-only, runtime-gated) ─────────────────────
    "TranscribeWithSpeech": tool_transcribe_with_speech,
    "ListSpeechLocales": tool_list_speech_locales,
}

# ── Action-enum mega-tools (W7 collapse) ────────────────────────────────────
# One dispatcher entry per action group (File, Shell, Window, …). These WRAP
# the legacy handlers above via dispatch() — the legacy PascalCase names stay
# dispatchable for existing surfaces (extension RPC) during the transition,
# but the advertised registry (catalog advertised=True + cloud rows) is
# mega-tools only. Spec + fan-out live in app/tools/actions.py.
from app.tools.actions import ACTION_GROUPS, make_group_handler  # noqa: E402

for _group in ACTION_GROUPS.values():
    if _group.dispatcher_name in TOOL_HANDLERS:  # pragma: no cover — guarded by parity tests
        raise RuntimeError(
            f"action group {_group.dispatcher_name!r} collides with a legacy dispatcher tool"
        )
    TOOL_HANDLERS[_group.dispatcher_name] = make_group_handler(_group)

TOOL_NAMES: list[str] = sorted(TOOL_HANDLERS.keys())


def list_tool_specs() -> list[dict[str, Any]]:
    """Return a list of tool specs (name, description, category, input_schema).

    Public accessor used by the matrx-extend Chrome extension's `capabilities`
    RPC command and any other consumer that wants the engine's tool catalog.
    Delegates to `app.tools.tool_schemas.generate_all_tool_schemas` so there
    is exactly one source of truth for tool metadata.

    The import is deferred because `tool_schemas` imports `TOOL_HANDLERS`
    from this module — module-level import would be circular.
    """
    from app.tools.tool_schemas import generate_all_tool_schemas

    return generate_all_tool_schemas()


# Cached catalog hash — computed lazily, then reused on every `pong`. The
# tool table (`TOOL_HANDLERS`) is static at process startup, so a single
# computation is correct for the lifetime of the process. If we later add
# dynamic registration, invalidate by setting `_TOOL_CATALOG_HASH = None`
# from the registration site.
_TOOL_CATALOG_HASH: str | None = None


def tool_catalog_hash() -> str:
    """Return a 16-char sha256 prefix over the canonical tool catalog.

    The matrx-extend extension reads this from every `pong` envelope and
    compares it against the hash it observed on its last successful
    `capabilities` RPC. A mismatch tells the extension it should refetch
    the catalog before issuing more invocations — avoids stale-schema
    drift after engine restarts that introduced new tools.

    Output: lowercase hex, 16 chars, derived from
    `sha256(json.dumps(list_tool_specs(), sort_keys=True))`. Truncation
    is fine: this is a freshness check, not a security primitive.
    """
    import hashlib
    import json as _json

    global _TOOL_CATALOG_HASH
    if _TOOL_CATALOG_HASH is None:
        try:
            specs = list_tool_specs()
        except Exception:
            # If catalog generation transiently fails, return a stable
            # sentinel so callers (the WS pong builder) don't crash. The
            # sentinel changes on every successful regeneration, so the
            # extension will retry naturally once the engine recovers.
            logger.exception("[dispatcher] tool_catalog_hash: list_tool_specs failed")
            return "0" * 16
        canonical = _json.dumps(specs, sort_keys=True, default=str).encode("utf-8")
        _TOOL_CATALOG_HASH = hashlib.sha256(canonical).hexdigest()[:16]
    return _TOOL_CATALOG_HASH


def _coerce_tool_input(handler: Callable, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Coerce tool_input values to match the handler's type annotations.

    The UI sends all form values as JSON-decoded Python objects, but HTML inputs
    often produce strings even for numeric/boolean fields (e.g. '5' instead of 5).
    This pass converts them to the declared parameter types so handlers don't crash.
    """
    try:
        # eval_str=True: the tool modules use `from __future__ import
        # annotations`, so without it every annotation is a *string* and the
        # type checks below silently never match (no coercion at all).
        sig = inspect.signature(handler, eval_str=True)
    except (ValueError, TypeError, NameError, AttributeError):
        try:
            sig = inspect.signature(handler)
        except (ValueError, TypeError):
            return tool_input

    coerced: dict[str, Any] = {}
    for key, value in tool_input.items():
        param = sig.parameters.get(key)
        if param is None or param.annotation is inspect.Parameter.empty:
            coerced[key] = value
            continue

        ann = param.annotation
        # Unwrap Optional[X] / X | None
        origin = getattr(ann, "__origin__", None)
        args = getattr(ann, "__args__", ())
        if origin is type(None):
            coerced[key] = value
            continue
        # X | None  (Python 3.10+ union syntax)
        if isinstance(ann, types.UnionType):
            non_none = [a for a in args if a is not type(None)]
            ann = non_none[0] if len(non_none) == 1 else ann
        # typing.Optional[X] / typing.Union[X, None]
        elif str(origin) in ("<class 'typing.Union'>", "typing.Union"):
            non_none = [a for a in args if a is not type(None)]
            ann = non_none[0] if len(non_none) == 1 else ann

        try:
            # bool must be checked before int because bool is a subclass of int
            if ann is bool and not isinstance(value, bool):
                if isinstance(value, str):
                    # Explicit truthy set: the old falsy-list approach mapped
                    # unknown strings like "off"/"n" to True.
                    coerced[key] = value.strip().lower() in (
                        "true", "1", "yes", "y", "on",
                    )
                else:
                    coerced[key] = bool(value)
            elif ann is int and not isinstance(value, int):
                coerced[key] = int(value)
            elif ann is float and not isinstance(value, float):
                coerced[key] = float(value)
            elif ann is str and not isinstance(value, str):
                coerced[key] = str(value)
            else:
                coerced[key] = value
        except (ValueError, TypeError):
            coerced[key] = value  # keep original; let the handler surface the error

    return coerced


async def dispatch(
    tool_name: str,
    tool_input: dict[str, Any],
    session: ToolSession,
) -> ToolResult:
    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        result = ToolResult(
            type=ToolResultType.ERROR,
            output=f"Unknown tool: {tool_name}. Available: {', '.join(TOOL_NAMES)}",
        )
    else:
        coerced_input = _coerce_tool_input(handler, tool_input)
        try:
            result = await handler(session=session, **coerced_input)
        except TypeError as e:
            logger.warning("Invalid parameters for tool %s: %s", tool_name, e)
            result = ToolResult(
                type=ToolResultType.ERROR,
                output=f"Invalid parameters for {tool_name}: {e}",
            )
        except (PermissionError, OSError) as e:
            if not isinstance(e, PermissionError) and getattr(e, "errno", None) not in (
                errno.EACCES,
                errno.EPERM,
            ):
                logger.exception("Tool %s failed", tool_name)
                result = ToolResult(
                    type=ToolResultType.ERROR,
                    output=f"Tool {tool_name} failed: {type(e).__name__}: {e}",
                )
            else:
                raw_path = getattr(e, "filename", None) or "the requested location"
                result = ToolResult(
                    type=ToolResultType.ERROR,
                    output=f"{tool_name} was denied access to {raw_path}.",
                    action_needed=filesystem_access_needed(
                        feature=tool_name,
                        path=str(raw_path),
                        operation="access",
                        source=f"tool:{tool_name}",
                    ),
                )
        except Exception as e:
            logger.exception("Tool %s failed", tool_name)
            result = ToolResult(
                type=ToolResultType.ERROR,
                output=f"Tool {tool_name} failed: {type(e).__name__}: {e}",
            )
    from app.services.action_needed.registry import get_action_needed_registry

    await get_action_needed_registry().reconcile_invocation(
        tool_name, tool_input, result.action_needed
    )
    return result
