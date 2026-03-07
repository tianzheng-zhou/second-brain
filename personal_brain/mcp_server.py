"""
mcp_server.py — FastMCP server exposing PersonalBrain capabilities.
Supports stdio, SSE, and Streamable HTTP transports.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from . import database as db
from .config import DELETE_CONFIRMATION, STORAGE_PATH
from .ingestion import (
    process_directory,
    process_file,
    refresh_index_for_file,
    refresh_index_global,
)
from .search import (
    search_hybrid,
    search_in_document,
    search_keyword,
    search_notes,
    search_semantic,
)
from .utils.logger import get_module_logger
from .utils.metrics import get_metrics

logger = get_module_logger(__name__)
metrics = get_metrics()

mcp = FastMCP("PersonalBrain")


def _serialize(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def _ok(data: Any) -> str:
    return json.dumps(data, default=str, ensure_ascii=False)


def _err(message: str) -> dict:
    return {"error": message}


# ---------------------------------------------------------------------------
# Search & Retrieval
# ---------------------------------------------------------------------------

@mcp.tool()
def search(
    query: str,
    limit: int = 5,
    time_range: Optional[str] = None,
    use_rerank: bool = False,
    expand_query: bool = False,
) -> str:
    """
    Hybrid search combining vector similarity and FTS5 keyword matching (RRF fusion).
    This is the recommended default search method for most queries.

    Args:
        query: Natural language search query — questions, keywords, or topic descriptions.
        limit: Max results (default 5).
        time_range: Natural language time filter on file upload time.
            Examples: '最近一周', 'last 7 days', '2024年1月到3月'.
        use_rerank: Apply LLM reranking for higher precision. Recommended when
            top-k quality matters. Adds ~1-2s latency.
        expand_query: Use LLM to rewrite the query for better recall.
            Recommended for conversational/vague queries (e.g. '那个关于XXX的文章').

    Returns: JSON array of search results with score, content, source info, and created_at.

    Best practices:
    - For precise keyword lookups → use search_keyword_tool.
    - For conceptual/fuzzy queries → use search_semantic_tool.
    - For vague/oral queries → set expand_query=True.
    - For important queries needing high precision → set use_rerank=True.
    - After finding relevant chunks → use get_adjacent_chunks to expand context.
    - After finding relevant files → use read_document with file_id for deep reading.
    """
    try:
        results = search_hybrid(query, limit, time_range, use_rerank, expand_query)
        return _ok([r.model_dump() for r in results])
    except Exception as e:
        logger.error("search failed", extra={"error": str(e)})
        return _ok(_err(str(e)))


@mcp.tool()
def search_semantic_tool(
    query: str,
    limit: int = 5,
    time_range: Optional[str] = None,
) -> str:
    """
    Pure vector semantic search — best for conceptual/fuzzy queries where exact
    keywords may not appear in the document (e.g. 'machine learning optimization techniques').
    Use the default 'search' tool if unsure which search mode to use.
    """
    try:
        results = search_semantic(query, limit, time_range)
        return _ok([r.model_dump() for r in results])
    except Exception as e:
        return _ok(_err(str(e)))


@mcp.tool()
def search_keyword_tool(
    query: str,
    limit: int = 5,
    time_range: Optional[str] = None,
) -> str:
    """
    FTS5 keyword search — best for exact term matching (e.g. function names,
    error codes, specific phrases). Chunks only, no entries.
    Use the default 'search' tool if unsure which search mode to use.
    """
    try:
        results = search_keyword(query, limit, time_range)
        return _ok([r.model_dump() for r in results])
    except Exception as e:
        return _ok(_err(str(e)))


@mcp.tool()
def search_notes_tool(
    query: str,
    limit: int = 5,
    tag: Optional[str] = None,
) -> str:
    """
    Vector search limited to notes/entries only (excludes file chunks).
    Use when searching user-written notes or auto-generated summaries.
    Optionally filter by tag.
    """
    try:
        results = search_notes(query, limit, tag)
        return _ok([r.model_dump() for r in results])
    except Exception as e:
        return _ok(_err(str(e)))


@mcp.tool()
def read_note(entry_id: str) -> str:
    """Read a note's full content by ID."""
    entry = db.get_entry(entry_id)
    if not entry:
        return _ok(_err(f"Entry {entry_id} not found"))
    return _ok(entry.model_dump())


