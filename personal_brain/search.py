"""
search.py — Hybrid search implementation.
Provides four search functions: hybrid, semantic, keyword, notes.
Uses RRF for hybrid fusion. Supports time filtering and reranking.
"""
from __future__ import annotations

import json
import math
import re
from calendar import monthrange
from datetime import datetime, timedelta
from typing import Optional

import dateparser

from . import config_manager
from . import database as db
from .llm import generate_embedding
from .models import SearchResult
from .reranker import rerank
from .utils.logger import get_module_logger
from .utils.metrics import get_metrics, timer

logger = get_module_logger(__name__)
metrics = get_metrics()

_RRF_K = 60


def _expand_single_date(raw: str, date: datetime) -> tuple[datetime, datetime]:
    """Expand a single parsed date to a range based on granularity cues in raw text."""
    # Year only: "2024" or "2024年"
    if re.fullmatch(r"\d{4}年?", raw.strip()):
        return datetime(date.year, 1, 1), datetime(date.year, 12, 31, 23, 59, 59)

    # Year + phase: 年初/年中/年底
    if any(k in raw for k in ("年初", "年头")):
        return datetime(date.year, 1, 1), datetime(date.year, 3, 31, 23, 59, 59)
    if "年中" in raw:
        return datetime(date.year, 4, 1), datetime(date.year, 9, 30, 23, 59, 59)
    if any(k in raw for k in ("年底", "年末", "年尾")):
        return datetime(date.year, 10, 1), datetime(date.year, 12, 31, 23, 59, 59)

    # Half-year / season
    if any(k in raw for k in ("上半年", "春", "Q1", "第一季度")):
        return datetime(date.year, 1, 1), datetime(date.year, 6, 30, 23, 59, 59)
    if any(k in raw for k in ("夏", "Q2", "第二季度")):
        return datetime(date.year, 4, 1), datetime(date.year, 6, 30, 23, 59, 59)
    if any(k in raw for k in ("秋", "Q3", "第三季度")):
        return datetime(date.year, 7, 1), datetime(date.year, 9, 30, 23, 59, 59)
    if any(k in raw for k in ("下半年", "冬", "Q4", "第四季度")):
        return datetime(date.year, 7, 1), datetime(date.year, 12, 31, 23, 59, 59)

    # Year + month: "2024年3月" or "2024-03"
    if re.search(r"\d{4}[年-]\d{1,2}月?$", raw.strip()):
        last_day = monthrange(date.year, date.month)[1]
        return datetime(date.year, date.month, 1), datetime(date.year, date.month, last_day, 23, 59, 59)

    # Fuzzy/approximate: ±90 days
    if any(k in raw for k in ("大约", "约", "左右", "around", "approximately", "circa")):
        delta = timedelta(days=90)
        return date - delta, date + delta

    # Default: single day
    return date.replace(hour=0, minute=0, second=0), date.replace(hour=23, minute=59, second=59)


def _parse_time_range(
    time_range: str | None,
) -> tuple[datetime, datetime] | None:
    if not time_range:
        return None
    try:
        # Explicit range: "2024年1月 到 2024年3月" or "start to end"
        if "到" in time_range or " to " in time_range:
            sep = "到" if "到" in time_range else " to "
            parts = time_range.split(sep, 1)
            start = dateparser.parse(parts[0].strip(), settings={"RETURN_AS_TIMEZONE_AWARE": False})
            end = dateparser.parse(parts[1].strip(), settings={"RETURN_AS_TIMEZONE_AWARE": False, "PREFER_DAY_OF_MONTH": "last"})
            if start and end:
                if start > end:
                    start, end = end, start
                return (start, end)
        else:
            # Relative keywords ("最近一周", "last week") → parse as "N ago" to now
            _relative_kw = ("最近", "上周", "上个月", "上月", "last", "past", "ago", "yesterday", "today", "本周", "本月")
            parsed = dateparser.parse(
                time_range,
                settings={"RETURN_AS_TIMEZONE_AWARE": False, "PREFER_DATES_FROM": "past", "PREFER_DAY_OF_MONTH": "first"},
            )
            if parsed:
                if any(k in time_range.lower() for k in _relative_kw):
                    return (parsed, datetime.utcnow())
                else:
                    return _expand_single_date(time_range.strip(), parsed)
    except Exception as e:
        logger.warning("Time range parse failed", extra={"time_range": time_range, "error": str(e)})
    return None


