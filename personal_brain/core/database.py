import sqlite3
import struct
import json
from pathlib import Path
from datetime import datetime
from personal_brain.config import DB_PATH, EMBEDDING_DIMENSION

def get_db_connection():
    """
    Get a connection to the SQLite database.
    Tries to load sqlite-vec extension if available.
    """
    # print(f"Connecting to database at: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    
    # Try to load sqlite-vec
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except ImportError:
        pass # print("Warning: sqlite-vec not installed. Vector search will not work.")
    except Exception as e:
        print(f"Warning: Failed to load sqlite-vec extension: {e}")

    return conn

def init_db():
    """
    Initialize the database schema.
    """
    # Ensure parent directory exists
    if not DB_PATH.parent.exists():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Files table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY,
            path TEXT UNIQUE,
            filename TEXT,
            type TEXT,
            size_bytes INTEGER,
            created_at TIMESTAMP,
            last_accessed TIMESTAMP,
            ocr_text TEXT,
            trash_score REAL,
            status TEXT
        )
    """)
    
    try:
        # Check if vec0 module exists (extension loaded)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vec_items'")
        if not cursor.fetchone():
             cursor.execute(f"""
                CREATE VIRTUAL TABLE vec_items USING vec0(
                    embedding float[{EMBEDDING_DIMENSION}]
                )
            """)
    except Exception as e:
        print(f"Warning: Could not create vector table. Vector search will be disabled. Error: {e}")

    # Mapping table for vector search (rowid <-> file_id)
    # DEPRECATED for new chunks, but kept for compatibility or cleanup
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS file_embeddings (
            rowid INTEGER PRIMARY KEY,
            file_id TEXT,
            FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
        )
    """)

    # File Chunks table (New)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS file_chunks (
            id TEXT PRIMARY KEY,
            file_id TEXT,
            chunk_index INTEGER,
            content TEXT,
            FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
        )
    """)

    # Chunk Embeddings mapping table (New)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chunk_embeddings (
            rowid INTEGER PRIMARY KEY,
            chunk_id TEXT,
            FOREIGN KEY(chunk_id) REFERENCES file_chunks(id) ON DELETE CASCADE
        )
    """)

    # Entities table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY,
            name TEXT,
            type TEXT,
            first_seen TIMESTAMP,
            mention_count INTEGER,
            metadata TEXT
        )
    """)
    
    # Relations table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS relations (
            source TEXT,
            target TEXT,
            type TEXT,
            file_id TEXT,
            confidence REAL,
            created_at TIMESTAMP,
            FOREIGN KEY(source) REFERENCES entities(id),
            FOREIGN KEY(target) REFERENCES entities(id),
            FOREIGN KEY(file_id) REFERENCES files(id)
        )
    """)
    
    # Chat History table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT,
            content TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Entries table (New in v3)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY,
            content_text TEXT,
            content_json TEXT,
            entry_type TEXT,
            created_at TIMESTAMP,
            source TEXT,
            tags TEXT,
            importance REAL,
            trash_score REAL,
            status TEXT
        )
    """)

    # Entry Files junction table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entry_files (
            entry_id TEXT,
            file_id TEXT,
            PRIMARY KEY (entry_id, file_id),
            FOREIGN KEY(entry_id) REFERENCES entries(id),
            FOREIGN KEY(file_id) REFERENCES files(id)
        )
    """)
    
    # Entry Embeddings table (for semantic search of entries)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entry_embeddings (
            rowid INTEGER PRIMARY KEY,
            entry_id TEXT,
            FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
        )
    """)
    
    conn.commit()
    conn.close()
    # print(f"Database initialized at {DB_PATH}")

def save_chunks(file_id: str, chunks: list[str], embeddings: list[list[float]]):
    """
    Save multiple chunks and their embeddings for a file.
    """
    if not chunks or not embeddings or len(chunks) != len(embeddings):
        print(f"Error: Mismatch in chunks ({len(chunks)}) and embeddings ({len(embeddings)})")
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # First, delete existing chunks for this file (full refresh)
        # Find existing chunk ids
        cursor.execute("SELECT id FROM file_chunks WHERE file_id = ?", (file_id,))
        existing_chunk_ids = [row['id'] for row in cursor.fetchall()]
        
        # Delete from chunk_embeddings (and implicitly vec_items via rowid lookup)
        for chunk_id in existing_chunk_ids:
            cursor.execute("SELECT rowid FROM chunk_embeddings WHERE chunk_id = ?", (chunk_id,))
            row = cursor.fetchone()
            if row:
                cursor.execute("DELETE FROM vec_items WHERE rowid = ?", (row['rowid'],))
            cursor.execute("DELETE FROM chunk_embeddings WHERE chunk_id = ?", (chunk_id,))
            
        # Delete from file_chunks
        cursor.execute("DELETE FROM file_chunks WHERE file_id = ?", (file_id,))
        
        # Insert new chunks
        for i, (text, embedding) in enumerate(zip(chunks, embeddings)):
            chunk_id = f"{file_id}_{i}"
            
            # 1. Insert into file_chunks
            cursor.execute("""
                INSERT INTO file_chunks (id, file_id, chunk_index, content)
                VALUES (?, ?, ?, ?)
            """, (chunk_id, file_id, i, text))
            
            # 2. Insert embedding into vec_items
            embedding_bytes = struct.pack(f'<{len(embedding)}f', *embedding)
            cursor.execute("INSERT INTO vec_items(embedding) VALUES (?)", (embedding_bytes,))
            rowid = cursor.lastrowid
            
            # 3. Map rowid to chunk_id
            cursor.execute("INSERT INTO chunk_embeddings (rowid, chunk_id) VALUES (?, ?)", (rowid, chunk_id))
            
        conn.commit()
        print(f"Saved {len(chunks)} chunks for file {file_id}")
        
    except Exception as e:
        print(f"Error saving chunks: {e}")
        conn.rollback()
    finally:
        conn.close()

def save_file(file):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO files (
            id, path, filename, type, size_bytes, created_at, last_accessed, ocr_text, trash_score, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        file.id, str(file.path), file.filename, file.type.value, file.size_bytes, 
        file.created_at, file.last_accessed, file.ocr_text, file.trash_score, file.status.value
    ))
    conn.commit()
    conn.close()

def save_embedding(file_id: str, embedding: list):
    if not embedding:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Pack as little-endian float32 array
        embedding_bytes = struct.pack(f'<{len(embedding)}f', *embedding)
        cursor.execute("INSERT INTO vec_items(embedding) VALUES (?)", (embedding_bytes,))
        rowid = cursor.lastrowid
        
        cursor.execute("INSERT OR REPLACE INTO file_embeddings(rowid, file_id) VALUES (?, ?)", (rowid, file_id))
        conn.commit()
    except Exception as e:
        print(f"Error saving embedding: {e}")
    finally:
        conn.close()

def get_file(file_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM files WHERE id = ?", (file_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def get_all_files():
    """
    Get all files from the database.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM files ORDER BY created_at DESC")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"Error fetching all files: {e}")
        return []
    finally:
        conn.close()

