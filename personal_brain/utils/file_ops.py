import hashlib
import shutil
import mimetypes
from pathlib import Path
from datetime import datetime
from personal_brain.config import STORAGE_PATH
from personal_brain.core.models import FileType

def calculate_file_id(file_path: Path, chunk_size=4096) -> str:
    """Calculate SHA256 hash of a file and return first 16 chars."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            sha256.update(chunk)
    return sha256.hexdigest()[:16]

def get_file_type(file_path: Path) -> FileType:
    """Determine file type based on mime type."""
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        return FileType.UNKNOWN
    
    if mime_type.startswith("image/"):
        return FileType.IMAGE
    elif mime_type.startswith("audio/"):
        return FileType.AUDIO
    elif mime_type == "application/pdf":
        return FileType.PDF
    elif mime_type.startswith("text/") or mime_type in ["application/json"]:
        return FileType.TEXT
    else:
        return FileType.UNKNOWN

def organize_file(file_path: Path, file_id: str) -> Path:
    """
    Copy file to STORAGE_PATH/YYYY-MM/filename.
    Returns the new absolute path.
    """
    # Create YYYY-MM folder
    now = datetime.now()
    folder_name = now.strftime("%Y-%m")
    dest_dir = STORAGE_PATH / folder_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    dest_path = dest_dir / file_path.name
    
    # Handle filename collision
    if dest_path.exists():
        # If content is same (hash check), just return existing path
        # But wait, checking hash of existing file is expensive if large.
        # We rely on file_id which is hash based.
        # If user provides file_id, we can compare.
        existing_id = calculate_file_id(dest_path)
        if existing_id == file_id:
            return dest_path
        
        # Different content but same name, rename
        base = dest_path.stem
        ext = dest_path.suffix
        counter = 1
        while dest_path.exists():
            dest_path = dest_dir / f"{base}_{counter}{ext}"
            counter += 1
            
    shutil.copy2(file_path, dest_path)
    return dest_path
