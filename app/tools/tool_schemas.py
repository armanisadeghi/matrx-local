"""Auto-generate Anthropic-compatible tool schemas from tool handler signatures.

Introspects each registered tool handler to produce JSON Schema definitions
compatible with the Anthropic Messages API tool_use format and MCP protocol.
"""

from __future__ import annotations

import inspect
import logging
import types
from typing import Any, Union, get_args, get_origin

from app.tools.dispatcher import TOOL_HANDLERS

logger = logging.getLogger(__name__)

# Python type → JSON Schema type
_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}

# Tool categories inferred from the dispatcher module groupings
TOOL_CATEGORIES: dict[str, list[str]] = {
    "File Operations": ["Read", "Write", "Edit", "Glob", "Grep"],
    "Execution": ["Bash", "BashOutput", "TaskStop"],
    "System": ["SystemInfo", "Screenshot", "ListScreens", "ListDirectory", "OpenUrl", "OpenPath"],
    "Clipboard": ["ClipboardRead", "ClipboardWrite"],
    "Notifications": ["Notify"],
    "Network": ["FetchUrl", "FetchWithBrowser", "Scrape", "Search", "Research"],
    "File Transfer": ["DownloadFile", "UploadFile"],
    "Process Management": ["ListProcesses", "LaunchApp", "KillProcess", "FocusApp", "ListPorts"],
    "Window Management": ["ListWindows", "FocusWindow", "MoveWindow", "MinimizeWindow"],
    "Input Automation": ["TypeText", "Hotkey", "MouseClick", "MouseMove"],
    "Audio": ["ListAudioDevices", "RecordAudio", "PlayAudio", "TranscribeAudio"],
    "Browser Automation": [
        "BrowserNavigate", "BrowserClick", "BrowserType",
        "BrowserExtract", "BrowserScreenshot", "BrowserEval", "BrowserTabs",
        "BrowserClose",
    ],
    "Network Discovery": ["NetworkInfo", "NetworkScan", "PortScan", "MDNSDiscover"],
    "System Monitoring": ["SystemResources", "BatteryStatus", "DiskUsage", "TopProcesses"],
    "File Watching": ["WatchDirectory", "WatchEvents", "StopWatch"],
    "OS Integration": ["AppleScript", "PowerShellScript", "GetInstalledApps"],
    "Scheduler": ["ScheduleTask", "ListScheduled", "CancelScheduled", "HeartbeatStatus", "PreventSleep"],
    "Media Processing": ["ImageOCR", "ImageResize", "PdfExtract", "OfficeGenerate", "ArchiveCreate", "ArchiveExtract"],
    "Local NER": ["ExtractEntities", "ExtractPII"],
    "WiFi & Bluetooth": ["WifiNetworks", "BluetoothDevices", "ConnectedDevices"],
    "Documents": ["ListDocuments", "ListDocumentFolders", "ReadDocument", "WriteDocument", "SearchDocuments"],
    "PowerShell": [
        "PSGetEnv", "PSSetEnv",
        "RegistryRead", "RegistryWrite",
        "ServiceList", "ServiceControl",
        "EventLog", "WindowsFeatures",
    ],
    "Mail": ["ListEmails", "SendEmail", "GetEmailAccounts"],
    "Messages": ["ListMessages", "ListConversations", "SendMessage"],
    "Contacts": ["SearchContacts", "GetContact"],
    "Calendar": ["ListEvents", "CreateEvent", "ListReminders", "CreateReminder"],
    "Photos": ["SearchPhotos", "GetPhoto"],
    "Location": ["GetLocation"],
    "Speech Recognition": ["TranscribeWithSpeech", "ListSpeechLocales"],
    # Action-enum mega-tools (W7 collapse) — the advertised surface.
    "Actions": [
        "File", "Shell", "Window", "Process", "Input", "Audio", "Clipboard",
        "Screen", "System", "Browser", "Web", "Net", "Monitor", "Schedule",
        "Documents", "Media", "Ner", "MacApps", "WindowsPs",
    ],
}

# Reverse lookup: tool name → category
_TOOL_TO_CATEGORY: dict[str, str] = {}
for cat, tools in TOOL_CATEGORIES.items():
    for t in tools:
        _TOOL_TO_CATEGORY[t] = cat


