import os
import requests
from typing import List, Dict, Any
# from dashscope import TextReRank # No longer using TextReRank class for qwen3-vl-rerank
from personal_brain.config import RERANK_MODEL, DASHSCOPE_API_KEY

def rerank_documents(query: str, documents: List[str], top_n: int = None) -> List[Dict[str, Any]]:
    """
    Rerank a list of documents based on the query using DashScope's qwen3-vl-rerank via REST API.
    
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

    # Truncate documents to avoid token limit (qwen3-vl-rerank has ~8k limit)
    # Using simple character truncation as proxy (8000 chars is safe enough)
    MAX_DOC_LEN = 8000
    truncated_docs = []
    for doc in documents:
        if len(doc) > MAX_DOC_LEN:
            truncated_docs.append(doc[:MAX_DOC_LEN])
        else:
            truncated_docs.append(doc)
            
    # Ensure API key is set
    api_key = os.environ.get("DASHSCOPE_API_KEY") or DASHSCOPE_API_KEY
    if not api_key:
        print("Warning: DASHSCOPE_API_KEY not set for reranking.")
        return [{"index": i, "relevance_score": 0.0, "document": doc} for i, doc in enumerate(documents)]

    try:
        # qwen3-vl-rerank uses the generic text-rerank endpoint but with specific model
        url = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": RERANK_MODEL,
            "input": {
                "query": query,
                "documents": truncated_docs
            },
            "parameters": {
                "return_documents": True
            }
        }
        
        if top_n is not None:
            payload["parameters"]["top_n"] = top_n
            
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code == 200:
            data = response.json()
            # Output format check
            if "output" in data and "results" in data["output"]:
                results = data["output"]["results"]
                
                # Map back to documents
                reranked = []
                for item in results:
                    idx = item.get("index")
                    score = item.get("relevance_score")
                    reranked.append({
                        "index": idx,
                        "relevance_score": score,
                        "document": documents[idx] # Use original document content, not truncated
                    })
                return reranked
            else:
                print(f"Unexpected rerank response format: {data}")
                return [{"index": i, "relevance_score": 0.0, "document": doc} for i, doc in enumerate(documents)]
        else:
            print(f"Rerank API error: {response.status_code} - {response.text}")
            return [{"index": i, "relevance_score": 0.0, "document": doc} for i, doc in enumerate(documents)]
            
    except Exception as e:
        print(f"Rerank exception: {e}")
        return [{"index": i, "relevance_score": 0.0, "document": doc} for i, doc in enumerate(documents)]