def get_file_chunks(file_id: str):
    """
    Get all chunks for a specific file.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM file_chunks WHERE file_id = ? ORDER BY chunk_index ASC", (file_id,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"Error fetching chunks for file {file_id}: {e}")
        return []
    finally:
        conn.close()

def get_db_schema():
    """
    Get the database schema (list of tables and their columns).
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    schema_info = {}
    try:
        # Get list of tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        
        for table in tables:
            table_name = table['name']
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = cursor.fetchall()
            schema_info[table_name] = [dict(col) for col in columns]
            
        return schema_info
    except Exception as e:
        print(f"Error fetching schema: {e}")
        return {}
    finally:
        conn.close()

def delete_file_record(file_id: str):
    """
    Delete a file record and its associated embeddings from the database.
    Updated to handle both legacy (file_embeddings) and new (chunk_embeddings) schemas.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. Handle Legacy Schema
        cursor.execute("SELECT rowid FROM file_embeddings WHERE file_id = ?", (file_id,))
        rows = cursor.fetchall()
        for row in rows:
            # Delete from vec_items
            cursor.execute("DELETE FROM vec_items WHERE rowid = ?", (row['rowid'],))
        
        cursor.execute("DELETE FROM file_embeddings WHERE file_id = ?", (file_id,))
        
        # 2. Handle New Chunk Schema
        # Find all chunks for this file
        cursor.execute("SELECT id FROM file_chunks WHERE file_id = ?", (file_id,))
        chunk_ids = [row['id'] for row in cursor.fetchall()]
        
        for chunk_id in chunk_ids:
            # Find associated embeddings
            cursor.execute("SELECT rowid FROM chunk_embeddings WHERE chunk_id = ?", (chunk_id,))
            emb_rows = cursor.fetchall()
            for row in emb_rows:
                # Delete from vec_items
                cursor.execute("DELETE FROM vec_items WHERE rowid = ?", (row['rowid'],))
            
            # Delete from chunk_embeddings
            cursor.execute("DELETE FROM chunk_embeddings WHERE chunk_id = ?", (chunk_id,))
            
        # Delete from file_chunks
        cursor.execute("DELETE FROM file_chunks WHERE file_id = ?", (file_id,))
        
        # 3. Delete from files table
        cursor.execute("DELETE FROM files WHERE id = ?", (file_id,))
        
        conn.commit()
        print(f"Deleted file record {file_id}")
        return True
    except Exception as e:
        print(f"Error deleting file record {file_id}: {e}")
        return False
    finally:
        conn.close()

def save_chat_message(role: str, content: str):
    """Save a chat message to history."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO chat_history (role, content) VALUES (?, ?)", (role, content))
        conn.commit()
    except Exception as e:
        print(f"Error saving chat message: {e}")
    finally:
        conn.close()

