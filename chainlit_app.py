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
    delete_file_record
)

# Initialize Data Layer
cl_data_layer = SQLiteDataLayer()

@cl.data_layer
def get_data_layer():
    return cl_data_layer

@cl.password_auth_callback
async def auth_callback(username, password):
    print(f"[DEBUG] auth_callback for user: {username}")
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
            print(f"[DEBUG] Assigning persisted ID to user: {persisted_user.id}")
            # Force set the ID to match database
            # Note: cl.User might be a Pydantic model or DataClass, handling both
            if hasattr(user, "id"):
                user.id = persisted_user.id
            else:
                # Fallback if id is not a direct attribute (unlikely in Chainlit)
                object.__setattr__(user, "id", persisted_user.id)
        return user
    print(f"[DEBUG] Auth failed for user: {username}")
    return None

@cl.on_chat_start
async def start():
    """Initialize the chat session."""
    # Initialize empty history for LLM context
    cl.user_session.set("history", [])
    
    # Display welcome message
    await cl.Message(content="👋 Welcome to **PersonalBrain**! \n\nUpload files to add them to your knowledge base, or ask questions to search your notes.").send()
    
    # User is already handled in auth_callback, no need to re-check here

@cl.on_chat_resume
async def on_chat_resume(thread: cl.types.ThreadDict):
    """Restore the chat session when a user clicks on a history item."""
    # Rebuild history for LLM context from the thread steps
    history = []
    for step in thread["steps"]:
        # Only include messages in context, not tool outputs or other steps
        if step["type"] == "user_message":
            history.append({"role": "user", "content": step["output"]})
        elif step["type"] == "assistant_message":
            history.append({"role": "assistant", "content": step["output"]})
    
    # Keep only last 10 turns (20 messages) to avoid token limit
    if len(history) > 20:
        history = history[-20:]
        
    cl.user_session.set("history", history)

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
    file_id = action.value
    # Delete file logic
    try:
        if delete_file_record(file_id):
            await cl.Message(content=f"✅ File {file_id} deleted successfully.").send()
        else:
            await cl.Message(content=f"❌ Failed to delete file {file_id}.").send()
    except Exception as e:
        await cl.Message(content=f"Error deleting file: {e}").send()

@cl.on_message
async def main(message: cl.Message):
    """Handle incoming messages (text and files)."""
    
    # --- 1. Handle Commands ---
    if message.content.strip() == "/files":
        # List all files with delete buttons
        files = get_all_files()
        if not files:
            await cl.Message(content="No files found in database.").send()
        else:
            # Create a list of actions for each file
            actions = []
            file_list_md = "**Your Files:**\n\n"
            
            # Show top 10 files to avoid clutter, or maybe just list them all?
            # Actions limit might exist.
            for f in files[:10]: # Limit to 10 for UI safety
                file_list_md += f"- **{f['filename']}** ({f['type']})\n"
                actions.append(
                    cl.Action(name="delete_file", value=str(f['id']), label=f"Delete {f['filename'][:10]}...")
                )
            
            await cl.Message(content=file_list_md, actions=actions).send()
        return

    # --- 2. Handle File Uploads ---
    if message.elements:
        processing_msg = cl.Message(content="📥 Processing uploaded files...")
        await processing_msg.send()
        
        count = 0
        # Create a temporary directory to handle uploads safely
        temp_dir = tempfile.mkdtemp()
        
        try:
            for element in message.elements:
                # Check for file path (Chainlit v1+)
                if hasattr(element, "path"):
                    original_name = element.name
                    # Create a path with original filename in temp dir
                    # This is important because ingest_path uses the filename extension to determine file type
                    temp_file_path = os.path.join(temp_dir, original_name)
                    
                    # Copy the temp file to our temp path with correct name
                    shutil.copy2(element.path, temp_file_path)
                    
                    status_msg = cl.Message(content=f"⏳ Ingesting {original_name}...")
                    await status_msg.send()
                    
                    try:
                        # Run ingestion in a separate thread to avoid blocking the UI
                        # ingest_path is synchronous, so we use cl.make_async
                        await cl.make_async(ingest_path)(temp_file_path)
                        count += 1
                        status_msg.content = f"✅ Successfully ingested: {original_name}"
                        await status_msg.update()
                    except Exception as e:
                        status_msg.content = f"❌ Failed to ingest {original_name}: {str(e)}"
                        await status_msg.update()
            
            if count > 0:
                await cl.Message(content=f"🎉 Processed {count} files successfully.").send()
                
        finally:
            # Cleanup temp directory
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except:
                pass

    # --- 3. Handle Text Query (RAG) ---
    # Only proceed if there is text content
    if message.content:
        
        msg = cl.Message(content="")
        await msg.send()
        
        # Get chat history
        history = cl.user_session.get("history", [])
        
        try:
            # Call your existing RAG function
            # ask_brain returns (response_stream, sources)
            # We need to make sure ask_brain is compatible with async calls if it's blocking
            response_stream, sources = await cl.make_async(ask_brain)(message.content, history=history, stream=True)
            
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
                    source_text = "\n\n**📚 References:**\n"
                    for i, src in enumerate(sources, 1):
                        source_text += f"{i}. **{src['filename']}** (Score: {src['score']:.4f})\n"
                    await msg.stream_token(source_text)
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
