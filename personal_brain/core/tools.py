import json
import uuid
import dateparser
from datetime import datetime
from personal_brain.core.database import save_entry, save_entry_embedding, link_entry_files, delete_entry_record
from personal_brain.core.indexer import generate_embedding
from personal_brain.core.search import search_files
from personal_brain.core.llm import call_llm

def write_entry(content, files=None, time_hint=None, source="web_chat", tags=None, importance=0.5):
    """
    Writes a new memory entry.
    """
    # 1. Parse time
    created_at = datetime.now()
    if time_hint:
        parsed = dateparser.parse(time_hint)
        if parsed:
            created_at = parsed
            
    # 2. Create ID
    entry_id = str(uuid.uuid4())
    
    # 3. Construct Entry Object
    entry_type = "text_only"
    if files:
        entry_type = "mixed"
    
    content_text = content.get("text", "")
    content_json = json.dumps({
        "files": files or [],
        "description": content.get("description", ""),
        "conversation_turns": content.get("conversation_turns", [])
    })
    
    entry_data = {
        "id": entry_id,
        "content_text": content_text,
        "content_json": content_json,
        "entry_type": entry_type,
        "created_at": created_at,
        "source": source,
        "tags": json.dumps(tags or []),
        "importance": importance,
        "trash_score": 0.0, # TODO: calculate score
        "status": "active"
    }
    
    # 4. Save to DB
    if save_entry(entry_data):
        # 5. Generate Embedding
        if content_text:
            embedding = generate_embedding(content_text)
            if embedding:
                save_entry_embedding(entry_id, embedding)
        
        # 6. Link Files
        if files:
            file_ids = [f.get("id") for f in files if f.get("id")]
            link_entry_files(entry_id, file_ids)

        # 7. Extract Entities (Optional/Async in future)
        # entities = extract_entities(content_text)
        # TODO: Save entities to graph
            
        return json.dumps({"entry_id": entry_id, "status": "success", "message": f"Entry saved at {created_at}"})
    else:
        return json.dumps({"status": "error", "message": "Database save failed"})

def search_semantic(query, time_hint=None, limit=5):
    """
    Search semantic memory with time filtering.
    """
    # For now, time filtering is not implemented in search_files, only semantic search.
    # Future: parse time_hint and filter results.
    
    results = search_files(query, limit=limit)
    
    # Format results for LLM
    formatted = []
    for res in results:
        formatted.append({
            "content": res.get("content", ""),
            "type": res.get("type", "unknown"),
            "score": res.get("score", 0),
            "created_at": str(res.get("created_at", "unknown"))
        })
        
    return json.dumps(formatted)

def extract_entities(text):
    if not text:
        return json.dumps({"entities": [], "relations": []})
        
    messages = [
        {"role": "system", "content": "Extract entities (name, type) and relations (source, target, type) from the text. Return valid JSON only."},
        {"role": "user", "content": text}
    ]
    try:
        resp = call_llm(messages)
        content = resp.choices[0].message.content
        # Strip markdown ```json
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        return content # Return JSON string
    except Exception as e:
        return json.dumps({"error": str(e)})

def delete_entry(entry_ids, reason="User request", confirmed=False):
    """
    Delete entries by ID.
    If DELETE_CONFIRMATION is True and confirmed is False, returns a confirmation request.
    """
    from personal_brain.config import DELETE_CONFIRMATION
    
    if isinstance(entry_ids, str):
        entry_ids = [entry_ids]
    
    # Check confirmation
    if DELETE_CONFIRMATION and not confirmed:
        # Return a special JSON indicating confirmation is needed
        return json.dumps({
            "status": "confirmation_needed",
            "message": f"Are you sure you want to delete {len(entry_ids)} entries? Reason: {reason}",
            "entry_ids": entry_ids,
            "reason": reason
        })
        
    deleted_count = 0
    for eid in entry_ids:
        if delete_entry_record(eid):
            deleted_count += 1
            
    return json.dumps({"status": "success", "deleted_count": deleted_count})

# Tool Definitions for OpenAI
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "write_entry",
            "description": "Write a new memory entry (note, idea, conversation summary).",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "The main text content"},
                            "description": {"type": "string", "description": "Description of files if any"}
                        },
                        "required": ["text"]
                    },
                    "time_hint": {"type": "string", "description": "Natural language time reference (e.g. 'last week', 'yesterday')"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "importance": {"type": "number", "description": "0.0 to 1.0"}
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_semantic",
            "description": "Search for memories or files using semantic query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                    "time_hint": {"type": "string", "description": "Optional time filter (e.g. 'last month')"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "extract_entities",
            "description": "Extract entities and relations from text for knowledge graph.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to analyze"}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_entry",
            "description": "Delete memory entries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entry_ids": {"type": "array", "items": {"type": "string"}, "description": "List of entry IDs to delete"},
                    "reason": {"type": "string"},
                    "confirmed": {"type": "boolean", "description": "Set to true only if user has explicitly confirmed deletion."}
                },
                "required": ["entry_ids"]
            }
        }
    }
]

AVAILABLE_TOOLS = {
    "write_entry": write_entry,
    "search_semantic": search_semantic,
    "extract_entities": extract_entities,
    "delete_entry": delete_entry
}
