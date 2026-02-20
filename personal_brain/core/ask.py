import os
from openai import OpenAI
from personal_brain.config import (
    DASHSCOPE_API_KEY, 
    DASHSCOPE_BASE_URL
)
from personal_brain.core.config_manager import config_manager
from personal_brain.core.search import search_files

def get_client():
    """Create and return an OpenAI client for DashScope."""
    if not DASHSCOPE_API_KEY:
        raise ValueError("DASHSCOPE_API_KEY is not set. Please set it in your environment variables.")
    
    return OpenAI(
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL
    )

def ask_brain(query: str, history: list = None, stream: bool = True):
    """
    Ask a question to the second brain using RAG.
    
    Args:
        query: User's question
        history: Previous chat history (optional)
        stream: Whether to stream the response
    """
    client = get_client()
    
    # 1. Retrieve relevant context
    # We search for top 10 relevant chunks (increased from 5 for better context)
    search_results = search_files(query, limit=10)
    
    context_str = ""
    sources = []
    
    if search_results:
        context_parts = []
        for i, res in enumerate(search_results):
            # Extract content snippet
            # Try 'content' first (new chunk schema), then 'ocr_text' (legacy schema)
            content = res.get('content') or res.get('ocr_text') or "No text content available."
            
            # Truncate if too long (e.g. 2000 chars per chunk to fit context)
            if len(content) > 2000:
                content = content[:2000] + "... [truncated]"
            
            filename = res.get('filename', 'Unknown File')
            file_type = res.get('file_type') or res.get('type', 'unknown')
            score = res.get('score', 0)
            
            context_parts.append(f"--- Document {i+1} ---\nFile: {filename} ({file_type})\nScore: {score:.4f}\nContent:\n{content}\n")
            
            # Record source for citation
            sources.append({
                "filename": filename,
                "type": file_type,
                "score": score
            })
            
        context_str = "\n".join(context_parts)
    else:
        context_str = "No relevant documents found in the database."

    # 2. Construct System Prompt
    system_prompt = f"""You are PersonalBrain, an AI assistant with access to the user's personal files and notes.
Your goal is to answer the user's questions based primarily on the provided context.

CONTEXT FROM USER'S FILES:
{context_str}

INSTRUCTIONS:
1. Answer the user's question using the information from the context above.
2. If the context contains the answer, cite the specific document (e.g., "According to Document 1...").
3. If the context does not contain the answer, you can use your general knowledge but clearly state that the information is not in the user's files.
4. Be concise, helpful, and friendly.
5. If the user asks about the content of a specific file, summarize it based on the context.

Current Date: {os.getenv('TODAY_DATE', 'Unknown')}
"""

    # Check for image content in context
    has_images = "![" in context_str and "](" in context_str
    
    messages = []
    
    # If we have images, we need to construct a multimodal message for the system prompt
    # OR we need to inject images into the system prompt context?
    # DashScope/OpenAI format usually expects images in user messages, but system prompt is text-only usually?
    # Actually, qwen-vl supports images in user messages.
    # We should parse the context_str to extract images and format them as multimodal content.
    
    if has_images:
        import re
        from pathlib import Path
        from personal_brain.config import STORAGE_PATH
        import base64
        
        # Helper to encode image
        def encode_image(image_path):
            with open(image_path, "rb") as image_file:
                return base64.b64encode(image_file.read()).decode('utf-8')

        # Parse context_str to separate text and images
        # We'll construct a new system prompt content list
        system_content_list = [{"type": "text", "text": system_prompt}]
        
        # Find all images in context
        image_matches = re.findall(r'!\[(.*?)\]\((.*?)\)', context_str)
        
        images_found_count = 0
        for alt, img_path in image_matches:
            # Resolve path (similar to admin_dashboard logic)
            clean_path = img_path.lstrip('./').lstrip('/').replace('/', os.sep)
            img_name = Path(clean_path).name
            
            found_img = None
            cache_dir = STORAGE_PATH / "mineru_cache"
            if cache_dir.exists():
                try:
                    found_imgs = list(cache_dir.rglob(img_name))
                    if found_imgs:
                        found_img = found_imgs[0]
                except Exception:
                    pass
            
            if found_img:
                try:
                    base64_img = encode_image(found_img)
                    # Determine mime type
                    mime_type = "image/jpeg"
                    if found_img.suffix.lower() == ".png": mime_type = "image/png"
                    elif found_img.suffix.lower() == ".webp": mime_type = "image/webp"
                    
                    system_content_list.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{base64_img}"}
                    })
                    images_found_count += 1
                except Exception as e:
                    print(f"Error encoding image {found_img}: {e}")
        
        if images_found_count > 0:
            # If we found images, use multimodal message for system prompt (or first user message?)
            # Usually system prompt is text. Let's put images in a separate "user" message with the context?
            # Or just append to the first user message?
            # Let's keep system prompt text-only to be safe, and add a "user" message with context + images
            # followed by the actual user query.
            
            # Update system prompt to be simpler, since context is moving to user message
            messages.append({"role": "system", "content": "You are PersonalBrain. Answer based on the provided context images and text."})
            
            # Construct context message
            context_message = {
                "role": "user",
                "content": system_content_list # Text prompt + images
            }
            messages.append(context_message)
            
            # Add an assistant acknowledgement to maintain chat flow
            messages.append({"role": "assistant", "content": "I have received the context and images. What is your question?"})
        else:
            # Fallback if images not found on disk
            messages.append({"role": "system", "content": system_prompt})
            has_images = False # Reset flag if no actual images found
            
    else:
        messages.append({"role": "system", "content": system_prompt})
    
    # Add history if provided
    # Note: History management in RAG is tricky because we usually want RAG context to be fresh.
    # But user might refer to previous turns.
    # Ideally we should rephrase the question with history, then search.
    # For now, we just append history.
    if history:
        for msg in history:
            messages.append(msg)
            
    # Note: We already added the user query implicitly in the system prompt context logic?
    # No, system prompt has context. We still need the user's question.
    messages.append({"role": "user", "content": query})

    # [DEBUG LOGGING]
    print("\n" + "="*50)
    print("🤖 [DEBUG] LLM REQUEST LOG")
    print("="*50)
    print(f"Model: {config_manager.get('chat_model')}")
    print("-" * 30)
    
    for idx, msg in enumerate(messages):
        role = msg.get('role', 'unknown')
        content = msg.get('content', '')
        
        print(f"Message #{idx} ({role}):")
        
        if isinstance(content, str):
            # Print first 200 chars of text content
            preview = content[:200] + "..." if len(content) > 200 else content
            print(f"  Content: {preview}")
            
            # Check for potential image markers in text
            if "![" in content and "](" in content:
                print("  [!] Found markdown image syntax in text content.")
        
        elif isinstance(content, list):
            # Handle multimodal content list
            print("  [Multimodal Content]")
            for item in content:
                item_type = item.get('type', 'unknown')
                if item_type == 'text':
                    text_val = item.get('text', '')
                    preview = text_val[:100] + "..." if len(text_val) > 100 else text_val
                    print(f"    - Text: {preview}")
                elif item_type == 'image_url':
                    img_url = item.get('image_url', {}).get('url', '')
                    is_base64 = img_url.startswith('data:image')
                    print(f"    - Image: {'[Base64 Data]' if is_base64 else img_url}")
                else:
                    print(f"    - Unknown type: {item_type}")
                    
    print("="*50 + "\n")

    # 3. Call LLM
    try:
        chat_model = config_manager.get("chat_model")
        
        # If we have images, we MUST use a vision model
        if has_images:
            vision_model = config_manager.get("vision_model", "qwen3-vl-plus")
            print(f"🖼️ Images detected in context. Switching model from {chat_model} to {vision_model}")
            chat_model = vision_model
            
        response = client.chat.completions.create(
            model=chat_model,
            messages=messages,
            stream=stream,
            temperature=0.7,
        )
        
        return response, sources
        
    except Exception as e:
        return f"Error communicating with AI service: {str(e)}", []
