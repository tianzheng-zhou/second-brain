"""
config.py — Environment variable layer.
Loads credentials and paths from .env / environment variables.
Does NOT contain business logic.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL: str = os.getenv(
    "DASHSCOPE_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)

_storage_path_raw = os.getenv("PB_STORAGE_PATH", "~/personal_brain_data")
STORAGE_PATH: Path = Path(_storage_path_raw).expanduser().resolve()

PB_DB_PATH: Path = Path(
    os.getenv("PB_DB_PATH", str(STORAGE_PATH / "brain.db"))
).expanduser().resolve()

ALIYUN_ACCESS_KEY_ID: str = os.getenv("ALIYUN_ACCESS_KEY_ID", "")
ALIYUN_ACCESS_KEY_SECRET: str = os.getenv("ALIYUN_ACCESS_KEY_SECRET", "")
ALIYUN_OSS_ENDPOINT: str = os.getenv("ALIYUN_OSS_ENDPOINT", "oss-cn-hangzhou.aliyuncs.com")
ALIYUN_OSS_BUCKET: str = os.getenv("ALIYUN_OSS_BUCKET", "")

MINERU_API_TOKEN: str = os.getenv("MINERU_API_TOKEN", "")
MINERU_BASE_URL: str = os.getenv("MINERU_BASE_URL", "https://mineru.net/api/v4")
MINERU_USE_SYSTEM_PROXY: bool = os.getenv("MINERU_USE_SYSTEM_PROXY", "true").lower() == "true"

DELETE_CONFIRMATION: bool = os.getenv("DELETE_CONFIRMATION", "true").lower() == "true"
