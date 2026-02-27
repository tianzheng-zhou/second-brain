import json
import uuid
import dateparser
from datetime import datetime
from personal_brain.core.database import (
    save_entry, save_entry_embedding, link_entry_files, delete_entry_record,
    save_entity, save_relation, get_entities_by_name, get_entity_relations, get_entry,
    get_db_connection
)
from personal_brain.core.ingestion import ingest_path
from personal_brain.core.indexer import generate_embedding
from personal_brain.core.search import search_files
from personal_brain.core.llm import call_llm

def write_entry(content, file_paths=None, time_hint=None, source="web_chat", tags=None, importance=0.5, save_to_graph=True, conversation_id=None):
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
        "trash_score": 0.0,
        "status": "active",
        "conversation_id": conversation_id
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

        # 8. Extract Entities (Auto if requested)
        if save_to_graph and content_text:
            try:
                # Extract and Save
                entities_json = extract_entities(content_text)
                data = json.loads(entities_json)
                
                # Save entities
                for ent in data.get("entities", []):
                    ent_id = save_entity(ent)
                    
                # Save relations
                for rel in data.get("relations", []):
                    src_ents = get_entities_by_name(rel['source'])
                    tgt_ents = get_entities_by_name(rel['target'])
                    
                    if src_ents and tgt_ents:
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

def update_entry(entry_id, new_content=None, new_tags=None):
    """
    Update an existing entry.
    """
    entry = get_entry(entry_id)
    if not entry:
        return json.dumps({"status": "error", "message": f"Entry {entry_id} not found"})
        
    if new_content:
        entry['content_text'] = new_content
        # Re-generate embedding
        embedding = generate_embedding(new_content)
        if embedding:
            save_entry_embedding(entry_id, embedding)
            
    if new_tags:
        entry['tags'] = json.dumps(new_tags)
        
    if save_entry(entry):
        return json.dumps({"status": "success", "message": f"Entry {entry_id} updated"})
    else:
        return json.dumps({"status": "error", "message": "Database update failed"})

