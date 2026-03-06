"""
indexer.py — Text extraction and embedding generation.
Handles PDF (MinerU + fallback), images, audio, and text files.
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Optional

from . import config_manager
from .config import STORAGE_PATH
from .llm import call_vision, generate_embedding, generate_embeddings_batch
from .models import FileChunk
from .utils.logger import get_module_logger

logger = get_module_logger(__name__)

_IMAGE_PROMPT = (
    "请对这张图片进行详细分析：\n"
    "1. 提取图片中所有文字（OCR）\n"
    "2. 描述图片内容（场景、物体、图表含义等）\n"
    "输出为结构化文本，先列出文字内容，再描述图片。"
)


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text(path: Path, file_type: str) -> tuple[str, Optional[Path]]:
    """
    Extract text from file.
    Returns (text, image_root_path).
    image_root_path is only set for PDF files parsed by MinerU.
    """
    if file_type == "pdf":
        return _extract_pdf(path)
    elif file_type == "image":
        return _extract_image(path), None
    elif file_type == "audio":
        return _extract_audio(path), None
    elif file_type in ("text", "unknown"):
        if file_type == "unknown":
            logger.warning("Unknown file type, attempting text read", extra={"path": str(path)})
        try:
            return path.read_text(encoding="utf-8", errors="replace"), None
        except Exception as e:
            logger.warning("Text read failed", extra={"path": str(path), "error": str(e)})
            return "", None
    else:
        logger.warning("Unsupported file type for text extraction", extra={"type": file_type})
        return "", None


def _extract_pdf(path: Path) -> tuple[str, Optional[Path]]:
    """Try MinerU first, fallback to Pillow + Vision OCR."""
    from .config import MINERU_API_TOKEN

    if MINERU_API_TOKEN:
        try:
            from .utils.mineru import parse_pdf
            from .utils.file_ops import calculate_file_id
            file_hash = calculate_file_id(path)
            md_text, image_root = parse_pdf(path, file_hash[:8])
            logger.info("PDF parsed via MinerU", extra={"path": str(path)})
            return md_text, image_root
        except Exception as e:
            logger.warning("MinerU failed, falling back to local OCR", extra={"error": str(e)})

    return _extract_pdf_local(path), None


def _extract_pdf_local(path: Path) -> str:
    """Convert PDF pages to images and OCR with vision model."""
    try:
        from PIL import Image
        import io
    except ImportError:
        raise RuntimeError("Pillow not installed. Run: pip install Pillow")

    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(path))
        texts = []
        model = config_manager.get("vision_model")
        for page_num, page in enumerate(doc):
            pix = page.get_pixmap(dpi=150)
            img_path = STORAGE_PATH / "tmp" / f"_pdf_page_{page_num}.png"
            img_path.parent.mkdir(parents=True, exist_ok=True)
            pix.save(str(img_path))
            try:
                page_text = call_vision(model, _IMAGE_PROMPT, [img_path])
                texts.append(f"[Page {page_num + 1}]\n{page_text}")
            finally:
                if img_path.exists():
                    img_path.unlink()
        doc.close()
        return "\n\n".join(texts)
    except ImportError:
        logger.warning("PyMuPDF not installed, PDF text extraction limited")
        return ""


def _extract_image(path: Path) -> str:
    """Use vision model to OCR and describe an image."""
    model = config_manager.get("vision_model")
    return call_vision(model, _IMAGE_PROMPT, [path])


def _extract_audio(path: Path) -> str:
    """Transcribe audio via DashScope ASR."""
    from .utils.asr_client import transcribe_audio
    return transcribe_audio(path)


# ---------------------------------------------------------------------------
# Chunking + Embedding
# ---------------------------------------------------------------------------

def generate_embedding_chunks(
    text: str,
    file_id: str,
    file_type: str,
    image_root: Optional[Path] = None,
) -> list[FileChunk]:
    """
    Split text into chunks with position tracking.
    Returns list of FileChunk objects (without embeddings).
    """
    use_semantic = config_manager.get("use_semantic_split")
    if use_semantic:
        return _semantic_chunks(text, file_id, image_root)
    return _simple_chunks(text, file_id, file_type)


def _simple_chunks(text: str, file_id: str, file_type: str) -> list[FileChunk]:
    """Fixed-size chunking with overlap, respecting paragraph boundaries."""
    chunk_size = config_manager.get("chunk_size")
    chunk_overlap = config_manager.get("chunk_overlap")

    # Split on paragraph boundaries first
    paragraphs = re.split(r"\n\n+", text)
    chunks: list[FileChunk] = []
    current = ""
    current_start = 0
    char_offset = 0
    chunk_idx = 0

    for para in paragraphs:
        if len(current) + len(para) + 2 <= chunk_size:
            if current:
                current += "\n\n"
            else:
                current_start = char_offset
            current += para
        else:
            if current:
                page_num = _estimate_page(current_start, text) if file_type == "pdf" else None
                chunks.append(FileChunk(
                    id=f"{file_id}_{chunk_idx}",
                    file_id=file_id,
                    chunk_index=chunk_idx,
                    content=current,
                    start_char=current_start,
                    page_number=page_num,
                ))
                chunk_idx += 1
                # Overlap: keep last chunk_overlap chars
                overlap_text = current[-chunk_overlap:] if len(current) > chunk_overlap else current
                current = overlap_text
                current_start = current_start + (len(current) - len(overlap_text))

            # If para itself is too large, split it forcibly
            if len(para) > chunk_size:
                for i in range(0, len(para), chunk_size - chunk_overlap):
                    sub = para[i:i + chunk_size]
                    page_num = _estimate_page(char_offset + i, text) if file_type == "pdf" else None
                    chunks.append(FileChunk(
                        id=f"{file_id}_{chunk_idx}",
                        file_id=file_id,
                        chunk_index=chunk_idx,
                        content=sub,
                        start_char=char_offset + i,
                        page_number=page_num,
                    ))
                    chunk_idx += 1
                current = ""
            else:
                current_start = char_offset
                current = para

        char_offset += len(para) + 2  # +2 for \n\n

    if current.strip():
        page_num = _estimate_page(current_start, text) if file_type == "pdf" else None
        chunks.append(FileChunk(
            id=f"{file_id}_{chunk_idx}",
            file_id=file_id,
            chunk_index=chunk_idx,
            content=current,
            start_char=current_start,
            page_number=page_num,
        ))

    return chunks


def _estimate_page(char_offset: int, text: str) -> Optional[int]:
    """Estimate PDF page number from [Page N] markers."""
    markers = list(re.finditer(r"\[Page (\d+)\]", text))
    page = None
    for m in markers:
        if m.start() <= char_offset:
            page = int(m.group(1))
        else:
            break
    return page


def _semantic_chunks(
    text: str,
    file_id: str,
    image_root: Optional[Path],
) -> list[FileChunk]:
    """LLM-based semantic chunking with sliding window."""
    from . import config_manager
    from .llm import call_llm

    chunk_size = config_manager.get("chunk_size")
    model = config_manager.get("semantic_split_model")
    min_size = int(chunk_size * 0.3)
    max_size = int(chunk_size * 1.5)
    batch_size = 30

    # Split into paragraph tokens
    paragraphs = re.split(r"\n\n+", text)
    segments = [p.strip() for p in paragraphs if p.strip()]

    if not segments:
        return []

    split_points: list[int] = [0]  # indices where new chunks start

    for batch_start in range(0, len(segments), batch_size - 5):
        batch = segments[batch_start: batch_start + batch_size]
        numbered = "\n".join(f"[{i}] {s[:200]}" for i, s in enumerate(batch))

        prompt = (
            f"以下是文档的段落列表（共{len(batch)}段）。"
            "请识别语义分割点（即应该开始新chunk的段落索引），"
            f"要求每个chunk大约{chunk_size}字符（{min_size}-{max_size}字符范围内）。"
            "输出JSON数组，包含应开始新chunk的段落索引（从0开始）。"
            f"例如：[0, 5, 12]\n\n{numbered}"
        )

        try:
            raw = call_llm(model, [{"role": "user", "content": prompt}], temperature=0)
            # Extract JSON array
            match = re.search(r"\[[\d,\s]+\]", raw)
            if match:
                points = json.loads(match.group())
                for p in points:
                    abs_idx = batch_start + p
                    if 0 < abs_idx < len(segments):
                        split_points.append(abs_idx)
        except Exception as e:
            logger.warning("Semantic split failed, using simple split", extra={"error": str(e)})
            return _simple_chunks(text, file_id, "text")

    split_points = sorted(set(split_points))
    split_points.append(len(segments))

    chunks = []
    char_offset = 0
    chunk_idx = 0

    for i in range(len(split_points) - 1):
        start_seg = split_points[i]
        end_seg = split_points[i + 1]
        chunk_text = "\n\n".join(segments[start_seg:end_seg])
        # Calculate approximate char offset
        chunks.append(FileChunk(
            id=f"{file_id}_{chunk_idx}",
            file_id=file_id,
            chunk_index=chunk_idx,
            content=chunk_text,
            start_char=char_offset,
            page_number=None,
        ))
        char_offset += len(chunk_text) + 2
        chunk_idx += 1

    return chunks


def embed_chunks(chunks: list[FileChunk]) -> list[list[float]]:
    """Generate embeddings for a list of chunks in batches."""
    batch_size = config_manager.get("embedding_batch_size")
    embeddings: list[list[float]] = []

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i: i + batch_size]
        texts = [c.content for c in batch]
        batch_embs = generate_embeddings_batch(texts)
        embeddings.extend(batch_embs)
        logger.debug(
            "Batch embedded",
            extra={"batch": f"{i}-{i+len(batch)}", "total": len(chunks)},
        )

    return embeddings


def save_processed_text(text: str, file_id: str) -> Path:
    """Save processed text to {STORAGE_PATH}/processed/{file_id}.md"""
    processed_dir = STORAGE_PATH / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    out_path = processed_dir / f"{file_id}.md"
    out_path.write_text(text, encoding="utf-8")
    return out_path
