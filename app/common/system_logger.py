import logging
import os
import sys
import time
import traceback
import re
from concurrent_log_handler import ConcurrentRotatingFileHandler
from app.config import LOG_LEVEL, LOG_DIR, MAX_LOG_FILE_SIZE, BACKUP_COUNT, LOCAL_DEV

# ── Windows: ensure stdout/stderr are UTF-8 before the first handler is created.
# run.py reconfigures the streams before importing this module, but if something
# imports system_logger directly (tests, scripts) without going through run.py,
# we reconfigure here as a safety net.  errors='replace' means unencodable
# characters become '?' instead of raising UnicodeEncodeError.
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure the log directory exists
os.makedirs(LOG_DIR, exist_ok=True)

# ANSI color codes — only used on the console, never in file logs.
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_CYAN   = "\033[36m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_BRIGHT_RED = "\033[91m"

_LEVEL_COLORS = {
    logging.DEBUG:    _CYAN,
    logging.INFO:     _GREEN,
    logging.WARNING:  _YELLOW,
    logging.ERROR:    _RED,
    logging.CRITICAL: _BRIGHT_RED + _BOLD,
}


class ColorFormatter(logging.Formatter):
    """Console formatter that colorizes the level name."""

    def __init__(self, fmt: str):
        super().__init__()
        self._fmt = fmt

    def format(self, record: logging.LogRecord) -> str:
        color = _LEVEL_COLORS.get(record.levelno, "")
        levelname = f"{color}{record.levelname}{_RESET}" if color else record.levelname
        # Build the message manually so we can slot in the colored level name.
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        return self._fmt.replace("%(levelname)s", levelname).replace("%(message)s", msg)


class SensitiveDataFilter(logging.Filter):
    """Mask tokens in log messages."""

    def filter(self, record):
        # Sanitize the message template
        if isinstance(record.msg, str):
            record.msg = self._sanitize(record.msg)

        # Sanitize any arguments passed to the log call
        if record.args:
            new_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    new_args.append(self._sanitize(arg))
                else:
                    new_args.append(arg)
            record.args = tuple(new_args)

        return True

    def _sanitize(self, text: str) -> str:
        """Apply multiple regex masks to a string."""
        # Mask query param: token=...
        text = re.sub(
            r"([?&]token=)([^& \t\n\r\f\v\"]+)",
            lambda m: m.group(1) + self._truncate(m.group(2)),
            text,
        )
        # Mask Bearer tokens: Bearer eyJ...
        text = re.sub(
            r"([Bb]earer\s+)([A-Za-z0-9._\-\/]+)",
            lambda m: (
                m.group(1) + self._truncate(m.group(2))
                if len(m.group(2)) > 30
                else m.group(0)
            ),
            text,
        )
        return text

    def _truncate(self, val: str) -> str:
        if len(val) < 40:
            return val
        return f"{val[:10]}...{val[-10:]}"


class ConsoleDedup(logging.Filter):
    """Suppress duplicate INFO/DEBUG log messages within a time window.

    Only applies to the console handler (stdout → UI). The file handler
    receives every message unfiltered so system.log remains a complete record.

    WARNING and above are NEVER suppressed — errors always show.

    When a message is suppressed, a single summary line is emitted after the
    window expires: "... repeated N times (suppressed)".
    """

    def __init__(self, window_secs: float = 10.0) -> None:
        super().__init__()
        self._window = window_secs
        self._seen: dict[str, tuple[float, int]] = {}
        self._last_flush = time.monotonic()

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True

        now = time.monotonic()

        if now - self._last_flush > self._window * 2:
            cutoff = now - self._window
            self._seen = {k: v for k, v in self._seen.items() if v[0] > cutoff}
            self._last_flush = now

        key = record.getMessage()
        entry = self._seen.get(key)

        if entry is None:
            self._seen[key] = (now, 0)
            return True

        first_seen, count = entry
        if now - first_seen > self._window:
            self._seen[key] = (now, 0)
            return True

        self._seen[key] = (first_seen, count + 1)
        return False


class _RootLogBridge(logging.Handler):
    """Forwards WARNING+ records from the root logger to the system_logger file+console.

    This ensures that any module using logging.getLogger(__name__) — third-party
    libraries, tool modules, uvicorn internals — has its warnings and errors
    captured in system.log and visible in the Activity tab, not silently dropped.

    We only bridge WARNING+ (not DEBUG/INFO) to avoid flooding system.log with
    high-volume informational output from libraries that log everything at INFO.
    """

    def __init__(self, target_logger: logging.Logger) -> None:
        super().__init__(level=logging.WARNING)
        self._target = target_logger
        self.addFilter(SensitiveDataFilter())

    def emit(self, record: logging.LogRecord) -> None:
        # Avoid re-entrancy: records already originating from system_logger skip the bridge.
        if record.name == "system_logger":
            return
        # Re-emit at the same level so WARNING stays WARNING, ERROR stays ERROR, etc.
        # Prefix with the original logger name so the source is identifiable in system.log.
        try:
            # Expand any %-style args into the message now, before we forward it.
            # We build a plain string message so system_logger receives a ready-to-log
            # string with no leftover args (avoids double-expansion).
            expanded = record.getMessage()
            prefix = f"[{record.name}] "
            # Forward exc_info so tracebacks appear in system.log for bridged records.
            self._target.log(record.levelno, prefix + expanded, exc_info=record.exc_info or None)
        except Exception:
            self.handleError(record)


class SystemLogger:
    def __init__(self):
        self.logger = logging.getLogger("system_logger")
        self.console_handler = None
        self.configure_logging()

    def configure_logging(self):
        # Re-entry guard: a second configure_logging() (or SystemLogger())
        # would attach duplicate console/file handlers and double every line.
        if self.logger.handlers:
            for h in list(self.logger.handlers):
                self.logger.removeHandler(h)

        # Set the logging level dynamically
        level = getattr(logging, LOG_LEVEL.upper(), logging.DEBUG)
        self.logger.setLevel(level)

        # Add filtering for sensitive data
        sensitive_filter = SensitiveDataFilter()
        self.logger.addFilter(sensitive_filter)

        # Prevent log records from bubbling up to the root logger (avoids duplicate lines)
        self.logger.propagate = False

        # Apply sensitive filter to uvicorn loggers (format is set via log_config in run.py)
        for u_logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error"]:
            logging.getLogger(u_logger_name).addFilter(sensitive_filter)

        # Console Handler — colored output, no timestamp in LOCAL_DEV mode
        self.console_handler = logging.StreamHandler(sys.stdout)
        self.console_handler.setLevel(level)
        console_fmt = (
            "%(levelname)s - %(message)s"
            if LOCAL_DEV
            else "%(asctime)s - %(levelname)s - %(message)s"
        )
        self.console_handler.setFormatter(ColorFormatter(console_fmt))
        self.console_handler.addFilter(ConsoleDedup(window_secs=10.0))
        self.logger.addHandler(self.console_handler)

        # File Handler with Concurrent Rotation — always full timestamp, no logger name
        file_handler = ConcurrentRotatingFileHandler(
            os.path.join(LOG_DIR, "system.log"),
            maxBytes=MAX_LOG_FILE_SIZE,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        self.logger.addHandler(file_handler)

        # Bridge: forward WARNING+ from the root logger (and all child loggers that
        # propagate) into system_logger so third-party and tool module warnings/errors
        # are captured in system.log and reach the Activity tab.
        root_logger = logging.getLogger()
        # Only attach once — guard against re-entrancy if configure_logging is called again.
        bridge_exists = any(isinstance(h, _RootLogBridge) for h in root_logger.handlers)
        if not bridge_exists:
            bridge = _RootLogBridge(self.logger)
            root_logger.addHandler(bridge)
            # Ensure the root logger is at least at WARNING so it passes records to the bridge.
            if root_logger.level == logging.NOTSET or root_logger.level > logging.WARNING:
                root_logger.setLevel(logging.WARNING)

    def disable_console_logging(self):
        if self.console_handler and self.console_handler in self.logger.handlers:
            self.logger.removeHandler(self.console_handler)

    def enable_console_logging(self):
        if self.console_handler and self.console_handler not in self.logger.handlers:
            self.logger.addHandler(self.console_handler)

    def _log(self, level, message, *args, **kwargs):
        extra = kwargs.pop("extra", {})
        exc_info = kwargs.pop("exc_info", None)

        if exc_info:
            extra["traceback"] = traceback.format_exc()

        try:
            self.logger.log(
                level, message, *args, extra=extra, exc_info=exc_info, **kwargs
            )
        except Exception as e:
            # Use ascii-safe fallback so this itself never raises UnicodeEncodeError
            try:
                safe = str(e).encode("ascii", errors="replace").decode("ascii")
                print(f"Logging error: {safe}", file=sys.stderr)
            except Exception:
                pass  # Absolute last resort — swallow to avoid infinite recursion

    def debug(self, message, *args, **kwargs):
        self._log(logging.DEBUG, message, *args, **kwargs)

    def info(self, message, *args, **kwargs):
        self._log(logging.INFO, message, *args, **kwargs)

    def warning(self, message, *args, **kwargs):
        self._log(logging.WARNING, message, *args, **kwargs)

    def error(self, message, *args, **kwargs):
        self._log(logging.ERROR, message, *args, **kwargs)

    def critical(self, message, *args, **kwargs):
        self._log(logging.CRITICAL, message, *args, **kwargs)

    def exception(self, message, *args, **kwargs):
        """ERROR with the current exception's traceback (stdlib-logger parity)."""
        kwargs.setdefault("exc_info", True)
        self._log(logging.ERROR, message, *args, **kwargs)


# Create a global instance of the logger
system_logger = SystemLogger()


# Function to get the logger instance
def get_logger():
    return system_logger
