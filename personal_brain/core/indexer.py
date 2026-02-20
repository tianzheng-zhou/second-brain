import os
import base64
import dashscope
from pathlib import Path
from openai import OpenAI
from personal_brain.config import (
    DASHSCOPE_API_KEY, 
    DASHSCOPE_BASE_URL, 
    EMBEDDING_MODEL, 
    EMBEDDING_DIMENSION,
    VISION_MODEL
)
from personal_brain.core.models import FileType
from personal_brain.utils.aliyun_oss import AliyunOSS
from personal_brain.utils.mineru import MinerUClient

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

def _process_pdf(file_path: Path) -> str:
    """Process PDF using Aliyun OSS and MinerU."""
    try:
        # Initialize clients
        oss = AliyunOSS()
        mineru = MinerUClient()
    except ValueError as e:
        print(f"PDF processing setup failed: {e}")
        return "" # Return empty on setup failure
    except Exception as e:
        print(f"PDF processing setup error: {e}")
        return ""

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
        content = mineru.download_and_extract_markdown(zip_url)
        
        return content
        
    except Exception as e:
        print(f"Error processing PDF {file_path}: {e}")
        return "" # Return empty on processing failure
        
    finally:
        # 6. Cleanup OSS
        if object_name:
            try:
                oss.delete_file(object_name)
            except Exception as e:
                print(f"Warning: Failed to delete temp file from OSS: {e}")

def extract_text(file_path: Path, file_type: FileType) -> str:
    """Extract text from file using appropriate method."""
    if not client:
        print("Error: DASHSCOPE_API_KEY not set.")
        return ""

    if file_type == FileType.TEXT:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception as e:
            print(f"Error reading text file {file_path}: {e}")
            return ""
            
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
                model=VISION_MODEL,
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
            return response.choices[0].message.content
        except Exception as e:
            print(f"Error processing image {file_path}: {e}")
            return ""
            
    elif file_type == FileType.AUDIO:
        # Placeholder for audio transcription
        # DashScope has audio models (Paraformer), but sticking to text/vision for now as requested
        return "[Audio file - transcription not implemented]"
        
    elif file_type == FileType.PDF:
        return _process_pdf(file_path)
    
    return ""

def generate_embedding_chunks(text: str):
    """
    Generate embeddings for text using qwen3-vl-embedding.
    Splits text into chunks and returns (chunks, embeddings_list).
    """
    if not text:
        return [], []
        
    if not dashscope.api_key:
        print("Error: DASHSCOPE_API_KEY not set.")
        return [], []

    # Use recursive splitter
    chunks = recursive_character_text_splitter(text, chunk_size=1500, chunk_overlap=200)
    print(f"Split into {len(chunks)} chunks.")
    
    valid_chunks = []
    embeddings = []
    total_chunks = len(chunks)
    
    for i, chunk in enumerate(chunks):
        if total_chunks > 1 and (i+1) % 5 == 0:
            print(f"Embedding chunk {i+1}/{total_chunks}...")
        try:
            # Use qwen3-vl-embedding via DashScope SDK
            resp = dashscope.MultiModalEmbedding.call(
                model=EMBEDDING_MODEL,
                input=[{"text": chunk}]
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
                            valid_chunks.append(chunk)
                            embeddings.append(embedding)
            else:
                print(f"Error generating embedding for chunk {i}: {resp.code} - {resp.message}")
                
        except Exception as e:
            print(f"Error generating embedding for chunk {i}: {e}")
            
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