def _expand_query(query: str) -> str:
    """Use LLM to rewrite a conversational query into optimized search terms."""
    from .llm import call_llm

    model = config_manager.get("semantic_split_model")
    prompt = (
        "你是一个搜索查询优化器。将用户的口语化查询改写为更适合知识库检索的规范化查询。\n"
        "规则：\n"
        "1. 提取核心关键词和概念\n"
        "2. 展开缩写和同义词（例如 'k8s' → 'Kubernetes'）\n"
        "3. 如果查询涉及多个子主题，用分号分隔\n"
        "4. 保持原始语言（中文/英文）\n"
        "5. 只输出改写后的查询文本，不要解释\n\n"
        f"原始查询：{query}"
    )
    try:
        rewritten = call_llm(
            model,
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=200,
        )
        rewritten = rewritten.strip()
        if rewritten:
            logger.info(
                "Query expanded",
                extra={"original": query[:50], "rewritten": rewritten[:80]},
            )
            return rewritten
        return query
    except Exception as e:
        logger.warning("Query expansion failed, using original", extra={"error": str(e)})
        return query


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
    query_start, query_end = time_range
    filtered = []
    for r in results:
        # Entries with event_time: overlap check ([event_start, event_end] ∩ [query_start, query_end] ≠ ∅)
        if r.event_time_start is not None and r.event_time_end is not None:
            if r.event_time_start <= query_end and r.event_time_end >= query_start:
                filtered.append(r)
        elif r.created_at is not None:
            # Chunks and entries without event_time: check upload time
            if query_start <= r.created_at <= query_end:
                filtered.append(r)
    return filtered


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
                       f.filename, f.status, f.created_at
                FROM file_chunks fc
                JOIN files f ON fc.file_id = f.id
                WHERE fc.id = ? AND f.status = 'active'
                """,
                (source_id,),
            ).fetchone()
            if conn_rows:
                created_at = None
                if conn_rows[6]:
                    try:
                        created_at = datetime.fromisoformat(conn_rows[6])
                    except (ValueError, TypeError):
                        pass
                results.append(SearchResult(
                    score=score,
                    content=conn_rows[0],
                    source_type="chunk",
                    source_file_id=conn_rows[3],
                    source_filename=conn_rows[4],
                    chunk_index=conn_rows[1],
                    page_number=conn_rows[2],
                    entry_id=None,
                    created_at=created_at,
                ))

        elif source_type == "entry":
            row = db._get_conn().execute(
                "SELECT content_text, status, created_at, metadata FROM entries WHERE id = ? AND status = 'active'",
                (source_id,),
            ).fetchone()
            if row:
                created_at = None
                if row[2]:
                    try:
                        created_at = datetime.fromisoformat(row[2])
                    except (ValueError, TypeError):
                        pass

                event_time_start = None
                event_time_end = None
                if row[3]:
                    try:
                        meta = json.loads(row[3]) if isinstance(row[3], str) else row[3]
                        et = (meta or {}).get("event_time")
                        if et:
                            if et.get("start"):
                                event_time_start = datetime.fromisoformat(et["start"])
                            if et.get("end"):
                                event_time_end = datetime.fromisoformat(et["end"])
                    except Exception:
                        pass

                results.append(SearchResult(
                    score=score,
                    content=row[0],
                    source_type="entry",
                    entry_id=source_id,
                    created_at=created_at,
                    event_time_start=event_time_start,
                    event_time_end=event_time_end,
                ))

        if len(results) >= limit * 3:
            break

    return results


def search_hybrid(
    query: str,
    limit: int = 5,
    time_range: str | None = None,
    use_rerank: bool = False,
    expand_query: bool = False,
) -> list[SearchResult]:
    """Hybrid search: vector + FTS5 with RRF fusion."""
    with timer("search_duration_ms"):
        tr = _parse_time_range(time_range)
        candidates = max(100, limit * 20)

        search_query = _expand_query(query) if expand_query else query

        emb = generate_embedding(search_query)
        vec_results = db.vector_search(emb, candidates)
        fts_results = db.fts_search(search_query, candidates)

        merged = _rrf_merge(vec_results, fts_results)
        results = _build_search_results(merged, limit)

        if tr:
            results = _filter_by_time(results, tr)

        # Rerank uses original query (user intent), not expanded query
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
