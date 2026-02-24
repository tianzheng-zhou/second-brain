import chainlit as cl
import os
import shutil
import tempfile
from pathlib import Path
import sys

# Add project root to sys.path to ensure imports work correctly
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import custom data layer
from personal_brain.core.chainlit_datalayer import SQLiteDataLayer

# Import existing backend logic
from personal_brain.core.ask import ask_brain
from personal_brain.core.ingestion import ingest_path
from personal_brain.core.database import (
    get_all_files, 
    delete_file_record,
    save_conversation
)

# Initialize Data Layer
cl_data_layer = SQLiteDataLayer()

@cl.data_layer
def get_data_layer():
    return cl_data_layer

@cl.password_auth_callback
async def auth_callback(username, password):
    # print(f"[DEBUG] auth_callback for user: {username}")
    # Default simple authentication
    if username == "admin" and password == "admin":
        # Ensure user exists in DB and get stable ID
        persisted_user = await cl_data_layer.get_user(username)
        if not persisted_user:
            # Create user if not exists
            temp_user = cl.User(identifier=username)
            persisted_user = await cl_data_layer.create_user(temp_user)
        
        # Return User with STABLE ID from database
        # This ensures threads are linked to the same user across sessions
        user = cl.User(identifier=username)
        if persisted_user and persisted_user.id:
            # print(f"[DEBUG] Assigning persisted ID to user: {persisted_user.id}")
            # Force set the ID to match database
            # Note: cl.User might be a Pydantic model or DataClass, handling both
            if hasattr(user, "id"):
                user.id = persisted_user.id
            else:
                # Fallback if id is not a direct attribute (unlikely in Chainlit)
                object.__setattr__(user, "id", persisted_user.id)
            
            # Update metadata if needed
            if hasattr(user, "metadata"):
                user.metadata = persisted_user.metadata
                
        return user
    # print(f"[DEBUG] Auth failed for user: {username}")
    return None

@cl.on_chat_start
async def start():
    """Initialize the chat session."""
    # Initialize empty history for LLM context
    cl.user_session.set("history", [])
    
    # Save conversation metadata
    session_id = cl.context.session.id
    save_conversation({
        "id": session_id,
        "title": "New Chat", # TODO: Generate title
        "summary": "New conversation started."
    })
    
    # Display welcome message
    await cl.Message(content="👋 Welcome to **PersonalBrain**! \n\nUpload files to add them to your knowledge base, or ask questions to search your notes.").send()
    
    # User is already handled in auth_callback, no need to re-check here

@cl.on_chat_resume
async def on_chat_resume(thread: cl.types.ThreadDict):
    """Restore the chat session when a user clicks on a history item."""
    # Rebuild history for LLM context from the thread steps
    history = []
    # print(f"[DEBUG] on_chat_resume thread keys: {thread.keys()}")
    
    # Check if 'steps' is in thread, if not, try to fetch it or use empty
    steps = thread.get("steps", [])
    
    for step in steps:
        # Only include messages in context, not tool outputs or other steps
        if step["type"] == "user_message":
            history.append({"role": "user", "content": step["output"]})
        elif step["type"] == "assistant_message":
            history.append({"role": "assistant", "content": step["output"]})
    
    # Keep only last 10 turns (20 messages) to avoid token limit
    if len(history) > 20:
        history = history[-20:]
        
    cl.user_session.set("history", history)
    # print(f"[DEBUG] Resumed chat with {len(history)} messages in history")

@cl.set_chat_profiles
async def chat_profile():
    return [
        cl.ChatProfile(
            name="RAG Assistant",
            markdown_description="Ask questions about your documents.",
            icon="https://picsum.photos/200",
        )
    ]

@cl.action_callback("delete_file")
async def on_delete_file(action: cl.Action):
    file_id = action.payload.get("value")
    # Delete file logic
    try:
        if delete_file_record(file_id):
            await cl.Message(content=f"✅ File {file_id} deleted successfully.").send()
            # Refresh list if it was side view? Hard to refresh side view without new message.
        else:
            await cl.Message(content=f"❌ Failed to delete file {file_id}.").send()
    except Exception as e:
        await cl.Message(content=f"Error deleting file: {e}").send()

