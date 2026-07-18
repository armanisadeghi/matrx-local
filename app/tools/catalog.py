"""Canonical tool catalog — the ONE code-side structural truth for local tools.

Built automatically from the dispatcher (``TOOL_HANDLERS``) plus signature
introspection (``app.tools.tool_schemas``). Every dispatched tool gets exactly
one :class:`CatalogEntry` carrying:

  - ``cloud_name``       — the canonical platform-wide tool name
                           (``tool.definition.name`` in Supabase, snake_case
                           ``local_*``). Names already registered in the cloud
                           are PINNED in ``_META`` and must never drift.
  - ``dispatcher_name``  — the PascalCase name used by ``dispatch()`` and the
                           matrx-extend ``/extension/rpc`` capabilities surface.
  - ``input_schema``     — JSON Schema for the arguments. When a Pydantic
                           arg model exists (``app/tools/arg_models``) its
                           ``model_json_schema()`` wins (richer: descriptions,
                           enums, bounds); otherwise the schema is introspected
                           from the handler signature + docstring.
  - ``category`` / ``tags`` / ``version`` / ``timeout_seconds``
  - ``platforms``        — declared platform gating (``("darwin",)`` /
                           ``("win32",)`` / ``None`` = all). Gated handlers
                           ALSO self-gate at runtime with a clear
                           "only available on <OS>" error envelope; this field
                           is advertisement metadata, not the enforcement.

Descriptions: the strings here are the PUSH PAYLOAD for new cloud rows and the
OFFLINE FALLBACK (engine Phase-A½ backfill). At runtime the DB-synced registry
is authoritative for descriptions — the house rule is descriptions live in the
DB (see matrx-extend ``src/lib/tools/descriptions.ts`` Rule 4).

ADDING A TOOL:
  1. Implement ``tool_xxx()`` in ``app/tools/tools/<module>.py``
  2. Wire it into ``TOOL_HANDLERS`` in ``app/tools/dispatcher.py``
  3. (Recommended) add an arg model in ``app/tools/arg_models/`` and reference
     it in ``_META`` below; otherwise introspection generates the schema
  4. ``uv run python -m app.tools.tool_sync status`` → ``emit-changeset`` and
     apply the changeset to Supabase (operator, via MCP)

The catalog build FAILS LOUDLY (RuntimeError) on: a ``_META`` key that is not
a dispatcher tool, duplicate cloud names, or a malformed generated name.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from app.tools.arg_models.app_args import (
    AppleScriptArgs,
    GetInstalledAppsArgs,
    ListDocumentFoldersArgs,
    ListDocumentsArgs,
    PowerShellScriptArgs,
    ReadDocumentArgs,
    SearchDocumentsArgs,
    WriteDocumentArgs,
)
from app.tools.arg_models.browser_args import (
    BrowserClickArgs,
    BrowserEvalArgs,
    BrowserExtractArgs,
    BrowserNavigateArgs,
    BrowserScreenshotArgs,
    BrowserTabsArgs,
    BrowserTypeArgs,
    FetchUrlArgs,
    FetchWithBrowserArgs,
    ResearchArgs,
    ScrapeArgs,
    SearchArgs,
)
from app.tools.arg_models.execution_args import BashArgs, BashOutputArgs, TaskStopArgs
from app.tools.arg_models.file_ops_args import (
    CopyArgs,
    DeleteArgs,
    EditArgs,
    GlobArgs,
    GrepArgs,
    FindPathsArgs,
    ListDirectoryArgs,
    PlacesArgs,
    SemanticFindPathsArgs,
    MkdirArgs,
    MoveArgs,
    ReadArgs,
    RenameArgs,
    WriteArgs,
)
from app.tools.arg_models.input_args import (
    FocusWindowArgs,
    HotkeyArgs,
    ListWindowsArgs,
    MinimizeWindowArgs,
    MouseClickArgs,
    MouseMoveArgs,
    MoveWindowArgs,
    TypeTextArgs,
)
from app.tools.arg_models.media_args import (
    ArchiveCreateArgs,
    ArchiveExtractArgs,
    ImageOcrArgs,
    ImageResizeArgs,
    OfficeGenerateArgs,
    PdfExtractArgs,
)
from app.tools.arg_models.ner_args import ExtractEntitiesArgs, ExtractPiiArgs
from app.tools.arg_models.network_args import (
    MdnsDiscoverArgs,
    NetworkInfoArgs,
    NetworkScanArgs,
    PortScanArgs,
)
from app.tools.arg_models.process_args import (
    FocusAppArgs,
    KillProcessArgs,
    LaunchAppArgs,
    ListPortsArgs,
    ListProcessesArgs,
)
from app.tools.arg_models.system_args import (
    BatteryStatusArgs,
    ClipboardReadArgs,
    ClipboardWriteArgs,
    DiskUsageArgs,
    NotifyArgs,
    OpenPathArgs,
    OpenUrlArgs,
    ScreenshotArgs,
    SystemInfoArgs,
    SystemResourcesArgs,
    TopProcessesArgs,
)
from app.tools.dispatcher import TOOL_HANDLERS
from app.content_ir import screenshot_artifact_json_schema

# Platform gating shorthands (values are `sys.platform` strings).
DARWIN: tuple[str, ...] = ("darwin",)
WINDOWS: tuple[str, ...] = ("win32",)

CATALOG_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Per-tool metadata (the hand-curated half of the catalog)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolMeta:
    """Curated metadata for one dispatcher tool.

    ``cloud_name=None`` means "auto-generate ``local_<snake_case>``".
    ``description=None`` means "use the handler docstring's first line".
    """

    cloud_name: str | None = None
    description: str | None = None
    category: str = "local_system"
    tags: tuple[str, ...] = ()
    arg_model: type | None = None
    timeout_seconds: float = 120.0
    platforms: tuple[str, ...] | None = None
    output_schema: dict[str, Any] | None = None


# NOTE ON PINNED NAMES: the entries below with an explicit ``cloud_name``
# reproduce the retired LOCAL_TOOL_MANIFEST lineage exactly — 49 of them are
# LIVE in Supabase ``tool.definition`` bound to executor ``matrx-local``.
# Renaming one strands a cloud-registered tool. Do not touch without a
# matching cloud changeset.
_META: dict[str, ToolMeta] = {
    # ── Execution ────────────────────────────────────────────────────────
    "Bash": ToolMeta(
        "local_bash",
        "Run a shell command on the local system with full OS access. "
        "Tracks working directory across calls via session state.",
        "local_execution", ("shell", "local", "os", "command"), BashArgs,
    ),
    "BashOutput": ToolMeta(
        "local_bash_output",
        "Read accumulated output from a background shell command started "
        "with local_bash (run_in_background=true).",
        "local_execution", ("shell", "local", "background"), BashOutputArgs,
    ),
    "TaskStop": ToolMeta(
        "local_task_stop",
        "Stop a background shell task started with local_bash.",
        "local_execution", ("shell", "local", "background", "stop"), TaskStopArgs,
    ),
    # ── File Operations ──────────────────────────────────────────────────
    "Read": ToolMeta(
        "local_read_file",
        "Read a file from the local filesystem. Returns line-numbered content. "
        "Supports offset and limit for large files.",
        "local_file_ops", ("file", "read", "local", "filesystem"), ReadArgs,
    ),
    "Write": ToolMeta(
        "local_write_file",
        "Write content to a file on the local filesystem. "
        "Creates parent directories as needed.",
        "local_file_ops", ("file", "write", "local", "filesystem"), WriteArgs,
    ),
    "Edit": ToolMeta(
        "local_edit_file",
        "Apply a precise string replacement to a file. old_string must match "
        "exactly (including whitespace) and be unique in the file, unless "
        "replace_all is true.",
        "local_file_ops", ("file", "edit", "local", "filesystem"), EditArgs,
    ),
    "Move": ToolMeta(
        "local_move_path",
        "Move a file or directory to a new location on the local filesystem. "
        "Moving a file into an existing directory keeps its name. Refuses to "
        "overwrite unless overwrite=true.",
        "local_file_ops", ("file", "move", "local", "filesystem"), MoveArgs,
    ),
    "Copy": ToolMeta(
        "local_copy_path",
        "Copy a file or directory on the local filesystem (recursive for "
        "directories, metadata preserved). Refuses to overwrite unless "
        "overwrite=true.",
        "local_file_ops", ("file", "copy", "local", "filesystem"), CopyArgs,
    ),
    "Delete": ToolMeta(
        "local_delete_path",
        "Delete a file or directory. Moves to the OS trash by default so the "
        "action is recoverable; pass permanent=true for an unrecoverable "
        "delete.",
        "local_file_ops", ("file", "delete", "trash", "local", "filesystem"), DeleteArgs,
    ),
    "Rename": ToolMeta(
        "local_rename_path",
        "Rename a file or directory in place (same parent directory). "
        "Use Move to relocate.",
        "local_file_ops", ("file", "rename", "local", "filesystem"), RenameArgs,
    ),
    "Mkdir": ToolMeta(
        "local_make_directory",
        "Create a directory on the local filesystem, including missing parent "
        "directories by default.",
        "local_file_ops", ("file", "directory", "create", "local", "filesystem"), MkdirArgs,
    ),
    "Glob": ToolMeta(
        "local_glob",
        "Find files matching a glob pattern on the local filesystem.",
        "local_file_ops", ("file", "search", "glob", "local"), GlobArgs,
    ),
    "Grep": ToolMeta(
        "local_grep",
        "Search file contents for a regex pattern on the local filesystem.",
        "local_file_ops", ("file", "search", "grep", "local"), GrepArgs,
    ),
    "ListDirectory": ToolMeta(
        "local_list_directory",
        "List the contents of a directory on the local filesystem.",
        "local_file_ops", ("file", "directory", "list", "local"), ListDirectoryArgs,
    ),
    "FilesystemPlaces": ToolMeta(
        "local_filesystem_places",
        "Return the user's local Home, standard folders, configured priority roots, and accessible volumes.",
        "local_file_ops", ("file", "directory", "places", "local"), PlacesArgs,
    ),
    "FindPaths": ToolMeta(
        "local_find_paths",
        "Find files and directories on the user's machine using the progressive metadata index.",
        "local_file_ops", ("file", "search", "find", "local"), FindPathsArgs,
    ),
    "SemanticFindPaths": ToolMeta(
        "local_semantic_find_paths",
        "Find local files by meaning using the optional background embedding index.",
        "local_file_ops", ("file", "search", "semantic", "local"), SemanticFindPathsArgs,
    ),
    # ── System ───────────────────────────────────────────────────────────
    "SystemInfo": ToolMeta(
        "local_system_info",
        "Get detailed information about the local system: OS, CPU, RAM, disk, hostname.",
        "local_system", ("system", "info", "local"), SystemInfoArgs,
    ),
    "Screenshot": ToolMeta(
        "local_screenshot",
        "Take a screenshot of the local screen and return a durable Content IR media artifact.",
        "local_system", ("screenshot", "screen", "local"), ScreenshotArgs,
        timeout_seconds=15.0,
        output_schema=screenshot_artifact_json_schema(),
    ),
    "ListScreens": ToolMeta(category="local_system", tags=("screen", "display", "local")),
    "OpenUrl": ToolMeta(
        "local_open_url",
        "Open a URL in the default web browser on the local system.",
        "local_system", ("browser", "url", "open", "local"), OpenUrlArgs,
    ),
    "OpenPath": ToolMeta(
        "local_open_path",
        "Open a file or directory in the system's default application "
        "(Finder on macOS, Explorer on Windows).",
        "local_system", ("file", "open", "local"), OpenPathArgs,
    ),
    "SystemResources": ToolMeta(
        "local_system_resources",
        "Get real-time CPU, memory, disk, and network usage statistics.",
        "local_system", ("monitor", "cpu", "memory", "local"), SystemResourcesArgs,
    ),
    "BatteryStatus": ToolMeta(
        "local_battery_status",
        "Get battery level, charging status, and estimated time remaining.",
        "local_system", ("battery", "power", "local"), BatteryStatusArgs,
    ),
    "DiskUsage": ToolMeta(
        "local_disk_usage",
        "Get disk usage statistics for all mounted volumes or a specific path.",
        "local_system", ("disk", "storage", "local"), DiskUsageArgs,
    ),
    "TopProcesses": ToolMeta(
        "local_top_processes",
        "Get top N processes by CPU or memory usage.",
        "local_system", ("process", "monitor", "local"), TopProcessesArgs,
    ),
    "ClipboardRead": ToolMeta(
        "local_clipboard_read",
        "Read the current contents of the system clipboard.",
        "local_system", ("clipboard", "local"), ClipboardReadArgs,
    ),
    "ClipboardWrite": ToolMeta(
        "local_clipboard_write",
        "Write text to the system clipboard.",
        "local_system", ("clipboard", "local"), ClipboardWriteArgs,
    ),
    "Notify": ToolMeta(
        "local_notify",
        "Show a desktop notification on the local system.",
        "local_system", ("notification", "local"), NotifyArgs,
    ),
    # ── Process Management ───────────────────────────────────────────────
    "ListProcesses": ToolMeta(
        "local_list_processes",
        "List running processes with PID, name, CPU%, and memory usage.",
        "local_process", ("process", "system", "local"), ListProcessesArgs,
    ),
    "ListPorts": ToolMeta(
        "local_list_ports",
        "List listening TCP/UDP ports and the processes bound to them.",
        "local_process", ("network", "ports", "local"), ListPortsArgs,
    ),
    "LaunchApp": ToolMeta(
        "local_launch_app",
        "Launch an application on the local system by name or path.",
        "local_process", ("app", "launch", "local"), LaunchAppArgs,
        timeout_seconds=60.0,
    ),
    "KillProcess": ToolMeta(
        "local_kill_process",
        "Kill a running process by PID or name.",
        "local_process", ("process", "kill", "local"), KillProcessArgs,
    ),
    "FocusApp": ToolMeta(
        "local_focus_app",
        "Bring an application window to the foreground. "
        "Uses AppleScript on macOS, PowerShell on Windows.",
        "local_process", ("app", "focus", "window", "local"), FocusAppArgs,
    ),
    "ListTerminals": ToolMeta(category="local_process", tags=("terminal", "process", "local")),
    "TailTerminal": ToolMeta(category="local_process", tags=("terminal", "output", "local")),
    # ── Browser Automation ───────────────────────────────────────────────
    "BrowserNavigate": ToolMeta(
        "local_browser_navigate",
        "Navigate the local Playwright-controlled browser to a URL.",
        "local_browser", ("browser", "navigate", "playwright", "local"),
        BrowserNavigateArgs, timeout_seconds=30.0,
    ),
    "BrowserClick": ToolMeta(
        "local_browser_click",
        "Click an element on the current browser page by CSS selector.",
        "local_browser", ("browser", "click", "playwright", "local"),
        BrowserClickArgs, timeout_seconds=15.0,
    ),
    "BrowserType": ToolMeta(
        "local_browser_type",
        "Type text into an input element on the current browser page.",
        "local_browser", ("browser", "type", "playwright", "local"),
        BrowserTypeArgs, timeout_seconds=15.0,
    ),
    "BrowserExtract": ToolMeta(
        "local_browser_extract",
        "Extract text, HTML, attributes, or form values from the current browser page.",
        "local_browser", ("browser", "extract", "scrape", "local"),
        BrowserExtractArgs, timeout_seconds=15.0,
    ),
    "BrowserScreenshot": ToolMeta(
        "local_browser_screenshot",
        "Take a screenshot of the current browser page or a specific element.",
        "local_browser", ("browser", "screenshot", "playwright", "local"),
        BrowserScreenshotArgs, timeout_seconds=15.0,
        output_schema=screenshot_artifact_json_schema(),
    ),
    "BrowserEval": ToolMeta(
        "local_browser_eval",
        "Execute JavaScript in the current browser page context.",
        "local_browser", ("browser", "javascript", "playwright", "local"),
        BrowserEvalArgs, timeout_seconds=15.0,
    ),
    "BrowserTabs": ToolMeta(
        "local_browser_tabs",
        "Manage browser tabs: list, open new, close, or switch to a tab.",
        "local_browser", ("browser", "tabs", "playwright", "local"),
        BrowserTabsArgs, timeout_seconds=15.0,
    ),
    "BrowserClose": ToolMeta(
        category="local_browser",
        tags=("browser", "close", "playwright", "local"),
        timeout_seconds=15.0,
    ),
    # ── Network / HTTP ───────────────────────────────────────────────────
    "FetchUrl": ToolMeta(
        "local_fetch_url",
        "Fetch content from a URL using HTTP (curl-cffi). Returns status, headers, and body.",
        "local_network", ("http", "fetch", "network", "local"), FetchUrlArgs,
        timeout_seconds=60.0,
    ),
    "FetchWithBrowser": ToolMeta(
        "local_fetch_with_browser",
        "Fetch a URL using a headless browser (Playwright). Use when the page "
        "requires JavaScript rendering.",
        "local_network", ("http", "fetch", "browser", "playwright", "local"),
        FetchWithBrowserArgs, timeout_seconds=60.0,
    ),
    "Scrape": ToolMeta(
        "local_scrape",
        "Scrape one or more URLs with the full scraper pipeline "
        "(JS rendering, content extraction, optional caching).",
        "local_network", ("scrape", "web", "local"), ScrapeArgs,
        timeout_seconds=120.0,
    ),
    "Search": ToolMeta(
        "local_search",
        "Search the web using Brave Search API and return results.",
        "local_network", ("search", "web", "brave", "local"), SearchArgs,
        timeout_seconds=30.0,
    ),
    "Research": ToolMeta(
        "local_research",
        "Deep web research: search + scrape all results + compile findings "
        "into a structured report.",
        "local_network", ("research", "search", "scrape", "web", "local"),
        ResearchArgs, timeout_seconds=300.0,
    ),
    # ── Network Discovery ────────────────────────────────────────────────
    "NetworkInfo": ToolMeta(
        "local_network_info",
        "Get local network information: IPs, interfaces, gateway, DNS, MAC addresses.",
        "local_network", ("network", "info", "local"), NetworkInfoArgs,
    ),
    "NetworkScan": ToolMeta(
        "local_network_scan",
        "Scan the local network for active hosts using ARP.",
        "local_network", ("network", "scan", "arp", "local"), NetworkScanArgs,
        timeout_seconds=60.0,
    ),
    "PortScan": ToolMeta(
        "local_port_scan",
        "Scan a host for open TCP ports.",
        "local_network", ("network", "ports", "scan", "local"), PortScanArgs,
        timeout_seconds=120.0,
    ),
    "MDNSDiscover": ToolMeta(
        "local_mdns_discover",
        "Discover mDNS/Bonjour services on the local network "
        "(smart devices, printers, AirPlay, HomeKit, etc.).",
        "local_network", ("network", "mdns", "bonjour", "discovery", "local"),
        MdnsDiscoverArgs, timeout_seconds=30.0,
    ),
    "WifiNetworks": ToolMeta(category="local_network", tags=("wifi", "network", "local")),
    "BluetoothDevices": ToolMeta(category="local_network", tags=("bluetooth", "devices", "local")),
    "ConnectedDevices": ToolMeta(category="local_network", tags=("devices", "usb", "local")),
    # ── Input Automation ─────────────────────────────────────────────────
    "TypeText": ToolMeta(
        "local_type_text",
        "Type text using the system keyboard (simulates keystrokes).",
        "local_input", ("keyboard", "type", "automation", "local"), TypeTextArgs,
    ),
    "Hotkey": ToolMeta(
        "local_hotkey",
        "Send a keyboard shortcut (e.g. 'cmd+c', 'ctrl+shift+s', 'alt+tab'). "
        "Modifiers: cmd/command, ctrl/control, alt/option, shift.",
        "local_input", ("keyboard", "hotkey", "automation", "local"), HotkeyArgs,
    ),
    "MouseClick": ToolMeta(
        "local_mouse_click",
        "Click the mouse at specific screen coordinates.",
        "local_input", ("mouse", "click", "automation", "local"), MouseClickArgs,
    ),
    "MouseMove": ToolMeta(
        "local_mouse_move",
        "Move the mouse cursor to specific screen coordinates.",
        "local_input", ("mouse", "move", "automation", "local"), MouseMoveArgs,
    ),
    # ── Window Management ────────────────────────────────────────────────
    "ListWindows": ToolMeta(
        "local_list_windows",
        "List all visible windows with title, app name, position, and size.",
        "local_window", ("window", "list", "local"), ListWindowsArgs,
    ),
    "FocusWindow": ToolMeta(
        "local_focus_window",
        "Bring a window to the foreground by app name and optional title.",
        "local_window", ("window", "focus", "local"), FocusWindowArgs,
    ),
    "MoveWindow": ToolMeta(
        "local_move_window",
        "Move and/or resize a window by app name.",
        "local_window", ("window", "move", "resize", "local"), MoveWindowArgs,
    ),
    "MinimizeWindow": ToolMeta(
        "local_minimize_window",
        "Minimize, maximize, or restore a window.",
        "local_window", ("window", "minimize", "maximize", "local"), MinimizeWindowArgs,
    ),
    # ── OS Integration ───────────────────────────────────────────────────
    "AppleScript": ToolMeta(
        "local_applescript",
        "Execute AppleScript on macOS. Controls Finder, Mail, Calendar, "
        "Safari, and any scriptable application.",
        "local_os", ("applescript", "macos", "automation", "local"),
        AppleScriptArgs, timeout_seconds=60.0, platforms=DARWIN,
    ),
    "PowerShellScript": ToolMeta(
        "local_powershell",
        "Execute a PowerShell script on Windows. Has access to COM, WMI, "
        ".NET APIs, and the registry.",
        "local_os", ("powershell", "windows", "automation", "local"),
        PowerShellScriptArgs, timeout_seconds=60.0,
    ),
    "GetInstalledApps": ToolMeta(
        "local_get_installed_apps",
        "List installed applications on the system, optionally filtered by name.",
        "local_os", ("apps", "installed", "local"), GetInstalledAppsArgs,
    ),
    "PSGetEnv": ToolMeta(category="local_os", tags=("env", "powershell", "local")),
    "PSSetEnv": ToolMeta(
        category="local_os", tags=("env", "powershell", "windows", "local"),
        platforms=WINDOWS,
    ),
    "RegistryRead": ToolMeta(
        category="local_os", tags=("registry", "windows", "local"), platforms=WINDOWS,
    ),
    "RegistryWrite": ToolMeta(
        category="local_os", tags=("registry", "windows", "local"), platforms=WINDOWS,
    ),
    "ServiceList": ToolMeta(category="local_os", tags=("services", "system", "local")),
    "ServiceControl": ToolMeta(category="local_os", tags=("services", "control", "local")),
    "EventLog": ToolMeta(
        category="local_os", tags=("eventlog", "windows", "local"), platforms=WINDOWS,
    ),
    "WindowsFeatures": ToolMeta(
        category="local_os", tags=("features", "windows", "local"), platforms=WINDOWS,
    ),
    # ── Media Processing ─────────────────────────────────────────────────
    "ImageOCR": ToolMeta(
        "local_image_ocr",
        "Extract text from an image file using OCR (Tesseract).",
        "local_media", ("ocr", "image", "text", "local"), ImageOcrArgs,
        timeout_seconds=60.0,
    ),
    "ImageResize": ToolMeta(
        "local_image_resize",
        "Resize or convert an image file.",
        "local_media", ("image", "resize", "convert", "local"), ImageResizeArgs,
        timeout_seconds=30.0,
    ),
    "PdfExtract": ToolMeta(
        "local_pdf_extract",
        "Extract text (and optionally images) from a PDF file.",
        "local_media", ("pdf", "extract", "text", "local"), PdfExtractArgs,
        timeout_seconds=60.0,
    ),
    "OfficeGenerate": ToolMeta(
        "local_office_generate",
        "Generate a Microsoft Office document (.docx / .pptx / .xlsx) from a "
        "structured spec and write it to a local path.",
        "local_media", ("office", "docx", "pptx", "xlsx", "generate", "local"),
        OfficeGenerateArgs, timeout_seconds=60.0,
    ),
    "ArchiveCreate": ToolMeta(
        "local_archive_create",
        "Create a zip or tar archive from files and directories.",
        "local_media", ("archive", "zip", "tar", "local"), ArchiveCreateArgs,
        timeout_seconds=120.0,
    ),
    "ArchiveExtract": ToolMeta(
        "local_archive_extract",
        "Extract a zip, tar, or 7z archive.",
        "local_media", ("archive", "extract", "zip", "tar", "local"),
        ArchiveExtractArgs, timeout_seconds=120.0,
    ),
    # ── Local NER ────────────────────────────────────────────────────────
    "ExtractEntities": ToolMeta(
        "local_extract_entities",
        "Extract zero-shot named entities from text using the local GLiNER / GLiNER2 NER subsystem.",
        "local_ner", ("ner", "entities", "gliner", "local"),
        ExtractEntitiesArgs, timeout_seconds=300.0,
    ),
    "ExtractPII": ToolMeta(
        "local_extract_pii",
        "Extract common PII entities from text using the local GLiNER / GLiNER2 NER subsystem.",
        "local_ner", ("ner", "pii", "privacy", "gliner", "local"),
        ExtractPiiArgs, timeout_seconds=300.0,
    ),
    # ── Documents ────────────────────────────────────────────────────────
    "ListDocuments": ToolMeta(
        "local_list_documents",
        "List documents in the local document store (~/.matrx/documents/).",
        "local_documents", ("documents", "notes", "local"), ListDocumentsArgs,
    ),
    "ReadDocument": ToolMeta(
        "local_read_document",
        "Read the full content of a document from the local document store.",
        "local_documents", ("documents", "notes", "read", "local"), ReadDocumentArgs,
    ),
    "WriteDocument": ToolMeta(
        "local_write_document",
        "Create or update a Markdown document in the local document store.",
        "local_documents", ("documents", "notes", "write", "local"), WriteDocumentArgs,
    ),
    "SearchDocuments": ToolMeta(
        "local_search_documents",
        "Search document content in the local store by keyword.",
        "local_documents", ("documents", "search", "notes", "local"),
        SearchDocumentsArgs,
    ),
    "ListDocumentFolders": ToolMeta(
        "local_list_document_folders",
        "List all folders in the local document store.",
        "local_documents", ("documents", "folders", "local"), ListDocumentFoldersArgs,
    ),
    # ── File Transfer ────────────────────────────────────────────────────
    "DownloadFile": ToolMeta(
        category="local_transfer", tags=("file", "download", "transfer", "local"),
        timeout_seconds=300.0,
    ),
    "UploadFile": ToolMeta(
        category="local_transfer", tags=("file", "upload", "transfer", "local"),
        timeout_seconds=300.0,
    ),
    # ── Audio ────────────────────────────────────────────────────────────
    "ListAudioDevices": ToolMeta(category="local_audio", tags=("audio", "devices", "local")),
    "RecordAudio": ToolMeta(
        category="local_audio", tags=("audio", "record", "microphone", "local"),
        timeout_seconds=300.0,
    ),
    "PlayAudio": ToolMeta(
        category="local_audio", tags=("audio", "play", "local"), timeout_seconds=300.0,
    ),
    "TranscribeAudio": ToolMeta(
        category="local_audio", tags=("audio", "transcribe", "whisper", "local"),
        timeout_seconds=300.0,
    ),
    # ── File Watching ────────────────────────────────────────────────────
    "WatchDirectory": ToolMeta(category="local_file_watch", tags=("file", "watch", "local")),
    "WatchEvents": ToolMeta(category="local_file_watch", tags=("file", "watch", "events", "local")),
    "StopWatch": ToolMeta(category="local_file_watch", tags=("file", "watch", "stop", "local")),
    # ── Scheduler / Heartbeat ────────────────────────────────────────────
    "ScheduleTask": ToolMeta(category="local_scheduler", tags=("schedule", "task", "local")),
    "ListScheduled": ToolMeta(category="local_scheduler", tags=("schedule", "list", "local")),
    "CancelScheduled": ToolMeta(category="local_scheduler", tags=("schedule", "cancel", "local")),
    "HeartbeatStatus": ToolMeta(category="local_scheduler", tags=("heartbeat", "status", "local")),
    "PreventSleep": ToolMeta(category="local_scheduler", tags=("power", "sleep", "local")),
    # ── Mail (macOS) ─────────────────────────────────────────────────────
    "ListEmails": ToolMeta(
        category="local_mail", tags=("mail", "email", "macos", "local"), platforms=DARWIN,
    ),
    "SendEmail": ToolMeta(
        category="local_mail", tags=("mail", "email", "send", "macos", "local"),
        platforms=DARWIN,
    ),
    "GetEmailAccounts": ToolMeta(
        category="local_mail", tags=("mail", "accounts", "macos", "local"), platforms=DARWIN,
    ),
    # ── Messages (macOS) ─────────────────────────────────────────────────
    "ListMessages": ToolMeta(
        category="local_messages", tags=("messages", "imessage", "macos", "local"),
        platforms=DARWIN,
    ),
    "ListConversations": ToolMeta(
        category="local_messages", tags=("messages", "conversations", "macos", "local"),
        platforms=DARWIN,
    ),
    "SendMessage": ToolMeta(
        category="local_messages", tags=("messages", "imessage", "send", "macos", "local"),
        platforms=DARWIN,
    ),
    # ── Contacts (macOS) ─────────────────────────────────────────────────
    "SearchContacts": ToolMeta(
        category="local_contacts", tags=("contacts", "search", "macos", "local"),
        platforms=DARWIN,
    ),
    "GetContact": ToolMeta(
        category="local_contacts", tags=("contacts", "macos", "local"), platforms=DARWIN,
    ),
    # ── Calendar & Reminders (macOS) ─────────────────────────────────────
    "ListEvents": ToolMeta(
        category="local_calendar", tags=("calendar", "events", "macos", "local"),
        platforms=DARWIN,
    ),
    "CreateEvent": ToolMeta(
        category="local_calendar", tags=("calendar", "events", "create", "macos", "local"),
        platforms=DARWIN,
    ),
    "ListReminders": ToolMeta(
        category="local_calendar", tags=("reminders", "macos", "local"), platforms=DARWIN,
    ),
    "CreateReminder": ToolMeta(
        category="local_calendar", tags=("reminders", "create", "macos", "local"),
        platforms=DARWIN,
    ),
    # ── Photos (macOS) ───────────────────────────────────────────────────
    "SearchPhotos": ToolMeta(
        category="local_photos", tags=("photos", "search", "macos", "local"),
        platforms=DARWIN,
    ),
    "GetPhoto": ToolMeta(
        category="local_photos", tags=("photos", "macos", "local"), platforms=DARWIN,
    ),
    # ── Location (macOS) ─────────────────────────────────────────────────
    "GetLocation": ToolMeta(
        category="local_location", tags=("location", "gps", "macos", "local"),
        platforms=DARWIN,
    ),
    # ── Speech Recognition (macOS) ───────────────────────────────────────
    "TranscribeWithSpeech": ToolMeta(
        category="local_speech", tags=("speech", "transcribe", "macos", "local"),
        platforms=DARWIN, timeout_seconds=300.0,
    ),
    "ListSpeechLocales": ToolMeta(
        category="local_speech", tags=("speech", "locales", "macos", "local"),
        platforms=DARWIN,
    ),
}


# ---------------------------------------------------------------------------
# Catalog entry + build
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CatalogEntry:
    """One tool in the canonical catalog."""

    cloud_name: str
    dispatcher_name: str
    description: str
    category: str
    tags: tuple[str, ...]
    input_schema: dict[str, Any]
    arg_model: type | None
    handler: Callable[..., Any]
    timeout_seconds: float
    platforms: tuple[str, ...] | None
    output_schema: dict[str, Any] | None = None
    version: str = CATALOG_VERSION
    # W7 action collapse: mega-tools (advertised=True) are what the platform
    # sees; legacy per-tool entries stay dispatchable but unadvertised during
    # the transition and their cloud rows are retired via tool_sync.
    advertised: bool = True
    # Flat cloud dialect (per-prop `required` bools + $variants) stored in
    # tool.definition.parameters for action-enum mega-tools. None → the
    # standard input_schema IS the cloud shape (legacy tools).
    cloud_parameters: dict[str, Any] | None = None


_SNAKE_RE_1 = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SNAKE_RE_2 = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_CLOUD_NAME_RE = re.compile(r"^local_[a-z0-9_]+$")


def _snake_case(name: str) -> str:
    """PascalCase → snake_case, acronym-aware (MDNSDiscover → mdns_discover)."""
    return _SNAKE_RE_2.sub("_", _SNAKE_RE_1.sub("_", name)).lower()


def _arg_model_schema(arg_model: type) -> dict[str, Any]:
    """JSON Schema from a Pydantic arg model — the exact shape stored in the
    cloud ``tool.definition.parameters`` column (pushed by the legacy
    tool_sync and reproduced here byte-for-byte for drift comparison)."""
    schema = arg_model.model_json_schema()  # type: ignore[attr-defined]
    return {
        "type": "object",
        "properties": schema.get("properties", {}),
        "required": schema.get("required", []),
    }


def _build_catalog() -> tuple[CatalogEntry, ...]:
    # Deferred import: tool_schemas imports the dispatcher; importing it at
    # module level here is safe, but deferring keeps catalog import light and
    # mirrors dispatcher.list_tool_specs().
    from app.tools.actions import ACTION_GROUPS, build_group_schemas, group_members
    from app.tools.tool_schemas import generate_tool_schema

    unknown_meta = sorted(set(_META) - set(TOOL_HANDLERS))
    if unknown_meta:
        raise RuntimeError(
            f"catalog _META references tools missing from the dispatcher: {unknown_meta}. "
            "A dispatcher tool was renamed or removed without updating the catalog."
        )

    members = group_members()  # legacy dispatcher name → owning mega-tool
    orphans = sorted(set(TOOL_HANDLERS) - set(members) - set(ACTION_GROUPS))
    if orphans:
        raise RuntimeError(
            f"dispatcher tools not covered by any action group: {orphans}. "
            "Every legacy tool must belong to exactly one ACTION_GROUPS entry "
            "(app/tools/actions.py) — add it to a group or create one."
        )

    entries: list[CatalogEntry] = []
    seen_cloud: dict[str, str] = {}

    for dispatcher_name in sorted(TOOL_HANDLERS):
        if dispatcher_name in ACTION_GROUPS:
            continue  # mega-tools are appended below, composed from legacy entries
        handler = TOOL_HANDLERS[dispatcher_name]
        meta = _META.get(dispatcher_name, ToolMeta())

        introspected = generate_tool_schema(dispatcher_name)
        if introspected is None:  # pragma: no cover — handler exists by construction
            raise RuntimeError(f"introspection failed for dispatcher tool {dispatcher_name}")

        cloud_name = meta.cloud_name or f"local_{_snake_case(dispatcher_name)}"
        if not _CLOUD_NAME_RE.match(cloud_name):
            raise RuntimeError(
                f"generated cloud name {cloud_name!r} for {dispatcher_name} is malformed "
                "(must match ^local_[a-z0-9_]+$)"
            )
        if cloud_name in seen_cloud:
            raise RuntimeError(
                f"duplicate cloud name {cloud_name!r}: {seen_cloud[cloud_name]} "
                f"and {dispatcher_name} both map to it"
            )
        seen_cloud[cloud_name] = dispatcher_name

        if meta.arg_model is not None:
            input_schema = _arg_model_schema(meta.arg_model)
        else:
            input_schema = introspected["input_schema"]

        description = meta.description or introspected["description"]

        entries.append(
            CatalogEntry(
                cloud_name=cloud_name,
                dispatcher_name=dispatcher_name,
                description=description,
                category=meta.category,
                tags=meta.tags,
                input_schema=input_schema,
                arg_model=meta.arg_model,
                handler=handler,
                timeout_seconds=meta.timeout_seconds,
                platforms=meta.platforms,
                output_schema=meta.output_schema,
                advertised=False,  # legacy tool: collapsed into a mega-tool
            )
        )

    # ── Action-enum mega-tools (the ADVERTISED surface) ─────────────────────
    by_dispatcher = {e.dispatcher_name: e for e in entries}
    for group_name in sorted(ACTION_GROUPS):
        group = ACTION_GROUPS[group_name]
        if group.cloud_name in seen_cloud:
            raise RuntimeError(
                f"mega-tool cloud name {group.cloud_name!r} collides with "
                f"legacy tool {seen_cloud[group.cloud_name]}"
            )
        seen_cloud[group.cloud_name] = group.dispatcher_name
        composed = build_group_schemas(group, by_dispatcher.get)
        entries.append(
            CatalogEntry(
                cloud_name=group.cloud_name,
                dispatcher_name=group.dispatcher_name,
                description=composed["description"],
                category=group.category,
                tags=group.tags,
                input_schema=composed["input_schema"],
                arg_model=None,
                handler=TOOL_HANDLERS[group.dispatcher_name],
                timeout_seconds=composed["timeout_seconds"],
                platforms=composed["platforms"],
                output_schema=composed["output_schema"],
                advertised=True,
                cloud_parameters=composed["cloud_parameters"],
            )
        )

    entries.sort(key=lambda e: e.dispatcher_name)
    return tuple(entries)


_CATALOG: tuple[CatalogEntry, ...] | None = None
_BY_CLOUD: dict[str, CatalogEntry] = {}
_BY_DISPATCHER: dict[str, CatalogEntry] = {}


def get_catalog() -> tuple[CatalogEntry, ...]:
    """The full catalog, sorted by dispatcher name. Built once per process."""
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = _build_catalog()
        _BY_CLOUD.update({e.cloud_name: e for e in _CATALOG})
        _BY_DISPATCHER.update({e.dispatcher_name: e for e in _CATALOG})
    return _CATALOG


def get_advertised_catalog() -> tuple[CatalogEntry, ...]:
    """Only the advertised surface — the action-enum mega-tools. This is what
    the cloud registry (tool.definition/tool.binding) and every tool-list
    consumer should present; legacy entries remain dispatchable only."""
    return tuple(e for e in get_catalog() if e.advertised)


def get_by_cloud_name(name: str) -> CatalogEntry | None:
    get_catalog()
    return _BY_CLOUD.get(name)


def get_by_dispatcher_name(name: str) -> CatalogEntry | None:
    get_catalog()
    return _BY_DISPATCHER.get(name)


def catalog_hash() -> str:
    """16-char sha256 prefix over the structural catalog (names, schemas,
    categories, platform gating, versions). Changes whenever the advertised
    tool surface changes — a cheap freshness check for consumers."""
    canonical = json.dumps(
        [
            {
                "cloud_name": e.cloud_name,
                "dispatcher_name": e.dispatcher_name,
                "input_schema": e.input_schema,
                "category": e.category,
                "platforms": e.platforms,
                "version": e.version,
                "advertised": e.advertised,
            }
            for e in get_catalog()
        ],
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]
