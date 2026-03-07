"""
database.py — Single source of truth for all DB reads and writes.
Uses SQLite + sqlite-vec extension. Module-level singleton connection.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config_manager
from .config import STORAGE_PATH, PB_DB_PATH
from .models import Entry, FileChunk, FileInfo, SearchResult, TaskInfo
from .utils.logger import get_module_logger

logger = get_module_logger(__name__)

_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _conn


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Initialize database: load sqlite-vec, create tables, detect vec_impl."""
    global _conn
    PB_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    _conn = sqlite3.connect(
        str(PB_DB_PATH),
        check_same_thread=False,
        timeout=10.0,
    )
    _conn.row_factory = sqlite3.Row

    # Load sqlite-vec extension
    try:
        _conn.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(_conn)
        _conn.enable_load_extension(False)
    except Exception as e:
        logger.warning("sqlite-vec load failed, vector search disabled", extra={"error": str(e)})

    # WAL mode
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA busy_timeout=10000")
    _conn.execute("PRAGMA foreign_keys=ON")

    # Detect vec_impl
    vec_impl = _detect_vec_impl()
    config_manager.set("vec_impl", vec_impl)

    # Determine embedding dim
    model = config_manager.get("embedding_model")
    dim = config_manager.get_embedding_dim_for_model(model)
    config_manager.set("embedding_dim", dim)

    _create_tables(vec_impl, dim)
    logger.info("Database initialized", extra={"vec_impl": vec_impl, "embedding_dim": dim})


def _detect_vec_impl() -> str:
    conn = _get_conn()
    try:
        conn.execute("CREATE VIRTUAL TABLE _vec_test USING vec0(x float[1], y TEXT)")
        conn.execute("DROP TABLE _vec_test")
        return "aux_column"
    except Exception:
        return "metadata_table"


