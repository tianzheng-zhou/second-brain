from personal_brain.core.indexer import generate_embedding
from personal_brain.core.database import get_db_connection
from personal_brain.core.reranker import rerank_documents
from typing import List, Dict
import struct

def search_files(query: str, limit: int = 5, use_rerank: bool = True) -> List[Dict]:
    """
    Search for files using semantic search with optional reranking.
    
    Args:
        query: User query
        limit: Number of final results to return
        use_rerank: Whether to apply reranking (default: True)
    """
    embedding = generate_embedding(query)
    if not embedding:
        return []
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Check if vec_items exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vec_items'")
        if not cursor.fetchone():
            print("Vector search not available (vec_items table missing).")
            return []
            
        # Serialize query embedding
        query_bytes = struct.pack(f'<{len(embedding)}f', *embedding)
        
        # Initial retrieval limit (fetch more for reranking)
        # If reranking, fetch 4x the limit or at least 20
        fetch_limit = max(20, limit * 4) if use_rerank else limit
        
        # Search using sqlite-vec
        cursor.execute("""
            SELECT rowid, distance 
            FROM vec_items 
            WHERE embedding MATCH ? 
            ORDER BY distance 
            LIMIT ?
        """, (query_bytes, fetch_limit))
        
        results = cursor.fetchall()
        
        files = []
        for row in results:
            rowid = row['rowid']
            distance = row['distance']
            
            # Get file_id from mapping
            cursor.execute("SELECT file_id FROM file_embeddings WHERE rowid = ?", (rowid,))
            mapping = cursor.fetchone()
            if mapping:
                file_id = mapping['file_id']
                # Get file details
                cursor.execute("SELECT * FROM files WHERE id = ?", (file_id,))
                file_row = cursor.fetchone()
                if file_row:
                    file_data = dict(file_row)
                    file_data['distance'] = distance
                    files.append(file_data)
                    
        # Apply Reranking
        if use_rerank and files:
            print(f"Reranking {len(files)} candidates...")
            # Extract texts for reranking
            # Prefer ocr_text, fallback to filename
            documents = [f.get('ocr_text') or f.get('filename') or "" for f in files]
            
            # Call reranker
            reranked_results = rerank_documents(query, documents, top_n=limit)
            
            if reranked_results:
                final_files = []
                for item in reranked_results:
                    original_idx = item['index']
                    file_data = files[original_idx]
                    # Update score with rerank score
                    file_data['rerank_score'] = item['relevance_score']
                    # Keep original distance for reference
                    final_files.append(file_data)
                return final_files
            else:
                # If rerank fails, fallback to original top N
                return files[:limit]
        
        return files[:limit]
        
    except Exception as e:
        print(f"Search error: {e}")
        return []
    finally:
        conn.close()
