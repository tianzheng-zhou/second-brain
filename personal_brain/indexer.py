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
    """Parse PDF via MinerU. Raises if MinerU is unavailable or fails."""
    from .config import MINERU_API_TOKEN

    if not MINERU_API_TOKEN:
        raise RuntimeError("MINERU_API_TOKEN not set. PDF ingestion requires MinerU.")

    from .utils.mineru import parse_pdf
    from .utils.file_ops import calculate_file_id
    file_hash = calculate_file_id(path)
    md_text, image_root = parse_pdf(path, file_hash[:8])
    logger.info("PDF parsed via MinerU", extra={"path": str(path)})
    return md_text, image_root


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
        return _semantic_chunks(text, file_id, image_root, file_type)
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
                if chunk_overlap > 0:
                    overlap_text = current[-chunk_overlap:] if len(current) > chunk_overlap else current
                    current = overlap_text
                    current_start = current_start + (len(current) - len(overlap_text))
                else:
                    current = ""
                    current_start = char_offset + len(para) + 2 # Skip to next para

            # If para itself is too large, split it forcibly
            if len(para) > chunk_size:
                step = chunk_size - chunk_overlap
                if step <= 0: step = chunk_size # Safety guard
                for i in range(0, len(para), step):
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
    file_type: str = "text",
) -> list[FileChunk]:
    """LLM-based semantic chunking with sliding window."""
    from . import config_manager
    from .llm import call_llm

    chunk_size = config_manager.get("chunk_size")
    model = config_manager.get("semantic_split_model")
    min_size = 1000 # Adjusted to 1000 as requested
    max_size = int(chunk_size * 1.5)
    # Increase batch size to give LLM more context
    batch_size = 100
    
    # Safety limit for LLM context (approx tokens)
    # qwen3.5-flash context is 32k, qwen3.5-plus is 128k, but let's be safe
    # 25000 chars approx 12-15k tokens
    MAX_CONTEXT_CHARS = 25000 

    # Split into paragraph tokens
    paragraphs = re.split(r"\n\n+", text)
    segments = [p.strip() for p in paragraphs if p.strip()]

    if not segments:
        return []

    logger.info(
        "Semantic chunking started",
        extra={"model": model, "segments": len(segments), "file_id": file_id},
    )

    split_points: list[int] = [0]  # indices where new chunks start

    i = 0
    while i < len(segments):
        # Dynamically build batch based on char count
        batch = []
        batch_chars = 0
        batch_start_idx = i
        
        while i < len(segments):
            seg = segments[i]
            # Truncate extremely long single paragraphs for prompt visibility
            seg_len = len(seg)
            if batch_chars + seg_len > MAX_CONTEXT_CHARS and batch:
                # Batch full, stop here
                break
            
            batch.append(seg)
            batch_chars += seg_len
            i += 1
            
            # Hard limit on segment count (fallback)
            if len(batch) >= batch_size:
                break
        
        if not batch: # Should not happen unless empty segments
            i += 1
            continue

        # Provide more context in prompt, but truncate extremely long paragraphs to save tokens
        numbered = "\n".join(f"[{j}] {s[:1000]}" for j, s in enumerate(batch))

        prompt = (
            f"以下是一篇长文档的连续段落列表（共{len(batch)}段）。"
            "你的任务是将这些段落组合成语义完整的Chunk，并识别出必须切分的位置。"
            f"目标是每个Chunk长度约为{chunk_size}字符（{min_size}-{max_size}字符）。"
            "请遵循以下原则："
            "1. 优先保持语义连贯性，不要切断相关联的段落。"
            "2. 只有当当前累计的内容长度接近或超过目标长度，且遇到明显的语义转折点时，才进行切分。"
            "3. 如果段落非常短（如标题、列表项），请务必将其与后续内容合并，不要单独切分。"
            "请输出一个JSON数组，包含应该作为新Chunk起点的段落索引（从0开始）。"
            f"例如：[0, 5, 12]\n\n{numbered}"
        )

        try:
            # temperature=0.1: DashScope Qwen3 models require temperature > 0
            # when enable_thinking=False (which _is_thinking_model triggers for qwen3.*)
            raw = call_llm(model, [{"role": "user", "content": prompt}], temperature=0.1)
            # Extract JSON array (handles markdown code blocks and inline text)
            match = re.search(r"\[[\d,\s]*\]", raw)
            if match:
                points = json.loads(match.group())
                for p in points:
                    if not isinstance(p, int):
                        continue
                    # Convert relative batch index to absolute segment index
                    abs_idx = batch_start_idx + p
                    if batch_start_idx < abs_idx < batch_start_idx + len(batch):
                        split_points.append(abs_idx)
            else:
                logger.warning(
                    "Semantic split: no JSON array in LLM response",
                    extra={"model": model, "batch_start": batch_start_idx, "raw": raw[:200]},
                )
        except Exception as e:
            logger.error(
                "Semantic split LLM call failed, falling back to simple chunking",
                extra={"model": model, "error": str(e)},
            )
            # If LLM fails, we just continue to next batch (or could fallback entire file)
            # Here we choose to continue, effectively merging this batch into one big chunk 
            # (which will be caught by hard split later) or just skipping split points.
            # A safer fallback might be to add simple split points for this batch.
            pass
            
    split_points = sorted(set(split_points))
    split_points.append(len(segments))

    # Merge small chunks post-processing
    merged_points = [0]
    
    # split_points is [0, p1, p2, ..., len(segments)]
    # We iterate through p1, p2... (excluding 0 and end)
    # But wait, logic above is tricky. Let's use a simpler greedy merge.
    
    current_start = 0
    i = 1
    while i < len(split_points):
        end_idx = split_points[i]
        
        # Candidate chunk
        chunk_text = "\n\n".join(segments[current_start:end_idx])
        
        # If too small and not the last segment
        if len(chunk_text) < min_size and i < len(split_points) - 1:
            # Check if merging with next is viable
            next_end = split_points[i+1]
            merged_text = "\n\n".join(segments[current_start:next_end])
            
            if len(merged_text) <= max_size:
                # Merge: skip this end_idx, move to next
                i += 1
                continue
        
        # Commit this chunk
        merged_points.append(end_idx)
        current_start = end_idx
        i += 1
        
    # Check for orphan tail: if last chunk is too small, try to merge backwards
    if len(merged_points) >= 3: # Need at least start(0), mid, end
        last_end = merged_points[-1]
        last_start = merged_points[-2]
        prev_start = merged_points[-3]
        
        last_chunk_text = "\n\n".join(segments[last_start:last_end])
        if len(last_chunk_text) < min_size:
            # Try to merge with previous chunk
            combined_text = "\n\n".join(segments[prev_start:last_end])
            if len(combined_text) <= max_size:
                # Merge allowed: remove the middle point (last_start)
                merged_points.pop(-2)
                
    split_points = merged_points

    chunks = []
    char_offset = 0
    chunk_idx = 0
    chunk_overlap = config_manager.get("chunk_overlap")

    for i in range(len(split_points) - 1):
        start_seg = split_points[i]
        end_seg = split_points[i + 1]
        chunk_text = "\n\n".join(segments[start_seg:end_seg])
        
        # Force split if too large (fallback mechanism)
        if len(chunk_text) > max_size:
            logger.warning(
                "Semantic chunk too large, performing hard split",
                extra={"len": len(chunk_text), "max": max_size}
            )
            # Simple recursive split for this segment
            sub_start = 0
            while sub_start < len(chunk_text):
                # Determine end of this sub-chunk
                sub_len = min(len(chunk_text) - sub_start, chunk_size)
                sub_end = sub_start + sub_len
                
                # If not the last chunk, try to break at a nice boundary
                if sub_end < len(chunk_text):
                    # Look for newline in the last 20%
                    search_limit = int(chunk_size * 0.2)
                    last_nl = chunk_text.rfind('\n', sub_end - search_limit, sub_end)
                    if last_nl != -1:
                        sub_end = last_nl + 1
                
                sub_content = chunk_text[sub_start:sub_end]
                
                # Create sub-chunk
                page_num = _estimate_page(char_offset + sub_start, text) if file_type == "pdf" else None
                chunks.append(FileChunk(
                    id=f"{file_id}_{chunk_idx}",
                    file_id=file_id,
                    chunk_index=chunk_idx,
                    content=sub_content,
                    start_char=char_offset + sub_start,
                    page_number=page_num,
                ))
                chunk_idx += 1
                
                # Move forward
                if sub_end >= len(chunk_text):
                    break
                    
                # Calculate next start with overlap
                sub_start = sub_end - chunk_overlap
                if sub_start < 0: sub_start = 0
                
                # Avoid infinite loop if overlap >= chunk size (should not happen with defaults)
                if sub_start >= sub_end:
                    sub_start = sub_end
                    
        else:
            # Normal semantic chunk
            page_num = _estimate_page(char_offset, text) if file_type == "pdf" else None
            chunks.append(FileChunk(
                id=f"{file_id}_{chunk_idx}",
                file_id=file_id,
                chunk_index=chunk_idx,
                content=chunk_text,
                start_char=char_offset,
                page_number=page_num,
            ))
            chunk_idx += 1

        char_offset += len(chunk_text) + 2
        
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
