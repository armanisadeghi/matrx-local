"""The matrx-files REST client (files.matrxserver.com) with the user's JWT.

Shared by every lane that writes a file to the person's AI Matrx Files on an
explicit request (artifacts, book capture, coding-session artifacts). There is
no background file mirror any more — see app/services/file_sync/FEATURE.md.
"""

from app.services.matrx_files.client import MatrxFilesClient, MatrxFilesHTTPError

__all__ = ["MatrxFilesClient", "MatrxFilesHTTPError"]
