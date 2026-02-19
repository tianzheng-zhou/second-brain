import asyncio
import os
import argparse
from mcp.server.fastmcp import FastMCP
from personal_brain.core.search import search_files
from personal_brain.core.ingestion import ingest_path
from personal_brain.core.ask import ask_brain
from personal_brain.config import STORAGE_PATH

# Initialize FastMCP server
mcp = FastMCP("PersonalBrain")

@mcp.tool()
async def search_notes(query: str, limit: int = 5) -> str:
    """
    Search for relevant notes and files in the PersonalBrain.
    
    Args:
        query: The search query (e.g., "notes about python").
        limit: Max number of results to return.
    """
    results = search_files(query, limit)
    if not results:
        return "No matching results found."
        
    response = []
    for res in results:
        meta = f"File: {res['filename']} (Type: {res['type']}, Score: {res['distance']:.4f})"
        snippet = res['ocr_text'][:200].replace('\n', ' ') if res['ocr_text'] else "No text content."
        response.append(f"{meta}\nSnippet: {snippet}...\nPath: {res['path']}")
        
    return "\n---\n".join(response)

@mcp.tool()
async def ask_brain_agent(question: str) -> str:
    """
    Ask a question to the PersonalBrain agent using RAG.
    Use this when the user asks a question that requires synthesizing information from notes.
    
    Args:
        question: The user's question.
    """
    # Reuse the logic from ask.py, but without history for now (stateless tool)
    response_stream, sources = ask_brain(question, history=[], stream=False)
    
    if isinstance(response_stream, str):
        return f"Error: {response_stream}"
    
    # Check if response_stream is a string (error) or ChatCompletion object
    try:
        if hasattr(response_stream, 'choices'):
            answer = response_stream.choices[0].message.content
        else:
            return f"Error: Unexpected response format: {response_stream}"
            
        source_text = ""
        if sources:
            source_text = "\n\nSources:\n" + "\n".join([f"- {s['filename']}" for s in sources])
            
        return answer + source_text
    except Exception as e:
        return f"Error processing LLM response: {e}"

@mcp.tool()
async def ingest_content(path: str) -> str:
    """
    Ingest a file or directory into PersonalBrain.
    
    Args:
        path: Absolute path to the file or directory.
    """
    if not os.path.exists(path):
        return f"Error: Path {path} does not exist."
        
    try:
        ingest_path(path)
        return f"Successfully ingested content from {path}"
    except Exception as e:
        return f"Error ingesting content: {e}"

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PersonalBrain MCP Server")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "sse"], help="Transport protocol to use (default: stdio)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (SSE only)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to (SSE only)")
    args = parser.parse_args()

    if args.transport == "sse":
        print(f"Starting MCP server on http://{args.host}:{args.port}/sse", flush=True)
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")
