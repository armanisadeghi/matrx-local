"""Download error types.

Lives apart from manager.py so app/services/downloads/failures.py can build on
NonRetryableDownloadError without importing the manager (which imports failures).
"""

from __future__ import annotations


class NonRetryableDownloadError(RuntimeError):
    """A download failure that retrying can never fix (e.g. Civitai 401/403
    without a valid API key). The retry loop surfaces it immediately with its
    message intact instead of burning attempts."""
