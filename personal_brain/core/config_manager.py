import json
import os
from pathlib import Path
from typing import Dict, Any

from personal_brain.config import STORAGE_PATH

CONFIG_FILE_NAME = "model_config.json"
CONFIG_PATH = STORAGE_PATH / CONFIG_FILE_NAME

DEFAULT_CONFIG = {
    "chat_model": "qwen-plus",
    "ai_search_model": "qwen-plus",
    "vision_model": "qwen3-vl-plus",
    "embedding_model": "qwen3-vl-embedding",
    "rerank_model": "qwen3-vl-rerank",
    "embedding_batch_size": 2,
}

class ConfigManager:
    _instance = None
    _config: Dict[str, Any] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
            cls._instance._load_config()
        return cls._instance

    def _load_config(self):
        """Load configuration from JSON file or use defaults."""
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    saved_config = json.load(f)
                    # Merge with defaults to ensure all keys exist
                    self._config = {**DEFAULT_CONFIG, **saved_config}
            except Exception as e:
                print(f"Error loading config: {e}. Using defaults.")
                self._config = DEFAULT_CONFIG.copy()
        else:
            self._config = DEFAULT_CONFIG.copy()
            self._save_config()

    def _save_config(self):
        """Save current configuration to JSON file."""
        try:
            # Ensure storage path exists
            if not STORAGE_PATH.exists():
                STORAGE_PATH.mkdir(parents=True, exist_ok=True)
                
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving config: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        return self._config.get(key, default)

    def set(self, key: str, value: Any):
        """Set a configuration value and save."""
        self._config[key] = value
        self._save_config()

    def get_all(self) -> Dict[str, Any]:
        """Get all configuration values."""
        return self._config.copy()

# Global instance
config_manager = ConfigManager()
