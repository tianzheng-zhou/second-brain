import os
from typing import List, Dict, Any
from dashscope import TextReRank
from personal_brain.config import RERANK_MODEL, DASHSCOPE_API_KEY

def rerank_documents(query: str, documents: List[str], top_n: int = None) -> List[Dict[str, Any]]:
    """
    Rerank a list of documents based on the query using DashScope's TextReRank.
    
    Args:
        query: The search query.
        documents: List of document strings to rerank.
        top_n: Number of top results to return. If None, returns all.
        
    Returns:
        List of results with 'index', 'relevance_score', and 'document'.
        Sorted by relevance_score descending.
    """
    if not documents:
        return []
        
    # Ensure API key is set
    if not os.environ.get("DASHSCOPE_API_KEY"):
        if DASHSCOPE_API_KEY:
            os.environ["DASHSCOPE_API_KEY"] = DASHSCOPE_API_KEY
        else:
            print("Warning: DASHSCOPE_API_KEY not set for reranking.")
            # Fallback to original order with dummy scores
            return [{"index": i, "relevance_score": 0.0, "document": doc} for i, doc in enumerate(documents)]

    try:
        # DashScope TextReRank call
        resp = TextReRank.call(
            model=RERANK_MODEL,
            query=query,
            documents=documents,
            top_n=top_n
        )
        
        if resp.status_code == 200:
            # Output format: 
            # resp.output.results = [{"index": 0, "relevance_score": 0.8}, ...]
            results = resp.output.results
            
            # Map back to documents
            reranked = []
            for item in results:
                idx = item.index
                score = item.relevance_score
                reranked.append({
                    "index": idx,
                    "relevance_score": score,
                    "document": documents[idx]
                })
            return reranked
        else:
            print(f"Rerank API error: {resp.code} - {resp.message}")
            return [{"index": i, "relevance_score": 0.0, "document": doc} for i, doc in enumerate(documents)]
            
    except Exception as e:
        print(f"Rerank exception: {e}")
        return [{"index": i, "relevance_score": 0.0, "document": doc} for i, doc in enumerate(documents)]