async def list_files_message(display_in_side_view=False):
    files = get_all_files()
    if not files:
        content = "No files found in database."
        if display_in_side_view:
             await cl.Message(content=content).send()
        return cl.Message(content=content)
    else:
        file_list_md = "**Your Knowledge Base:**\n\n"
        actions = []
        for f in files: 
            file_list_md += f"- **{f['filename']}** ({f['type']}) - {f.get('size_bytes', 0)//1024} KB\n"
            actions.append(
                cl.Action(name="delete_file", payload={"value": str(f['id'])}, label=f"🗑️ Delete {f['filename'][:15]}...")
            )
        
        if display_in_side_view:
             element = cl.Text(name="知识库列表", content=file_list_md, display="side", language="markdown")
             await cl.Message(content="✅ 列表已生成！\n请点击下方的 **“知识库列表”** 按钮/图标，在右侧侧边栏查看详细内容 👉", elements=[element]).send()
             return None
        
        # Limit actions for inline display to avoid clutter
        if len(actions) > 10:
            file_list_md += "\n*(Showing first 10 delete buttons)*"
            actions = actions[:10]
            
        return cl.Message(content=file_list_md, actions=actions)

@cl.on_message
async def main(message: cl.Message):
    """Handle incoming messages (text and files)."""
    
    # --- 1. Handle Commands ---
    if message.content.strip() == "/files":
        msg = await list_files_message(display_in_side_view=False)
        if msg:
            await msg.send()
        return

    if message.content.strip() == "/side":
        await list_files_message(display_in_side_view=True)
        return

    # --- 2. Handle File Uploads & Context ---
    uploaded_file_paths = []
    
    if message.elements:
        # Create a persistent temp directory for this session/message 
        # (Note: In production, you might want better cleanup policies)
        temp_dir = os.path.join(tempfile.gettempdir(), "personal_brain_uploads")
        os.makedirs(temp_dir, exist_ok=True)
        
        for element in message.elements:
            if hasattr(element, "path"):
                original_name = element.name
                # Use a unique prefix to avoid collisions
                safe_name = f"{cl.user_session.get('id')}_{original_name}"
                dest_path = os.path.join(temp_dir, safe_name)
                
                shutil.copy2(element.path, dest_path)
                uploaded_file_paths.append(dest_path)
                
        if uploaded_file_paths:
             await cl.Message(content=f"📎 Received {len(uploaded_file_paths)} files. I can analyze them or save them to memory.").send()

    # --- 3. Handle Text Query (RAG) ---
    # Proceed if there is text OR files (if files only, treat as "analyze these files")
    if message.content or uploaded_file_paths:
        
        msg = cl.Message(content="")
        await msg.send()
        
        # Get chat history
        history = cl.user_session.get("history", [])
        
        # Construct query with file context if needed
        query_text = message.content or "Please analyze the uploaded files."
        
        # Add file paths to the system context or message
        # We append a hidden system-like instruction to the user message for the Agent to see
        if uploaded_file_paths:
            file_context = "\n\n[System Context] User uploaded files at these paths:\n" + "\n".join(uploaded_file_paths)
            full_query_for_agent = query_text + file_context
        else:
            full_query_for_agent = query_text

        try:
            # Call your existing RAG function
            # ask_brain returns (response_stream, sources)
            session_id = cl.context.session.id
            response_stream, sources = await cl.make_async(ask_brain)(
                full_query_for_agent, 
                history=history, 
                stream=True,
                conversation_id=session_id
            )
            
            full_response = ""
            
            if isinstance(response_stream, str):
                # Error message returned as string
                full_response = f"Error: {response_stream}"
                msg.content = full_response
                await msg.update()
            else:
                # Stream the response from OpenAI client generator
                for chunk in response_stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        token = chunk.choices[0].delta.content
                        full_response += token
                        await msg.stream_token(token)
                
                # Append sources if available
                if sources:
                    # Create a separate step for references to make it collapsible (as a step)
                    async with cl.Step(name="📚 References") as source_step:
                        source_text = ""
                        for i, src in enumerate(sources, 1):
                            # Use small text
                            source_text += f"{i}. {src['filename']} (Score: {src['score']:.4f})\n"
                        
                        source_step.output = source_text
                    
                    # Don't append to main message, just show the step
                    # Don't save sources text to history context, just the answer
                
                await msg.update()
                
                # Update history (keep last 10 turns)
                history.append({"role": "user", "content": message.content})
                history.append({"role": "assistant", "content": full_response})
                if len(history) > 20:
                    history = history[-20:]
                cl.user_session.set("history", history)
                
        except Exception as e:
            msg.content = f"Error processing query: {str(e)}"
            await msg.update()
