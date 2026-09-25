"""The retired desktop file mirror — only its one-time cleanup remains.

The old mirror copied the person's whole cloud Files tree into the Files
folder as zero-byte placeholders. It is gone (Arman, 2026-09-24): the new
folder sync syncs only folders the person picks. What remains here removes
the placeholders it left behind. See FEATURE.md.
"""

from app.services.file_sync.retirement import retire_file_mirror

__all__ = ["retire_file_mirror"]
