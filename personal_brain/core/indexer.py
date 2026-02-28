import os
import base64
import dashscope
import re
import hashlib
import json
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
def semantic_text_splitter(text: str, image_root: Path = None, chunk_size: int = 1500, chunk_overlap: int = 200, model: str = None) -> list[str]:
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
        model: LLM model to use for semantic analysis

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
            return _semantic_split_with_images(text, image_root, chunk_size, chunk_overlap, model)
        else:
            # Use text-only prompt for plain text documents
            return _semantic_split_text_only(text, chunk_size, chunk_overlap, model)

    except Exception as e:
        # Fallback to recursive character splitter on any error
        print(f"  Semantic splitting failed ({e}), falling back to character-based splitting...")
        return recursive_character_text_splitter(text, chunk_size, chunk_overlap)


def _semantic_split_text_only(text: str, chunk_size: int, chunk_overlap: int, model: str = None) -> list[str]:
    """
    Semantic splitting for text-only documents.

    Strategy:
    1. Split into semantic units (each heading = one unit)
    2. Merge consecutive small units if needed
    3. Keep units under max size
    """

    # Step 1: Split text into semantic units (each heading = one unit)
    # Pass model to allow LLM-based structure refinement
    content_units, unit_boundaries = _split_into_semantic_units(text, model)

    if len(content_units) < 2:
        return content_units

    # Step 2: Merge strategy - combine consecutive small units
    # But don't merge if it would create oversized chunk or merge different Steps
    min_chunk_size = int(chunk_size * 0.5)
    max_chunk_size = int(chunk_size * 1.5)

    chunks = []
    current_chunk = []
    current_size = 0

    for i, unit in enumerate(content_units):
        unit_size = len(unit)

        # Check if this unit starts a new Step (has heading)
        is_new_step = bool(re.match(r'^#{1,6}\s', unit.strip()))

        # Decide whether to start a new chunk
        start_new_chunk = False

        if not current_chunk:
            # First unit
            start_new_chunk = True
        elif is_new_step:
            # New Step - check if we should merge with previous or start new
            if current_size < min_chunk_size:
                # Previous chunk is small, merge this Step into it
                start_new_chunk = False
            elif current_size + unit_size > max_chunk_size:
                # Would exceed max size, start new chunk
                start_new_chunk = True
            else:
                # Normal case: new Step gets its own chunk
                start_new_chunk = True
        else:
            # Not a new Step (continuation) - merge with current
            start_new_chunk = False

        if start_new_chunk and current_chunk:
            # Save current chunk
            chunk_text = '\n\n'.join(current_chunk)
            chunks.append(chunk_text)
            current_chunk = []
            current_size = 0

        # Add unit to current chunk
        current_chunk.append(unit)
        current_size += unit_size

    # Don't forget the last chunk
    if current_chunk:
        chunk_text = '\n\n'.join(current_chunk)
        chunks.append(chunk_text)

    # Step 3: Fix any chapter boundary issues
    chunks = _fix_chapter_boundaries(chunks)

    # Step 4: Final size check - only merge if chunk is extremely small (< 200 chars)
    # and don't merge across heading boundaries
    final_chunks = []
    for chunk in chunks:
        if len(chunk) < 200 and final_chunks:
            # Very small chunk, merge with previous if it doesn't make it too large
            if len(final_chunks[-1]) + len(chunk) < chunk_size * 1.2:
                final_chunks[-1] = final_chunks[-1] + "\n\n" + chunk
            else:
                final_chunks.append(chunk)
        else:
            final_chunks.append(chunk)

    return final_chunks


