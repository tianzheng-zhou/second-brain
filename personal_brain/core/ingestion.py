import os
from pathlib import Path
from datetime import datetime
from personal_brain.core.models import File, FileType, FileStatus
from personal_brain.utils.file_ops import calculate_file_id, get_file_type, organize_file
from personal_brain.core.indexer import extract_text, generate_embedding_chunks, generate_embedding
from personal_brain.core.cleaner import calculate_trash_score
from personal_brain.core.database import save_file, save_chunks, get_file, save_embedding, get_db_connection

def process_file(file_path: Path):
    """Process a single file."""
    print(f"Processing {file_path}...")
    
    try:
        # 1. Calculate ID
        file_id = calculate_file_id(file_path)
        
        # 2. Check if exists
        existing = get_file(file_id)
        if existing:
            print(f"File {file_path.name} already exists (ID: {file_id}). Skipping.")
            return
        
        # 3. Organize file (Move/Copy to storage)
        stored_path = organize_file(file_path, file_id)
        
        # 4. Create File object
        file_type = get_file_type(stored_path)
        stat = stored_path.stat()
        
        file_obj = File(
            id=file_id,
            path=str(stored_path),
            filename=stored_path.name,
            type=file_type,
            size_bytes=stat.st_size,
            created_at=datetime.fromtimestamp(stat.st_ctime),
            last_accessed=datetime.fromtimestamp(stat.st_atime),
            status=FileStatus.ACTIVE
        )
        
        # 5. Extract text
        print(f"Extracting text from {file_obj.filename}...")
        text, image_root = extract_text(stored_path, file_type)
        file_obj.ocr_text = text
        
        # 6. Calculate trash score
        file_obj.trash_score = calculate_trash_score(file_obj)
        print(f"Trash score: {file_obj.trash_score}")
        
        # 7. Save file metadata
        save_file(file_obj)
        
        # 8. Generate and save embedding chunks
        # Only embed if it has text and is not absolute trash
        if text and file_obj.trash_score > 0.2:
            print("Generating embedding chunks...")
            chunks, embeddings = generate_embedding_chunks(text, image_root)
            if chunks and embeddings:
                save_chunks(file_id, chunks, embeddings)
                
        print(f"Ingestion complete for {file_obj.filename}")
        
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        # Mark as failed in DB if possible, or just log

def refresh_index_for_file(file_id: str):
    """
    Refresh the index for a specific file.
    Re-extracts text and regenerates embedding.
    """
    try:
        file_data = get_file(file_id)
        if not file_data:
            print(f"File {file_id} not found.")
            return False
            
        file_path = Path(file_data['path'])
        if not file_path.exists():
            print(f"File path {file_path} does not exist.")
            return False
            
        print(f"Refreshing index for {file_path.name}...")
        
        # 1. Re-extract text
        file_type = get_file_type(file_path)
        text, image_root = extract_text(file_path, file_type)
        
        # Check if text extraction failed
        if not text and file_type == FileType.PDF:
            print(f"Text extraction failed for {file_path.name}. Aborting refresh.")
            return False

        # 2. Update DB record with new text
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Calculate trash score (Dummy for now, or fetch logic)
        trash_score = 0.5 
        
        cursor.execute("UPDATE files SET ocr_text = ?, trash_score = ? WHERE id = ?", (text, trash_score, file_id))
        conn.commit()
        conn.close()
        
        # 3. Regenerate chunks and embeddings
        if text:
            print("Regenerating embedding chunks...")
            chunks, embeddings = generate_embedding_chunks(text, image_root)
            if chunks and embeddings:
                save_chunks(file_id, chunks, embeddings)
                
        print(f"Refresh complete for {file_path.name}")
        return True
        
    except Exception as e:
        print(f"Error refreshing file {file_id}: {e}")
        return False

def ingest_path(path_str: str):
    """Ingest a file or directory."""
    path = Path(path_str).resolve()
    if not path.exists():
        print(f"Path {path} does not exist.")
        return
        
    if path.is_file():
        process_file(path)
    elif path.is_dir():
        for root, _, files in os.walk(path):
            for file in files:
                file_path = Path(root) / file
                # Skip system files or hidden files
                if file.startswith('.'):
                    continue
                try:
                    process_file(file_path)
                except Exception as e:
                    print(f"Error processing {file_path}: {e}")
