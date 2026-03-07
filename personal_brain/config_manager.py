"""
config_manager.py — Business configuration layer.
Unified config entry: env vars > model_config.json > code defaults.
Manages model names, chunking params, and other mutable business config.
"""
import json
import os
from pathlib import Path
from typing import Any

from .config import STORAGE_PATH

_CONFIG_FILE: Path = STORAGE_PATH / "model_config.json"

_DEFAULTS: dict[str, Any] = {
    "embedding_model": "qwen3-vl-embedding",
    "embedding_dim": 2560,
    "rerank_model": "qwen3-vl-rerank",
    "vision_model": "kimi-k2.5",
    "enrichment_model": "kimi-k2.5",
    "embedding_batch_size": 6,
    "use_semantic_split": True,
    "semantic_split_model": "qwen3.5-flash",
    "chunk_size": 1500,
    "chunk_overlap": 0,
    "vec_impl": "aux_column",
}

_ENV_MAP: dict[str, str] = {
    "embedding_model": "PB_EMBEDDING_MODEL",
    "embedding_dim": "PB_EMBEDDING_DIM",
    "rerank_model": "PB_RERANK_MODEL",
    "vision_model": "PB_VISION_MODEL",
    "enrichment_model": "PB_ENRICHMENT_MODEL",
    "embedding_batch_size": "PB_EMBEDDING_BATCH_SIZE",
    "use_semantic_split": "PB_USE_SEMANTIC_SPLIT",
    "semantic_split_model": "PB_SEMANTIC_SPLIT_MODEL",
    "chunk_size": "PB_CHUNK_SIZE",
    "chunk_overlap": "PB_CHUNK_OVERLAP",
}

_EMBEDDING_DIM_MAP: dict[str, int] = {
    "qwen3-vl-embedding": 2560,
    "text-embedding-v3": 1024,
    "text-embedding-v2": 1536,
}


def _load_file() -> dict[str, Any]:
    if _CONFIG_FILE.exists():
        try:
            return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_file(data: dict[str, Any]) -> None:
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get(key: str) -> Any:
    """Get config value: env var > model_config.json > default."""
    env_key = _ENV_MAP.get(key)
    if env_key:
        env_val = os.getenv(env_key)
        if env_val is not None:
            default = _DEFAULTS.get(key)
            if isinstance(default, bool):
                return env_val.lower() in ("true", "1", "yes")
            if isinstance(default, int):
                return int(env_val)
            return env_val

    file_data = _load_file()
    if key in file_data:
        return file_data[key]

    return _DEFAULTS.get(key)


def set(key: str, value: Any) -> None:
    """Persist a config value to model_config.json."""
    data = _load_file()
    data[key] = value
    _save_file(data)


def get_all() -> dict[str, Any]:
    """Return merged config (defaults + file overrides)."""
    data = dict(_DEFAULTS)
    data.update(_load_file())
    return data


def get_embedding_dim_for_model(model_name: str) -> int:
    """Return embedding dimension for a given model name."""
    return _EMBEDDING_DIM_MAP.get(model_name, 2560)


def ensure_initialized() -> None:
    """Called by init_db to write vec_impl and embedding_dim after detection."""
    pass  # init_db handles writing via set()
