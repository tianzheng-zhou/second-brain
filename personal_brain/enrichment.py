"""
enrichment.py — Auto summary and tag extraction for ingested files.
Creates an Entry (auto_enrichment) and generates its embedding.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

from . import config_manager
from . import database as db
from .llm import call_llm, generate_embedding
from .models import Entry, FileChunk, FileInfo
from .utils.logger import get_module_logger

logger = get_module_logger(__name__)

_SUMMARY_PROMPT = (
    "请为以下文档内容生成一个简洁的中文摘要（200-400字），"
    "涵盖主要内容、关键信息和核心观点。\n\n"
    "文档名称：{filename}\n"
    "文档类型：{file_type}\n\n"
    "文档内容：\n{content}"
)

_TAG_PROMPT = (
    "根据以下摘要，提取3-5个最相关的标签（关键词或短语），"
    "以JSON数组形式返回，例如：[\"标签1\", \"标签2\", \"标签3\"]\n\n"
    "摘要：\n{summary}"
)

_EVENT_TIME_PROMPT = (
    "请分析以下文档摘要，提取文档内容所描述的事件或内容发生的时间范围。\n"
    "注意：提取的是内容涉及的时间，而非文档的创建/上传时间。\n\n"
    "规则：\n"
    "1. 如果时间明确，使用精确日期（YYYY-MM-DD）\n"
    "2. 如果时间模糊（如「年初」、「大约」、「某年夏天」），给出合理范围\n"
    "3. 如果完全无法确定，返回 null\n"
    "4. 只输出 JSON 对象或 null，不要其他文字：\n"
    "   {{\"raw\": \"原始时间描述\", \"start\": \"YYYY-MM-DD\", \"end\": \"YYYY-MM-DD\", "
    "\"precision\": \"day|month|quarter|year|fuzzy\"}}\n\n"
    "示例：\n"
    "- 内容发生在2024年3月15日 → {{\"raw\": \"2024年3月15日\", \"start\": \"2024-03-15\", \"end\": \"2024-03-15\", \"precision\": \"day\"}}\n"
    "- 内容描述2023年全年 → {{\"raw\": \"2023年\", \"start\": \"2023-01-01\", \"end\": \"2023-12-31\", \"precision\": \"year\"}}\n"
    "- 内容描述2024年上半年 → {{\"raw\": \"2024年上半年\", \"start\": \"2024-01-01\", \"end\": \"2024-06-30\", \"precision\": \"quarter\"}}\n"
    "- 内容描述「大约2022年底」 → {{\"raw\": \"大约2022年底\", \"start\": \"2022-09-01\", \"end\": \"2023-03-31\", \"precision\": \"fuzzy\"}}\n"
    "- 时间完全不确定 → null\n\n"
    "文档摘要：\n{summary}"
)

# Token estimation constants
_CJK_CHARS_PER_TOKEN = 1.2
_OTHER_CHARS_PER_TOKEN = 0.35
_IMAGE_TOKENS = 1000
_TOKEN_THRESHOLD = 20000
_MAX_REPRESENTATIVE_CHUNKS = 10


def _estimate_tokens(text: str) -> int:
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff" or "\u3000" <= c <= "\u303f")
    other = len(text) - cjk
    return int(cjk / _CJK_CHARS_PER_TOKEN + other / _OTHER_CHARS_PER_TOKEN)


def _build_content_for_summary(text: str, chunks: list[FileChunk]) -> str:
    """Return content to feed to LLM for summarization."""
    tokens = _estimate_tokens(text)
    if tokens <= _TOKEN_THRESHOLD:
        return text

    # Large document: select representative chunks
    if not chunks:
        return text[:8000]

    selected: list[FileChunk] = []
    if len(chunks) <= _MAX_REPRESENTATIVE_CHUNKS:
        selected = chunks
    else:
        # First + last + evenly spaced middle
        step = max(1, (len(chunks) - 2) // (_MAX_REPRESENTATIVE_CHUNKS - 2))
        selected = [chunks[0]]
        i = step
        while i < len(chunks) - 1 and len(selected) < _MAX_REPRESENTATIVE_CHUNKS - 1:
            selected.append(chunks[i])
            i += step
        selected.append(chunks[-1])

    return "\n\n...\n\n".join(c.content for c in selected)


def _extract_event_time(summary: str, model: str) -> dict | None:
    """Extract event time from document summary using LLM. Returns dict or None."""
    prompt = _EVENT_TIME_PROMPT.format(summary=summary)
    try:
        response = call_llm(model, [{"role": "user", "content": prompt}], temperature=0)
        response = response.strip()
        if not response or response.lower() == "null":
            return None
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if match:
            data = json.loads(match.group())
            if data.get("start") and data.get("end"):
                return data
    except Exception as e:
        logger.warning("Event time extraction failed", extra={"error": str(e)})
    return None


def enrich_file(
    file_obj: FileInfo,
    text: str,
    chunks: list[FileChunk],
) -> None:
    """
    Generate summary and tags for a file.
    Saves result as an auto_enrichment Entry linked to the file.
    Updates enrichment_status in files table.
    """
    model = config_manager.get("enrichment_model")

    try:
        content_for_summary = _build_content_for_summary(text, chunks)

        # Generate summary
        summary_prompt = _SUMMARY_PROMPT.format(
            filename=file_obj.filename,
            file_type=file_obj.type,
            content=content_for_summary,
        )
        summary = call_llm(model, [{"role": "user", "content": summary_prompt}])
        logger.debug("Summary generated", extra={"file_id": file_obj.id, "length": len(summary)})

        # Extract tags from summary
        tag_prompt = _TAG_PROMPT.format(summary=summary)
        tag_response = call_llm(model, [{"role": "user", "content": tag_prompt}], temperature=0)
        tags = _parse_tags(tag_response)

        # Extract event time from document content
        event_time = _extract_event_time(summary, model)
        metadata = {"event_time": event_time} if event_time else None

        # Generate embedding for the entry
        embedding = generate_embedding(summary)

        # Create entry
        entry = Entry(
            id=str(uuid.uuid4()),
            content_text=summary,
            metadata=metadata,
            created_at=datetime.utcnow(),
            source="auto_enrichment",
            tags=tags,
            status="active",
        )

        db.save_entry(entry, embedding)
        db.link_entry_file(entry.id, file_obj.id)
        db.update_file_enrichment_status(file_obj.id, "completed")

        logger.info(
            "Enrichment completed",
            extra={"file_id": file_obj.id, "entry_id": entry.id, "tags": tags},
        )

    except Exception as e:
        db.update_file_enrichment_status(file_obj.id, "failed")
        logger.error(
            "Enrichment failed",
            extra={"file_id": file_obj.id, "error": str(e)},
        )
        raise


def _parse_tags(response: str) -> list[str]:
    """Extract JSON array of tags from LLM response."""
    match = re.search(r"\[.*?\]", response, re.DOTALL)
    if match:
        try:
            tags = json.loads(match.group())
            return [str(t).strip() for t in tags if t][:5]
        except Exception:
            pass

    # Fallback: split by common delimiters
    clean = re.sub(r"[「」【】\[\]\"'`]", "", response)
    parts = re.split(r"[,，、\n]", clean)
    return [p.strip() for p in parts if p.strip()][:5]
