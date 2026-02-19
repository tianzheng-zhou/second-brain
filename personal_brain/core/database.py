import sqlite3
import struct
import json
from pathlib import Path
from personal_brain.config import DB_PATH, EMBEDDING_DIMENSION

def get_db_connection():
    """
    Get a connection to the SQLite database.
    Tries to load sqlite-vec extension if available.
    """
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS file_embeddings (
            rowid INTEGER PRIMARY KEY,
            file_id TEXT,
            FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
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
    
    conn.commit()
    conn.close()
    # print(f"Database initialized at {DB_PATH}")

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

if __name__ == "__main__":
    init_db()
