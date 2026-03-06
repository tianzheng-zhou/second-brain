"""
logger.py — Structured JSON logging.
Outputs JSON to stderr (INFO+) and a rolling file.
"""
import json
import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

# LogRecord built-in attribute names that cannot be overwritten via extra={}
_LOGRECORD_ATTRS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname",
    "filename", "module", "exc_info", "exc_text", "stack_info",
    "lineno", "funcName", "created", "msecs", "relativeCreated",
    "thread", "threadName", "processName", "process", "message",
    "taskName",
})


class _SafeLogger(logging.Logger):
    """Logger that prefixes conflicting extra keys with 'x_' to avoid LogRecord clashes."""

    def makeRecord(self, name, level, fn, lno, msg, args, exc_info,
                   func=None, extra=None, sinfo=None):
        if extra:
            safe_extra = {}
            for k, v in extra.items():
                if k in _LOGRECORD_ATTRS:
                    safe_extra[f"x_{k}"] = v
                else:
                    safe_extra[k] = v
            extra = safe_extra
        return super().makeRecord(name, level, fn, lno, msg, args, exc_info, func, extra, sinfo)


logging.setLoggerClass(_SafeLogger)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "module": record.module,
            "event": record.getMessage(),
        }
        if record.exc_info:
            data["exc_info"] = self.formatException(record.exc_info)
        # Merge any extra fields passed via extra={...}
        for key, val in record.__dict__.items():
            if key not in _LOGRECORD_ATTRS and not key.startswith("_"):
                data[key] = val
        return json.dumps(data, ensure_ascii=False, default=str)


def get_logger(name: str, storage_path: Path | None = None) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    formatter = _JsonFormatter()

    # Console handler (INFO+)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (DEBUG+, rolling daily, 7 days)
    if storage_path is not None:
        log_dir = storage_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            log_dir / "pb.log",
            when="midnight",
            backupCount=7,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger


def get_module_logger(name: str) -> logging.Logger:
    from personal_brain.config import STORAGE_PATH
    return get_logger(name, STORAGE_PATH)
