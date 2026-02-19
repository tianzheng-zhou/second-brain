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
    
    return ""

def generate_embedding(text: str):
    """Generate embedding for text using qwen3-vl-embedding."""
    if not text:
        return None
        
    if not dashscope.api_key:
        print("Error: DASHSCOPE_API_KEY not set.")
        return None

    try:
        # Use qwen3-vl-embedding via DashScope SDK
        # Input format for multimodal embedding: input=[{"text": "..."}]
        resp = dashscope.MultiModalEmbedding.call(
            model=EMBEDDING_MODEL,
            input=[{"text": text}],
            # User requested using largest dimension (default 2560 for qwen3-vl-embedding)
            # parameters={"dimension": EMBEDDING_DIMENSION} # If needed, but user said default is 2560
        )
        
        if resp.status_code == 200:
            # Check response structure
            # Handle both attribute access and dict access
            output = getattr(resp, 'output', None)
            if output is None and isinstance(resp, dict):
                output = resp.get('output')
            
            if output:
                embeddings = getattr(output, 'embeddings', None)
                if embeddings is None and isinstance(output, dict):
                    embeddings = output.get('embeddings')
                
                if embeddings and len(embeddings) > 0:
                    embedding_item = embeddings[0]
                    embedding = getattr(embedding_item, 'embedding', None)
                    if embedding is None and isinstance(embedding_item, dict):
                        embedding = embedding_item.get('embedding')
                    
                    if embedding:
                        return embedding
            
            print(f"Unexpected response format from embedding model: {resp}")
            return None
        else:
            print(f"Error generating embedding: {resp.code} - {resp.message}")
            return None
            
    except Exception as e:
        print(f"Error generating embedding: {e}")
        return None
