import os
import ollama
from pathlib import Path
from personal_brain.config import OLLAMA_BASE_URL, EMBEDDING_MODEL, VISION_MODEL
from personal_brain.core.models import FileType

# Configure Ollama host
os.environ["OLLAMA_HOST"] = OLLAMA_BASE_URL

def extract_text(file_path: Path, file_type: FileType) -> str:
    """Extract text from file using appropriate method."""
    if file_type == FileType.TEXT:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception as e:
            print(f"Error reading text file {file_path}: {e}")
            return ""
    elif file_type == FileType.IMAGE:
        # Use llama3.2-vision to describe image
        try:
            # Check if model exists, if not pull it?
            # For now assume user has it or we catch error.
            res = ollama.chat(
                model=VISION_MODEL,
                messages=[{
                    'role': 'user',
                    'content': 'Describe this image in detail and extract any visible text.',
                    'images': [str(file_path)]
                }]
            )
            return res['message']['content']
        except Exception as e:
            print(f"Error processing image {file_path}: {e}")
            return ""
    elif file_type == FileType.AUDIO:
        # Placeholder for audio transcription
        # Could use whisper via ollama if available
        return "[Audio file - transcription not implemented]"
    
    return ""

def generate_embedding(text: str):
    """Generate embedding for text."""
    if not text:
        return None
    try:
        res = ollama.embeddings(model=EMBEDDING_MODEL, prompt=text)
        return res['embedding']
    except Exception as e:
        print(f"Error generating embedding: {e}")
        return None