def get_chat_history(limit: int = 50):
    """Get recent chat history."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT role, content, timestamp FROM chat_history ORDER BY timestamp DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        # Reverse to return in chronological order
        return [dict(row) for row in rows][::-1]
    except Exception as e:
        print(f"Error fetching chat history: {e}")
        return []
    finally:
        conn.close()

def save_entry(entry: dict):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Check if entry exists
        cursor.execute("SELECT id FROM entries WHERE id = ?", (entry['id'],))
        exists = cursor.fetchone()
        
        if exists:
            # Update
            cursor.execute("""
                UPDATE entries SET
                    content_text = ?, content_json = ?, entry_type = ?,
                    created_at = ?, source = ?, tags = ?,
                    importance = ?, trash_score = ?, status = ?
                WHERE id = ?
            """, (
                entry.get('content_text'), entry.get('content_json'), entry.get('entry_type'),
                entry.get('created_at'), entry.get('source'), entry.get('tags'),
                entry.get('importance'), entry.get('trash_score'), entry.get('status'),
                entry['id']
            ))
        else:
            # Insert
            cursor.execute("""
                INSERT INTO entries (
                    id, content_text, content_json, entry_type,
                    created_at, source, tags, importance, trash_score, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry['id'], entry.get('content_text'), entry.get('content_json'), entry.get('entry_type'),
                entry.get('created_at'), entry.get('source'), entry.get('tags'),
                entry.get('importance'), entry.get('trash_score'), entry.get('status')
            ))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error saving entry {entry.get('id')}: {e}")
        return False
    finally:
        conn.close()