def _create_tables(vec_impl: str, dim: int) -> None:
    conn = _get_conn()
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS files (
            id                  TEXT PRIMARY KEY,
            path                TEXT UNIQUE,
            processed_text_path TEXT,
            filename            TEXT,
            type                TEXT,
            size_bytes          INTEGER,
            created_at          TIMESTAMP,
            last_accessed       TIMESTAMP,
            status              TEXT DEFAULT 'active',
            enrichment_status   TEXT DEFAULT 'pending'
        );

        CREATE TABLE IF NOT EXISTS file_chunks (
            id          TEXT PRIMARY KEY,
            file_id     TEXT,
            chunk_index INTEGER,
            content     TEXT,
            start_char  INTEGER DEFAULT 0,
            page_number INTEGER
        );

        CREATE TABLE IF NOT EXISTS entries (
            id           TEXT PRIMARY KEY,
            content_text TEXT,
            metadata     TEXT,
            created_at   TIMESTAMP,
            source       TEXT,
            tags         TEXT,
            status       TEXT DEFAULT 'active'
        );

        CREATE TABLE IF NOT EXISTS entry_files (
            entry_id TEXT,
            file_id  TEXT,
            PRIMARY KEY (entry_id, file_id)
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id          TEXT PRIMARY KEY,
            type        TEXT,
            status      TEXT,
            file_path   TEXT,
            result_json TEXT,
            created_at  TIMESTAMP,
            updated_at  TIMESTAMP
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks USING fts5(
            chunk_id,
            content,
            tokenize='unicode61'
        );
    """)

    # vec_items
    if vec_impl == "aux_column":
        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_items USING vec0(
                embedding float[{dim}],
                source_type TEXT,
                source_id   TEXT
            )
        """)
    else:
        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_items USING vec0(
                embedding float[{dim}]
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vec_metadata (
                rowid       INTEGER PRIMARY KEY REFERENCES vec_items(rowid),
                source_type TEXT NOT NULL,
                source_id   TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_vec_metadata_source
            ON vec_metadata(source_type, source_id)
        """)

    conn.commit()


def reset_db() -> None:
    """Drop and recreate the database."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None
    if PB_DB_PATH.exists():
        PB_DB_PATH.unlink()
    init_db()
    logger.info("Database reset complete")


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------

def save_file(file_info: FileInfo) -> None:
    conn = _get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO files
            (id, path, processed_text_path, filename, type, size_bytes,
             created_at, last_accessed, status, enrichment_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        file_info.id,
        file_info.path,
        file_info.processed_text_path,
        file_info.filename,
        file_info.type,
        file_info.size_bytes,
        file_info.created_at.isoformat(),
        file_info.last_accessed.isoformat() if file_info.last_accessed else None,
        file_info.status,
        file_info.enrichment_status,
    ))
    conn.commit()


def get_file(file_id: str) -> FileInfo | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    return _row_to_fileinfo(row) if row else None


def list_files(
    type: str | None = None,
    status: str | None = None,
    enrichment_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[FileInfo], int]:
    conn = _get_conn()
    conditions = []
    params: list = []
    if type:
        conditions.append("type = ?")
        params.append(type)
    if status:
        conditions.append("status = ?")
        params.append(status)
    if enrichment_status:
        conditions.append("enrichment_status = ?")
        params.append(enrichment_status)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    total = conn.execute(f"SELECT COUNT(*) FROM files {where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM files {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return [_row_to_fileinfo(r) for r in rows], total


def delete_file(file_id: str) -> None:
    """Cascade delete: chunks → vectors → fts → entry_files → orphan entries → file row → filesystem."""
    conn = _get_conn()
    file_row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    if not file_row:
        raise FileNotFoundError(f"File {file_id} not found")

    vec_impl = config_manager.get("vec_impl")

    with conn:
        # Get chunk ids for vec/fts cleanup
        chunk_ids = [
            r[0] for r in conn.execute(
                "SELECT id FROM file_chunks WHERE file_id = ?", (file_id,)
            ).fetchall()
        ]

        # Delete chunks
        conn.execute("DELETE FROM file_chunks WHERE file_id = ?", (file_id,))

        # Delete chunk vectors
        for cid in chunk_ids:
            _delete_vec_by_source(conn, vec_impl, "chunk", cid)

        # Delete FTS
        for cid in chunk_ids:
            conn.execute("DELETE FROM fts_chunks WHERE chunk_id = ?", (cid,))

        # Find orphan auto_enrichment entries
        orphan_entry_ids = [
            r[0] for r in conn.execute("""
                SELECT e.id FROM entries e
                JOIN entry_files ef ON e.id = ef.entry_id
                WHERE ef.file_id = ? AND e.source = 'auto_enrichment'
                AND NOT EXISTS (
                    SELECT 1 FROM entry_files ef2
                    WHERE ef2.entry_id = e.id AND ef2.file_id != ?
                )
            """, (file_id, file_id)).fetchall()
        ]

        # Delete entry_files for this file
        conn.execute("DELETE FROM entry_files WHERE file_id = ?", (file_id,))

        # Delete orphan entries and their vectors
        for eid in orphan_entry_ids:
            _delete_vec_by_source(conn, vec_impl, "entry", eid)
            conn.execute("DELETE FROM entries WHERE id = ?", (eid,))

        # Delete files row
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))

    # Delete filesystem files
    file_path = file_row["path"]
    if file_path and Path(file_path).exists():
        try:
            Path(file_path).unlink()
        except Exception as e:
            logger.warning("Failed to delete file from filesystem", extra={"path": file_path, "error": str(e)})

    processed_path = file_row["processed_text_path"]
    if processed_path and Path(processed_path).exists():
        try:
            Path(processed_path).unlink()
        except Exception as e:
            logger.warning("Failed to delete processed file", extra={"path": processed_path, "error": str(e)})


def archive_file(file_id: str) -> None:
    conn = _get_conn()
    conn.execute("UPDATE files SET status = 'archived' WHERE id = ?", (file_id,))
    conn.commit()


