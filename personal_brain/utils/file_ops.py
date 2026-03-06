"""
file_ops.py — File type detection, SHA256 hashing, storage directory organization.
"""
from __future__ import annotations

import hashlib
import shutil
from datetime import datetime
from pathlib import Path

# Extension → type mapping
_EXT_MAP: dict[str, str] = {
    # Images
    ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".webp": "image", ".gif": "image",
    # Audio
    ".mp3": "audio", ".wav": "audio", ".ogg": "audio", ".m4a": "audio",
    # PDF
    ".pdf": "pdf",
    # Text
    ".txt": "text", ".md": "text", ".markdown": "text",
    ".json": "text", ".csv": "text", ".py": "text",
    ".js": "text", ".html": "text", ".css": "text",
    ".yaml": "text", ".yml": "text", ".xml": "text",
}

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(_EXT_MAP.keys())


def detect_file_type(path: Path) -> str:
    """Return file type string based on extension."""
    return _EXT_MAP.get(path.suffix.lower(), "unknown")


def calculate_file_id(path: Path) -> str:
    """Compute SHA256[:16] of file content."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def organize_file(src: Path, storage_path: Path) -> Path:
    """
    Copy file to storage_path/YYYY-MM/filename.
    If a file with the same name but different content already exists,
    append a numeric suffix (_1, _2, ...).
    Returns destination path.
    """
    now = datetime.now()
    dest_dir = storage_path / now.strftime("%Y-%m")
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest = dest_dir / src.name
    counter = 0
    while dest.exists():
        counter += 1
        stem = src.stem
        suffix = src.suffix
        dest = dest_dir / f"{stem}_{counter}{suffix}"

    shutil.copy2(src, dest)
    return dest
