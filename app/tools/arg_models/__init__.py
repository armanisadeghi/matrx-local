"""Pydantic arg models for all matrx-local tools.

Each model:
- Validates arguments passed by the AI before the tool handler is called.
- Automatically generates the JSON Schema that is stored in the tools DB
  (via .model_json_schema()) so schemas are always in sync with the code.
"""

from app.tools.arg_models.execution_args import (
    BashArgs,
    BashOutputArgs,
    TaskStopArgs,
)
from app.tools.arg_models.file_ops_args import (
    ReadArgs,
    WriteArgs,
    EditArgs,
    GlobArgs,
    GrepArgs,
    ListDirectoryArgs,
)
from app.tools.arg_models.book_args import BookCaptureArgs
from app.tools.arg_models.system_args import (
    SystemInfoArgs,
    ScreenshotArgs,
    OpenUrlArgs,
    OpenPathArgs,
    SystemResourcesArgs,
    BatteryStatusArgs,
    DiskUsageArgs,
    TopProcessesArgs,
    ClipboardReadArgs,
    ClipboardWriteArgs,
    NotifyArgs,
)
from app.tools.arg_models.process_args import (
    ListProcessesArgs,
    ListPortsArgs,
    LaunchAppArgs,
    KillProcessArgs,
    FocusAppArgs,
)
from app.tools.arg_models.browser_args import (
    BrowserNavigateArgs,
    BrowserClickArgs,
    BrowserTypeArgs,
    BrowserExtractArgs,
    BrowserScreenshotArgs,
    BrowserEvalArgs,
    BrowserTabsArgs,
    FetchUrlArgs,
    FetchWithBrowserArgs,
    ScrapeArgs,
    SearchArgs,
    ResearchArgs,
)
from app.tools.arg_models.network_args import (
    NetworkInfoArgs,
    NetworkScanArgs,
    PortScanArgs,
    MdnsDiscoverArgs,
)
from app.tools.arg_models.input_args import (
    TypeTextArgs,
    HotkeyArgs,
    MouseClickArgs,
    MouseMoveArgs,
    ListWindowsArgs,
    FocusWindowArgs,
    MoveWindowArgs,
    MinimizeWindowArgs,
)
from app.tools.arg_models.media_args import (
    ImageOcrArgs,
    ImageResizeArgs,
    PdfExtractArgs,
    ArchiveCreateArgs,
    ArchiveExtractArgs,
)
from app.tools.arg_models.app_args import (
    AppleScriptArgs,
    PowerShellScriptArgs,
    GetInstalledAppsArgs,
    ListDocumentsArgs,
    ReadDocumentArgs,
    WriteDocumentArgs,
    SearchDocumentsArgs,
    ListDocumentFoldersArgs,
)

__all__ = [
    "BookCaptureArgs",
    # execution
    "BashArgs", "BashOutputArgs", "TaskStopArgs",
    # file ops
    "ReadArgs", "WriteArgs", "EditArgs", "GlobArgs", "GrepArgs", "ListDirectoryArgs",
    # system
    "SystemInfoArgs", "ScreenshotArgs", "OpenUrlArgs", "OpenPathArgs",
    "SystemResourcesArgs", "BatteryStatusArgs", "DiskUsageArgs", "TopProcessesArgs",
    "ClipboardReadArgs", "ClipboardWriteArgs", "NotifyArgs",
    # process
    "ListProcessesArgs", "ListPortsArgs", "LaunchAppArgs", "KillProcessArgs", "FocusAppArgs",
    # browser / network tools
    "BrowserNavigateArgs", "BrowserClickArgs", "BrowserTypeArgs", "BrowserExtractArgs",
    "BrowserScreenshotArgs", "BrowserEvalArgs", "BrowserTabsArgs",
    "FetchUrlArgs", "FetchWithBrowserArgs", "ScrapeArgs", "SearchArgs", "ResearchArgs",
    # network discovery
    "NetworkInfoArgs", "NetworkScanArgs", "PortScanArgs", "MdnsDiscoverArgs",
    # input / window
    "TypeTextArgs", "HotkeyArgs", "MouseClickArgs", "MouseMoveArgs",
    "ListWindowsArgs", "FocusWindowArgs", "MoveWindowArgs", "MinimizeWindowArgs",
    # media
    "ImageOcrArgs", "ImageResizeArgs", "PdfExtractArgs", "ArchiveCreateArgs", "ArchiveExtractArgs",
    # app integration / documents
    "AppleScriptArgs", "PowerShellScriptArgs", "GetInstalledAppsArgs",
    "ListDocumentsArgs", "ReadDocumentArgs", "WriteDocumentArgs",
    "SearchDocumentsArgs", "ListDocumentFoldersArgs",
]
