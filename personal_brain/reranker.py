"""
reranker.py — Result reranking via DashScope REST API (not SDK).
Falls back to original order with score=0 on failure.
"""
from __future__ import annotations

import httpx

from . import config_manager
from .config import DASHSCOPE_API_KEY
from .models import SearchResult
from .utils.logger import get_module_logger

logger = get_module_logger(__name__)

_RERANK_URL = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
_MAX_DOC_LEN = 8000


def rerank(query: str, results: list[SearchResult], top_n: int) -> list[SearchResult]:
    """Rerank search results. Returns top_n results sorted by relevance."""
    if not results:
        return results

    model = config_manager.get("rerank_model")
    docs = [r.content[:_MAX_DOC_LEN] for r in results]

    payload = {
        "model": model,
        "input": {
            "query": query,
            "documents": docs,
        },
        "parameters": {"top_n": top_n, "return_documents": False},
    }
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(_RERANK_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        rerank_results = data.get("output", {}).get("results", [])
        # Build reranked list
        reranked: list[SearchResult] = []
        for item in sorted(rerank_results, key=lambda x: x.get("index", 0)):
            idx = item.get("index", 0)
            score = item.get("relevance_score", 0.0)
            if idx < len(results):
                r = results[idx].model_copy(update={"score": score})
                reranked.append(r)

        return reranked[:top_n]

    except Exception as e:
        logger.warning("Rerank failed, using original order", extra={"error": str(e)})
        # Return original order with score=0
        for r in results:
            r.score = 0.0
        return results[:top_n]