def search_semantic(query, time_hint=None, time_range_start=None, time_range_end=None, entry_type=None, limit=20, file_id=None):
    """
    Search semantic memory with time filtering.
    """
    time_range = None
    
    # Prefer explicit range
    if time_range_start:
        start_dt = dateparser.parse(time_range_start)
        end_dt = dateparser.parse(time_range_end) if time_range_end else datetime.now()
        if start_dt:
            time_range = (start_dt, end_dt)
            
    # Fallback to hint
    elif time_hint:
        dt = dateparser.parse(time_hint)
        if dt:
             # Assume query is "after this date" or around this date
             # If it's a specific date like "2023-01-01", maybe we want that day?
             # For now, keep "since then" logic as default unless refined
             time_range = (dt, datetime.now())
    
    results = search_files(query, limit=limit, time_range=time_range, entry_type=entry_type, file_id=file_id)
    
    # Format results for LLM
    formatted = []
    for res in results:
        ref_type = None
        ref_id = None
        res_type = res.get("type")
        if res_type == "chunk":
            ref_type = "chunk"
            chunk_index = res.get("chunk_index")
            file_id_val = res.get("file_id")
            if file_id_val is not None and chunk_index is not None:
                ref_id = f"{file_id_val}_{chunk_index}"
        elif res_type == "file":
            ref_type = "file"
            ref_id = res.get("file_id")
        elif res_type == "entry":
            ref_type = "entry"
            ref_id = res.get("entry_id")

        formatted.append({
            "content": res.get("content", ""),
            "type": res.get("type", "unknown"),
            "score": res.get("score", 0),
            "created_at": str(res.get("created_at", "unknown")),
            "filename": res.get("filename", ""),
            "file_id": res.get("file_id"),
            "chunk_index": res.get("chunk_index"),
            "entry_id": res.get("entry_id"),
            "entry_type": res.get("entry_type"),
            "ref_type": ref_type,
            "ref_id": ref_id
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

def read_document(query, by="filename"):
    """
    Read the full content of a document (file).
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if by == "id":
            cursor.execute("SELECT * FROM files WHERE id = ?", (query,))
        else:
            # Search by filename (partial match)
            cursor.execute("SELECT * FROM files WHERE filename LIKE ?", (f"%{query}%",))
        
        row = cursor.fetchone()
        if row:
            file_data = dict(row)
            content = file_data.get('ocr_text') or ""
            
            # Estimate tokens more accurately
            # Heuristic: 
            # - CJK characters: ~1 token (range 0.6-1.5, use 1.2 for safety)
            # - Non-CJK (English/Code): ~0.3 tokens per char (1 token ~= 3-4 chars)
            # - Images: Count markdown image syntax ![]() and add "virtual tokens" (e.g. 1000 per image)
            
            import re
            
            # 1. Count images
            image_pattern = r'!\[.*?\]\(.*?\)'
            images = re.findall(image_pattern, content)
            image_count = len(images)
            IMAGE_COST = 1000 # Conservative estimate for vision processing
            
            # 2. Remove image syntax for text counting to avoid double counting
            text_only = re.sub(image_pattern, '', content)
            
            # 3. Count CJK characters
            # Range: \u4e00-\u9fff (Common CJK)
            cjk_pattern = r'[\u4e00-\u9fff]'
            cjk_chars = len(re.findall(cjk_pattern, text_only))
            
            # 4. Count other characters
            other_chars = len(text_only) - cjk_chars
            
            # 5. Calculate Total Estimated Tokens
            total_tokens = (cjk_chars * 1.2) + (other_chars * 0.35) + (image_count * IMAGE_COST)
            
            TOKEN_LIMIT = 20000
            
            if total_tokens > TOKEN_LIMIT:
                # Fallback to semantic search automatically
                print(f"Document too large ({int(total_tokens)} est. tokens > {TOKEN_LIMIT}). Suggesting semantic search...")
                
                # Perform a preliminary semantic search to get "summary" like chunks for the user
                # Query: "summary abstract introduction conclusion"
                # This helps giving the user SOME content to start with.
                # Since we don't have the user query, we use a generic summarization query.
                
                # Import here to avoid circular dependency
                from personal_brain.core.search import search_files
                
                # Get top 5 chunks that look like summary/intro
                preview_results = search_files(
                    query="summary abstract introduction conclusion main points", 
                    limit=5, 
                    file_id=file_data['id']
                )
                
                formatted_preview = []
                for res in preview_results:
                    # Truncate if individual chunk is huge (unlikely given splitting)
                    chunk_text = res.get('content', '')[:500] + "..." if len(res.get('content', '')) > 500 else res.get('content', '')
                    formatted_preview.append(f"[Chunk {res.get('chunk_index', '?')}] {chunk_text}")
                
                preview_text = "\n\n".join(formatted_preview)
                
                return json.dumps({
                    "status": "too_large", 
                    "message": f"Document '{file_data['filename']}' is too large ({int(total_tokens)} tokens). Reading full content is disabled to prevent context overflow.",
                    "suggestion": f"I have performed a preliminary scan. Here are some key excerpts. Please use 'search_semantic' with specific questions to explore further.",
                    "file_id": file_data['id'],
                    "filename": file_data['filename'],
                    "reranked_preview": preview_text
                })
            
            return json.dumps({
                "filename": file_data['filename'],
                "file_id": file_data['id'],
                "content": content,
                "type": file_data['type']
            })
        else:
            return json.dumps({"status": "error", "message": f"File not found for query: {query}"})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})
    finally:
        conn.close()

# Tool Definitions for OpenAI
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_document",
            "description": "Read the FULL text content of a specific file/document. Useful when search results are fragmented.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The filename (partial match) or file ID"},
                    "by": {"type": "string", "enum": ["filename", "id"], "default": "filename"}
                },
                "required": ["query"],
                "description": "Read the FULL text content of a specific file/document. If doc > 20k tokens, returns summary excerpts and suggests semantic search."
            }
        }
    },
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
                    "importance": {"type": "number", "description": "0.0 to 1.0"},
                    "save_to_graph": {"type": "boolean", "description": "Whether to automatically extract entities and relations to graph (default True)"}
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_entry",
            "description": "Update an existing entry content or tags.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entry_id": {"type": "string", "description": "ID of the entry to update"},
                    "new_content": {"type": "string", "description": "New text content"},
                    "new_tags": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["entry_id"]
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
                    "query": {"type": "string", "description": "搜索关键词。提取用户问题的核心实体词（如'微分 导数 定义'），不要包含礼貌用语或完整问句"},
                    "time_hint": {"type": "string", "description": "Optional time filter (e.g. 'last month')"},
                    "time_range_start": {"type": "string", "description": "ISO8601 start date"},
                    "time_range_end": {"type": "string", "description": "ISO8601 end date"},
                    "entry_type": {"type": "string", "description": "Filter by type: 'file_only', 'text_only', 'mixed'"},
                    "file_id": {"type": "string", "description": "Filter by specific file ID (useful for large documents)"},
                    "limit": {"type": "integer", "description": "Number of results to return (default 20). Increase for comprehensive research."}
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
    "update_entry": update_entry,
    "search_semantic": search_semantic,
    "search_graph": search_graph,
    "extract_entities": extract_entities,
    "delete_entry": delete_entry,
    "read_document": read_document
}