def get_entry(entry_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    finally:
        conn.close()

def save_entry_embedding(entry_id: str, embedding: list):
    if not embedding:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # First clear old embeddings for this entry?
        # Find rowids
        cursor.execute("SELECT rowid FROM entry_embeddings WHERE entry_id = ?", (entry_id,))
        rows = cursor.fetchall()
        for row in rows:
            cursor.execute("DELETE FROM vec_items WHERE rowid = ?", (row['rowid'],))
        cursor.execute("DELETE FROM entry_embeddings WHERE entry_id = ?", (entry_id,))

        embedding_bytes = struct.pack(f'<{len(embedding)}f', *embedding)
        cursor.execute("INSERT INTO vec_items(embedding) VALUES (?)", (embedding_bytes,))
        rowid = cursor.lastrowid
        cursor.execute("INSERT INTO entry_embeddings(rowid, entry_id) VALUES (?, ?)", (rowid, entry_id))
        conn.commit()
    except Exception as e:
        print(f"Error saving entry embedding: {e}")
    finally:
        conn.close()

def link_entry_files(entry_id: str, file_ids: list):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        for fid in file_ids:
            cursor.execute("INSERT OR IGNORE INTO entry_files (entry_id, file_id) VALUES (?, ?)", (entry_id, fid))
        conn.commit()
    except Exception as e:
        print(f"Error linking files to entry: {e}")
    finally:
        conn.close()

def delete_entry_record(entry_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Delete embeddings
        cursor.execute("SELECT rowid FROM entry_embeddings WHERE entry_id = ?", (entry_id,))
        rows = cursor.fetchall()
        for row in rows:
            cursor.execute("DELETE FROM vec_items WHERE rowid = ?", (row['rowid'],))
        cursor.execute("DELETE FROM entry_embeddings WHERE entry_id = ?", (entry_id,))
        
        # Delete file links
        cursor.execute("DELETE FROM entry_files WHERE entry_id = ?", (entry_id,))
        
        # Delete entry
        cursor.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        
        conn.commit()
        return True
    except Exception as e:
        print(f"Error deleting entry {entry_id}: {e}")
        return False
    finally:
        conn.close()

def save_entity(entity: dict):
    """
    Save an entity to the knowledge graph.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Check if exists (by name and type) or ID
        # If ID provided, use it. If not, check name.
        if not entity.get('id'):
            # Try to find by name
            cursor.execute("SELECT id, mention_count FROM entities WHERE name = ? AND type = ?", (entity['name'], entity['type']))
            existing = cursor.fetchone()
            if existing:
                # Update count
                new_count = (existing['mention_count'] or 1) + 1
                cursor.execute("UPDATE entities SET mention_count = ? WHERE id = ?", (new_count, existing['id']))
                entity['id'] = existing['id'] # Return existing ID
            else:
                # Insert new
                import uuid
                new_id = str(uuid.uuid4())
                cursor.execute("""
                    INSERT INTO entities (id, name, type, first_seen, mention_count, metadata)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    new_id, entity['name'], entity['type'], 
                    entity.get('first_seen', datetime.now()), 
                    1, 
                    json.dumps(entity.get('metadata', {}))
                ))
                entity['id'] = new_id
        else:
            # Update existing by ID
             cursor.execute("""
                INSERT OR REPLACE INTO entities (id, name, type, first_seen, mention_count, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                entity['id'], entity['name'], entity['type'], 
                entity.get('first_seen', datetime.now()), 
                entity.get('mention_count', 1), 
                json.dumps(entity.get('metadata', {}))
            ))
            
        conn.commit()
        return entity['id']
    except Exception as e:
        print(f"Error saving entity {entity.get('name')}: {e}")
        return None
    finally:
        conn.close()

def save_relation(relation: dict):
    """
    Save a relation between entities.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Check if exists
        cursor.execute("""
            SELECT rowid FROM relations 
            WHERE source = ? AND target = ? AND type = ?
        """, (relation['source'], relation['target'], relation['type']))
        
        if not cursor.fetchone():
            cursor.execute("""
                INSERT INTO relations (source, target, type, file_id, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                relation['source'], relation['target'], relation['type'],
                relation.get('file_id'), relation.get('confidence', 1.0),
                relation.get('created_at', datetime.now())
            ))
            conn.commit()
        return True
    except Exception as e:
        print(f"Error saving relation: {e}")
        return False
    finally:
        conn.close()

def get_entities_by_name(name: str):
    """
    Search entities by name (partial match).
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM entities WHERE name LIKE ?", (f"%{name}%",))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_entity_relations(entity_id: str):
    """
    Get all relations for an entity (source or target).
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT r.*, e1.name as source_name, e2.name as target_name 
            FROM relations r
            JOIN entities e1 ON r.source = e1.id
            JOIN entities e2 ON r.target = e2.id
            WHERE r.source = ? OR r.target = ?
        """, (entity_id, entity_id))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