def _semantic_split_with_images(text: str, image_root: Path, chunk_size: int, chunk_overlap: int, model: str = None) -> list[str]:
    """
    Semantic splitting for documents with images (PDF, markdown with figures).

    Strategy:
    1. Split into semantic units by headings (like text-only version)
    2. Keep images with their context
    3. Merge consecutive small units if needed
    """

    # Step 1: Use the same semantic unit splitting as text-only
    # but with image-aware grouping
    # Pass model to allow LLM-based structure refinement
    content_units, unit_boundaries = _split_into_semantic_units(text, model)

    if len(content_units) < 2:
        return content_units

    # Step 2: Merge strategy - same as text-only
    min_chunk_size = int(chunk_size * 0.5)
    max_chunk_size = int(chunk_size * 1.5)

    chunks = []
    current_chunk = []
    current_size = 0

    for i, unit in enumerate(content_units):
        unit_size = len(unit)

        # Check if this unit starts a new Step (has heading)
        is_new_step = bool(re.match(r'^#{1,6}\s', unit.strip()))

        # Decide whether to start a new chunk
        start_new_chunk = False

        if not current_chunk:
            # First unit
            start_new_chunk = True
        elif is_new_step:
            # New Step - check if we should merge with previous or start new
            if current_size < min_chunk_size:
                # Previous chunk is small, merge this Step into it
                start_new_chunk = False
            elif current_size + unit_size > max_chunk_size:
                # Would exceed max size, start new chunk
                start_new_chunk = True
            else:
                # Normal case: new Step gets its own chunk
                start_new_chunk = True
        else:
            # Not a new Step (continuation) - merge with current
            start_new_chunk = False

        if start_new_chunk and current_chunk:
            # Save current chunk
            chunk_text = '\n\n'.join(current_chunk)
            chunks.append(chunk_text)
            current_chunk = []
            current_size = 0

        # Add unit to current chunk
        current_chunk.append(unit)
        current_size += unit_size

    # Don't forget the last chunk
    if current_chunk:
        chunk_text = '\n\n'.join(current_chunk)
        chunks.append(chunk_text)

    # Step 3: Fix chapter boundaries
    chunks = _fix_chapter_boundaries(chunks)

    # Step 4: Final size check - only merge if chunk is extremely small (< 200 chars)
    # and don't merge across heading boundaries
    final_chunks = []
    for chunk in chunks:
        if len(chunk) < 200 and final_chunks:
            # Very small chunk, merge with previous if it doesn't make it too large
            if len(final_chunks[-1]) + len(chunk) < chunk_size * 1.2:
                final_chunks[-1] = final_chunks[-1] + "\n\n" + chunk
            else:
                final_chunks.append(chunk)
        else:
            final_chunks.append(chunk)

    return final_chunks


def _fix_chapter_boundaries(chunks: list[str]) -> list[str]:
    """
    Fix chunks that break at chapter boundaries.
    Ensures no chunk ends with just a heading and no chunk starts in the middle of a chapter.
    """
    if len(chunks) <= 1:
        return chunks

    heading_pattern = re.compile(r'^#{1,6}\s', re.MULTILINE)
    fixed_chunks = []

    i = 0
    while i < len(chunks):
        chunk = chunks[i]
        lines = chunk.split('\n')

        # Check if this chunk ends with just a heading (or very little content after heading)
        if len(lines) >= 1:
            last_line = lines[-1].strip()
            # Check if last line is a heading or chunk only has a heading
            if heading_pattern.match(last_line) or (len(lines) <= 2 and heading_pattern.match(chunk)):
                # This chunk ends with a heading - merge with next chunk if exists
                if i + 1 < len(chunks):
                    merged = chunk + "\n\n" + chunks[i + 1]
                    fixed_chunks.append(merged)
                    i += 2  # Skip next chunk as it's merged
                    continue

        # Check if this chunk starts with a heading that continues from previous
        # (This is handled by the previous check - if previous ended with heading)

        fixed_chunks.append(chunk)
        i += 1

    return fixed_chunks


