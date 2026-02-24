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

# Aliyun OSS Configuration
ALIYUN_ACCESS_KEY_ID = os.getenv("ALIYUN_ACCESS_KEY_ID")
ALIYUN_ACCESS_KEY_SECRET = os.getenv("ALIYUN_ACCESS_KEY_SECRET")
ALIYUN_OSS_ENDPOINT = os.getenv("ALIYUN_OSS_ENDPOINT", "oss-cn-hangzhou.aliyuncs.com")
ALIYUN_OSS_BUCKET = os.getenv("ALIYUN_OSS_BUCKET")

# MinerU Configuration
MINERU_API_TOKEN = os.getenv("MINERU_API_TOKEN")
MINERU_BASE_URL = os.getenv("MINERU_BASE_URL", "https://mineru.net/api/v4")
MINERU_USE_SYSTEM_PROXY = os.getenv("MINERU_USE_SYSTEM_PROXY", "true").lower() == "true"

# Agent Settings
DELETE_CONFIRMATION = os.getenv("DELETE_CONFIRMATION", "true").lower() == "true"

# Ensure directories exist
def ensure_dirs():
    if not STORAGE_PATH.exists():
        STORAGE_PATH.mkdir(parents=True, exist_ok=True)