def restore_file(file_id: str) -> None:
    conn = _get_conn()
    conn.execute("UPDATE files SET status = 'active' WHERE id = ?", (file_id,))
    conn.commit()


def update_file_enrichment_status(file_id: str, status: str) -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE files SET enrichment_status = ? WHERE id = ?",
        (status, file_id),
    )
    conn.commit()


def update_file_processed_path(file_id: str, processed_path: str) -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE files SET processed_text_path = ? WHERE id = ?",
        (processed_path, file_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Chunk operations
# ---------------------------------------------------------------------------

def save_chunks(chunks: list[FileChunk], embeddings: list[list[float]]) -> None:
    """Batch write chunks to file_chunks + vec_items + fts_chunks (in one transaction)."""
    conn = _get_conn()
    vec_impl = config_manager.get("vec_impl")

    with conn:
        for chunk, emb in zip(chunks, embeddings):
            conn.execute("""
                INSERT OR REPLACE INTO file_chunks
                    (id, file_id, chunk_index, content, start_char, page_number)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                chunk.id,
                chunk.file_id,
                chunk.chunk_index,
                chunk.content,
                chunk.start_char,
                chunk.page_number,
            ))

            _insert_vec(conn, vec_impl, emb, "chunk", chunk.id)

            conn.execute(
                "INSERT OR REPLACE INTO fts_chunks (chunk_id, content) VALUES (?, ?)",
                (chunk.id, chunk.content),
            )


def get_chunks_for_file(file_id: str) -> list[dict]:
    """Return chunk details for a file, including whether a vector exists."""
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT fc.id, fc.chunk_index, fc.content, fc.start_char, fc.page_number,
               EXISTS(SELECT 1 FROM fts_chunks ft WHERE ft.chunk_id = fc.id) AS has_vector
        FROM file_chunks fc
        WHERE fc.file_id = ?
        ORDER BY fc.chunk_index
        """,
        (file_id,),
    ).fetchall()
    return [
        {
            "id": r[0],
            "chunk_index": r[1],
            "content": r[2],
            "start_char": r[3],
            "page_number": r[4],
            "char_count": len(r[2]) if r[2] else 0,
            "has_vector": bool(r[5]),
        }
        for r in rows
    ]


def get_adjacent_chunks(chunk_id: str, window: int = 1) -> list[dict]:
    """Get chunks adjacent to the given chunk_id within the same file."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT file_id, chunk_index FROM file_chunks WHERE id = ?", (chunk_id,)
    ).fetchone()
    if not row:
        return []
    file_id, idx = row[0], row[1]
    rows = conn.execute(
        """
        SELECT id, chunk_index, content, start_char, page_number
        FROM file_chunks
        WHERE file_id = ? AND chunk_index BETWEEN ? AND ?
        ORDER BY chunk_index
        """,
        (file_id, idx - window, idx + window),
    ).fetchall()
    return [
        {
            "id": r[0],
            "chunk_index": r[1],
            "content": r[2],
            "start_char": r[3],
            "page_number": r[4],
            "is_current": r[1] == idx,
        }
        for r in rows
    ]


def delete_chunks_for_file(file_id: str) -> None:
    conn = _get_conn()
    vec_impl = config_manager.get("vec_impl")

    chunk_ids = [
        r[0] for r in conn.execute(
            "SELECT id FROM file_chunks WHERE file_id = ?", (file_id,)
        ).fetchall()
    ]

    with conn:
        conn.execute("DELETE FROM file_chunks WHERE file_id = ?", (file_id,))
        for cid in chunk_ids:
            _delete_vec_by_source(conn, vec_impl, "chunk", cid)
            conn.execute("DELETE FROM fts_chunks WHERE chunk_id = ?", (cid,))


# ---------------------------------------------------------------------------
# Entry operations
# ---------------------------------------------------------------------------

def save_entry(entry: Entry, embedding: list[float] | None = None) -> None:
    conn = _get_conn()
    vec_impl = config_manager.get("vec_impl")

    with conn:
        conn.execute("""
            INSERT OR REPLACE INTO entries
                (id, content_text, metadata, created_at, source, tags, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            entry.id,
            entry.content_text,
            json.dumps(entry.metadata) if entry.metadata else None,
            entry.created_at.isoformat(),
            entry.source,
            json.dumps(entry.tags),
            entry.status,
        ))

        if embedding:
            _insert_vec(conn, vec_impl, embedding, "entry", entry.id)