def _postprocess_chunks(chunks: list[str], chunk_size: int, chunk_overlap: int) -> list[str]:
    """
    Post-process chunks: merge small ones.

    Note: Overlap between chunks has been removed to avoid content duplication.
    Each chunk now contains unique, non-overlapping content.

    Key logic:
    1. Collect consecutive small chunks and merge them together
    2. Merge small chunks with the next normal chunk
    3. Ensure minimum chunk size is respected
    """

    if not chunks:
        return chunks

    MIN_CHUNK_RATIO = 0.4  # Consistent threshold for small chunk detection
    min_chunk_size = int(chunk_size * MIN_CHUNK_RATIO)

    # Step 1: First pass - group consecutive small chunks together
    grouped_chunks = []
    pending_small = []  # Buffer for consecutive small chunks

    for chunk in chunks:
        if not chunk.strip():
            continue

        if len(chunk) < min_chunk_size:
            # Small chunk - add to pending buffer
            pending_small.append(chunk)
        else:
            # Normal chunk
            if pending_small:
                # Merge pending small chunks with this normal chunk
                merged = "\n\n".join(pending_small) + "\n\n" + chunk
                grouped_chunks.append(merged)
                pending_small = []
            else:
                grouped_chunks.append(chunk)

    # Handle any remaining small chunks at the end
    if pending_small:
        if grouped_chunks:
            # Merge with the last normal chunk
            last_chunk = grouped_chunks.pop()
            merged = last_chunk + "\n\n" + "\n\n".join(pending_small)
            grouped_chunks.append(merged)
        else:
            # All chunks were small, join them together
            grouped_chunks.append("\n\n".join(pending_small))

    # Step 2: Second pass - ensure first chunk meets minimum size
    # Merge first chunk with second if first is still too small
    while len(grouped_chunks) > 1 and len(grouped_chunks[0]) < min_chunk_size:
        second = grouped_chunks.pop(1)
        grouped_chunks[0] = grouped_chunks[0] + "\n\n" + second

    return grouped_chunks


def _split_into_semantic_units(text: str, model: str = None) -> tuple[list[str], list[tuple[int, int, int]]]:
    """
    Split text into semantic units, preserving structural elements like tables,
    code blocks, lists, and document sections.

    Strategy:
    - Each heading (like "# Step X") starts a new semantic unit
    - Keep tables, code blocks intact within their section
    - Each unit = one heading + its content until next heading
    - This ensures Steps are not merged together

    If model is provided, it uses LLM to identify which units should be merged
    (e.g., "Notes" belonging to previous "Step").

    Returns:
        Tuple of (content_units, unit_boundaries)
        unit_boundaries: list of (start_char, end_char, unit_index)
    """
    # Split text into lines
    if '\r\n' in text:
        lines = text.split('\r\n')
        line_ending = '\r\n'
    else:
        lines = text.split('\n')
        line_ending = '\n'

    def get_line_pos(idx: int) -> int:
        """Get character position of line start"""
        return sum(len(lines[j]) + len(line_ending) for j in range(idx))

    def is_heading(line_idx: int) -> bool:
        """Check if line is a heading (starts with #)"""
        if line_idx >= len(lines):
            return False
        line = lines[line_idx]
        # Markdown heading: # Heading
        if re.match(r'^#{1,6}\s', line.strip()):
            # Note: We rely on LLM to merge sections, so we accept ALL headers here.
            # No keyword filtering.
            return True
        return False

    units = []
    boundaries = []
    current_unit_lines = []
    current_unit_start = 0
    i = 0

    # Skip leading empty lines
    while i < len(lines) and not lines[i].strip():
        i += 1

    if i < len(lines):
        current_unit_start = i

    while i < len(lines):
        line = lines[i]

        # Check if this is a heading (and not the first line of current unit)
        if is_heading(i) and current_unit_lines and i > current_unit_start:
            # Save current unit
            start = get_line_pos(current_unit_start)
            end = get_line_pos(i - 1) + len(lines[i - 1])
            content = line_ending.join(current_unit_lines).strip()
            if content:
                units.append(content)
                boundaries.append((start, end, len(units) - 1))

            # Start new unit with this heading
            current_unit_lines = [line]
            current_unit_start = i
            i += 1
            continue

        # Add line to current unit
        if not current_unit_lines and line.strip():
            current_unit_start = i
        current_unit_lines.append(line)
        i += 1

    # Save last unit
    if current_unit_lines:
        start = get_line_pos(current_unit_start)
        if len(lines) > 0:
            end = get_line_pos(len(lines) - 1) + len(lines[-1])
        else:
            end = start
        content = line_ending.join(current_unit_lines).strip()
        if content:
            units.append(content)
            boundaries.append((start, end, len(units) - 1))

    # LLM Refinement Step
    if model and len(units) > 1:
        print(f"  Refining {len(units)} semantic units using LLM ({model})...")
        units = _refine_structure_with_llm(units, model)
        # Note: boundaries are now invalid/approximate for merged units, but that's okay
        # as subsequent logic doesn't strictly rely on them for splitting.
        # We rebuild approximate boundaries just in case.
        boundaries = []
        current_pos = 0
        for idx, u in enumerate(units):
            end_pos = current_pos + len(u)
            boundaries.append((current_pos, end_pos, idx))
            current_pos = end_pos + 2 # +2 for \n\n

    # Post-process: split very large units at paragraph boundaries
    # But keep heading with its content
    MAX_UNIT_SIZE = 1200
    final_units = []
    final_boundaries = []

    for unit_idx, unit in enumerate(units):
        # Retrieve boundary if available, else approximate
        start = boundaries[unit_idx][0] if unit_idx < len(boundaries) else 0
        
        if len(unit) <= MAX_UNIT_SIZE:
            final_units.append(unit)
            final_boundaries.append((start, start + len(unit), len(final_units) - 1))
        else:
            # Split large unit, but keep heading with first chunk
            paragraphs = unit.split('\n\n')
            if not paragraphs:
                continue

            # First chunk includes the heading (first paragraph)
            heading = paragraphs[0]
            current_chunk = [heading]
            current_size = len(heading)
            chunk_start = start

            for para in paragraphs[1:]:
                para_size = len(para) + 2  # +2 for \n\n

                if current_size + para_size > MAX_UNIT_SIZE and len(current_chunk) > 1:
                    # Save current chunk (has heading + content)
                    chunk_content = '\n\n'.join(current_chunk)
                    chunk_end = chunk_start + len(chunk_content)
                    final_units.append(chunk_content)
                    final_boundaries.append((chunk_start, chunk_end, len(final_units) - 1))

                    # Start new chunk (without heading, just continuation)
                    current_chunk = [para]
                    current_size = para_size
                    chunk_start = chunk_end + 2
                else:
                    current_chunk.append(para)
                    current_size += para_size

            # Save last chunk
            if current_chunk:
                chunk_content = '\n\n'.join(current_chunk)
                chunk_end = start + len(unit)
                final_units.append(chunk_content)
                final_boundaries.append((chunk_start, chunk_end, len(final_units) - 1))

    return final_units, final_boundaries


