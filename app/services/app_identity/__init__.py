"""The single identity authority for naming a running macOS app.

Every desktop tool that takes an app name from a person or an agent resolves
it here and then addresses the app by bundle id (AppleScript) and pid
(System Events, CoreGraphics). See :mod:`app.services.app_identity.identity`.
"""

from app.services.app_identity.identity import (
    AppNotRunning,
    ReaderApp,
    RunningApp,
    close_running_names,
    list_running_apps,
    match_reader_app,
    match_running_app,
    not_running_message,
    parse_process_table,
    require_running_app,
    resolve_reader_app,
    resolve_running_app,
)

__all__ = [
    "AppNotRunning",
    "ReaderApp",
    "RunningApp",
    "close_running_names",
    "list_running_apps",
    "match_reader_app",
    "match_running_app",
    "not_running_message",
    "parse_process_table",
    "require_running_app",
    "resolve_reader_app",
    "resolve_running_app",
]
