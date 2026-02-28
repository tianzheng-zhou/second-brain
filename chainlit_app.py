import chainlit as cl
import os
import shutil
import tempfile
from pathlib import Path
import sys
import html
import re

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
    save_conversation,
    get_db_connection
)

# Initialize Data Layer
cl_data_layer = SQLiteDataLayer()

try:
    from chainlit.server import app
    from fastapi.responses import HTMLResponse

    from fastapi.routing import APIRoute

    async def view_reference(ref_type: str, ref_id: str):
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            title = ""
            content = ""

            if ref_type == "file":
                cursor.execute("SELECT filename, ocr_text FROM files WHERE id = ?", (ref_id,))
                row = cursor.fetchone()
                if row:
                    title = row["filename"] or "File"
                    content = row["ocr_text"] or ""
            elif ref_type == "chunk":
                cursor.execute("""
                    SELECT f.filename, fc.content
                    FROM file_chunks fc
                    JOIN files f ON fc.file_id = f.id
                    WHERE fc.id = ?
                """, (ref_id,))
                row = cursor.fetchone()
                if row:
                    title = row["filename"] or "Chunk"
                    content = row["content"] or ""
            elif ref_type == "entry":
                cursor.execute("SELECT id, entry_type, content_text FROM entries WHERE id = ?", (ref_id,))
                row = cursor.fetchone()
                if row:
                    title = f"Entry: {row['entry_type'] or ''}".strip()
                    content = row["content_text"] or ""

            if not content:
                return HTMLResponse("<h3>Not found</h3>", status_code=404)

            safe_title = html.escape(title)
            safe_content = html.escape(content)
            clean_title = re.sub(r'[\\/*?:"<>|]', "", title)
            
            html_content = f"""
            <html>
            <head>
                <title>{safe_title}</title>
                <style>
                    body {{ font-family: sans-serif; padding: 20px; max-width: 800px; margin: 0 auto; }}
                    .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }}
                    button {{ padding: 8px 16px; background-color: #0F172A; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; }}
                    button:hover {{ background-color: #1E293B; }}
                    pre {{ white-space: pre-wrap; background-color: #f1f5f9; padding: 15px; border-radius: 8px; overflow-x: auto; }}
                </style>
            </head>
            <body>
                <div class="header">
                    <h2>{safe_title}</h2>
                    <button onclick="downloadContent()">📥 Export to File</button>
                </div>
                <pre id="content">{safe_content}</pre>
                <script>
                    function downloadContent() {{
                        const content = document.getElementById('content').textContent;
                        const blob = new Blob([content], {{ type: 'text/plain' }});
                        const url = window.URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = "{clean_title}.txt";
                        document.body.appendChild(a);
                        a.click();
                        window.URL.revokeObjectURL(url);
                        document.body.removeChild(a);
                    }}
                </script>
            </body>
            </html>
            """
            return HTMLResponse(html_content)
        finally:
            conn.close()

    # Manually add route to beginning to ensure priority over Chainlit catch-all
    app.routes.insert(0, APIRoute("/ref/{ref_type}/{ref_id}", view_reference, methods=["GET"], response_class=HTMLResponse))
except Exception as e:
    print(f"[ERROR] Failed to register route: {e}")
    app = None