@mcp.tool()
def read_document(file_id: str, query: Optional[str] = None) -> str:
    """
    Read file's processed text.
    With query: returns most relevant chunks (vector search within doc).
    Without query: returns full processed text.
    """
    file_obj = db.get_file(file_id)
    if not file_obj:
        return _ok(_err(f"File {file_id} not found"))

    if query:
        results = search_in_document(file_id, query)
        return _ok({
            "file_id": file_id,
            "filename": file_obj.filename,
            "mode": "search",
            "results": [r.model_dump() for r in results],
        })

    # Return full processed text
    if file_obj.processed_text_path and Path(file_obj.processed_text_path).exists():
        text = Path(file_obj.processed_text_path).read_text(encoding="utf-8")
    elif file_obj.path and Path(file_obj.path).exists():
        text = Path(file_obj.path).read_text(encoding="utf-8", errors="replace")
    else:
        return _ok(_err("Document text not available"))

    return _ok({
        "file_id": file_id,
        "filename": file_obj.filename,
        "mode": "full",
        "content": text,
    })


@mcp.tool()
def get_adjacent_chunks(chunk_id: str, window: int = 1) -> str:
    """
    Get chunks adjacent to a specific chunk for expanded context.
    Use after search to read surrounding content of a relevant chunk.

    Args:
        chunk_id: The chunk ID from a search result (format: '{file_id}_{chunk_index}').
        window: Number of chunks before and after to include (default 1).

    Returns: JSON array of chunks ordered by chunk_index, with is_current=true
        marking the requested chunk.
    """
    try:
        chunks = db.get_adjacent_chunks(chunk_id, window)
        if not chunks:
            return _ok(_err(f"Chunk {chunk_id} not found"))
        return _ok(chunks)
    except Exception as e:
        return _ok(_err(str(e)))