def get_entry(entry_id: str) -> Entry | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return _row_to_entry(row) if row else None


def list_entries(
    tag: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Entry], int]:
    conn = _get_conn()
    conditions = ["status = 'active'"]
    params: list = []
    if source:
        conditions.append("source = ?")
        params.append(source)

    where = f"WHERE {' AND '.join(conditions)}"
    rows = conn.execute(
        f"SELECT * FROM entries {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    entries = [_row_to_entry(r) for r in rows]

    # Filter by tag (JSON array)
    if tag:
        entries = [e for e in entries if tag in e.tags]

    total = conn.execute(f"SELECT COUNT(*) FROM entries {where}", params).fetchone()[0]
    return entries, total


def update_entry(
    entry_id: str,
    content: str | None = None,
    tags: list[str] | None = None,
    status: str | None = None,
    embedding: list[float] | None = None,
) -> None:
    conn = _get_conn()
    vec_impl = config_manager.get("vec_impl")

    updates = []
    params: list = []
    if content is not None:
        updates.append("content_text = ?")
        params.append(content)
    if tags is not None:
        updates.append("tags = ?")
        params.append(json.dumps(tags))
    if status is not None:
        updates.append("status = ?")
        params.append(status)

    if not updates:
        return

    params.append(entry_id)
    with conn:
        conn.execute(
            f"UPDATE entries SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        if content is not None and embedding:
            _delete_vec_by_source(conn, vec_impl, "entry", entry_id)
            _insert_vec(conn, vec_impl, embedding, "entry", entry_id)


def delete_entry(entry_id: str) -> None:
    conn = _get_conn()
    vec_impl = config_manager.get("vec_impl")

    with conn:
        _delete_vec_by_source(conn, vec_impl, "entry", entry_id)
        conn.execute("DELETE FROM entry_files WHERE entry_id = ?", (entry_id,))
        conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))


def link_entry_file(entry_id: str, file_id: str) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO entry_files (entry_id, file_id) VALUES (?, ?)",
        (entry_id, file_id),
    )
    conn.commit()


def get_entry_files(entry_id: str) -> list[str]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT file_id FROM entry_files WHERE entry_id = ?", (entry_id,)
    ).fetchall()
    return [r[0] for r in rows]


