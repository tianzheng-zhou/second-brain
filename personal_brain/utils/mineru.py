"""
mineru.py — MinerU PDF parsing API client.
Flow: submit → poll → download ZIP → extract MD.
Includes local caching to avoid redundant API calls.
"""
from __future__ import annotations

import time
import uuid
import zipfile
from pathlib import Path

import httpx

from ..config import (
    ALIYUN_OSS_BUCKET,
    ALIYUN_OSS_ENDPOINT,
    MINERU_API_TOKEN,
    MINERU_BASE_URL,
    MINERU_USE_SYSTEM_PROXY,
    STORAGE_PATH,
)
from ..utils.aliyun_oss import delete_file, upload_file
from ..utils.logger import get_module_logger

logger = get_module_logger(__name__)

_POLL_INTERVAL = 10  # seconds
_MAX_WAIT = 1800  # 30 minutes


def _get_client() -> httpx.Client:
    proxies = None if MINERU_USE_SYSTEM_PROXY else {}
    return httpx.Client(
        timeout=60,
        headers={"Authorization": f"Bearer {MINERU_API_TOKEN}"},
        **({"proxies": proxies} if proxies is not None else {}),
    )


def parse_pdf(pdf_path: Path, file_hash_prefix: str) -> tuple[str, Path]:
    """
    Parse a PDF via MinerU API (with local caching).
    Returns (markdown_text, image_root_path).
    """
    cache_dir = STORAGE_PATH / "mineru_cache" / file_hash_prefix
    cache_md = cache_dir / "output.md"

    if cache_md.exists():
        logger.debug("MinerU cache hit", extra={"file_hash_prefix": file_hash_prefix})
        return cache_md.read_text(encoding="utf-8"), cache_dir

    cache_dir.mkdir(parents=True, exist_ok=True)

    # Upload PDF to OSS
    oss_key = f"mineru_tmp/{uuid.uuid4()}.pdf"
    oss_url = upload_file(pdf_path, oss_key)

    try:
        task_id = _submit_mineru_task(oss_url, pdf_path.name)
        zip_path = _poll_and_download(task_id, cache_dir)
        md_text = _extract_zip(zip_path, cache_dir)
        # Copy PDF to cache
        import shutil
        shutil.copy2(pdf_path, cache_dir / "original.pdf")
        return md_text, cache_dir
    finally:
        try:
            delete_file(oss_key)
        except Exception as e:
            logger.warning("Failed to delete MinerU OSS temp", extra={"oss_key": oss_key, "error": str(e)})


def _submit_mineru_task(oss_url: str, filename: str) -> str:
    payload = {
        "url": oss_url,
        "is_ocr": True,
        "enable_formula": True,
        "enable_table": True,
        "layout_model": "doclayout_yolo",
        "extra": {"filename": filename},
    }
    with _get_client() as client:
        resp = client.post(f"{MINERU_BASE_URL}/extract/task", json=payload)
        resp.raise_for_status()
        data = resp.json()

    task_id = data.get("data", {}).get("task_id") or data.get("task_id")
    if not task_id:
        raise RuntimeError(f"MinerU task submission failed: {data}")
    logger.info("MinerU task submitted", extra={"task_id": task_id})
    return task_id


def _poll_and_download(task_id: str, cache_dir: Path) -> Path:
    elapsed = 0
    with _get_client() as client:
        while elapsed < _MAX_WAIT:
            resp = client.get(f"{MINERU_BASE_URL}/extract/task/{task_id}")
            resp.raise_for_status()
            data = resp.json()
            state = (data.get("data") or data).get("state", "")

            if state == "done":
                zip_url = (data.get("data") or data).get("zip_url", "")
                if not zip_url:
                    raise RuntimeError("MinerU done but no zip_url")
                zip_path = cache_dir / "result.zip"
                zip_resp = client.get(zip_url)
                zip_path.write_bytes(zip_resp.content)
                logger.info("MinerU download complete", extra={"task_id": task_id})
                return zip_path

            if state == "failed":
                raise RuntimeError(f"MinerU task {task_id} failed")

            logger.debug("MinerU polling", extra={"task_id": task_id, "state": state})
            time.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL

    raise TimeoutError(f"MinerU task {task_id} timed out after {_MAX_WAIT}s")


def _extract_zip(zip_path: Path, cache_dir: Path) -> str:
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(cache_dir)

    # Find the .md file
    md_files = list(cache_dir.glob("**/*.md"))
    if not md_files:
        raise RuntimeError("No markdown file found in MinerU output ZIP")

    # Prefer the largest MD file (usually the main document)
    md_file = max(md_files, key=lambda p: p.stat().st_size)
    text = md_file.read_text(encoding="utf-8")

    # Save canonical copy
    canonical = cache_dir / "output.md"
    canonical.write_text(text, encoding="utf-8")
    return text
