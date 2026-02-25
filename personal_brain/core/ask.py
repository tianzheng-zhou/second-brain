import json
from personal_brain.core.llm import call_llm
from personal_brain.core.tools import TOOL_DEFINITIONS, AVAILABLE_TOOLS
from personal_brain.core.database import log_agent_action

def ask_brain(query: str, history: list = None, stream: bool = True, conversation_id: str = None):
    """
    Agent-based ask brain.
    """
    messages = [{"role": "system", "content": """You are PersonalBrain. 
    Core Capabilities:
    1. **Memory**: Store important information, files, and conversations using `write_entry`. 
       - If the user uploads files (you see file paths in context), use `write_entry(content=..., file_paths=[...])` to save them.
       - Always summarize what you are saving.
       - You can control graph extraction with `save_to_graph`. Default is True.
    2. **Retrieval**: 
       - Use `search_semantic` for natural language queries.
         - IMPORTANT: If user mentions a time range (e.g., "last week"), try to convert it to ISO8601 `time_range_start` and `time_range_end`.
         - Use `entry_type` to filter ('file', 'text', 'mixed') if user specifically asks for files or notes.
       - Use `search_graph` for entity-specific queries (e.g. "who is Zhang San", "projects related to Rust").
    3. **Maintenance**:
       - Use `update_entry` to modify existing entries if user corrects you or adds details.

    IMPORTANT: 
    - If a tool returns 'confirmation_needed', you MUST ask the user for confirmation clearly and explicitly.
    - When answering based on search results, cite the sources."""}]
    
    # Filter history to remove Tool messages if any (to keep context clean for now, or keep them?)
    # OpenAI requires Tool messages to follow Tool Calls.
    # If history comes from Chainlit, it's just user/assistant text.
    if history:
        messages.extend(history)
        
    messages.append({"role": "user", "content": query})
    
    sources = []
    MAX_TURNS = 5
    
    for turn in range(MAX_TURNS):
        # We use non-streaming first to detect tool calls reliably
        try:
            response = call_llm(messages, tools=TOOL_DEFINITIONS, stream=False)
            msg = response.choices[0].message
        except Exception as e:
            return f"Error communicating with AI: {str(e)}", []
        
        # If no tool calls, this is the final answer (or if it's the last turn, we accept it)
        if not msg.tool_calls:
            if stream:
                # Re-generate with stream=True
                return call_llm(messages, stream=True), sources
            else:
                return response, sources
        
        # Handle Tool Calls
        # Append the assistant's "thinking" (tool call) to history
        messages.append(msg)
        
        tool_calls_log = []
        tool_results_log = []
        
        print(f"Turn {turn+1}: Agent is using tools...")
        
        for tool_call in msg.tool_calls:
            function_name = tool_call.function.name
            function_args_str = tool_call.function.arguments
            
            try:
                function_args = json.loads(function_args_str)
            except json.JSONDecodeError:
                function_args = {}
            
            # Inject conversation_id if tool supports it (write_entry)
            if function_name == "write_entry" and conversation_id:
                function_args["conversation_id"] = conversation_id
            
            # Execute Tool
            if function_name in AVAILABLE_TOOLS:
                print(f"Executing tool: {function_name} with args: {function_args}")
                tool_func = AVAILABLE_TOOLS[function_name]
                
                tool_calls_log.append({"name": function_name, "args": function_args})
                
                try:
                    tool_result = tool_func(**function_args)
                    
                    # Special handling for read_document "too_large" status
                    # If result is JSON string, parse it to check status
                    try:
                        res_data = json.loads(tool_result) if isinstance(tool_result, str) else tool_result
                        if isinstance(res_data, dict) and res_data.get("status") == "too_large":
                            # The tool itself now returns a "reranked_preview" (summary chunks)
                            # We don't need to do extra work here, just pass the result back to LLM.
                            # The LLM will see the "message" and "reranked_preview" and "suggestion".
                            pass
                    except:
                        pass
                        
                    tool_result_str = str(tool_result)
                except Exception as e:
                    tool_result_str = json.dumps({"error": str(e)})
                
                tool_results_log.append(tool_result_str)
                
                # Extract sources if search
                if function_name == "search_semantic":
                    try:
                        # tool_result is already a JSON string from tools.py
                        results = json.loads(tool_result_str)
                        if isinstance(results, list):
                            for r in results:
                                sources.append({
                                    "filename": r.get("filename", "Unknown"),
                                    "score": r.get("score", 0),
                                    "type": r.get("type", "unknown"),
                                    "content": r.get("content", ""),
                                    "ref_type": r.get("ref_type"),
                                    "ref_id": r.get("ref_id"),
                                    "file_id": r.get("file_id"),
                                    "entry_id": r.get("entry_id"),
                                    "chunk_index": r.get("chunk_index"),
                                    "entry_type": r.get("entry_type")
                                })
                    except:
                        pass

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": function_name,
                    "content": tool_result_str
                })
        
        # Log Audit
        if conversation_id:
            log_agent_action(conversation_id, query, tool_calls_log, tool_results_log)
            
    # If we fall through the loop (max turns reached), force a final response
    if stream:
        return call_llm(messages, stream=True), sources
    else:
        return call_llm(messages, stream=False), sources
