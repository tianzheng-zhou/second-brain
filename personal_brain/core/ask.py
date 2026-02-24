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
    
    # 1. First turn: Decide intent (Tool or Answer)
    # We use non-streaming first to detect tool calls reliably
    try:
        response = call_llm(messages, tools=TOOL_DEFINITIONS, stream=False)
        msg = response.choices[0].message
    except Exception as e:
        return f"Error communicating with AI: {str(e)}", []
    
    sources = []
    
    # 2. Handle Tool Calls
    if msg.tool_calls:
        # Append the assistant's "thinking" (tool call) to history
        messages.append(msg)
        
        tool_calls_log = []
        tool_results_log = []
        
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
                                    "type": r.get("type", "unknown")
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
        
        # 3. Final Answer (Streaming)
        # Call LLM again with tool outputs
        if stream:
            return call_llm(messages, stream=True), sources
        else:
            return call_llm(messages, stream=False), sources
            
    else:
        # No tool called, just return the response
        # Since the caller expects a stream, and we already have a non-stream response...
        # We can either return the text (and let chainlit handle it) or re-stream.
        # Chainlit's `main` handles `isinstance(response_stream, str)` as error.
        # But `ChatCompletion` object is not a string.
        # To be safe and support streaming effect, let's re-call with stream=True.
        # This costs 2x tokens for the first chunk but ensures consistent behavior.
        # Optimization: We could yield the content from `msg` if we wrap it in a generator.
        
        if stream:
            # Re-generate with stream=True
            return call_llm(messages, stream=True), sources
        else:
            return response, sources
