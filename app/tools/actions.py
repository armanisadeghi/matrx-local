"""Action-enum mega-tools — the collapsed desktop tool surface (W7).

The platform moved from ~115 flat tools to a small set of ACTION-ENUM
mega-tools: one ``tool.definition`` row per capability group whose
``parameters`` carry an ``action`` discriminator plus a ``$variants``
per-action contract (the ``note`` tool in matrx-ai is the reference shape).

This module is the single source of truth for that collapse on the desktop:

* ``ACTION_GROUPS`` — the declarative map: mega-tool → {action → legacy
  dispatcher tool}. Every one of the 116 legacy tools belongs to exactly one
  group (enforced by ``tests/parity/test_tool_count.py``).
* ``make_group_handler`` — the runtime fan-out. Mega handlers WRAP the
  existing ``tool_*`` handlers via ``dispatcher.dispatch``; they never
  re-implement behavior. Legacy PascalCase names stay dispatchable during
  the transition (extension RPC), but the ADVERTISED registry (catalog
  ``advertised=True`` + the cloud rows) is mega-tools only.
* ``build_group_schemas`` — composes, from the legacy catalog entries,
  BOTH schema dialects:
    - a standard JSON Schema (``{type, properties, required}``) for local
      consumers (extension capabilities, local agent loop, tests), and
    - the flat cloud dialect stored in ``tool.definition.parameters``
      (per-property ``required: bool`` notation + ``$variants``), which is
      what matrx-ai's ``ToolDefinition._build_json_schema`` renders for
      providers ($-prefixed keys are skipped there by design).

Arg aliases: three legacy tools already take a parameter named ``action``
(BrowserTabs, MinimizeWindow, ServiceControl). Their mega variants expose it
under an aliased name (``tab_action`` / ``window_action`` / ``service_action``)
and the handler renames it back before fan-out.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.tools.types import ToolResult, ToolResultType

if TYPE_CHECKING:
    from app.tools.session import ToolSession


# ---------------------------------------------------------------------------
# Group spec
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ActionGroup:
    """One mega-tool: an action-enum facade over existing dispatcher tools."""

    dispatcher_name: str                 # PascalCase registry key (e.g. "File")
    cloud_name: str                      # tool.definition name (e.g. "local_file")
    description: str                     # lead sentence; action list is appended
    category: str
    tags: tuple[str, ...]
    actions: Mapping[str, str]           # action -> legacy dispatcher name
    # Structural registry metadata. These values are compared directly with
    # tool.definition by scripts/check_tool_db_drift.py.
    tier: str | None = None
    admin_only: bool = False
    # action -> {mega_param_name: inner_param_name} renames applied pre-dispatch
    arg_aliases: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    # Model-facing text for top-level params in the cloud dialect. A param named
    # here gets this text verbatim (leading with the actions it serves) instead
    # of the first variant's text plus a "Used with action(s)" suffix. Must equal
    # the tool.definition row, which tool_sync diffs in full.
    param_descriptions: Mapping[str, str] = field(default_factory=dict)
    # The `action` property's description; None omits it (the enum + the tool
    # description already carry each action).
    action_description: str | None = "The operation to perform."


ACTION_GROUPS: dict[str, ActionGroup] = {
    g.dispatcher_name: g
    for g in (
        ActionGroup(
            "File", "local_file",
            "Filesystem operations on the user's machine.",
            "desktop", ("file", "filesystem", "local", "actions"),
            {
                "read": "Read", "write": "Write", "edit": "Edit",
                "move": "Move", "copy": "Copy", "delete": "Delete",
                "rename": "Rename", "mkdir": "Mkdir", "glob": "Glob",
                "grep": "Grep", "list": "ListDirectory",
                "places": "FilesystemPlaces", "find": "FindPaths",
                "semantic_find": "SemanticFindPaths",
            },
            param_descriptions={
                "path": "glob, grep, list: where to search/list (grep: file or directory); default working directory. find: optional root; omit to search the machine index. delete, rename, mkdir: the target (required).",
                "limit": "find, list: page size (max 500). read: max lines (default all, up to the 256 KB cap). semantic_find: max results (max 100).",
                "query": "find: file or directory name/path text. semantic_find: natural-language description of the files.",
                "cursor": "find, list: cursor from the previous page.",
                "offset": "read: 1-based start line.",
                "source": "copy, move: path to copy or move.",
                "content": "write: full file content.",
                "include": "grep: glob restricting which files are searched, e.g. '*.py'.",
                "parents": "mkdir: create missing parent directories.",
                "pattern": "glob: glob pattern, e.g. '**/*.py'. grep: regular expression.",
                "new_name": "rename: bare name, no path separators; use move to relocate.",
                "file_path": "read, write, edit: the file.",
                "overwrite": "copy, move: replace an existing destination; a directory destination is replaced whole.",
                "permanent": "delete: true = permanent, unrecoverable; false = OS trash.",
                "new_string": "edit: replacement text.",
                "old_string": "edit: exact text to replace, whitespace included; must be unique unless replace_all.",
                "destination": "copy, move: target path. A file source into an existing directory keeps its name; a directory source is never nested into an existing destination.",
                "max_results": "grep: max matching lines.",
                "replace_all": "edit: replace every occurrence.",
                "show_hidden": "list: include dot-entries.",
                "create_directories": "write: create missing parent directories.",
            },
            action_description=None,
        ),
        ActionGroup(
            "Shell", "local_shell",
            "Run shell commands on the user's machine (foreground or background).",
            "desktop", ("shell", "command", "local", "actions"),
            {"run": "Bash", "output": "BashOutput", "stop": "TaskStop"},
        ),
        ActionGroup(
            "Window", "local_window",
            "Desktop window management.",
            "desktop", ("window", "desktop", "local", "actions"),
            {
                "list": "ListWindows", "focus": "FocusWindow",
                "move": "MoveWindow", "minimize": "MinimizeWindow",
            },
            arg_aliases={"minimize": {"window_action": "action"}},
        ),
        ActionGroup(
            "Process", "local_process",
            "Process and application management.",
            "desktop", ("process", "app", "local", "actions"),
            {
                "list": "ListProcesses", "ports": "ListPorts",
                "launch_app": "LaunchApp", "kill": "KillProcess",
                "focus_app": "FocusApp", "terminals": "ListTerminals",
                "tail_terminal": "TailTerminal", "installed_apps": "GetInstalledApps",
            },
        ),
        ActionGroup(
            "Input", "local_input",
            "Keyboard and mouse automation.",
            "desktop", ("input", "keyboard", "mouse", "local", "actions"),
            {
                "type_text": "TypeText", "hotkey": "Hotkey",
                "mouse_click": "MouseClick", "mouse_move": "MouseMove",
            },
        ),
        ActionGroup(
            "Audio", "local_audio",
            "Audio devices, recording, playback, and transcription.",
            "desktop", ("audio", "transcribe", "local", "actions"),
            {
                "devices": "ListAudioDevices", "record": "RecordAudio",
                "play": "PlayAudio", "transcribe": "TranscribeAudio",
                "transcribe_with_speech": "TranscribeWithSpeech",
                "speech_locales": "ListSpeechLocales",
            },
        ),
        ActionGroup(
            "Clipboard", "local_clipboard",
            "System clipboard access.",
            "desktop", ("clipboard", "local", "actions"),
            {"read": "ClipboardRead", "write": "ClipboardWrite"},
        ),
        ActionGroup(
            "Screen", "local_screen",
            "Screen capture and display enumeration.",
            "desktop", ("screen", "screenshot", "local", "actions"),
            {
                "screenshot": "Screenshot", "list": "ListScreens",
                "capture_book": "BookCapture",
            },
            param_descriptions={
                "ocr": "capture_book: add a searchable text layer when an OCR engine is available; the result states whether it was written.",
                "region": "screenshot: crop [x, y, width, height]; absolute when monitor='all', else relative to the chosen monitor.",
                "monitor": "screenshot: 'all' (whole virtual desktop), 'primary', or a 1-based index from list.",
                "app_name": "capture_book: reader app holding the open book, e.g. 'Books', 'Kindle', 'Preview'.",
                "crop_top": "capture_book: fraction of window height trimmed from the top (reader toolbar).",
                "crop_bottom": "capture_book: fraction of window height trimmed from the bottom (page slider).",
                "crop_left": "capture_book: fraction of window width trimmed from the left.",
                "crop_right": "capture_book: fraction of window width trimmed from the right.",
                "max_pages": "capture_book: safety cap; capture normally stops when the page stops changing.",
                "window_title": "capture_book: text in the reader window's title, when the app has several windows and the largest is not the book.",
                "next_page_key": "capture_book: page-turn key: right, left, down, up, space, page_down, page_up, return.",
                "send_to_library": "capture_book: false = the PDF stays on this Mac only.",
                "page_delay_seconds": "capture_book: wait after each page turn before capturing, so the reader re-renders.",
                "end_repeat_threshold": "capture_book: consecutive unchanged frames that mean the book ended; 2 can misfire on a slow render.",
                "page_change_threshold": "capture_book: fraction of the page that must change to count as a new page; lower it for near-identical pages (index, verse).",
            },
            action_description=None,
        ),
        ActionGroup(
            "System", "local_system",
            "System information, opening URLs/paths, and desktop notifications.",
            "desktop", ("system", "local", "actions"),
            {
                "info": "SystemInfo", "open_url": "OpenUrl",
                "open_path": "OpenPath", "notify": "Notify",
            },
        ),
        ActionGroup(
            "Browser", "local_browser",
            "Local Playwright browser automation on the user's machine.",
            "desktop-web", ("browser", "playwright", "local", "actions"),
            {
                "navigate": "BrowserNavigate", "click": "BrowserClick",
                "type_text": "BrowserType", "extract": "BrowserExtract",
                "screenshot": "BrowserScreenshot", "eval": "BrowserEval",
                "tabs": "BrowserTabs", "close": "BrowserClose",
            },
            arg_aliases={"tabs": {"tab_action": "action"}},
        ),
        ActionGroup(
            "Web", "local_web",
            "Web access from the user's machine: fetch, scrape, search, research, and file transfer.",
            "desktop-web", ("web", "http", "scrape", "search", "local", "actions"),
            {
                "fetch": "FetchUrl", "fetch_with_browser": "FetchWithBrowser",
                "scrape": "Scrape", "search": "Search", "research": "Research",
                "download": "DownloadFile", "upload": "UploadFile",
            },
        ),
        ActionGroup(
            "Net", "local_net",
            "Local network inspection and discovery.",
            "desktop-web", ("network", "discovery", "local", "actions"),
            {
                "info": "NetworkInfo", "scan": "NetworkScan",
                "port_scan": "PortScan", "mdns_discover": "MDNSDiscover",
                "wifi_networks": "WifiNetworks",
                "bluetooth_devices": "BluetoothDevices",
                "connected_devices": "ConnectedDevices",
            },
        ),
        ActionGroup(
            "Monitor", "local_monitor",
            "System monitoring and filesystem watching.",
            "desktop", ("monitor", "resources", "watch", "local", "actions"),
            {
                "resources": "SystemResources", "battery": "BatteryStatus",
                "disk_usage": "DiskUsage", "top_processes": "TopProcesses",
                "watch_directory": "WatchDirectory", "watch_events": "WatchEvents",
                "stop_watch": "StopWatch", "heartbeat": "HeartbeatStatus",
            },
        ),
        ActionGroup(
            "Schedule", "local_schedule",
            "Scheduled tasks on the user's machine.",
            "desktop", ("schedule", "tasks", "local", "actions"),
            {
                "create": "ScheduleTask", "list": "ListScheduled",
                "cancel": "CancelScheduled", "prevent_sleep": "PreventSleep",
            },
        ),
        ActionGroup(
            "Documents", "local_documents",
            "The user's managed document library.",
            "desktop", ("documents", "library", "local", "actions"),
            {
                "list": "ListDocuments", "folders": "ListDocumentFolders",
                "read": "ReadDocument", "write": "WriteDocument",
                "search": "SearchDocuments",
            },
        ),
        ActionGroup(
            "Media", "local_media",
            "Local media processing: OCR, image resize, PDF extraction, Office generation, archives.",
            "desktop", ("media", "image", "pdf", "office", "archive", "local", "actions"),
            {
                "ocr": "ImageOCR", "resize": "ImageResize",
                "pdf_extract": "PdfExtract", "office_generate": "OfficeGenerate",
                "archive_create": "ArchiveCreate",
                "archive_extract": "ArchiveExtract",
            },
        ),
        ActionGroup(
            "Ner", "local_ner",
            "Local named-entity and PII extraction (data never leaves the machine).",
            "desktop", ("ner", "pii", "privacy", "local", "actions"),
            {"entities": "ExtractEntities", "pii": "ExtractPII"},
        ),
        ActionGroup(
            "MacApps", "local_mac_apps",
            "macOS app integrations: AppleScript, Mail, Messages, Contacts, Calendar, Reminders, Photos, Location.",
            "desktop", ("macos", "apps", "local", "actions"),
            {
                "applescript": "AppleScript",
                "list_emails": "ListEmails", "send_email": "SendEmail",
                "email_accounts": "GetEmailAccounts",
                "list_messages": "ListMessages",
                "list_conversations": "ListConversations",
                "send_message": "SendMessage",
                "search_contacts": "SearchContacts", "get_contact": "GetContact",
                "list_events": "ListEvents", "create_event": "CreateEvent",
                "list_reminders": "ListReminders", "create_reminder": "CreateReminder",
                "search_photos": "SearchPhotos", "get_photo": "GetPhoto",
                "location": "GetLocation",
            },
        ),
        ActionGroup(
            "WindowsPs", "local_windows_ps",
            "PowerShell and Windows administration: scripts, env vars, registry, services, event log, features.",
            "desktop", ("windows", "powershell", "local", "actions"),
            {
                "run": "PowerShellScript",
                "get_env": "PSGetEnv", "set_env": "PSSetEnv",
                "registry_read": "RegistryRead", "registry_write": "RegistryWrite",
                "service_list": "ServiceList", "service_control": "ServiceControl",
                "event_log": "EventLog", "windows_features": "WindowsFeatures",
            },
            arg_aliases={"service_control": {"service_action": "action"}},
        ),
    )
}


def group_members() -> dict[str, str]:
    """legacy dispatcher name → owning mega-tool dispatcher name."""
    members: dict[str, str] = {}
    for group in ACTION_GROUPS.values():
        for target in group.actions.values():
            if target in members:
                raise RuntimeError(
                    f"legacy tool {target!r} appears in two action groups: "
                    f"{members[target]} and {group.dispatcher_name}"
                )
            members[target] = group.dispatcher_name
    return members


# ---------------------------------------------------------------------------
# Runtime fan-out
# ---------------------------------------------------------------------------

def make_group_handler(
    group: ActionGroup,
) -> Callable[..., Coroutine[Any, Any, ToolResult]]:
    """Build the dispatcher handler for one mega-tool.

    The handler validates the ``action`` discriminator, applies arg aliases,
    and fans out to the wrapped legacy tool THROUGH ``dispatch`` — so input
    coercion, error envelopes, and all hooks living in the underlying
    handlers are preserved untouched.
    """

    async def group_handler(session: ToolSession, **tool_input: Any) -> ToolResult:
        action = tool_input.pop("action", None)
        target = group.actions.get(action) if isinstance(action, str) else None
        if target is None:
            valid = ", ".join(sorted(group.actions))
            return ToolResult(
                type=ToolResultType.ERROR,
                output=(
                    f"{group.cloud_name}: unknown action {action!r}. "
                    f"Valid actions: {valid}"
                ),
            )
        for mega_key, inner_key in group.arg_aliases.get(action, {}).items():
            if mega_key in tool_input:
                tool_input[inner_key] = tool_input.pop(mega_key)

        # Validate through the same Pydantic model that handles a direct call
        # to the wrapped legacy tool. This makes the composed mega-tool schema
        # an executable contract, rather than a generated catalog mirror that
        # the dispatcher never enforces.
        from app.tools.catalog import get_by_dispatcher_name

        target_entry = get_by_dispatcher_name(target)
        if target_entry is None:  # pragma: no cover - catalog parity guards this
            raise RuntimeError(
                f"{group.cloud_name}: dispatcher target {target!r} is missing from the catalog"
            )
        if target_entry.arg_model is not None:
            try:
                parsed = target_entry.arg_model.model_validate(tool_input)
                tool_input = parsed.model_dump(exclude_none=True)
            except Exception as exc:
                return ToolResult(
                    type=ToolResultType.ERROR,
                    output=(
                        f"Invalid parameters for {group.cloud_name} "
                        f"action {action!r}: {exc}"
                    ),
                )

        from app.tools.dispatcher import dispatch  # deferred: avoids import cycle

        result = await dispatch(target, tool_input, session)
        metadata = dict(result.metadata or {})
        metadata.setdefault("action", action)
        metadata.setdefault("delegated_to", target)
        result.metadata = metadata
        return result

    group_handler.__name__ = f"tool_{group.cloud_name.removeprefix('local_')}_actions"
    group_handler.__qualname__ = group_handler.__name__
    group_handler.__doc__ = group.description
    return group_handler


# ---------------------------------------------------------------------------
# Schema composition
# ---------------------------------------------------------------------------

_DROP_KEYS = ("title",)


def _normalize_prop(spec: Any) -> dict[str, Any]:
    """Normalize one property schema for composition.

    Drops Pydantic ``title`` noise and unwraps ``anyOf: [X, null]`` optionals
    (the flat cloud dialect has no anyOf; matrx-ai's renderer reads ``type``).
    """
    if not isinstance(spec, dict):
        return {"type": "string"}
    out = copy.deepcopy(spec)
    for key in _DROP_KEYS:
        out.pop(key, None)
    branches = out.pop("anyOf", None)
    if isinstance(branches, list):
        non_null = [
            b for b in branches
            if isinstance(b, dict) and b.get("type") != "null"
        ]
        if non_null:
            merged = _normalize_prop(non_null[0])
            # keys already on the wrapper (description, default) win
            for k, v in merged.items():
                out.setdefault(k, v)
    if "type" not in out and "enum" not in out:
        out["type"] = "string"
    return out


def _variant_props(
    legacy_schema: dict[str, Any],
    aliases: Mapping[str, str],
) -> dict[str, dict[str, Any]]:
    """Legacy ``{type, properties, required}`` → flat per-action param map."""
    inverse = {inner: mega for mega, inner in aliases.items()}
    props = legacy_schema.get("properties", {})
    required = set(legacy_schema.get("required", []))
    out: dict[str, dict[str, Any]] = {}
    for name, spec in props.items():
        flat = _normalize_prop(spec)
        if name in required:
            flat["required"] = True
        out[inverse.get(name, name)] = flat
    return out


def _first_sentence(text: str, limit: int = 90) -> str:
    head = text.strip().split("\n")[0]
    # Sentence boundary = period + space (a bare "." would split inside
    # paths like "~/.matrx"). A trailing period is stripped either way.
    if ". " in head:
        head = head.split(". ")[0]
    head = head.rstrip(".")
    if len(head) > limit:
        head = head[: limit - 1].rstrip() + "…"
    return head


def build_group_schemas(
    group: ActionGroup,
    resolve: Callable[[str], Any],
) -> dict[str, Any]:
    """Compose everything the catalog needs for one mega-tool.

    ``resolve(legacy_dispatcher_name)`` must return the legacy CatalogEntry
    (an object with ``input_schema``, ``description``, ``platforms``,
    ``timeout_seconds``). Returns a dict with:
      description, input_schema (standard), cloud_parameters (flat dialect
      with $variants), platforms, timeout_seconds.
    """
    variants: dict[str, dict[str, dict[str, Any]]] = {}
    action_lines: list[str] = []
    platform_sets: list[tuple[str, ...] | None] = []
    timeout = 120.0
    output_schemas: list[dict[str, Any]] = []

    for action in sorted(group.actions):
        target = group.actions[action]
        entry = resolve(target)
        if entry is None:
            raise RuntimeError(
                f"action group {group.dispatcher_name}: legacy tool {target!r} "
                "is not in the catalog"
            )
        variants[action] = _variant_props(
            entry.input_schema, group.arg_aliases.get(action, {})
        )
        note = ""
        if entry.platforms == ("darwin",):
            note = " (macOS only)"
        elif entry.platforms == ("win32",):
            note = " (Windows only)"
        action_lines.append(f"{action}: {_first_sentence(entry.description)}{note}")
        platform_sets.append(entry.platforms)
        timeout = max(timeout, entry.timeout_seconds)
        if entry.output_schema is not None:
            output_schemas.append(entry.output_schema)

    # Group-level gating only when EVERY member shares the same gate.
    unique_platforms = {p for p in platform_sets}
    platforms = unique_platforms.pop() if len(unique_platforms) == 1 else None

    # Top-level union of variant params (flat dialect, `required` stripped —
    # only `action` is unconditionally required).
    union: dict[str, dict[str, Any]] = {}
    prop_actions: dict[str, list[str]] = {}
    for action in sorted(variants):
        for name, spec in variants[action].items():
            if name not in union:
                cleaned = {k: v for k, v in spec.items() if k != "required"}
                union[name] = cleaned
            prop_actions.setdefault(name, []).append(action)
    all_actions = sorted(group.actions)
    if unknown := sorted(set(group.param_descriptions) - set(union)):
        raise RuntimeError(
            f"action group {group.dispatcher_name}: param_descriptions names "
            f"params no action takes: {unknown}"
        )
    for name, used_by in prop_actions.items():
        if name in group.param_descriptions:
            union[name]["description"] = group.param_descriptions[name]
        elif len(used_by) < len(all_actions):
            base = union[name].get("description", "").rstrip()
            suffix = f"Used with action(s): {', '.join(used_by)}."
            union[name]["description"] = f"{base} {suffix}".strip()

    action_prop: dict[str, Any] = {"type": "string", "enum": all_actions}
    if group.action_description is not None:
        action_prop["description"] = group.action_description
    action_prop["required"] = True

    cloud_parameters: dict[str, Any] = {"action": action_prop}
    cloud_parameters.update(union)
    cloud_parameters["$variants"] = variants

    standard_props: dict[str, Any] = {
        "action": {k: v for k, v in action_prop.items() if k != "required"},
    }
    for name, spec in union.items():
        standard_props[name] = {k: v for k, v in spec.items() if k != "required"}
    input_schema = {
        "type": "object",
        "properties": standard_props,
        "required": ["action"],
    }

    description = group.description + " Actions — " + "; ".join(action_lines) + "."

    output_schema = None
    if output_schemas:
        from app.content_ir import generic_tool_output_json_schema

        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        shared_defs: dict[str, Any] = {}
        for schema in [*output_schemas, generic_tool_output_json_schema()]:
            branch = copy.deepcopy(schema)
            defs = branch.pop("$defs", None)
            if isinstance(defs, dict):
                for name, definition in defs.items():
                    existing = shared_defs.get(name)
                    if existing is not None and existing != definition:
                        raise RuntimeError(
                            f"action group {group.dispatcher_name}: conflicting "
                            f"output-schema $defs entry {name!r}"
                        )
                    shared_defs[name] = definition
            fingerprint = json.dumps(branch, sort_keys=True)
            if fingerprint not in seen:
                seen.add(fingerprint)
                unique.append(branch)
        output_schema = {
            **({"$defs": shared_defs} if shared_defs else {}),
            "oneOf": unique,
        }

    return {
        "description": description,
        "input_schema": input_schema,
        "cloud_parameters": cloud_parameters,
        "platforms": platforms,
        "timeout_seconds": timeout,
        "output_schema": output_schema,
    }
