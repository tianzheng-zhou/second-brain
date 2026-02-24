import json
import uuid
import dateparser
from datetime import datetime
from personal_brain.core.database import (
    save_entry, save_entry_embedding, link_entry_files, delete_entry_record,
    save_entity, save_relation, get_entities_by_name, get_entity_relations
)
from personal_brain.core.ingestion import ingest_path
from personal_brain.core.indexer import generate_embedding
from personal_brain.core.search import search_files
from personal_brain.core.llm import call_llm

def write_entry(content, file_paths=None, time_hint=None, source="web_chat", tags=None, importance=0.5):
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
    
    # 3. Process Files (if any)
    processed_file_ids = []
    file_info_list = []
    if file_paths:
        print(f"Processing {len(file_paths)} files for entry...")
        for path in file_paths:
            # ingest_path now returns stats with file_ids
            stats = ingest_path(path)
            if stats.get("file_ids"):
                processed_file_ids.extend(stats["file_ids"])
                file_info_list.append({"path": path, "status": "ingested"})
            else:
                file_info_list.append({"path": path, "status": "failed", "error": stats.get("errors")})

    # 4. Construct Entry Object
    entry_type = "text_only"
    if processed_file_ids:
        entry_type = "mixed" if content.get("text") else "file_only"
    
    content_text = content.get("text", "")
    content_json = json.dumps({
        "file_paths": file_paths or [],
        "file_ids": processed_file_ids,
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
    
    # 5. Save to DB
    if save_entry(entry_data):
        # 6. Generate Embedding
        if content_text:
            embedding = generate_embedding(content_text)
            if embedding:
                save_entry_embedding(entry_id, embedding)
        
        # 7. Link Files
        if processed_file_ids:
            link_entry_files(entry_id, processed_file_ids)

        # 8. Extract Entities (Auto)
        if content_text:
            try:
                # Extract and Save
                entities_json = extract_entities(content_text)
                data = json.loads(entities_json)
                
                # Save entities
                for ent in data.get("entities", []):
                    ent_id = save_entity(ent)
                    
                # Save relations
                for rel in data.get("relations", []):
                    # We need IDs for relations. 
                    # If extract_entities returned names, we need to map them back to IDs.
                    # Simple approach: save_entity returns ID.
                    # But relations rely on source/target names in the extraction result.
                    # We should probably map names to IDs first.
                    
                    # For now, let's just save them if we can resolve names to IDs
                    # Or modify save_relation to look up by name? 
                    # No, better to look up here.
                    src_ents = get_entities_by_name(rel['source'])
                    tgt_ents = get_entities_by_name(rel['target'])
                    
                    if src_ents and tgt_ents:
                        # Pick best match (simplest: first one)
                        rel['source'] = src_ents[0]['id']
                        rel['target'] = tgt_ents[0]['id']
                        rel['file_id'] = processed_file_ids[0] if processed_file_ids else None
                        save_relation(rel)
                        
            except Exception as e:
                print(f"Entity extraction warning: {e}")
            
        return json.dumps({
            "entry_id": entry_id, 
            "status": "success", 
            "message": f"Entry saved at {created_at}",
            "files_processed": len(processed_file_ids)
        })
    else:
        return json.dumps({"status": "error", "message": "Database save failed"})

def search_semantic(query, time_hint=None, limit=5):
    """
    Search semantic memory with time filtering.
    """
    time_range = None
    if time_hint:
        # Simple parsing logic
        # For "last week", we want (now-7d, now)
        # dateparser.parse returns a single date.
        # We need a range.
        # This is complex. Let's simplify: 
        # If time_hint is specific (e.g. 2023), start=2023-01-01, end=2023-12-31?
        # For now, let's just use dateparser to get a 'start' point and assume end is now?
        # Or just pass the hint to LLM to resolve to ISO range?
        # Better: Let Agent resolve it? No, search_semantic is a tool called BY Agent.
        # Agent should pass ISO range? 
        # PRD says "time_hint" is string.
        # Let's try to parse "start" date.
        dt = dateparser.parse(time_hint)
        if dt:
             # Assume query is "after this date"
             time_range = (dt, datetime.now())
    
    results = search_files(query, limit=limit, time_range=time_range)
    
    # Format results for LLM
    formatted = []
    for res in results:
        formatted.append({
            "content": res.get("content", ""),
            "type": res.get("type", "unknown"),
            "score": res.get("score", 0),
            "created_at": str(res.get("created_at", "unknown")),
            "filename": res.get("filename", "")
        })
        
    return json.dumps(formatted)

def search_graph(entity_name, depth=1):
    """
    Search knowledge graph for an entity and its relations.
    """
    entities = get_entities_by_name(entity_name)
    if not entities:
        return json.dumps({"message": f"No entity found for '{entity_name}'"})
        
    result = {
        "entity": entities[0],
        "relations": []
    }
    
    # Get relations for the top match
    rels = get_entity_relations(entities[0]['id'])
    for r in rels:
        result["relations"].append({
            "source": r['source_name'],
            "target": r['target_name'],
            "type": r['type'],
            "confidence": r['confidence']
        })
        
    return json.dumps(result)

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
            "description": "Write a new memory entry (note, idea, conversation summary). Can include file paths.",
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
                    "file_paths": {"type": "array", "items": {"type": "string"}, "description": "List of absolute file paths to ingest and link"},
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
            "name": "search_graph",
            "description": "Search knowledge graph for an entity and its relations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_name": {"type": "string", "description": "Name of the entity to search"},
                    "depth": {"type": "integer", "description": "Search depth (default 1)"}
                },
                "required": ["entity_name"]
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
    "search_graph": search_graph,
    "extract_entities": extract_entities,
    "delete_entry": delete_entry
}
