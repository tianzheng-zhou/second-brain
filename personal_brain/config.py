import os
from pathlib import Path

# Default paths
DEFAULT_HOME = Path.home() / "personal_brain_data"
DB_NAME = "brain.db"

# Environment variables can override defaults
STORAGE_PATH = Path(os.getenv("PB_STORAGE_PATH", DEFAULT_HOME))
DB_PATH = Path(os.getenv("PB_DB_PATH", STORAGE_PATH / DB_NAME))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# Embedding model
EMBEDDING_MODEL = "nomic-embed-text"
VISION_MODEL = "llama3.2-vision"

# Ensure directories exist
def ensure_dirs():
    if not STORAGE_PATH.exists():
        STORAGE_PATH.mkdir(parents=True, exist_ok=True)