def _python_type_to_json_schema(annotation: Any) -> dict[str, Any]:
    """Convert a Python type annotation to a JSON Schema fragment."""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {"type": "string"}

    origin = get_origin(annotation)

    # Handle Optional[X] (Union[X, None])
    if origin is type(None):
        return {"type": "string"}

    # Handle list[X]
    if origin is list:
        args = get_args(annotation)
        items = _python_type_to_json_schema(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": items}

    # Handle dict[K, V]
    if origin is dict:
        return {"type": "object"}

    # Handle Union types (e.g. str | None, list[str] | dict[str, str]).
    if isinstance(annotation, types.UnionType) or origin is Union:
        args = get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _python_type_to_json_schema(non_none[0])
        schemas = [_python_type_to_json_schema(arg) for arg in non_none]
        if len(schemas) == 1:
            return schemas[0]
        return {"anyOf": schemas}

    # Simple types
    if annotation in _TYPE_MAP:
        return {"type": _TYPE_MAP[annotation]}

    return {"type": "string"}


def _extract_param_descriptions(docstring: str | None) -> dict[str, str]:
    """Extract parameter descriptions from a docstring (Google/numpy style)."""
    if not docstring:
        return {}

    descriptions: dict[str, str] = {}
    lines = docstring.split("\n")
    in_params = False

    for line in lines:
        stripped = line.strip()
        if stripped.lower() in ("args:", "parameters:", "params:"):
            in_params = True
            continue
        if in_params:
            if stripped and not stripped.startswith(" ") and stripped.endswith(":"):
                in_params = False
                continue
            # Match "param_name: description" or "param_name (type): description"
            if ":" in stripped and not stripped.startswith("-"):
                parts = stripped.split(":", 1)
                param_name = parts[0].strip().split("(")[0].strip().lstrip("-").strip()
                if param_name and parts[1].strip():
                    descriptions[param_name] = parts[1].strip()

    return descriptions


def _handler_signature(handler: Any) -> inspect.Signature:
    """Signature with REAL type annotations.

    Every tool module uses ``from __future__ import annotations`` (PEP 563),
    so a plain ``inspect.signature()`` returns *string* annotations and every
    parameter would silently degrade to ``{"type": "string"}``. ``eval_str=True``
    resolves them against the handler's module globals; if a stray annotation
    cannot be evaluated we fall back to the raw signature for that handler.
    """
    try:
        return inspect.signature(handler, eval_str=True)
    except (NameError, AttributeError, TypeError) as exc:
        logger.warning(
            "Could not resolve annotations for %s (%s) — its schema degrades "
            "to string-typed parameters",
            getattr(handler, "__name__", handler), exc,
        )
        return inspect.signature(handler)


def generate_tool_schema(tool_name: str) -> dict[str, Any] | None:
    """Generate an Anthropic-compatible tool schema for a single tool."""
    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return None

    # Action-enum mega-tools take **tool_input (nothing to introspect); their
    # composed schema lives on the catalog entry. Deferred import — the
    # catalog imports generate_tool_schema for LEGACY names only, so this
    # cannot recurse.
    from app.tools.actions import ACTION_GROUPS

    if tool_name in ACTION_GROUPS:
        from app.tools.catalog import get_by_dispatcher_name

        entry = get_by_dispatcher_name(tool_name)
        if entry is None:  # pragma: no cover — catalog build guarantees presence
            return None
        return {
            "name": tool_name,
            "description": entry.description,
            "category": _TOOL_TO_CATEGORY.get(tool_name, "Actions"),
            "input_schema": entry.input_schema,
        }

    sig = _handler_signature(handler)
    docstring = inspect.getdoc(handler) or f"Execute the {tool_name} tool."
    param_docs = _extract_param_descriptions(docstring)

    # Use just the first line of docstring as description
    description = docstring.split("\n")[0].strip()
    if not description:
        description = f"Execute the {tool_name} tool."

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        # Skip 'session' parameter — injected by dispatcher
        if param_name == "session":
            continue

        schema = _python_type_to_json_schema(param.annotation)

        # Add description from docstring if available
        if param_name in param_docs:
            schema["description"] = param_docs[param_name]

        # Add default value info
        if param.default is not inspect.Parameter.empty:
            if param.default is not None:
                schema["default"] = param.default
        else:
            required.append(param_name)

        properties[param_name] = schema

    return {
        "name": tool_name,
        "description": description,
        "category": _TOOL_TO_CATEGORY.get(tool_name, "Other"),
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def generate_all_tool_schemas() -> list[dict[str, Any]]:
    """Generate schemas for all registered tools."""
    schemas = []
    for tool_name in sorted(TOOL_HANDLERS.keys()):
        schema = generate_tool_schema(tool_name)
        if schema:
            schemas.append(schema)
    return schemas


def get_tool_schemas_by_category() -> dict[str, list[dict[str, Any]]]:
    """Return tool schemas grouped by category."""
    schemas = generate_all_tool_schemas()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for schema in schemas:
        cat = schema.get("category", "Other")
        grouped.setdefault(cat, []).append(schema)
    return grouped


def get_anthropic_tools() -> list[dict[str, Any]]:
    """Return tool schemas in the exact format expected by the Anthropic Messages API.

    Strips the 'category' field since the API doesn't accept it.
    """
    schemas = generate_all_tool_schemas()
    return [
        {
            "name": s["name"],
            "description": s["description"],
            "input_schema": s["input_schema"],
        }
        for s in schemas
    ]