def _refine_structure_with_llm(units: list[str], model: str) -> list[str]:
    """
    Use LLM to identify which units should be merged into the previous one.
    """
    if len(units) < 2:
        return units
    
    # Prepare prompt with headers only
    headers = []
    for i, u in enumerate(units):
        lines = u.split('\n')
        # Take first line, max 100 chars
        header = lines[0].strip()[:100] 
        headers.append(f"{i}: {header}")
    
    headers_text = "\n".join(headers)
    
    prompt = f"""You are a document structure analyzer.
Here is a list of document sections. Each section starts with a markdown header.
Your task is to identify which sections are logically sub-sections, notes, or continuations of the **previous** section and should be merged into it to maintain context.

Common examples of sections to merge:
- "Precautions", "Notes", "Tips", "Warnings", "Attention" following a step.
- Sub-steps (e.g., "1.1 xxx") that are too small to stand alone.
- Headers that are just labels for the previous content.

Return a JSON object with a key "merge_indices" containing a list of indices that should be merged with their PREVIOUS section.
Example: If section 2 belongs to section 1, return [2].
Do NOT merge section 0.

Sections:
{headers_text}

JSON Output:"""

    try:
        response = call_llm(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            enable_thinking=False
        )
        content = response.choices[0].message.content
        
        # Parse JSON
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
            
        data = json.loads(content)
        merge_indices = set(data.get("merge_indices", []))
        
        if not merge_indices:
            return units
            
        new_units = []
        current_unit = units[0]
        
        for i in range(1, len(units)):
            if i in merge_indices:
                current_unit += "\n\n" + units[i]
            else:
                new_units.append(current_unit)
                current_unit = units[i]
        new_units.append(current_unit)
        
        return new_units

    except Exception as e:
        print(f"LLM structure refinement failed: {e}")
        return units
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
        chunks = semantic_text_splitter(text, image_root=image_root, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP, model=semantic_split_model)
        inputs = chunks
    elif USE_SEMANTIC_SPLIT:
        # Semantic-aware splitting using LLM (for text-only documents)
        print(f"Using Semantic Splitter (model: {semantic_split_model})...")
        chunks = semantic_text_splitter(text, image_root=None, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP, model=semantic_split_model)
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