def get_file_entry(file_id: str) -> list[str]:
    """Get entry IDs associated with a file."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT entry_id FROM entry_files WHERE file_id = ?", (file_id,)
    ).fetchall()
    return [r[0] for r in rows]


def get_file_summaries(file_ids: list[str]) -> dict[str, str]:
    """Batch-fetch auto_enrichment summary text for given file IDs.
    Returns {file_id: summary_text}. Files without a summary are omitted.
    """
    if not file_ids:
        return {}
    conn = _get_conn()
    placeholders = ",".join("?" * len(file_ids))
    rows = conn.execute(
        f"""
        SELECT ef.file_id, e.content_text
        FROM entry_files ef
        JOIN entries e ON ef.entry_id = e.id
        WHERE ef.file_id IN ({placeholders})
          AND e.source = 'auto_enrichment'
          AND e.status = 'active'
        """,
        file_ids,
    ).fetchall()
    return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------------------
# Search operations
# ---------------------------------------------------------------------------

def vector_search(
    embedding: list[float],
    limit: int,
    source_type: str | None = None,
    file_id: str | None = None,
) -> list[tuple[str, str, float]]:
    """
    KNN search on vec_items.
    Returns list of (source_type, source_id, distance).
    """
    conn = _get_conn()
    vec_impl = config_manager.get("vec_impl")
    dim = config_manager.get("embedding_dim")

    import struct
    emb_bytes = struct.pack(f"{dim}f", *embedding)

    candidates = limit * 20

    if vec_impl == "aux_column":
        if source_type:
            rows = conn.execute(f"""
                SELECT source_type, source_id, distance
                FROM vec_items
                WHERE embedding MATCH ? AND k = ?
                AND source_type = ?
                ORDER BY distance
            """, (emb_bytes, candidates, source_type)).fetchall()
        else:
            rows = conn.execute(f"""
                SELECT source_type, source_id, distance
                FROM vec_items
                WHERE embedding MATCH ? AND k = ?
                ORDER BY distance
            """, (emb_bytes, candidates)).fetchall()
        results = [(r[0], r[1], r[2]) for r in rows]
    else:
        rows = conn.execute(f"""
            SELECT vm.source_type, vm.source_id, v.distance
            FROM vec_items v
            JOIN vec_metadata vm ON vm.rowid = v.rowid
            WHERE v.embedding MATCH ? AND v.k = ?
            {"AND vm.source_type = ?" if source_type else ""}
            ORDER BY v.distance
        """, (emb_bytes, candidates, *([source_type] if source_type else []))).fetchall()
        results = [(r[0], r[1], r[2]) for r in rows]

    # Filter by file_id for in-document search
    if file_id:
        results = [
            (st, sid, dist) for st, sid, dist in results
            if st == "chunk" and sid.startswith(file_id)
        ]

    return results[:limit]


def fts_search(query: str, limit: int) -> list[tuple[str, float]]:
    """
    FTS5 search on fts_chunks.
    Returns list of (chunk_id, bm25_score).
    """
    conn = _get_conn()
    candidates = limit * 20
    try:
        rows = conn.execute("""
            SELECT chunk_id, bm25(fts_chunks) as score
            FROM fts_chunks
            WHERE content MATCH ?
            ORDER BY score
            LIMIT ?
        """, (query, candidates)).fetchall()
        return [(r[0], abs(float(r[1]))) for r in rows]
    except Exception as e:
        logger.warning("FTS search failed", extra={"error": str(e), "query": query})
        return []


# ---------------------------------------------------------------------------
# Task operations
# ---------------------------------------------------------------------------

def create_task(type: str, file_path: str | None = None) -> str:
    conn = _get_conn()
    task_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO tasks (id, type, status, file_path, result_json, created_at, updated_at)
        VALUES (?, ?, 'pending', ?, NULL, ?, ?)
    """, (task_id, type, file_path, now, now))
    conn.commit()
    return task_id


def update_task(task_id: str, status: str, result_json: str | None = None) -> None:
    conn = _get_conn()
    now = datetime.utcnow().isoformat()
    conn.execute("""
        UPDATE tasks SET status = ?, result_json = ?, updated_at = ?
        WHERE id = ?
    """, (status, result_json, now, task_id))
    conn.commit()


def get_task(task_id: str) -> TaskInfo | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_taskinfo(row) if row else None


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def get_stats() -> dict:
    conn = _get_conn()

    total_files = conn.execute("SELECT COUNT(*) FROM files WHERE status='active'").fetchone()[0]
    by_type = conn.execute(
        "SELECT type, COUNT(*) FROM files WHERE status='active' GROUP BY type"
    ).fetchall()
    total_entries = conn.execute("SELECT COUNT(*) FROM entries WHERE status='active'").fetchone()[0]
    total_chunks = conn.execute("SELECT COUNT(*) FROM file_chunks").fetchone()[0]

    try:
        total_vecs = conn.execute("SELECT COUNT(*) FROM vec_items").fetchone()[0]
    except Exception:
        total_vecs = 0

    db_size_mb = PB_DB_PATH.stat().st_size / (1024 * 1024) if PB_DB_PATH.exists() else 0
    storage_size = _dir_size_gb(STORAGE_PATH)

    return {
        "total_files": total_files,
        "files_by_type": {r[0]: r[1] for r in by_type},
        "total_entries": total_entries,
        "total_chunks": total_chunks,
        "total_vectors": total_vecs,
        "db_size_mb": round(db_size_mb, 2),
        "storage_size_gb": round(storage_size, 3),
    }


