import os
import base64
import dashscope
import re
import hashlib
from pathlib import Path
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from personal_brain.config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    EMBEDDING_DIMENSION,
    STORAGE_PATH,
    USE_SEMANTIC_SPLIT,
    CHUNK_SIZE,
    CHUNK_OVERLAP
)
from personal_brain.core.config_manager import config_manager
from personal_brain.core.llm import call_llm
from personal_brain.core.models import FileType
from personal_brain.utils.aliyun_oss import AliyunOSS
from personal_brain.utils.mineru import MinerUClient
from personal_brain.utils.asr_client import ASRClient

# Semantic-aware text splitter using LLM
def semantic_text_splitter(text: str, image_root: Path = None, chunk_size: int = 1500, chunk_overlap: int = 200) -> list[str]:
    """
    Split text into semantically coherent chunks using LLM.

    NEW APPROACH: Instead of asking LLM to count characters (which is unreliable),
    we ask it to identify which paragraphs are good split boundaries based on
    semantic content and topic transitions.

    For documents with images (e.g., PDF), it preserves image boundaries and
    ensures images are kept with their relevant textual context.

    Chunk Size Range: Accepts chunks from 30% to 150% of target size to allow
    flexible, semantically meaningful boundaries.

    Note: Chunk overlap has been removed. Each chunk contains unique content.

    Args:
        text: Input text to split
        image_root: Path to image folder (for PDF/markdown with images)
        chunk_size: Target chunk size in characters
        chunk_overlap: Deprecated - overlap is no longer applied between chunks

    Returns:
        List of text chunks with semantic coherence (no overlap)
    """
    if not text or len(text) <= chunk_size:
        return [text.strip()] if text.strip() else []

    # For very short text, no need to split
    if len(text) < chunk_size * 0.8:
        return [text.strip()]

    try:
        # Check if there are images in the text (markdown image syntax)
        has_images = image_root is not None and re.search(r'!\[.*?\]\(.*?\)', text)

        if has_images:
            # Use multimodal prompt for documents with images
            return _semantic_split_with_images(text, image_root, chunk_size, chunk_overlap)
        else:
            # Use text-only prompt for plain text documents
            return _semantic_split_text_only(text, chunk_size, chunk_overlap)

    except Exception as e:
        # Fallback to recursive character splitter on any error
        print(f"  Semantic splitting failed ({e}), falling back to character-based splitting...")
        return recursive_character_text_splitter(text, chunk_size, chunk_overlap)