@mcp.tool()
def list_notes(
    tag: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> str:
    """List notes with optional tag/source filter and pagination."""
    try:
        entries, total = db.list_entries(tag=tag, source=source, limit=limit, offset=offset)
        return _ok({
            "entries": [e.model_dump() for e in entries],
            "total_count": total,
        })
    except Exception as e:
        return _ok(_err(str(e)))


@mcp.tool()
def list_files_tool(
    type: Optional[str] = None,
    status: Optional[str] = None,
    enrichment_status: Optional[str] = None,
    include_summary: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> str:
    """
    List files with optional filters and pagination.

    Args:
        type: Filter by file type ('text', 'pdf', 'image', 'audio', etc.).
        status: Filter by status ('active' or 'archived').
        enrichment_status: Filter by enrichment status ('pending', 'completed', 'failed').
        include_summary: If True, attach the auto-generated summary to each file.
            Use this to quickly scan what each file is about before deciding which
            to read in detail with read_document(file_id).
        limit: Max files to return (default 20).
        offset: Pagination offset.

    Workflow tip: When search yields no results, enumerate files with
    include_summary=True, skim summaries, then call read_document(file_id)
    on the most relevant ones.
    """
    try:
        files, total = db.list_files(
            type=type, status=status,
            enrichment_status=enrichment_status,
            limit=limit, offset=offset,
        )
        file_dicts = [f.model_dump() for f in files]

        if include_summary and files:
            summaries = db.get_file_summaries([f.id for f in files])
            for fd in file_dicts:
                fd["summary"] = summaries.get(fd["id"])

        return _ok({
            "files": file_dicts,
            "total_count": total,
        })
    except Exception as e:
        return _ok(_err(str(e)))


@mcp.tool()
def get_file_info(file_id: str) -> str:
    """Get file metadata + associated enrichment entries and tags."""
    file_obj = db.get_file(file_id)
    if not file_obj:
        return _ok(_err(f"File {file_id} not found"))

    entry_ids = db.get_file_entry(file_id)
    entries = [db.get_entry(eid) for eid in entry_ids]
    entries = [e for e in entries if e]

    result = file_obj.model_dump()
    result["entries"] = [e.model_dump() for e in entries]
    return _ok(result)


# ---------------------------------------------------------------------------
# Write & Modify
# ---------------------------------------------------------------------------

@mcp.tool()
def write_note(
    content: str,
    tags: Optional[list[str]] = None,
    file_paths: Optional[list[str]] = None,
) -> str:
    """
    Write a note. Optionally link file paths (synchronously ingested).
    Returns: {entry_id, created_at, linked_file_ids, failed_paths}
    """
    from .llm import generate_embedding
    from .models import Entry

    try:
        embedding = generate_embedding(content)
        entry = Entry(
            id=str(uuid.uuid4()),
            content_text=content,
            created_at=datetime.utcnow(),
            source="mcp",
            tags=tags or [],
            status="active",
        )
        db.save_entry(entry, embedding)

        linked_file_ids = []
        failed_paths = []

        if file_paths:
            for fp in file_paths:
                path = Path(fp)
                try:
                    result = process_file(path)
                    fid = result["file_id"]
                    db.link_entry_file(entry.id, fid)
                    linked_file_ids.append(fid)
                except Exception as e:
                    failed_paths.append({"path": fp, "error": str(e)})

        return _ok({
            "entry_id": entry.id,
            "created_at": entry.created_at.isoformat(),
            "linked_file_ids": linked_file_ids,
            "failed_paths": failed_paths,
        })
    except Exception as e:
        return _ok(_err(str(e)))


@mcp.tool()
def update_note(
    entry_id: str,
    content: Optional[str] = None,
    tags: Optional[list[str]] = None,
    status: Optional[str] = None,
) -> str:
    """Update note content, tags, or status. Regenerates embedding if content changes."""
    if not db.get_entry(entry_id):
        return _ok(_err(f"Entry {entry_id} not found"))

    try:
        embedding = None
        if content:
            from .llm import generate_embedding
            embedding = generate_embedding(content)

        db.update_entry(entry_id, content=content, tags=tags, status=status, embedding=embedding)
        return _ok({"entry_id": entry_id, "updated": True})
    except Exception as e:
        return _ok(_err(str(e)))


@mcp.tool()
def delete_note(entry_id: str, confirm: bool = False) -> str:
    """Delete a note. Requires confirm=True when DELETE_CONFIRMATION env is true."""
    if not db.get_entry(entry_id):
        return _ok(_err(f"Entry {entry_id} not found"))

    if DELETE_CONFIRMATION and not confirm:
        return _ok({
            "requires_confirmation": True,
            "message": f"Set confirm=true to delete entry {entry_id}",
        })

    try:
        db.delete_entry(entry_id)
        return _ok({"deleted": True, "entry_id": entry_id})
    except Exception as e:
        return _ok(_err(str(e)))


@mcp.tool()
def ingest_file(path: str, async_mode: bool = True) -> str:
    """
    Ingest a file or directory.
    async_mode=True (default): returns {task_id} immediately.
    async_mode=False: waits for completion and returns result.
    """
    file_path = Path(path)
    if not file_path.exists():
        return _ok(_err(f"Path not found: {path}"))

    if async_mode:
        task_id = db.create_task("ingest", path)
        db.update_task(task_id, "running")

        def _run():
            try:
                if file_path.is_dir():
                    result = process_directory(file_path)
                else:
                    result = process_file(file_path)
                db.update_task(task_id, "completed", json.dumps(result))
            except Exception as e:
                db.update_task(task_id, "failed", json.dumps({"error": str(e)}))

        threading.Thread(target=_run, daemon=True).start()
        return _ok({"task_id": task_id})
    else:
        try:
            if file_path.is_dir():
                result = process_directory(file_path)
            else:
                result = process_file(file_path)
            return _ok(result)
        except Exception as e:
            return _ok(_err(str(e)))


@mcp.tool()
def archive_file_tool(file_id: str) -> str:
    """Archive a file (exclude from search, keep data)."""
    if not db.get_file(file_id):
        return _ok(_err(f"File {file_id} not found"))
    db.archive_file(file_id)
    return _ok({"file_id": file_id, "status": "archived"})


@mcp.tool()
def restore_file_tool(file_id: str) -> str:
    """Restore an archived file (re-include in search)."""
    if not db.get_file(file_id):
        return _ok(_err(f"File {file_id} not found"))
    db.restore_file(file_id)
    return _ok({"file_id": file_id, "status": "active"})


@mcp.tool()
def delete_file_tool(file_id: str, confirm: bool = False) -> str:
    """Cascade delete a file and all associated data."""
    if not db.get_file(file_id):
        return _ok(_err(f"File {file_id} not found"))

    if DELETE_CONFIRMATION and not confirm:
        return _ok({
            "requires_confirmation": True,
            "message": f"Set confirm=true to delete file {file_id}",
        })

    try:
        db.delete_file(file_id)
        return _ok({"deleted": True, "file_id": file_id})
    except Exception as e:
        return _ok(_err(str(e)))


# ---------------------------------------------------------------------------
# System management
# ---------------------------------------------------------------------------

@mcp.tool()
def get_stats() -> str:
    """Knowledge base statistics."""
    try:
        stats = db.get_stats()
        stats["metrics"] = metrics.get_summary()
        return _ok(stats)
    except Exception as e:
        return _ok(_err(str(e)))


@mcp.tool()
def refresh_index(file_id: Optional[str] = None) -> str:
    """
    Rebuild index. With file_id: sync single file. Without: async all active files.
    """
    if file_id:
        try:
            result = refresh_index_for_file(file_id)
            return _ok(result)
        except Exception as e:
            return _ok({**_err(str(e)), "rollback": True})
    else:
        task_id = db.create_task("refresh_index")
        db.update_task(task_id, "running")
        import asyncio
        threading.Thread(
            target=lambda: asyncio.run(refresh_index_global(task_id)),
            daemon=True,
        ).start()
        return _ok({"task_id": task_id})


@mcp.tool()
def get_task_status(task_id: str) -> str:
    """Query async task status and result."""
    task = db.get_task(task_id)
    if not task:
        return _ok(_err(f"Task {task_id} not found"))
    return _ok(task.model_dump())


@mcp.tool()
def health_check() -> str:
    """System health check across all components."""
    from .config import DASHSCOPE_API_KEY

    components: dict[str, Any] = {}

    # Database
    try:
        stats = db.get_stats()
        db_size_mb = stats.get("db_size_mb", 0)
        components["database"] = {"status": "ok", "size_mb": db_size_mb}
    except Exception as e:
        components["database"] = {"status": "error", "error": str(e)}

    # Vec index
    try:
        from . import config_manager
        vec_impl = config_manager.get("vec_impl")
        vec_count = stats.get("total_vectors", 0)
        components["vec_index"] = {"status": "ok", "vec_impl": vec_impl, "count": vec_count}
    except Exception as e:
        components["vec_index"] = {"status": "error", "error": str(e)}

    # Storage
    try:
        if STORAGE_PATH.exists():
            import shutil
            total, used, free = shutil.disk_usage(STORAGE_PATH)
            free_gb = free / (1024 ** 3)
            status = "degraded" if free_gb < 5 else "ok"
            components["storage"] = {
                "status": status,
                "path": str(STORAGE_PATH),
                "free_gb": round(free_gb, 2),
            }
        else:
            components["storage"] = {"status": "error", "error": "Storage path does not exist"}
    except Exception as e:
        components["storage"] = {"status": "error", "error": str(e)}

    # DashScope API
    if DASHSCOPE_API_KEY:
        components["dashscope_api"] = {"status": "ok"}
    else:
        components["dashscope_api"] = {"status": "error", "error": "DASHSCOPE_API_KEY not set"}

    # Aggregate status
    statuses = [v.get("status", "error") for v in components.values()]
    if "error" in statuses:
        overall = "error"
    elif "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "ok"

    m = metrics.get_summary()
    return _ok({
        "status": overall,
        "components": components,
        "metrics": m,
    })


def run_server(transport: str = "stdio", host: str = "0.0.0.0", port: int = 8765) -> None:
    """Start the MCP server with the specified transport."""
    db.init_db()
    logger.info("Starting MCP server", extra={"transport": transport, "port": port})

    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport == "sse":
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.run(transport="sse")
    elif transport in ("streamhttp", "stream-http", "http"):
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.run(transport="streamable-http")
    else:
        raise ValueError(f"Unknown transport: {transport}")