def _get_reference_text(ref_type: str, ref_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if ref_type == "file":
            cursor.execute("SELECT filename, ocr_text FROM files WHERE id = ?", (ref_id,))
            row = cursor.fetchone()
            if not row:
                return None, None
            return row["filename"] or "File", row["ocr_text"] or ""
        if ref_type == "chunk":
            cursor.execute("""
                SELECT f.filename, fc.content
                FROM file_chunks fc
                JOIN files f ON fc.file_id = f.id
                WHERE fc.id = ?
            """, (ref_id,))
            row = cursor.fetchone()
            if not row:
                return None, None
            return row["filename"] or "Chunk", row["content"] or ""
        if ref_type == "entry":
            cursor.execute("SELECT entry_type, content_text FROM entries WHERE id = ?", (ref_id,))
            row = cursor.fetchone()
            if not row:
                return None, None
            title = f"Entry: {row['entry_type'] or ''}".strip()
            return title, row["content_text"] or ""
        return None, None
    finally:
        conn.close()

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

@cl.action_callback("open_ref")
async def on_open_ref(action: cl.Action):
    ref_type = action.payload.get("ref_type")
    ref_id = action.payload.get("ref_id")
    filename = action.payload.get("filename") or "Reference"
    if not ref_type or not ref_id:
        await cl.Message(content="Invalid reference.").send()
        return
    title, content = _get_reference_text(ref_type, ref_id)
    if not content:
        await cl.Message(content="Reference not found.").send()
        return
    preview = content if len(content) <= 12000 else content[:12000] + "\n\n...(truncated)"
    element = cl.Text(name=filename, content=f"{title}\n\n{preview}", display="side", language="text")
    await cl.Message(content=f"已打开：{filename}", elements=[element]).send()

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
             await cl.Message(content='✅ 列表已生成！\n请点击下方的 **"知识库列表"** 按钮/图标，在右侧侧边栏查看详细内容 👉', elements=[element]).send()
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

    # --- 1.5 Handle Knowledge Base Meta-Queries ---
    # 检测用户询问知识库内容的意图，强制检索数据库
    query_lower = message.content.strip().lower()
    kb_keywords = [
        "知识库", "knowledge base", "数据库", "database",
        "存了啥", "有什么", "有啥", "都有什么",
        "有哪些文件", "有哪些文档", "上传了什么",
        "what do i have", "what's in", "what is in",
        "show me my files", "list my files", "what files"
    ]
    if any(keyword in query_lower for keyword in kb_keywords):
        # 不再直接返回，而是调用 ask_brain 并强制检索
        # 这样 LLM 会先搜索数据库，然后基于检索结果回答
        history = cl.user_session.get("history", [])
        session_id = cl.context.session.id

        # 获取文件数量用于 prompt 优化
        files = get_all_files()
        entries_count = 0
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as count FROM entries")
            row = cursor.fetchone()
            if row:
                entries_count = row["count"]
        finally:
            conn.close()

        if not files and entries_count == 0:
            # 知识库为空时直接返回
            await cl.Message(content="📭 **知识库是空的**\n\n还没有存储任何文件或笔记。你可以：\n- 上传文件（PDF、Word、图片等）\n- 直接发送文字让我保存").send()
            return

        # 调用 ask_brain 并强制检索
        enhanced_query = f"{message.content.strip()}\n\n[系统提示] 用户正在询问知识库内容。请先检索数据库，然后基于检索结果回答。当前知识库包含 {len(files)} 个文件和 {entries_count} 条笔记。"

        response_stream, sources = await cl.make_async(ask_brain)(
            enhanced_query,
            history=history,
            stream=True,
            conversation_id=session_id,
            force_retrieve=True
        )

        full_response = ""

        if isinstance(response_stream, str):
            full_response = f"Error: {response_stream}"
            msg = cl.Message(content=full_response)
            await msg.send()
        else:
            msg = cl.Message(content="")
            await msg.send()

            for chunk in response_stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    token = chunk.choices[0].delta.content
                    full_response += token
                    await msg.stream_token(token)

            # Append sources if available
            if sources:
                async with cl.Step(name="📚 References") as source_step:
                    lines = []
                    for i, src in enumerate(sources, 1):
                        filename = src.get("filename") or "Unknown"
                        score = src.get("score", 0)
                        ref_type = src.get("ref_type")
                        ref_id = src.get("ref_id")
                        if ref_type and ref_id:
                            lines.append(f"{i}. [{filename}](/ref/{ref_type}/{ref_id}) (Score: {score:.4f})")
                        else:
                            lines.append(f"{i}. {filename} (Score: {score:.4f})")

                    source_step.output = "\n".join(lines)

            await msg.update()

            # Update history
            history.append({"role": "user", "content": message.content})
            history.append({"role": "assistant", "content": full_response})
            if len(history) > 20:
                history = history[-20:]
            cl.user_session.set("history", history)

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
                msg = cl.Message(content=full_response)
                await msg.send()
            else:
                msg = cl.Message(content="")
                await msg.send()

                # Stream the response from OpenAI client generator
                for chunk in response_stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        token = chunk.choices[0].delta.content
                        full_response += token
                        await msg.stream_token(token)

                # Append sources if available
                if sources:
                    # Create a separate step for references to make it collapsible
                    async with cl.Step(name="📚 References") as source_step:
                        # Build a markdown list of clickable links
                        lines = []
                        for i, src in enumerate(sources, 1):
                            filename = src.get("filename") or "Unknown"
                            score = src.get("score", 0)
                            ref_type = src.get("ref_type")
                            ref_id = src.get("ref_id")
                            if ref_type and ref_id:
                                lines.append(f"{i}. [{filename}](/ref/{ref_type}/{ref_id}) (Score: {score:.4f})")
                            else:
                                lines.append(f"{i}. {filename} (Score: {score:.4f})")

                        # Set the step output to the markdown list directly to ensure it is inside the collapsible area
                        source_step.output = "\n".join(lines)

                        # Remove the child message to avoid content appearing outside the step
                        # links_md = "\n".join(lines)
                        # links_msg = cl.Message(content=links_md)
                        # links_msg.parent_id = source_step.id
                        # await links_msg.send()

                # Update history (keep last 10 turns)

                await msg.update()

                # Update history (keep last 10 turns)
                history.append({"role": "user", "content": message.content})
                history.append({"role": "assistant", "content": full_response})
                if len(history) > 20:
                    history = history[-20:]
                cl.user_session.set("history", history)

        except Exception as e:
            await cl.Message(content=f"Error processing query: {str(e)}").send()