def _semantic_split_text_only(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """
    Semantic splitting for text-only documents.

    KEY INSIGHT: Instead of asking LLM to count characters (which it's bad at),
    we split text into semantic units (preserving tables, lists, code blocks),
    then ask LLM which units are good split boundaries.
    """

    # Step 1: Split text into semantic units (preserving structural elements)
    content_units, unit_boundaries = _split_into_semantic_units(text)

    # If too few units, semantic splitting cannot proceed
    if len(content_units) < 3:
        raise Exception(f"Text too short for semantic splitting: only {len(content_units)} units found")

    # Step 2: Ask LLM to identify which units are good split points
    unit_previews = []
    for i, u in enumerate(content_units):
        preview = u[:80].replace('\n', ' ')
        # Mark special content types
        marker = ""
        if u.strip().startswith('```'):
            marker = "[代码块]"
        elif u.strip().startswith('|'):
            marker = "[表格]"
        elif re.match(r'^\s*(\d+[.)]|[-*+])\s', u.strip()):
            marker = "[列表]"
        unit_previews.append(f"[{i}]{marker} {preview}... ({len(u)} 字)")

    units_text = "\n".join(unit_previews)

    analysis_prompt = f"""请分析以下内容单元列表，识别**好的分割边界**（主题转换处）。

【任务】
从以下单元中选择合适的分割点，使每个块在语义上独立完整。
重要：**不要**在表格、代码块或列表中间分割，保持这些结构的完整性。

【目标块大小】
- 目标：约 {chunk_size} 字符
- 最小：{int(chunk_size * 0.5)} 字符（低于此值的块会被合并）
- 最大：{int(chunk_size * 1.5)} 字符

【选择标准 - 优先级从高到低】
1. **主题转换**：不同主题/领域之间（如从"安装步骤"转到"配置说明"）
2. **章节边界**：明确的章节、小节结束处
3. **逻辑完整**：一个完整步骤、功能说明或概念论述结束后

【特别注意事项】
- 带有[表格]标记的单元必须在同一chunk中保持完整
- 带有[代码块]标记的单元不要拆开
- 连续的[列表]项应尽可能保持在同一块中

【避免选择】
- 表格、代码块、列表的中间位置
- 同一主题或步骤内部的单元之间
- 因果关系紧密的内容之间

【输出格式】
只返回单元编号，用逗号分隔。例如：2, 5, 8
表示在第2单元后、第5单元后、第8单元后分割。
如果不确定，返回最明确的主题转换点，宁少勿多。

内容单元列表（共 {len(content_units)} 个）：
{units_text}

分割点（只返回数字，用逗号分隔）："""

    # Get semantic split model from dynamic config
    semantic_split_model = config_manager.get("semantic_split_model", "qwen3.5-flash")

    response = call_llm(
        model=semantic_split_model,
        messages=[
            {"role": "user", "content": analysis_prompt}
        ],
        # Thinking mode disabled by default for speed
        enable_thinking=False
    )

    # Parse paragraph indices from response
    split_indices_str = response.choices[0].message.content.strip()

    # Enhanced parsing: extract numbers, handling various formats
    # Look for standalone numbers or numbers followed by punctuation
    numbers = re.findall(r'\b\d+\b', split_indices_str)
    split_paragraph_indices = sorted([int(n) for n in numbers])

    # Validate indices are within reasonable range
    split_paragraph_indices = [idx for idx in split_paragraph_indices if 0 <= idx < len(unit_boundaries)]

    # Remove duplicates while preserving order
    seen = set()
    split_paragraph_indices = [x for x in split_paragraph_indices if not (x in seen or seen.add(x))]

    # Step 3: Convert paragraph indices to character positions with intelligent merging
    # This is the KEY: WE calculate positions, not the LLM!
    valid_splits = []
    min_chunk_size = int(chunk_size * 0.5)  # Increased minimum to reduce small chunks
    max_chunk_size = int(chunk_size * 1.5)

    for idx in split_paragraph_indices:
        if 0 <= idx < len(unit_boundaries):
            # Get the end position of this paragraph
            _, end_pos, _ = unit_boundaries[idx]

            # Validate: ensure reasonable chunk sizes
            if len(valid_splits) > 0:
                prev_split = valid_splits[-1]
                chunk_len = end_pos - prev_split
                if chunk_len < min_chunk_size:
                    # Too small, skip this split (will be merged with next)
                    continue
                if chunk_len > max_chunk_size:
                    # Too large, accept anyway but warn (better than bad split)
                    pass
            else:
                # First split: ensure first chunk isn't too small
                if end_pos < min_chunk_size:
                    continue  # First chunk would be too small

            # Ensure not too close to the end (leave at least 20% for last chunk)
            remaining = len(text) - end_pos
            if remaining < min_chunk_size * 0.5:
                # Don't split here, let the rest be part of previous chunk
                continue

            valid_splits.append(end_pos)

    # If no valid splits from LLM or too few, add fallback splits
    if len(valid_splits) == 0:
        # Generate automatic splits based on chunk size
        current_pos = 0
        while current_pos + chunk_size < len(text):
            # Find the nearest paragraph boundary after target position
            target_pos = current_pos + chunk_size
            best_split = None
            for start, end, idx in unit_boundaries:
                if end >= target_pos - chunk_size * 0.3 and end <= target_pos + chunk_size * 0.3:
                    if end > current_pos and (best_split is None or abs(end - target_pos) < abs(best_split - target_pos)):
                        best_split = end
            if best_split and best_split not in valid_splits:
                valid_splits.append(best_split)
                current_pos = best_split
            else:
                # No good boundary found, move forward
                current_pos = target_pos

    if not valid_splits:
        raise Exception("No valid split points could be determined")

    # Step 4: Create chunks using the validated split positions
    chunks = []
    prev = 0
    for split_pos in valid_splits:
        chunk = text[prev:split_pos].strip()
        if chunk:
            chunks.append(chunk)
        prev = split_pos

    if prev < len(text):
        chunk = text[prev:].strip()
        if chunk:
            chunks.append(chunk)

    # Step 5: Post-process
    return _postprocess_chunks(chunks, chunk_size, chunk_overlap)


def _semantic_split_with_images(text: str, image_root: Path, chunk_size: int, chunk_overlap: int) -> list[str]:
    """
    Semantic splitting for documents with images (PDF, markdown with figures).

    KEY INSIGHT: Instead of asking LLM to count characters, we:
    1. Split into paragraphs (keeping images with their context)
    2. Ask LLM which paragraphs are good split boundaries
    3. Calculate actual positions ourselves

    Preserves image-text relationships by keeping images with their context.
    """

    # Step 1: Split text into paragraphs, but keep image blocks intact
    # Image blocks include: the image markdown + caption paragraph
    paragraphs = re.split(r'(\n\n+)', text)

    # Group paragraphs into "semantic units"
    # A unit can be: [text], [image + caption], [text + image reference]
    content_units = []
    unit_boundaries = []  # (start_char, end_char, unit_index)

    current_pos = 0
    image_pattern = r'!\[.*?\]\(.*?\)'

    i = 0
    while i < len(paragraphs):
        p = paragraphs[i]

        if not p.strip():
            current_pos += len(p)
            i += 1
            continue

        unit_start = current_pos
        unit_content = [p]
        current_pos += len(p)

        # Check if this paragraph contains or references an image
        has_image = re.search(image_pattern, p)
        is_caption = has_image  # Paragraph with image is likely a caption

        # Look ahead: check if next paragraph is an image or caption
        j = i + 1
        while j < len(paragraphs) - 1:
            next_p = paragraphs[j]
            next_next_p = paragraphs[j + 1] if j + 1 < len(paragraphs) else ""

            # Skip separators
            if not next_p.strip():
                j += 1
                continue

            # Check if next is an image
            if re.search(image_pattern, next_p):
                unit_content.append(next_p)
                current_pos += len(paragraphs[j])
                j += 1

                # Also include caption after image
                if j < len(paragraphs) and paragraphs[j].strip():
                    if len(paragraphs[j]) < 200:  # Caption is usually short
                        unit_content.append(paragraphs[j])
                        current_pos += len(paragraphs[j])
                        j += 1
                break

            # Check if current paragraph has image and next is caption
            if has_image and len(next_p) < 200 and not re.search(image_pattern, next_p):
                # Likely a caption
                unit_content.append(next_p)
                current_pos += len(paragraphs[j])
                j += 1
                break

            # No image relationship, stop grouping
            break

        i = j

        unit_end = current_pos
        content_units.append("".join(unit_content))
        unit_boundaries.append((unit_start, unit_end, len(content_units) - 1))

    # If too few units, semantic splitting cannot proceed
    if len(content_units) < 3:
        raise Exception(f"Text too short for semantic splitting: only {len(content_units)} units found")

    # Step 2: Ask LLM to identify split points by unit index
    unit_previews = []
    for i, u in enumerate(content_units):
        preview = u[:100].replace('\n', ' ')
        has_img = "[图片]" if re.search(image_pattern, u) else ""
        unit_previews.append(f"[{i}]{has_img} {preview}... ({len(u)} 字)")

    units_text = "\n".join(unit_previews)

    analysis_prompt = f"""请分析以下文档单元列表（包含文本和图片），识别**好的分割边界**（主题转换处）。

【任务】
从以下单元中选择合适的分割点，使每个块在语义上独立完整。**保持图片与其说明文字在同一块中**。

【目标块大小】
- 目标：约 {chunk_size} 字符
- 最小：{int(chunk_size * 0.5)} 字符（低于此值的块会被合并）
- 最大：{int(chunk_size * 1.5)} 字符

【选择标准 - 优先级从高到低】
1. **主题转换**：不同主题/领域之间（如从"医疗"转到"教育"）
2. **章节边界**：明确的章节、小节结束处
3. **逻辑完整**：一个完整概念论述结束后
4. **图片边界**：优先在图片单元之前或之后分割，不在图片单元中间分割

【避免选择】
- 同一主题内部的段落之间
- 因果关系紧密的段落之间
- 图片与其说明文字之间
- 连续的例子或并列说明中间

【输出格式】
只返回单元编号，用逗号分隔。例如：2, 5, 8
表示在第2单元后、第5单元后、第8单元后分割。
如果不确定，返回最明确的主题转换点，宁少勿多。

文档单元列表（共 {len(content_units)} 单元，[图片]标记表示包含图片）：
{units_text}

分割点（只返回数字，用逗号分隔）："""

    # Get semantic split model from dynamic config
    semantic_split_model = config_manager.get("semantic_split_model", "qwen3.5-flash")

    response = call_llm(
        model=semantic_split_model,
        messages=[
            {"role": "user", "content": analysis_prompt}
        ],
        # Thinking mode disabled by default for speed
        enable_thinking=False
    )

    # Parse unit indices from response
    split_indices_str = response.choices[0].message.content.strip()

    # Enhanced parsing: extract numbers, handling various formats
    numbers = re.findall(r'\b\d+\b', split_indices_str)
    split_unit_indices = sorted([int(n) for n in numbers])

    # Validate indices are within reasonable range
    split_unit_indices = [idx for idx in split_unit_indices if 0 <= idx < len(unit_boundaries)]

    # Remove duplicates while preserving order
    seen = set()
    split_unit_indices = [x for x in split_unit_indices if not (x in seen or seen.add(x))]

    # Step 3: Convert unit indices to character positions with intelligent merging
    valid_splits = []
    min_chunk_size = int(chunk_size * 0.5)  # Increased minimum to reduce small chunks
    max_chunk_size = int(chunk_size * 1.5)

    for idx in split_unit_indices:
        if 0 <= idx < len(unit_boundaries):
            _, end_pos, _ = unit_boundaries[idx]

            # Validate: ensure reasonable chunk sizes
            if len(valid_splits) > 0:
                prev_split = valid_splits[-1]
                chunk_len = end_pos - prev_split
                if chunk_len < min_chunk_size:
                    continue  # Too small, skip this split
                if chunk_len > max_chunk_size:
                    pass  # Too large, accept anyway
            else:
                if end_pos < min_chunk_size:
                    continue  # First chunk would be too small

            # Ensure not too close to the end
            remaining = len(text) - end_pos
            if remaining < min_chunk_size * 0.5:
                continue

            valid_splits.append(end_pos)

    # If no valid splits or too few, add fallback splits
    if len(valid_splits) == 0:
        # Generate automatic splits based on chunk size
        current_pos = 0
        while current_pos + chunk_size < len(text):
            target_pos = current_pos + chunk_size
            best_split = None
            for start, end, idx in unit_boundaries:
                if end >= target_pos - chunk_size * 0.3 and end <= target_pos + chunk_size * 0.3:
                    if end > current_pos and (best_split is None or abs(end - target_pos) < abs(best_split - target_pos)):
                        best_split = end
            if best_split and best_split not in valid_splits:
                valid_splits.append(best_split)
                current_pos = best_split
            else:
                current_pos = target_pos

    if not valid_splits:
        raise Exception("No valid split points could be determined for document with images")

    # Step 4: Create chunks
    chunks = []
    prev = 0
    for split_pos in valid_splits:
        chunk = text[prev:split_pos].strip()
        if chunk:
            chunks.append(chunk)
        prev = split_pos

    if prev < len(text):
        chunk = text[prev:].strip()
        if chunk:
            chunks.append(chunk)

    return _postprocess_chunks(chunks, chunk_size, chunk_overlap)


def _postprocess_chunks(chunks: list[str], chunk_size: int, chunk_overlap: int) -> list[str]:
    """
    Post-process chunks: merge small ones.

    Note: Overlap between chunks has been removed to avoid content duplication.
    Each chunk now contains unique, non-overlapping content.

    Key logic:
    1. Merge small chunks with the PREVIOUS chunk to preserve forward context
    2. Handle the first chunk specially - merge with next if too small
    """

    if not chunks:
        return chunks

    # Step 1: Merge very small chunks
    # Strategy: iterate backwards and merge small chunks with previous
    # This preserves the "forward-looking" nature of chunks
    final_chunks = []

    for i, chunk in enumerate(chunks):
        # Skip empty chunks
        if not chunk.strip():
            continue

        # Check if this chunk is too small
        is_too_small = len(chunk) < chunk_size * 0.4

        if is_too_small:
            if final_chunks:
                # Merge with previous chunk (preferred)
                final_chunks[-1] += "\n\n" + chunk
            elif i < len(chunks) - 1:
                # First chunk is small - merge with next
                # But don't add to final_chunks yet, wait for next iteration
                # Store as "pending merge"
                final_chunks.append(chunk)  # Will be merged with next
            else:
                # Last chunk and small - just add it
                final_chunks.append(chunk)
        else:
            # Check if previous chunk was small and needs merging
            if final_chunks and len(final_chunks[-1]) < chunk_size * 0.4:
                # Previous was small, merge current with it
                prev_small = final_chunks.pop()
                final_chunks.append(prev_small + "\n\n" + chunk)
            else:
                final_chunks.append(chunk)

    # Step 2: If first chunk is still too small, merge with second
    while len(final_chunks) > 1 and len(final_chunks[0]) < chunk_size * 0.5:
        if len(final_chunks) >= 2:
            second = final_chunks.pop(1)
            final_chunks[0] += "\n\n" + second

    # Step 3: Return chunks WITHOUT overlap
    # Note: Overlap has been removed to avoid content duplication between chunks
    # Each chunk now contains unique, non-overlapping content
    return final_chunks


def _split_into_semantic_units(text: str) -> tuple[list[str], list[tuple[int, int, int]]]:
    """
    Split text into semantic units, preserving structural elements like tables,
    code blocks, and lists.

    Returns:
        Tuple of (content_units, unit_boundaries)
        unit_boundaries: list of (start_char, end_char, unit_index)
    """
    # Pattern for structural elements that should not be split
    # 1. Markdown tables (lines starting with | containing |)
    # 2. Code blocks (```...```)
    # 3. Numbered lists (1. 2. etc.)
    # 4. Bullet lists (- * +)

    # Split text into lines while preserving line structure
    if '\r\n' in text:
        lines = text.split('\r\n')
        line_ending = '\r\n'
    else:
        lines = text.split('\n')
        line_ending = '\n'

    units = []
    boundaries = []
    current_pos = 0

    i = 0
    while i < len(lines):
        line = lines[i]

        # Check for code block
        if line.strip().startswith('```'):
            # Collect entire code block
            code_block = [line]
            start_line_idx = i
            i += 1
            while i < len(lines) and not lines[i].strip().startswith('```'):
                code_block.append(lines[i])
                i += 1
            if i < len(lines):
                code_block.append(lines[i])
                i += 1
            # Calculate exact position in original text
            start = sum(len(lines[j]) + len(line_ending) for j in range(start_line_idx))
            # For the last line, don't add line ending
            end = sum(len(lines[j]) + len(line_ending) for j in range(i - 1)) + len(lines[i - 1])
            unit_content = line_ending.join(code_block)
            units.append(unit_content)
            boundaries.append((start, end, len(units) - 1))
            current_pos = end
            continue

        # Check for markdown table
        if line.strip().startswith('|') and '|' in line[1:]:
            # Collect entire table
            table_lines = [line]
            start_line_idx = i
            i += 1
            while i < len(lines) and lines[i].strip().startswith('|'):
                table_lines.append(lines[i])
                i += 1
            start = sum(len(lines[j]) + len(line_ending) for j in range(start_line_idx))
            end = sum(len(lines[j]) + len(line_ending) for j in range(i - 1)) + len(lines[i - 1])
            unit_content = line_ending.join(table_lines)
            units.append(unit_content)
            boundaries.append((start, end, len(units) - 1))
            current_pos = end
            continue

        # Check for list items (numbered or bullet)
        list_pattern = r'^\s*(\d+[.)]|[-*+])\s'
        if re.match(list_pattern, line):
            # Collect related list items (consecutive list items with similar indentation)
            list_items = [line]
            base_indent = len(line) - len(line.lstrip())
            start_line_idx = i
            i += 1
            while i < len(lines):
                next_line = lines[i]
                if not next_line.strip():
                    # Empty line - check if next non-empty line is a list item
                    j = i + 1
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    if j < len(lines) and re.match(list_pattern, lines[j]):
                        # Add empty lines between list items
                        while i < j:
                            list_items.append(lines[i])
                            i += 1
                        list_items.append(lines[i])
                        i += 1
                        continue
                    break
                elif re.match(list_pattern, next_line):
                    list_items.append(next_line)
                    i += 1
                elif next_line.strip() and len(next_line) - len(next_line.lstrip()) > base_indent:
                    # Indented continuation of list item
                    list_items.append(next_line)
                    i += 1
                else:
                    break
            start = sum(len(lines[j]) + len(line_ending) for j in range(start_line_idx))
            end = sum(len(lines[j]) + len(line_ending) for j in range(i - 1)) + len(lines[i - 1])
            unit_content = line_ending.join(list_items)
            units.append(unit_content)
            boundaries.append((start, end, len(units) - 1))
            current_pos = end
            continue

        # Regular paragraph - collect consecutive non-empty lines
        if line.strip():
            para_lines = [line]
            start_line_idx = i
            i += 1
            while i < len(lines) and lines[i].strip():
                # Check if next line starts a special block
                if lines[i].strip().startswith('```') or lines[i].strip().startswith('|') or re.match(list_pattern, lines[i]):
                    break
                para_lines.append(lines[i])
                i += 1
            start = sum(len(lines[j]) + len(line_ending) for j in range(start_line_idx))
            end = sum(len(lines[j]) + len(line_ending) for j in range(i - 1)) + len(lines[i - 1])
            unit_content = line_ending.join(para_lines)
            units.append(unit_content)
            boundaries.append((start, end, len(units) - 1))
            current_pos = end
        else:
            # Empty line - just advance position
            current_pos += len(line) + len(line_ending)
            i += 1

    return units, boundaries
def recursive_character_text_splitter(text: str, chunk_size: int = 1500, chunk_overlap: int = 200) -> list[str]:
    """
    Split text into chunks of specified size with overlap.
    This is a simplified implementation of RecursiveCharacterTextSplitter.
    """
    if not text:
        return []
        
    separators = ["\n\n", "\n", " ", ""]
    chunks = []
    
    # Simple implementation: split by length first, respecting basic separators could be added
    # For now, let's do a sliding window approach which is robust enough
    
    start = 0
    text_len = len(text)
    
    while start < text_len:
        end = start + chunk_size
        
        # Adjust end to not break words if possible (look for space or newline)
        if end < text_len:
            # Try to find a separator near the end
            found_separator = False
            for sep in separators:
                if sep == "": continue
                # Search backwards from end
                sep_pos = text.rfind(sep, start, end)
                if sep_pos != -1 and sep_pos > start + chunk_size // 2: # Don't go back too far
                    end = sep_pos + len(sep)
                    found_separator = True
                    break
        
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        
        # Move start forward, accounting for overlap
        start = end - chunk_overlap
        if start < 0: start = 0 # Should not happen but safety
        
        # If we didn't advance (e.g. chunk size smaller than one word?), force advance
        if start <= (end - chunk_size):
             start = end
             
    return chunks

def _parse_multimodal_chunk(text: str, image_root: Path) -> list:
    """Parse chunk text and resolve images for DashScope input."""
    # Pattern: ![alt](path)
    # We need to split text by images
    pattern = r'!\[(.*?)\]\((.*?)\)'
    parts = re.split(pattern, text)
    matches = re.findall(pattern, text)
    
    inputs = []
    
    # re.split returns [text, alt, path, text, alt, path, text...]
    # If no matches, parts = [text]
    
    current_text_idx = 0
    match_idx = 0
    
    while current_text_idx < len(parts):
        text_part = parts[current_text_idx]
        if text_part.strip():
            inputs.append({"text": text_part})
        
        # If there's a match following this text part
        if match_idx < len(matches):
            alt, path = matches[match_idx]
            # Resolve path
            # MinerU paths are relative to the markdown file location
            # However, MinerU markdown might use paths like "images/xxx.jpg"
            # And image_root is where the markdown file is.
            # But wait, MinerU output structure:
            # - xxx/
            #   - xxx.md
            #   - images/
            #     - xxx.jpg
            
            # If path is "images/xxx.jpg", and image_root is "xxx/", then "xxx/images/xxx.jpg" works.
            # But on Windows, paths might be tricky.
            # Also, some paths might start with "./" or "/"
            clean_path = path.lstrip('./').lstrip('/').replace('/', os.sep)
            full_path = image_root / clean_path
            
            if full_path.exists():
                # Convert to Base64
                try:
                    base64_img = _encode_image(full_path)
                    mime_type = "image/jpeg"
                    if full_path.suffix.lower() == ".png":
                        mime_type = "image/png"
                    elif full_path.suffix.lower() == ".webp":
                        mime_type = "image/webp"
                    elif full_path.suffix.lower() == ".gif":
                        mime_type = "image/gif"
                    
                    inputs.append({"image": f"data:{mime_type};base64,{base64_img}"})
                except Exception as e:
                    print(f"Error encoding image {full_path}: {e}")
            else:
                # Try finding it recursively? Or maybe path is absolute?
                # Check if it exists relative to image_root parent?
                # Sometimes MinerU structure varies.
                # Let's try to just find the file by name in image_root recursively
                found = False
                
                # Check if it is in an 'images' folder inside image_root
                # Based on user LS: d:\...\mineru_cache\...\images\xxx.jpg
                # And image_root seems to be d:\...\mineru_cache\...\
                # The path in markdown is likely "images/xxx.jpg"
                
                # Try direct check in 'images' folder if path is just filename
                if 'images' not in str(clean_path):
                     maybe_path = image_root / 'images' / Path(clean_path).name
                     if maybe_path.exists():
                         # Found in images subdir
                         # Convert to Base64 to avoid Windows path issues with SDK
                         try:
                             base64_img = _encode_image(maybe_path)
                             mime_type = "image/jpeg"
                             if maybe_path.suffix.lower() == ".png":
                                 mime_type = "image/png"
                             elif maybe_path.suffix.lower() == ".webp":
                                 mime_type = "image/webp"
                             elif maybe_path.suffix.lower() == ".gif":
                                 mime_type = "image/gif"
                             
                             inputs.append({"image": f"data:{mime_type};base64,{base64_img}"})
                             found = True
                         except Exception as e:
                             print(f"Error encoding image {maybe_path}: {e}")
                
                if not found:
                    for f in image_root.rglob(Path(path).name):
                        # Found recursively
                        try:
                             base64_img = _encode_image(f)
                             mime_type = "image/jpeg"
                             if f.suffix.lower() == ".png":
                                 mime_type = "image/png"
                             elif f.suffix.lower() == ".webp":
                                 mime_type = "image/webp"
                             elif f.suffix.lower() == ".gif":
                                 mime_type = "image/gif"
                             
                             inputs.append({"image": f"data:{mime_type};base64,{base64_img}"})
                             found = True
                             break
                        except Exception as e:
                             print(f"Error encoding image {f}: {e}")
                
                if not found:
                    print(f"Warning: Image not found at {full_path}")
                    # Keep text representation instead of failing?
                    # But we consumed the text part in regex split.
                    # We should probably add the markdown image syntax back as text fallback
                    inputs.append({"text": f"![{alt}]({path})"})
            
            match_idx += 1
            # Skip alt and path in parts list (they are at +1 and +2)
            current_text_idx += 3
        else:
            current_text_idx += 1
            
    return inputs

def markdown_multimodal_splitter(text: str, image_root: Path) -> tuple[list[str], list[list]]:
    """
    Split markdown into chunks, respecting image boundaries.
    Returns (raw_chunks, input_lists).
    """
    # Simple strategy: Split by double newlines (paragraphs)
    # If a paragraph has images, keep them together.
    # Accumulate text-only paragraphs until size limit.
    
    paragraphs = text.split('\n\n')
    
    raw_chunks = []
    input_lists = []
    
    current_chunk_text = []
    current_chunk_inputs = []
    current_len = 0
    CHUNK_SIZE = 1000
    
    # DashScope Limitation: max 20 inputs (text/image parts) per API call
    MAX_INPUTS_PER_CHUNK = 20
    
    for p in paragraphs:
        # Parse this paragraph
        p_inputs = _parse_multimodal_chunk(p, image_root)
        
        # If p_inputs itself is too large, we must split it
        if len(p_inputs) > MAX_INPUTS_PER_CHUNK:
             # Flush current if any
             if current_chunk_inputs:
                 raw_chunks.append("".join(current_chunk_text))
                 input_lists.append(current_chunk_inputs)
                 current_chunk_text = []
                 current_chunk_inputs = []
                 current_len = 0
             
             # Split p_inputs
             for i in range(0, len(p_inputs), MAX_INPUTS_PER_CHUNK):
                 sub_inputs = p_inputs[i:i+MAX_INPUTS_PER_CHUNK]
                 # For text representation, we can't easily map back to original text parts of p
                 # without complex logic.
                 # Simplified approach: Use a generic label or try to extract text from sub_inputs
                 sub_text_parts = []
                 for item in sub_inputs:
                     if 'text' in item:
                         sub_text_parts.append(item['text'])
                     else:
                         sub_text_parts.append("[Image]")
                 
                 raw_chunks.append("".join(sub_text_parts))
                 input_lists.append(sub_inputs)
                 
             continue
        
        # Check if adding this paragraph would exceed limits
        extra_item_for_separator = 0
        if current_chunk_inputs and ("text" not in current_chunk_inputs[-1]):
            extra_item_for_separator = 1
            
        would_exceed_inputs = (len(current_chunk_inputs) + len(p_inputs) + extra_item_for_separator) > MAX_INPUTS_PER_CHUNK
        would_exceed_len = (current_len + len(p)) > CHUNK_SIZE
        
        # If chunk is full or input count limit reached
        if current_chunk_inputs and (would_exceed_len or would_exceed_inputs):
             raw_chunks.append("".join(current_chunk_text))
             input_lists.append(current_chunk_inputs)
             current_chunk_text = []
             current_chunk_inputs = []
             current_len = 0
             
        # Add to current
        if current_chunk_text:
            current_chunk_text.append("\n\n")
            # Add separator to inputs if previous was text
            # Note: adding separator might increase input count if it's a new dict
            # But here we append to existing text if possible, or new text dict
            if current_chunk_inputs and "text" in current_chunk_inputs[-1]:
                current_chunk_inputs[-1]["text"] += "\n\n"
            elif current_chunk_inputs:
                current_chunk_inputs.append({"text": "\n\n"})
                # We added a new item, check limit again? 
                # Actually, strictly speaking, we should check limit before adding separator too.
                # But separator is small. Let's just be careful.
                if len(current_chunk_inputs) > MAX_INPUTS_PER_CHUNK:
                     # This edge case (separator pushing over limit) is rare but possible.
                     # Force split previous chunk without separator? 
                     # Or just accept 21? API says 20.
                     # Let's simple: if we just split, current_chunk_inputs is empty, so we are fine.
                     pass
                
        current_chunk_text.append(p)
        current_chunk_inputs.extend(p_inputs)
        
        current_len += len(p)
        
        # If a single paragraph has > 20 inputs (e.g. many small images), we need to split it further?
        # For now assume paragraph is reasonable.
        if len(current_chunk_inputs) >= MAX_INPUTS_PER_CHUNK:
             raw_chunks.append("".join(current_chunk_text))
             input_lists.append(current_chunk_inputs)
             current_chunk_text = []
             current_chunk_inputs = []
             current_len = 0
             
    # Flush remaining
    if current_chunk_text:
        raw_chunks.append("".join(current_chunk_text))
        input_lists.append(current_chunk_inputs)
        
    return raw_chunks, input_lists

# Configure OpenAI client for DashScope
client = None
if DASHSCOPE_API_KEY:
    client = OpenAI(
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL
    )
    # Also set dashscope api key for SDK usage
    dashscope.api_key = DASHSCOPE_API_KEY

def _encode_image(image_path: Path) -> str:
    """Encode image to base64 string."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def _process_pdf(file_path: Path) -> tuple[str, Path]:
    """Process PDF using Aliyun OSS and MinerU. Returns (content, image_root)."""
    try:
        # Initialize clients
        oss = AliyunOSS()
        mineru = MinerUClient()
    except ValueError as e:
        print(f"PDF processing setup failed: {e}")
        return "", None # Return empty on setup failure
    except Exception as e:
        print(f"PDF processing setup error: {e}")
        return "", None

    object_name = None
    try:
        # 1. Upload to OSS
        print(f"Uploading {file_path.name} to OSS...")
        object_name = oss.upload_file(file_path)
        
        # 2. Generate Signed URL
        url = oss.sign_url(object_name, expiration=3600)
        
        # 3. Submit to MinerU
        print(f"Submitting to MinerU...")
        task_id = mineru.submit_task(url, is_ocr=True)
        
        # 4. Wait for result
        zip_url = mineru.wait_for_completion(task_id)
        
        # 5. Download and extract
        # Create cache dir based on file hash or name
        file_hash = hashlib.md5(str(file_path).encode()).hexdigest()
        save_dir = STORAGE_PATH / "mineru_cache" / file_hash
        
        md_path = mineru.download_and_extract_markdown(zip_url, save_dir)
        
        with open(md_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        return content, save_dir
        
    except Exception as e:
        print(f"Error processing PDF {file_path}: {e}")
        return "", None # Return empty on processing failure
        
    finally:
        # 6. Cleanup OSS
        if object_name:
            try:
                oss.delete_file(object_name)
            except Exception as e:
                print(f"Warning: Failed to delete temp file from OSS: {e}")

def extract_text(file_path: Path, file_type: FileType) -> tuple[str, Path | None]:
    """Extract text from file using appropriate method. Returns (content, image_root)."""
    if not client:
        print("Error: DASHSCOPE_API_KEY not set.")
        return "", None

    if file_type == FileType.TEXT:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read(), None
        except Exception as e:
            print(f"Error reading text file {file_path}: {e}")
            return "", None
            
    elif file_type == FileType.IMAGE:
        # Use qwen-vl-max to describe image
        try:
            base64_image = _encode_image(file_path)
            
            # Identify mime type based on extension
            suffix = file_path.suffix.lower()
            mime_type = "image/jpeg"
            if suffix == ".png":
                mime_type = "image/png"
            elif suffix == ".webp":
                mime_type = "image/webp"
            elif suffix == ".gif":
                mime_type = "image/gif"
                
            response = client.chat.completions.create(
                model=config_manager.get("vision_model"),
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe this image in detail and extract any visible text."},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ]
            )
            return response.choices[0].message.content, None
        except Exception as e:
            print(f"Error processing image {file_path}: {e}")
            return "", None
            
    elif file_type == FileType.AUDIO:
        # Use qwen3-asr-flash-filetrans
        try:
            print(f"Transcribing audio {file_path.name}...")
            asr_client = ASRClient()
            text = asr_client.transcribe(file_path)
            if not text:
                print(f"ASR returned empty text for {file_path.name}")
                return "", None
            return text, None
        except Exception as e:
            print(f"Error transcribing audio {file_path}: {e}")
            return "", None
        
    elif file_type == FileType.PDF:
        return _process_pdf(file_path)
    
    return "", None

import concurrent.futures
import time

def _sanitize_base64_image(base64_str: str) -> str:
    """Sanitize base64 image: ensure RGB JPEG format, resize if too large."""
    try:
        # Decode
        if "," in base64_str:
            header, data = base64_str.split(",", 1)
        else:
            data = base64_str
            header = "data:image/jpeg;base64" # Assume jpeg
            
        img_data = base64.b64decode(data)
        
        # Open with PIL
        from PIL import Image
        import io
        
        with Image.open(io.BytesIO(img_data)) as img:
            # Convert to RGB
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            
            # Check size. If > 2048 on any side, resize.
            # (DashScope has a limit, typically 20MB or resolution. 2048 is safe.)
            if max(img.size) > 2048:
                 img.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            
            # Re-save
            out_buf = io.BytesIO()
            img.save(out_buf, format='JPEG', quality=85) # slightly lower quality to ensure valid file
            return f"data:image/jpeg;base64,{base64.b64encode(out_buf.getvalue()).decode('utf-8')}"
            
    except Exception as e:
        print(f"Image sanitization failed: {e}")
        return base64_str # Return original if failed

@retry(
    stop=stop_after_attempt(3), 
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True
)
def _generate_single_embedding(chunk_input, index, total):
    """Helper function to generate embedding for a single chunk."""
    if total > 1 and (index+1) % 5 == 0:
        print(f"Embedding chunk {index+1}/{total}...")
        
    try:
        final_input = chunk_input
        if isinstance(chunk_input, str):
            final_input = [{"text": chunk_input}]
            
        resp = dashscope.MultiModalEmbedding.call(
            model=config_manager.get("embedding_model"),
            input=final_input
        )
        
        if resp.status_code == 200:
            output = getattr(resp, 'output', None)
            if output is None and isinstance(resp, dict):
                output = resp.get('output')
            
            if output:
                embeddings_data = getattr(output, 'embeddings', None)
                if embeddings_data is None and isinstance(output, dict):
                    embeddings_data = output.get('embeddings')
                
                if embeddings_data and len(embeddings_data) > 0:
                    embedding_item = embeddings_data[0]
                    embedding = getattr(embedding_item, 'embedding', None)
                    if embedding is None and isinstance(embedding_item, dict):
                        embedding = embedding_item.get('embedding')
                    
                    if embedding:
                        return chunk_input, embedding
        elif resp.status_code == 429:
            # Explicitly raise exception to trigger retry
            raise Exception(f"Rate limit exceeded (429): {resp.message}")
        elif resp.status_code == 500 or (hasattr(resp, 'code') and 'InternalError' in str(resp.code)):
             # DashScope InternalError.Algo often caused by image issues.
             # User requirement: Do NOT drop image. Do NOT downgrade.
             # Strategy: Try to sanitize image (convert to RGB JPEG, resize if needed) and retry.
             
             print(f"Warning: Chunk {index} failed with {getattr(resp, 'code', resp.status_code)}. Attempting to sanitize images and retry...")
             
             try:
                 new_input = []
                 sanitized = False
                 
                 if isinstance(chunk_input, list):
                     import copy
                     new_input = copy.deepcopy(chunk_input)
                     
                     for item in new_input:
                         if 'image' in item:
                             # Sanitize
                             old_b64 = item['image']
                             new_b64 = _sanitize_base64_image(old_b64)
                             if new_b64 != old_b64:
                                 item['image'] = new_b64
                                 sanitized = True
                 
                 if sanitized:
                     # Retry with sanitized input
                     # Note: this call is synchronous and blocking inside the retry loop of the main function?
                     # No, _generate_single_embedding is the one being retried.
                     # But we are inside it.
                     # We can just make the call. If it fails, we raise Exception, and @retry will catch it and retry the WHOLE function again.
                     # But if we retry the whole function, we use the original 'chunk_input' again!
                     # So the sanitization won't persist across retries unless we change the logic.
                     
                     # Actually, we should just perform the call here. If it succeeds, return.
                     # If it fails, raise exception.
                     
                     retry_resp = dashscope.MultiModalEmbedding.call(
                         model=config_manager.get("embedding_model"),
                         input=new_input
                     )
                     
                     if retry_resp.status_code == 200:
                          output = getattr(retry_resp, 'output', None)
                          if output is None and isinstance(retry_resp, dict):
                              output = retry_resp.get('output')
                          
                          if output:
                              embeddings_data = getattr(output, 'embeddings', None)
                              if embeddings_data is None and isinstance(output, dict):
                                  embeddings_data = output.get('embeddings')
                              
                              if embeddings_data and len(embeddings_data) > 0:
                                  embedding_item = embeddings_data[0]
                                  embedding = getattr(embedding_item, 'embedding', None)
                                  if embedding is None and isinstance(embedding_item, dict):
                                      embedding = embedding_item.get('embedding')
                                  
                                  if embedding:
                                      print(f"Successfully embedded chunk {index} after sanitization.")
                                      return new_input, embedding
                     
                     print(f"Retry with sanitized images failed: {getattr(retry_resp, 'code', retry_resp.status_code)} - {retry_resp.message}")
                     raise Exception(f"Embedding failed after image sanitization: {retry_resp.message}")
                 else:
                     # No images were changed (maybe sanitization failed or wasn't needed)
                     print(f"Sanitization made no changes for chunk {index}.")
                     raise Exception(f"InternalError.Algo (500) on chunk {index}: {resp.message}")
                     
             except Exception as e:
                 print(f"Error during image sanitization retry for chunk {index}: {e}")
                 raise e
        else:
            print(f"Error generating embedding for chunk {index}: {resp.code} - {resp.message}")
            
    except Exception as e:
        print(f"Error generating embedding for chunk {index}: {e}")
        raise e # Raise exception for retry or external capture
        
    return None, None

def generate_embedding_chunks(text: str, image_root: Path = None):
    """
    Generate embeddings for text using qwen3-vl-embedding.
    Splits text into chunks and returns (chunks, embeddings_list).
    If image_root is provided, uses multimodal splitting.
    """
    if not text:
        return [], []

    if not dashscope.api_key:
        print("Error: DASHSCOPE_API_KEY not set.")
        return [], []

    chunks = []
    inputs = []

    # Get semantic split model from dynamic config
    semantic_split_model = config_manager.get("semantic_split_model", "qwen3.5-flash")

    if image_root:
        # For documents with images (PDF), always use semantic splitter that preserves image context
        # The semantic splitter uses the configured model to analyze structure and keep images with their context
        print(f"Using Semantic Splitter for document with images (model: {semantic_split_model})...")
        chunks = semantic_text_splitter(text, image_root=image_root, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
        inputs = chunks
    elif USE_SEMANTIC_SPLIT:
        # Semantic-aware splitting using LLM (for text-only documents)
        print(f"Using Semantic Splitter (model: {semantic_split_model})...")
        chunks = semantic_text_splitter(text, image_root=None, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
        inputs = chunks
    else:
        # Use recursive character-based splitter (default for text-only)
        chunks = recursive_character_text_splitter(text, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
        inputs = chunks  # Inputs are just strings

    total_chunks = len(chunks)
    print(f"Split into {total_chunks} chunks. Starting parallel embedding generation...")
    
    valid_chunks = []
    embeddings = []
    
    # Use ThreadPoolExecutor for parallel processing with batching
    # Batching helps reduce CPU spikes and memory usage by not submitting all tasks at once
    batch_size = config_manager.get("embedding_batch_size", 1)
    
    # Pre-allocate list for results to maintain order naturally
    # (Though we still verify index for safety)
    results = [None] * total_chunks
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=batch_size) as executor:
        for i in range(0, total_chunks, batch_size):
            # Create a batch of tasks
            batch_end = min(i + batch_size, total_chunks)
            batch_futures = {}
            
            for j in range(i, batch_end):
                chunk_inp = inputs[j]
                # Pass the chunk content for storage reference? 
                # _generate_single_embedding returns (chunk_input, embedding)
                # We need to map back to 'chunks' (the text content)
                # Let's just trust the index.
                future = executor.submit(_generate_single_embedding, chunk_inp, j, total_chunks)
                batch_futures[future] = j
            
            # Wait for current batch to complete
            for future in concurrent.futures.as_completed(batch_futures):
                index = batch_futures[future]
                try:
                    chunk_res, embedding_res = future.result()
                    if embedding_res: # We only care if embedding succeeded
                        results[index] = (chunks[index], embedding_res)
                except Exception as exc:
                    print(f"Chunk {index} generated an exception: {exc}")
            
            # Small sleep to let CPU cool down and prevent rate limit spikes
            time.sleep(0.5)

    # Filter out None results (failed chunks)
    valid_results = [r for r in results if r is not None]
    
    if len(valid_results) != total_chunks:
        print(f"Warning: Only {len(valid_results)}/{total_chunks} chunks successfully embedded.")
        # Fail fast: if any chunk fails, return empty to trigger failure in ingestion
        return [], []

    valid_chunks = [r[0] for r in valid_results]
    embeddings = [r[1] for r in valid_results]
            
    return valid_chunks, embeddings

def generate_embedding(text: str):
    """
    Legacy wrapper for single embedding generation (averaged).
    Deprecated but kept for compatibility.
    """
    chunks, embeddings = generate_embedding_chunks(text)
    if not embeddings:
        return None
    if len(embeddings) == 1:
        return embeddings[0]
    
    # Average pooling for legacy support
    print(f"Averaging {len(embeddings)} embeddings (Legacy Mode)...")
    avg_embedding = [sum(x) / len(embeddings) for x in zip(*embeddings)]
    return avg_embedding
