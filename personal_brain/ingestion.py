"""
ingestion.py — File ingestion pipeline.
Handles single file processing, directory batch ingestion, and index refresh.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config_manager
from . import database as db
from .config import STORAGE_PATH
from .enrichment import enrich_file
from .indexer import (
    embed_chunks,
    extract_text,
    generate_embedding_chunks,
    save_processed_text,
)
from .models import FileChunk, FileInfo
from .utils.file_ops import (
    SUPPORTED_EXTENSIONS,
    calculate_file_id,
    detect_file_type,
    organize_file,
)
from .utils.logger import get_module_logger
from .utils.metrics import get_metrics

logger = get_module_logger(__name__)
metrics = get_metrics()

_SKIP_DIRS = frozenset({"__pycache__", "node_modules", ".git", ".venv", "venv", ".env"})


def process_file(file_path: Path) -> dict:
    """
    Full file ingestion pipeline.
    Returns dict with result info (file_id, status, message).
    """
    start = datetime.utcnow()

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    # Step 1: Compute file_id
    file_id = calculate_file_id(file_path)

    # Step 2: Dedup check
    existing = db.get_file(file_id)
    if existing:
        if existing.status == "active":
            logger.info("File already active, skipping", extra={"file_id": file_id})
            metrics.increment("ingest_count", "skip")
            return {"file_id": file_id, "status": "skip", "message": "Already exists (active)"}
        elif existing.status == "archived":
            db.restore_file(file_id)
            logger.info("File re-activated from archived", extra={"file_id": file_id})
            metrics.increment("ingest_count", "skip")
            return {"file_id": file_id, "status": "restored", "message": "Re-activated from archived"}

    # Step 3: Organize file (copy to STORAGE_PATH)
    dest_path = organize_file(file_path, STORAGE_PATH)

    # Step 4: Detect file type
    file_type = detect_file_type(file_path)

    # Step 5: Extract text
    try:
        text, image_root = extract_text(dest_path, file_type)
    except Exception as e:
        logger.error("Text extraction failed", extra={"file_id": file_id, "error": str(e)})
        # File copied but no DB record — orphan; don't clean up automatically
        raise

    # Save processed text if needed
    processed_text_path: Optional[str] = None
    if file_type in ("pdf", "image", "audio") and text:
        processed_path = save_processed_text(text, file_id)
        processed_text_path = str(processed_path)

    # Step 6: Atomically save file + chunks
    now = datetime.utcnow()
    file_info = FileInfo(
        id=file_id,
        path=str(dest_path),
        processed_text_path=processed_text_path,
        filename=file_path.name,
        type=file_type,
        size_bytes=dest_path.stat().st_size,
        created_at=now,
        last_accessed=now,
        status="active",
        enrichment_status="pending",
    )

    chunks: list[FileChunk] = []
    embeddings: list[list[float]] = []

    conn = db._get_conn()
    try:
        with conn:
            db.save_file(file_info)

            if text:
                chunks = generate_embedding_chunks(text, file_id, file_type, image_root)
                embeddings = embed_chunks(chunks)
                db.save_chunks(chunks, embeddings)
    except Exception as e:
        logger.error("DB save failed, rolling back", extra={"file_id": file_id, "error": str(e)})
        raise

    # Step 7: Enrich (non-transactional; failure doesn't block ingest)
    try:
        enrich_file(file_info, text, chunks)
    except Exception as e:
        logger.warning("Enrichment failed (non-fatal)", extra={"file_id": file_id, "error": str(e)})

    elapsed_ms = (datetime.utcnow() - start).total_seconds() * 1000
    metrics.increment("ingest_count", "success")
    logger.info(
        "Ingest completed",
        extra={
            "file_id": file_id,
            "file_name": file_path.name,
            "duration_ms": round(elapsed_ms),
            "chunks": len(chunks),
        },
    )
    return {
        "file_id": file_id,
        "status": "success",
        "filename": file_path.name,
        "chunks": len(chunks),
        "duration_ms": round(elapsed_ms),
    }


def process_directory(dir_path: Path) -> dict:
    """
    Recursively ingest all supported files in a directory.
    Skips hidden files/dirs and common temp dirs.
    """
    if not dir_path.is_dir():
        raise NotADirectoryError(f"Not a directory: {dir_path}")

    successes = 0
    skips = 0
    failures = []

    for file_path in dir_path.rglob("*"):
        if not file_path.is_file():
            continue
        # Skip hidden files
        if any(part.startswith(".") for part in file_path.parts):
            continue
        # Skip common temp dirs
        if any(part in _SKIP_DIRS for part in file_path.parts):
            continue
        # Skip unsupported extensions
        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        try:
            result = process_file(file_path)
            if result["status"] in ("skip", "restored"):
                skips += 1
            else:
                successes += 1
        except Exception as e:
            failures.append({"path": str(file_path), "error": str(e)})
            logger.error("Ingest failed", extra={"path": str(file_path), "error": str(e)})

    return {
        "success": successes,
        "skip": skips,
        "fail": len(failures),
        "failures": failures,
    }


def refresh_index_for_file(file_id: str) -> dict:
    """
    Rebuild chunks, embeddings, and enrichment for a single file.
    Fully transactional: failure rolls back to original state.
    """
    file_obj = db.get_file(file_id)
    if not file_obj:
        raise FileNotFoundError(f"File {file_id} not found")

    file_path = Path(file_obj.path)
    file_type = file_obj.type

    processed_dir = STORAGE_PATH / "processed"
    tmp_path = processed_dir / f"{file_id}.md.tmp"
    processed_dir.mkdir(parents=True, exist_ok=True)

    # Step 2: Re-extract text to tmp path
    try:
        text, image_root = extract_text(file_path, file_type)
        if text and file_type in ("pdf", "image", "audio"):
            tmp_path.write_text(text, encoding="utf-8")
    except Exception as e:
        if tmp_path.exists():
            tmp_path.unlink()
        raise RuntimeError(f"Text extraction failed during refresh: {e}") from e

    conn = db._get_conn()
    try:
        conn.execute(f"SAVEPOINT refresh_{file_id}")

        # Delete old chunks (cascades vectors + fts)
        db.delete_chunks_for_file(file_id)

        # Delete old auto_enrichment entries only linked to this file
        for entry_id in db.get_file_entry(file_id):
            entry = db.get_entry(entry_id)
            if entry and entry.source == "auto_enrichment":
                linked_files = db.get_entry_files(entry_id)
                if len(linked_files) <= 1:
                    db.delete_entry(entry_id)

        # Rebuild chunks
        chunks: list[FileChunk] = []
        embeddings: list[list[float]] = []
        if text:
            chunks = generate_embedding_chunks(text, file_id, file_type, image_root)
            embeddings = embed_chunks(chunks)
            db.save_chunks(chunks, embeddings)

        # Re-enrich
        try:
            enrich_file(file_obj, text, chunks)
        except Exception as e:
            db.update_file_enrichment_status(file_id, "failed")
            logger.warning("Enrichment failed during refresh", extra={"file_id": file_id, "error": str(e)})

        conn.execute(f"RELEASE SAVEPOINT refresh_{file_id}")

    except Exception as e:
        conn.execute(f"ROLLBACK TO SAVEPOINT refresh_{file_id}")
        if tmp_path.exists():
            tmp_path.unlink()
        logger.error("Refresh rollback", extra={"file_id": file_id, "error": str(e)})
        raise RuntimeError(f"refresh_index failed, rolled back: {e}") from e

    # Atomic rename
    final_path = processed_dir / f"{file_id}.md"
    if tmp_path.exists():
        try:
            tmp_path.replace(final_path)
            db.update_file_processed_path(file_id, str(final_path))
        except Exception as e:
            logger.warning("Rename failed after refresh", extra={"error": str(e)})

    return {"file_id": file_id, "status": "success", "chunks": len(chunks)}


async def refresh_index_global(task_id: str) -> None:
    """Async: rebuild index for all active files. Updates task record."""
    import asyncio

    db.update_task(task_id, "running")
    successes = 0
    failures = []

    files, _ = db.list_files(status="active", limit=10000)
    for file_obj in files:
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, refresh_index_for_file, file_obj.id
            )
            successes += 1
        except Exception as e:
            failures.append({"file_id": file_obj.id, "error": str(e)})
            logger.error("Global refresh single file failed", extra={"file_id": file_obj.id, "error": str(e)})

    result = json.dumps({"success": successes, "fail": len(failures), "failures": failures})
    db.update_task(task_id, "completed", result)
    logger.info("Global refresh complete", extra={"success": successes, "fail": len(failures)})
