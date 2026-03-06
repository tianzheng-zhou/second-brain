"""
asr_client.py — Audio transcription via DashScope ASR API.
Flow: upload to OSS → submit ASR task → poll → return transcript → delete OSS file.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

import httpx

from ..config import DASHSCOPE_API_KEY
from ..utils.aliyun_oss import delete_file, upload_file
from ..utils.logger import get_module_logger

logger = get_module_logger(__name__)

_ASR_SUBMIT_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"
_POLL_INTERVAL = 5  # seconds
_MAX_WAIT = 600  # 10 minutes


def transcribe_audio(audio_path: Path) -> str:
    """
    Upload audio to OSS, submit DashScope ASR task, poll for result,
    clean up OSS file, and return transcript text.
    """
    oss_key = f"asr_tmp/{uuid.uuid4()}{audio_path.suffix}"
    oss_url = upload_file(audio_path, oss_key)

    try:
        task_id = _submit_asr_task(oss_url)
        transcript = _poll_asr_task(task_id)
        return transcript
    finally:
        try:
            delete_file(oss_key)
        except Exception as e:
            logger.warning("Failed to delete OSS temp file", extra={"oss_key": oss_key, "error": str(e)})


def _submit_asr_task(file_url: str) -> str:
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    payload = {
        "model": "paraformer-v2",
        "input": {"file_urls": [file_url]},
        "parameters": {"language_hints": ["zh", "en"]},
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(_ASR_SUBMIT_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    task_id = data.get("output", {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"ASR task submission failed: {data}")
    logger.info("ASR task submitted", extra={"task_id": task_id})
    return task_id


def _poll_asr_task(task_id: str) -> str:
    headers = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}"}
    url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
    elapsed = 0

    with httpx.Client(timeout=30) as client:
        while elapsed < _MAX_WAIT:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            status = data.get("output", {}).get("task_status", "")

            if status == "SUCCEEDED":
                results = data.get("output", {}).get("results", [])
                texts = [r.get("transcription_url", "") for r in results]
                # Fetch actual transcript text from each URL
                full_text = ""
                for turl in texts:
                    if turl:
                        tr = client.get(turl)
                        transcript_data = tr.json()
                        for item in transcript_data.get("transcripts", []):
                            full_text += item.get("transcript", "") + "\n"
                return full_text.strip()

            if status in ("FAILED", "CANCELLED"):
                raise RuntimeError(f"ASR task {task_id} failed with status: {status}")

            logger.debug("ASR polling", extra={"task_id": task_id, "status": status})
            time.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL

    raise TimeoutError(f"ASR task {task_id} timed out after {_MAX_WAIT}s")
