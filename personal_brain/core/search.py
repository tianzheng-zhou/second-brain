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
        # If reranking, fetch 10x the limit or at least 50
        fetch_limit = max(50, limit * 10) if use_rerank else limit
        
        # Search using sqlite-vec
        cursor.execute("""
            SELECT rowid, distance 
            FROM vec_items 
            WHERE embedding MATCH ? 
            ORDER BY distance 
            LIMIT ?
        """, (query_bytes, fetch_limit))
        
        results = cursor.fetchall()
        
        candidates = []
        for row in results:
            rowid = row['rowid']
            distance = row['distance']
            
            # 1. Try to map via chunk_embeddings (New Chunk Logic)
            cursor.execute("""
                SELECT fc.content, fc.file_id, fc.chunk_index, f.filename, f.type
                FROM chunk_embeddings ce
                JOIN file_chunks fc ON ce.chunk_id = fc.id
                JOIN files f ON fc.file_id = f.id
                WHERE ce.rowid = ?
            """, (rowid,))
            chunk_data = cursor.fetchone()
            
            if chunk_data:
                candidates.append({
                    'type': 'chunk',
                    'content': chunk_data['content'],
                    'file_id': chunk_data['file_id'],
                    'filename': chunk_data['filename'],
                    'file_type': chunk_data['type'],
                    'distance': distance
                })
                continue
                
            # 2. Fallback to old file_embeddings (Legacy Logic)
            cursor.execute("SELECT file_id FROM file_embeddings WHERE rowid = ?", (rowid,))
            mapping = cursor.fetchone()
            if mapping:
                file_id = mapping['file_id']
                cursor.execute("SELECT * FROM files WHERE id = ?", (file_id,))
                file_row = cursor.fetchone()
                if file_row:
                    file_data = dict(file_row)
                    candidates.append({
                        'type': 'file',
                        'content': file_data.get('ocr_text') or "",
                        'file_id': file_data['id'],
                        'filename': file_data['filename'],
                        'file_type': file_data['type'],
                        'distance': distance
                    })
                continue

            # 3. Check for Entry Embeddings (New in v3)
            cursor.execute("""
                SELECT e.content_text, e.id, e.entry_type, e.created_at, e.tags
                FROM entry_embeddings ee
                JOIN entries e ON ee.entry_id = e.id
                WHERE ee.rowid = ?
            """, (rowid,))
            entry_data = cursor.fetchone()
            
            if entry_data:
                candidates.append({
                    'type': 'entry',
                    'content': entry_data['content_text'],
                    'entry_id': entry_data['id'],
                    'entry_type': entry_data['entry_type'],
                    'created_at': entry_data['created_at'],
                    'tags': entry_data['tags'],
                    'distance': distance,
                    'filename': f"Entry: {entry_data['entry_type']}", # for display compat
                    'file_type': 'entry'
                })
                continue
                    
        # Apply Reranking
        if use_rerank and candidates:
            print(f"Reranking {len(candidates)} candidates...")
            # Extract texts for reranking
            documents = [c.get('content') or "" for c in candidates]
            
            # Call reranker
            reranked_results = rerank_documents(query, documents, top_n=limit)
            
            if reranked_results:
                final_results = []
                for item in reranked_results:
                    original_idx = item['index']
                    candidate = candidates[original_idx]
                    # Update score with rerank score (higher is better)
                    candidate['score'] = item['relevance_score']
                    final_results.append(candidate)
                return final_results
            else:
                # If rerank fails, fallback
                return candidates[:limit]
        
        # If no rerank, normalize distance to score (lower distance is better, so invert or just return)
        # For consistency with rerank score (higher better), let's just return distance as is for now
        for c in candidates:
            c['score'] = 1.0 / (1.0 + c['distance']) # Simple conversion
            
        return candidates[:limit]
        
    except Exception as e:
        print(f"Search error: {e}")
        return []
    finally:
        conn.close()
