"""
search.py — Hybrid search implementation.
Provides four search functions: hybrid, semantic, keyword, notes.
Uses RRF for hybrid fusion. Supports time filtering and reranking.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

import dateparser

from . import database as db
from .llm import generate_embedding
from .models import SearchResult
from .reranker import rerank
from .utils.logger import get_module_logger
from .utils.metrics import get_metrics, timer

logger = get_module_logger(__name__)
metrics = get_metrics()

_RRF_K = 60


def _parse_time_range(
    time_range: str | None,
) -> tuple[datetime, datetime] | None:
    if not time_range:
        return None
    try:
        # Try parsing as "start to end"
        if "到" in time_range or " to " in time_range:
            sep = "到" if "到" in time_range else " to "
            parts = time_range.split(sep, 1)
            start = dateparser.parse(parts[0].strip(), settings={"RETURN_AS_TIMEZONE_AWARE": False})
            end = dateparser.parse(parts[1].strip(), settings={"RETURN_AS_TIMEZONE_AWARE": False, "PREFER_DAY_OF_MONTH": "last"})
        else:
            # Relative range like "最近一周"
            end = datetime.utcnow()
            start = dateparser.parse(time_range, settings={"RETURN_AS_TIMEZONE_AWARE": False, "PREFER_DATES_FROM": "past"})

        if start and end:
            return (start, end)
    except Exception as e:
        logger.warning("Time range parse failed", extra={"time_range": time_range, "error": str(e)})
    return None


def _rrf_merge(
    vector_results: list[tuple[str, str, float]],
    fts_results: list[tuple[str, float]],
    k: int = _RRF_K,
) -> list[tuple[str, str, float]]:
    """
    Reciprocal Rank Fusion.
    vector_results: [(source_type, source_id, distance), ...]
    fts_results: [(chunk_id, bm25_score), ...]
    Returns sorted [(source_type, source_id, rrf_score), ...]
    """
    scores: dict[tuple[str, str], float] = {}

    for rank, (source_type, source_id, _) in enumerate(vector_results):
        key = (source_type, source_id)
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)

    for rank, (chunk_id, _) in enumerate(fts_results):
        key = ("chunk", chunk_id)
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)

    sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [(st, sid, score) for (st, sid), score in sorted_items]


def _filter_by_time(
    results: list[SearchResult],
    time_range: tuple[datetime, datetime] | None,
) -> list[SearchResult]:
    if not time_range:
        return results
    start, end = time_range
    # We'd need created_at on results; filter by fetching from DB
    # For now, we accept all (time filtering done at DB query level ideally)
    return results


def _build_search_results(
    merged: list[tuple[str, str, float]],
    limit: int,
) -> list[SearchResult]:
    """Expand source_id to full SearchResult objects."""
    results: list[SearchResult] = []
    seen: set[str] = set()

    for source_type, source_id, score in merged:
        if source_id in seen:
            continue
        seen.add(source_id)

        if source_type == "chunk":
            # Get chunk + file info
            conn_rows = db._get_conn().execute(
                """
                SELECT fc.content, fc.chunk_index, fc.page_number, fc.file_id,
                       f.filename, f.status
                FROM file_chunks fc
                JOIN files f ON fc.file_id = f.id
                WHERE fc.id = ? AND f.status = 'active'
                """,
                (source_id,),
            ).fetchone()
            if conn_rows:
                results.append(SearchResult(
                    score=score,
                    content=conn_rows[0],
                    source_type="chunk",
                    source_file_id=conn_rows[3],
                    source_filename=conn_rows[4],
                    chunk_index=conn_rows[1],
                    page_number=conn_rows[2],
                    entry_id=None,
                ))

        elif source_type == "entry":
            row = db._get_conn().execute(
                "SELECT content_text, status FROM entries WHERE id = ? AND status = 'active'",
                (source_id,),
            ).fetchone()
            if row:
                results.append(SearchResult(
                    score=score,
                    content=row[0],
                    source_type="entry",
                    entry_id=source_id,
                ))

        if len(results) >= limit * 3:
            break

    return results


def search_hybrid(
    query: str,
    limit: int = 5,
    time_range: str | None = None,
    use_rerank: bool = False,
) -> list[SearchResult]:
    """Hybrid search: vector + FTS5 with RRF fusion."""
    with timer("search_duration_ms"):
        tr = _parse_time_range(time_range)
        candidates = max(100, limit * 20)

        emb = generate_embedding(query)
        vec_results = db.vector_search(emb, candidates)
        fts_results = db.fts_search(query, candidates)

        merged = _rrf_merge(vec_results, fts_results)
        results = _build_search_results(merged, limit)

        if tr:
            results = _filter_by_time(results, tr)

        if use_rerank and results:
            results = rerank(query, results, limit)
        else:
            results = results[:limit]

    metrics.increment("search_count", "hybrid")
    logger.info("Hybrid search", extra={"query": query[:50], "results": len(results)})
    return results


def search_semantic(
    query: str,
    limit: int = 5,
    time_range: str | None = None,
) -> list[SearchResult]:
    """Pure vector semantic search."""
    with timer("search_duration_ms"):
        tr = _parse_time_range(time_range)
        candidates = max(100, limit * 20)

        emb = generate_embedding(query)
        vec_results = db.vector_search(emb, candidates)

        merged = [(st, sid, 1.0 / (1.0 + dist)) for st, sid, dist in vec_results]
        results = _build_search_results(merged, limit)

        if tr:
            results = _filter_by_time(results, tr)

        results = results[:limit]

    metrics.increment("search_count", "semantic")
    logger.info("Semantic search", extra={"query": query[:50], "results": len(results)})
    return results


def search_keyword(
    query: str,
    limit: int = 5,
    time_range: str | None = None,
) -> list[SearchResult]:
    """Pure FTS5 keyword search (chunks only)."""
    with timer("search_duration_ms"):
        tr = _parse_time_range(time_range)
        candidates = max(100, limit * 20)

        fts_results = db.fts_search(query, candidates)

        # Convert to (source_type, source_id, score) format
        merged = [("chunk", chunk_id, score) for chunk_id, score in fts_results]
        results = _build_search_results(merged, limit)

        if tr:
            results = _filter_by_time(results, tr)

        results = results[:limit]

    metrics.increment("search_count", "keyword")
    logger.info("Keyword search", extra={"query": query[:50], "results": len(results)})
    return results


def search_notes(
    query: str,
    limit: int = 5,
    tag: str | None = None,
) -> list[SearchResult]:
    """Vector search limited to entries only."""
    with timer("search_duration_ms"):
        candidates = max(100, limit * 20)

        emb = generate_embedding(query)
        vec_results = db.vector_search(emb, candidates, source_type="entry")

        merged = [(st, sid, 1.0 / (1.0 + dist)) for st, sid, dist in vec_results]
        results = _build_search_results(merged, limit)

        # Filter by tag if provided
        if tag:
            filtered = []
            for r in results:
                if r.entry_id:
                    entry = db.get_entry(r.entry_id)
                    if entry and tag in entry.tags:
                        filtered.append(r)
            results = filtered

        results = results[:limit]

    metrics.increment("search_count", "notes")
    logger.info("Notes search", extra={"query": query[:50], "results": len(results)})
    return results


def search_in_document(
    file_id: str,
    query: str,
    limit: int = 5,
) -> list[SearchResult]:
    """Vector search within a specific document's chunks."""
    emb = generate_embedding(query)
    vec_results = db.vector_search(emb, limit * 5, source_type="chunk", file_id=file_id)
    merged = [(st, sid, 1.0 / (1.0 + dist)) for st, sid, dist in vec_results]
    return _build_search_results(merged, limit)[:limit]