def _dir_size_gb(path: Path) -> float:
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    except Exception:
        pass
    return total / (1024 ** 3)


def scan_orphan_files() -> list[Path]:
    """Find files on disk not in files table."""
    conn = _get_conn()
    known = {
        r[0] for r in conn.execute("SELECT path FROM files").fetchall()
    }
    orphans = []
    for month_dir in STORAGE_PATH.glob("????-??"):
        if not month_dir.is_dir():
            continue
        for f in month_dir.iterdir():
            if f.is_file() and str(f) not in known:
                orphans.append(f)
    return orphans


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _insert_vec(
    conn: sqlite3.Connection,
    vec_impl: str,
    embedding: list[float],
    source_type: str,
    source_id: str,
) -> None:
    import struct
    dim = config_manager.get("embedding_dim")
    emb_bytes = struct.pack(f"{dim}f", *embedding)

    if vec_impl == "aux_column":
        conn.execute(
            "INSERT INTO vec_items (embedding, source_type, source_id) VALUES (?, ?, ?)",
            (emb_bytes, source_type, source_id),
        )
    else:
        cursor = conn.execute(
            "INSERT INTO vec_items (embedding) VALUES (?)",
            (emb_bytes,),
        )
        rowid = cursor.lastrowid
        conn.execute(
            "INSERT INTO vec_metadata (rowid, source_type, source_id) VALUES (?, ?, ?)",
            (rowid, source_type, source_id),
        )


def _delete_vec_by_source(
    conn: sqlite3.Connection,
    vec_impl: str,
    source_type: str,
    source_id: str,
) -> None:
    if vec_impl == "aux_column":
        conn.execute(
            "DELETE FROM vec_items WHERE source_type = ? AND source_id = ?",
            (source_type, source_id),
        )
    else:
        rowids = [
            r[0] for r in conn.execute(
                "SELECT rowid FROM vec_metadata WHERE source_type = ? AND source_id = ?",
                (source_type, source_id),
            ).fetchall()
        ]
        for rid in rowids:
            conn.execute("DELETE FROM vec_items WHERE rowid = ?", (rid,))
        conn.execute(
            "DELETE FROM vec_metadata WHERE source_type = ? AND source_id = ?",
            (source_type, source_id),
        )


def _row_to_fileinfo(row: sqlite3.Row) -> FileInfo:
    d = dict(row)
    for ts_field in ("created_at", "last_accessed"):
        val = d.get(ts_field)
        if val and isinstance(val, str):
            d[ts_field] = datetime.fromisoformat(val)
    return FileInfo(**d)


def _row_to_entry(row: sqlite3.Row) -> Entry:
    d = dict(row)
    if d.get("created_at") and isinstance(d["created_at"], str):
        d["created_at"] = datetime.fromisoformat(d["created_at"])
    if d.get("metadata") and isinstance(d["metadata"], str):
        d["metadata"] = json.loads(d["metadata"])
    if d.get("tags") and isinstance(d["tags"], str):
        d["tags"] = json.loads(d["tags"])
    else:
        d["tags"] = []
    return Entry(**d)


def _row_to_taskinfo(row: sqlite3.Row) -> TaskInfo:
    d = dict(row)
    for ts_field in ("created_at", "updated_at"):
        val = d.get(ts_field)
        if val and isinstance(val, str):
            d[ts_field] = datetime.fromisoformat(val)
    return TaskInfo(**d)
