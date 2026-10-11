"""Notes management tools — CRUD for local .md notes (local-first).

All operations read/write the local filesystem directly.
Supabase sync is handled separately and never called from here.

Default location: ~/Documents/Matrx/Notes/ (MATRX_NOTES_DIR in config).
"""

from __future__ import annotations

import json

from app.services.documents.file_manager import file_manager
from app.tools.session import ToolSession
from app.tools.types import ToolResult, ToolResultType


async def tool_list_documents(
    session: ToolSession,
    folder: str | None = None,
) -> ToolResult:
    """List all documents, optionally filtered by folder."""
    if folder:
        notes = file_manager.list_notes_in_folder(folder)
        if not notes:
            return ToolResult(
                output=f"No documents found in folder '{folder}'",
                metadata={"folder": folder, "documents": []},
            )
        lines = [f"Documents in '{folder}' ({len(notes)} files):"]
        for n in notes:
            lines.append(f"  {n['label']}.md  ({n['size']} bytes)")
        return ToolResult(
            output="\n".join(lines),
            metadata={"folder": folder, "documents": notes},
        )

    files = file_manager.scan_all()
    if not files:
        return ToolResult(
            output="No documents found. Documents directory is empty.",
            metadata={"documents": []},
        )

    # Group by home (a home is a directory)
    by_home: dict[str, list] = {}
    for f in files:
        home_dir = f.get("folder", "General")
        by_home.setdefault(home_dir, []).append(f)

    lines = [f"Documents ({len(files)} total):"]
    for home_dir, docs in sorted(by_home.items()):
        lines.append(f"\n  [{home_dir}] ({len(docs)} files)")
        for d in docs:
            lines.append(f"    {d['label']}.md")

    return ToolResult(
        output="\n".join(lines),
        metadata={"total": len(files), "folders": by_home},
    )


async def tool_read_document(
    session: ToolSession,
    file_path: str | None = None,
    folder: str | None = None,
    label: str | None = None,
) -> ToolResult:
    """Read a document's content by file_path or by folder+label."""
    if file_path:
        content = file_manager.read_note(file_path)
    elif folder and label:
        path = file_manager.note_path(folder, label)
        rel = file_manager.relative_path(path)
        content = file_manager.read_note(rel)
        file_path = rel
    else:
        return ToolResult(
            type=ToolResultType.ERROR,
            output="Provide either file_path or both folder and label",
        )

    if content is None:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=f"Document not found: {file_path}",
        )

    return ToolResult(
        output=content,
        metadata={
            "file_path": file_path,
            "content_hash": file_manager.note_hash(file_path or ""),
            "length": len(content),
        },
    )


async def tool_write_document(
    session: ToolSession,
    label: str,
    content: str,
    folder: str = "General",
) -> ToolResult:
    """Create or update a document in the local store."""
    file_path = file_manager.write_note(folder, label, content)
    return ToolResult(
        output=f"Document written: {file_path} ({len(content)} bytes)",
        metadata={
            "file_path": file_path,
            "label": label,
            "folder": folder,
            "content_hash": file_manager.note_hash(file_path),
        },
    )


async def tool_search_documents(
    session: ToolSession,
    query: str,
) -> ToolResult:
    """Search document contents for a query string."""
    files = file_manager.scan_all()
    matches: list[dict] = []

    for f in files:
        content = file_manager.read_note(f["file_path"])
        if content and query.lower() in content.lower():
            # Find matching lines
            matching_lines = [
                line.strip()
                for line in content.split("\n")
                if query.lower() in line.lower()
            ]
            matches.append({
                "file_path": f["file_path"],
                "label": f["label"],
                "folder": f.get("folder", "General"),
                "matching_lines": matching_lines[:5],
            })

    if not matches:
        return ToolResult(
            output=f"No documents match '{query}'",
            metadata={"query": query, "matches": []},
        )

    lines = [f"Found {len(matches)} document(s) matching '{query}':"]
    for m in matches:
        lines.append(f"\n  {m['folder']}/{m['label']}.md")
        for ml in m["matching_lines"][:3]:
            lines.append(f"    > {ml[:120]}")

    return ToolResult(
        output="\n".join(lines),
        metadata={"query": query, "matches": matches},
    )


async def tool_list_document_folders(
    session: ToolSession,
) -> ToolResult:
    """List all document folders."""
    folders = file_manager.list_folders()
    if not folders:
        return ToolResult(
            output="No document folders found.",
            metadata={"folders": []},
        )
    lines = [f"Document folders ({len(folders)}):"]
    for f in folders:
        notes = file_manager.list_notes_in_folder(f)
        lines.append(f"  {f}/ ({len(notes)} documents)")
    return ToolResult(
        output="\n".join(lines),
        metadata={"folders": folders},
    )
