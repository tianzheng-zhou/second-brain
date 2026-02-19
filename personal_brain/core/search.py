from personal_brain.core.indexer import generate_embedding
from personal_brain.core.database import get_db_connection
from typing import List, Dict
import struct

def search_files(query: str, limit: int = 5) -> List[Dict]:
    """Search for files using semantic search."""
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
        
        # Search using sqlite-vec
        # Syntax: SELECT rowid, distance FROM vec_items WHERE embedding MATCH ? ORDER BY distance LIMIT ?
        cursor.execute("""
            SELECT rowid, distance 
            FROM vec_items 
            WHERE embedding MATCH ? 
            ORDER BY distance 
            LIMIT ?
        """, (query_bytes, limit))
        
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
                    
        return files
        
    except Exception as e:
        print(f"Search error: {e}")
        return []
    finally:
        conn.close()
