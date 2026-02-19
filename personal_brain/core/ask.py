import os
from openai import OpenAI
from personal_brain.config import (
    DASHSCOPE_API_KEY, 
    DASHSCOPE_BASE_URL, 
    CHAT_MODEL
)
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
    # We search for top 5 relevant chunks
    search_results = search_files(query, limit=5)
    
    context_str = ""
    sources = []
    
    if search_results:
        context_parts = []
        for i, res in enumerate(search_results):
            # Extract content snippet
            content = res.get('ocr_text', '') or "No text content available."
            # Truncate if too long (e.g. 1000 chars per chunk)
            content = content[:1000]
            
            filename = res.get('filename', 'Unknown File')
            file_type = res.get('type', 'unknown')
            score = res.get('distance', 0)
            
            context_parts.append(f"--- Document {i+1} ---\nFile: {filename} ({file_type})\nContent: {content}\n")
            
            # Record source for citation
            sources.append({
                "filename": filename,
                "type": file_type,
                "path": res.get('path', ''),
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

    messages = [{"role": "system", "content": system_prompt}]
    
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

    # 3. Call LLM
    try:
        response = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=messages,
            stream=stream,
            temperature=0.7,
        )
        
        return response, sources
        
    except Exception as e:
        return f"Error communicating with AI service: {str(e)}", []
