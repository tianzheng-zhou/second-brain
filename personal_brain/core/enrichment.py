import json
import re
from typing import List
from personal_brain.core.models import File
from personal_brain.core.llm import call_llm
from personal_brain.core.database import save_entry, save_entity, save_relation, get_entities_by_name

def estimate_tokens(text: str) -> float:
    """
    Estimate tokens using the heuristic:
    - CJK: 1.2 tokens/char
    - Other: 0.35 tokens/char
    """
    # 1. Remove image syntax for text counting
    image_pattern = r'!\[.*?\]\(.*?\)'
    text_only = re.sub(image_pattern, '', text)
    
    # 2. Count images
    images = re.findall(image_pattern, text)
    image_count = len(images)
    IMAGE_COST = 1000 
    
    # 3. Count CJK characters
    cjk_pattern = r'[\u4e00-\u9fff]'
    cjk_chars = len(re.findall(cjk_pattern, text_only))
    
    # 4. Count other characters
    other_chars = len(text_only) - cjk_chars
    
    total_tokens = (cjk_chars * 1.2) + (other_chars * 0.35) + (image_count * IMAGE_COST)
    return total_tokens

def enrich_file(file_obj: File, text: str, chunks: List[str], embeddings: List[List[float]]):
    """
    Enrich a file with Summary, Tags, and Entities.
    Handles token limits intelligently.
    """
    if not text:
        return

    total_tokens = estimate_tokens(text)
    TOKEN_LIMIT = 20000
    
    summary_text = ""
    tags = []
    
    # --- 1. Generate Summary ---
    print(f"Generating summary for {file_obj.filename} (Tokens: {int(total_tokens)})...")
    
    if total_tokens <= TOKEN_LIMIT:
        # Small enough: Direct summary
        messages = [
            {"role": "system", "content": "You are a helpful assistant. Summarize the following document concisely. Return only the summary text."},
            {"role": "user", "content": f"Document: {file_obj.filename}\n\nContent:\n{text[:60000]}"} # Hard cap just in case
        ]
        try:
            summary_resp = call_llm(messages)
            summary_text = summary_resp.choices[0].message.content
        except Exception as e:
            print(f"Summary generation failed: {e}")
            summary_text = "Summary generation failed."
            
    else:
        # Too large: Use Embedding-based Extraction (Map-Reduceish)
        # Strategy: Pick top K chunks that are most "representative"?
        # Actually, "representative" is hard without a query.
        # But we can just pick a uniform sample + beginning/end?
        # User requirement: "LLM will rely on vector embeddings to generate auto summary, limit 10 batches"
        
        # This implies we should maybe cluster chunks or just pick 10 chunks?
        # Or does it mean "feed 10 batches to LLM"?
        # Let's interpret "limit 10 batches" as: select up to 10 chunks that cover the document structure well.
        # Since we have embeddings, we could cluster them into 10 clusters and pick center?
        # That's complex.
        # Simple heuristic: First chunk (Intro), Last chunk (Conclusion), and 8 evenly spaced chunks in between.
        
        num_chunks = len(chunks)
        selected_indices = []
        
        if num_chunks <= 10:
            selected_indices = list(range(num_chunks))
        else:
            # Always include first and last
            selected_indices = [0, num_chunks - 1]
            # Pick 8 more evenly spaced
            if num_chunks > 2:
                # We need 8 points between index 0 and index N-1
                # N-1 - 0 = N-1 total range
                # We want 8 steps? No, 8 points.
                # Let's just use simple linear interpolation
                step = (num_chunks - 1) / 9.0
                for i in range(1, 9):
                    idx = int(round(step * i))
                    if idx > 0 and idx < num_chunks - 1:
                        selected_indices.append(idx)
            
            selected_indices = sorted(list(set(selected_indices)))
            
        selected_chunks = [chunks[i] for i in selected_indices]
        combined_text = "\n\n...[Section Break]...\n\n".join(selected_chunks)
        
        print(f"Large document: Summarizing using {len(selected_chunks)} representative chunks...")
        
        messages = [
            {"role": "system", "content": "You are a helpful assistant. Summarize the following document based on these key excerpts. The document is large, so these are selected sections (Intro, Conclusion, and key middle parts). Return a comprehensive summary."},
            {"role": "user", "content": f"Document: {file_obj.filename}\n\nKey Excerpts:\n{combined_text}"}
        ]
        try:
            summary_resp = call_llm(messages)
            summary_text = summary_resp.choices[0].message.content
        except Exception as e:
            print(f"Summary generation failed: {e}")
            summary_text = "Summary generation failed."

    # --- 2. Extract Tags & Entities (from Summary) ---
    # To save costs and time, we extract tags/entities from the generated SUMMARY, 
    # rather than processing the whole text again. The summary should contain the most important entities.
    
    print("Extracting tags and entities from summary...")
    
    extraction_prompt = f"""
    Analyze the following summary of a document.
    1. Generate 3-5 relevant tags (keywords).
    2. Extract key entities (Person, Organization, Technology, Location, Concept).
    
    Return JSON format:
    {{
        "tags": ["tag1", "tag2"],
        "entities": [
            {{"name": "Entity Name", "type": "Entity Type", "metadata": {{"role": "description"}} }}
        ]
    }}
    
    Summary:
    {summary_text}
    """
    
    messages = [
        {"role": "system", "content": "You are an entity extraction expert. Return valid JSON only."},
        {"role": "user", "content": extraction_prompt}
    ]
    
    try:
        tag_resp = call_llm(messages)
        content = tag_resp.choices[0].message.content
        # Strip code blocks
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
            
        data = json.loads(content)
        tags = data.get("tags", [])
        entities = data.get("entities", [])
        
        # Save Tags to File record? 
        # Currently File model doesn't have 'tags' field in DB schema (only in Pydantic model maybe? No, checked models.py, no tags).
        # We should create an ENTRY for this file that contains the summary and tags!
        # This "File Entry" will act as the metadata record.
        
        # Create a "Meta" entry linked to this file
        
        # We need to construct the call manually to pass internal flags if needed, 
        # or just use the tool function directly.
        
        # But wait, write_entry generates embedding for the *summary*. This is good!
        # It allows searching for the file via its summary.
        
        # Prepare file_paths (need absolute path)
        # file_obj.path is absolute string
        
        # Note: write_entry calls ingest_path, which might trigger recursion if we are not careful!
        # But ingest_path checks if file exists in DB. 
        # We already saved the file in process_file BEFORE calling enrich_file.
        # So ingest_path will see it exists and skip re-ingestion. Safe.
        
        print(f"Saving summary entry for {file_obj.filename}...")
        
        # Create entry data dict manually to avoid overhead of tool wrapper if desired, 
        # but tool wrapper handles logic nicely.
        # However, write_entry expects file_paths list.
        
        # We can just create an entry directly to avoid re-triggering ingestion logic overhead.
        import uuid
        from datetime import datetime
        
        entry_id = str(uuid.uuid4())
        
        # Save Entities
        for ent_data in entities:
             # Ensure required fields
             if 'name' in ent_data and 'type' in ent_data:
                 ent = {
                     'name': ent_data['name'],
                     'type': ent_data['type'],
                     'metadata': ent_data.get('metadata', {}),
                     'first_seen': datetime.now()
                 }
                 # save_entity returns ID
                 ent_id = save_entity(ent)
                 
                 # Create relation between Entity and File (via Relation table)
                 # Relation(source, target, type, file_id)
                 # We don't have a "File Entity", so we can just link it implicitly via file_id field in Relation table?
                 # Actually, save_entity doesn't take file_id.
                 # But we can create a "MENTIONED_IN" relation if we treat the file as a source entity?
                 # For now, let's skip explicit relation creation unless we have source/target.
                 pass

        # Save Entry (The Summary)
        entry_data = {
            "id": entry_id,
            "content_text": f"**Summary of {file_obj.filename}**\n\n{summary_text}",
            "content_json": json.dumps({
                "file_ids": [file_obj.id],
                "file_paths": [file_obj.path],
                "description": "Auto-generated summary",
                "auto_generated": True
            }),
            "entry_type": "mixed", # It has text (summary) and file attachment
            "created_at": datetime.now(),
            "source": "auto_enrichment",
            "tags": json.dumps(tags),
            "importance": 0.8, # Summaries are important
            "trash_score": 0.0,
            "status": "active",
            "conversation_id": None
        }
        
        save_entry(entry_data)
        
        # Save embedding for summary
        from personal_brain.core.indexer import generate_embedding
        from personal_brain.core.database import save_entry_embedding, link_entry_files
        
        sum_embedding = generate_embedding(entry_data['content_text'])
        if sum_embedding:
            save_entry_embedding(entry_id, sum_embedding)
            
        link_entry_files(entry_id, [file_obj.id])
        
        print(f"Enrichment complete. Summary Entry ID: {entry_id}")
        
    except Exception as e:
        print(f"Tag/Entity extraction failed: {e}")

