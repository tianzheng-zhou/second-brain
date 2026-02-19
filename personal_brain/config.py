import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Default paths
DEFAULT_HOME = Path.home() / "personal_brain_data"
DB_NAME = "brain.db"

# Environment variables can override defaults
STORAGE_PATH = Path(os.getenv("PB_STORAGE_PATH", DEFAULT_HOME))
DB_PATH = Path(os.getenv("PB_DB_PATH", STORAGE_PATH / DB_NAME))

# DashScope Configuration
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

# Models
EMBEDDING_MODEL = "qwen3-vl-embedding"
EMBEDDING_DIMENSION = 2560
RERANK_MODEL = "qwen3-vl-rerank"
VISION_MODEL = "qwen3-vl-plus"
CHAT_MODEL = "qwen3-max"

# Ensure directories exist
def ensure_dirs():
    if not STORAGE_PATH.exists():
        STORAGE_PATH.mkdir(parents=True, exist_ok=True)
