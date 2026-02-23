import json
from personal_brain.core.llm import call_llm
from personal_brain.core.tools import TOOL_DEFINITIONS, AVAILABLE_TOOLS

def ask_brain(query: str, history: list = None, stream: bool = True):
    """
    Agent-based ask brain.
    """
    messages = [{"role": "system", "content": "You are PersonalBrain. You can store memories using write_entry or search for information using search_semantic. When answering based on search results, cite the sources. IMPORTANT: If a tool returns 'confirmation_needed', you MUST ask the user for confirmation clearly and explicitly, and do not proceed with the action until they confirm."}]
    
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
        
        for tool_call in msg.tool_calls:
            function_name = tool_call.function.name
            function_args_str = tool_call.function.arguments
            
            try:
                function_args = json.loads(function_args_str)
            except json.JSONDecodeError:
                function_args = {}
            
            # Execute Tool
            if function_name in AVAILABLE_TOOLS:
                print(f"Executing tool: {function_name} with args: {function_args}")
                tool_func = AVAILABLE_TOOLS[function_name]
                
                try:
                    tool_result = tool_func(**function_args)
                    tool_result_str = str(tool_result)
                except Exception as e:
                    tool_result_str = json.dumps({"error": str(e)})
                
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
