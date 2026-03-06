"""
aliyun_oss.py — Aliyun OSS file upload/download/delete.
Used as intermediate storage for MinerU and ASR APIs.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..utils.logger import get_module_logger

logger = get_module_logger(__name__)


def _get_bucket():
    try:
        import oss2
        from personal_brain.config import (
            ALIYUN_ACCESS_KEY_ID,
            ALIYUN_ACCESS_KEY_SECRET,
            ALIYUN_OSS_ENDPOINT,
            ALIYUN_OSS_BUCKET,
        )
        auth = oss2.Auth(ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET)
        return oss2.Bucket(auth, f"https://{ALIYUN_OSS_ENDPOINT}", ALIYUN_OSS_BUCKET)
    except ImportError:
        raise RuntimeError("oss2 package not installed. Run: pip install oss2")


def upload_file(local_path: Path, oss_key: str, signed_url_expires: int = 3600) -> str:
    """Upload a file to OSS and return a signed URL (valid for *signed_url_expires* seconds)."""
    bucket = _get_bucket()
    with local_path.open("rb") as f:
        bucket.put_object(oss_key, f)
    url = bucket.sign_url("GET", oss_key, signed_url_expires)
    logger.info("OSS upload complete", extra={"oss_key": oss_key, "url": url})
    return url


def download_file(oss_key: str, local_path: Path) -> None:
    """Download a file from OSS to local path."""
    bucket = _get_bucket()
    bucket.get_object_to_file(oss_key, str(local_path))


def delete_file(oss_key: str) -> None:
    """Delete a file from OSS."""
    bucket = _get_bucket()
    bucket.delete_object(oss_key)
    logger.debug("OSS delete", extra={"oss_key": oss_key})
