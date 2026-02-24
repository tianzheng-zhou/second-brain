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
    STORAGE_PATH
)
from personal_brain.core.config_manager import config_manager
from personal_brain.core.models import FileType
from personal_brain.utils.aliyun_oss import AliyunOSS
from personal_brain.utils.mineru import MinerUClient
from personal_brain.utils.asr_client import ASRClient

# Simple text splitter logic
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

    if image_root:
        # Multimodal splitting
        print("Using Multimodal Splitter...")
        chunks, inputs = markdown_multimodal_splitter(text, image_root)
    else:
        # Use recursive splitter
        chunks = recursive_character_text_splitter(text, chunk_size=1500, chunk_overlap=200)
        inputs = chunks # Inputs are just strings
        
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
