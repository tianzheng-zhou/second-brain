import streamlit as st
import os
import time
from pathlib import Path
from personal_brain.core.database import init_db
from personal_brain.config import ensure_dirs, STORAGE_PATH, DB_PATH
from personal_brain.core.ingestion import ingest_path
from personal_brain.core.search import search_files
from personal_brain.core.ask import ask_brain

# Page configuration
st.set_page_config(
    page_title="PersonalBrain AI",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: #4B0082;
        margin-bottom: 1rem;
    }
    .chat-message {
        padding: 1.5rem;
        border-radius: 0.5rem;
        margin-bottom: 1rem;
        display: flex;
        flex-direction: row;
        align-items: flex-start;
    }
    .chat-message.user {
        background-color: #f0f2f6;
    }
    .chat-message.assistant {
        background-color: #ffffff;
        border: 1px solid #e0e0e0;
    }
    .source-expander {
        margin-top: 10px;
        font-size: 0.8rem;
    }
</style>
""", unsafe_allow_html=True)

# Sidebar
st.sidebar.title("🧠 PersonalBrain")
st.sidebar.markdown("Your second brain for managing personal information.")

menu = ["Chat", "Ingest", "Manage"]
choice = st.sidebar.selectbox("Navigation", menu)

# Helper functions
def get_db_status():
    if os.path.exists(DB_PATH):
        return "Active", "green"
    return "Not Initialized", "red"

# Chat Page
if choice == "Chat":
    st.markdown('<div class="main-header">Chat with your Brain</div>', unsafe_allow_html=True)
    
    # Initialize chat history
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Display chat messages from history on app rerun
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if "sources" in message and message["sources"]:
                with st.expander(f"📚 References ({len(message['sources'])})"):
                    for src in message["sources"]:
                        st.markdown(f"- **{src['filename']}** ({src['type']}) - Score: {src['score']:.4f}")

    # React to user input
    if prompt := st.chat_input("Ask something about your notes..."):
        # Check DB status first
        if not os.path.exists(DB_PATH):
            st.error("Database not initialized. Please go to 'Manage' tab and initialize first.")
        else:
            # Display user message in chat message container
            st.chat_message("user").markdown(prompt)
            # Add user message to chat history
            st.session_state.messages.append({"role": "user", "content": prompt})

            # Display assistant response in chat message container
            with st.chat_message("assistant"):
                message_placeholder = st.empty()
                full_response = ""
                
                with st.spinner("Thinking..."):
                    # Call RAG function
                    # Convert session state history to format expected by ask_brain (optional, but good for context)
                    history_for_rag = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages[:-1]]
                    
                    response_stream, sources = ask_brain(prompt, history=history_for_rag, stream=True)
                    
                    if isinstance(response_stream, str):
                        # Error case
                        full_response = response_stream
                        message_placeholder.markdown(full_response)
                    else:
                        # Stream response
                        for chunk in response_stream:
                            if chunk.choices[0].delta.content:
                                full_response += chunk.choices[0].delta.content
                                message_placeholder.markdown(full_response + "▌")
                        message_placeholder.markdown(full_response)
                
                # Show sources if available
                if sources:
                    with st.expander(f"📚 References ({len(sources)})"):
                        for src in sources:
                            st.markdown(f"- **{src['filename']}** ({src['type']}) - Score: {src['score']:.4f}")

            # Add assistant response to chat history
            st.session_state.messages.append({
                "role": "assistant", 
                "content": full_response,
                "sources": sources
            })

# Ingest Page
elif choice == "Ingest":
    st.markdown('<div class="main-header">Ingest Content</div>', unsafe_allow_html=True)
    
    st.markdown("Add new files or folders to your PersonalBrain.")
    
    tab1, tab2 = st.tabs(["From Path", "Upload File"])
    
    with tab1:
        path_input = st.text_input("Enter file or folder path:", placeholder="D:\\Documents\\Notes")
        if st.button("Ingest Path"):
            if not path_input:
                st.warning("Please enter a path.")
            elif not os.path.exists(path_input):
                st.error("Path does not exist.")
            else:
                with st.status("Ingesting...", expanded=True) as status:
                    st.write(f"Processing {path_input}...")
                    try:
                        ingest_path(path_input)
                        status.update(label="Ingestion Complete!", state="complete", expanded=False)
                        st.success(f"Successfully ingested {path_input}")
                    except Exception as e:
                        status.update(label="Ingestion Failed", state="error")
                        st.error(f"Error: {str(e)}")

    with tab2:
        uploaded_files = st.file_uploader("Upload files", accept_multiple_files=True)
        if uploaded_files:
            if st.button("Process Uploaded Files"):
                # Create a temp directory for uploads
                upload_dir = STORAGE_PATH / "uploads"
                upload_dir.mkdir(parents=True, exist_ok=True)
                
                with st.status("Processing uploads...", expanded=True) as status:
                    count = 0
                    for uploaded_file in uploaded_files:
                        file_path = upload_dir / uploaded_file.name
                        with open(file_path, "wb") as f:
                            f.write(uploaded_file.getbuffer())
                        
                        st.write(f"Ingesting {uploaded_file.name}...")
                        try:
                            ingest_path(str(file_path))
                            count += 1
                            # Clean up temp file
                            if file_path.exists():
                                file_path.unlink()
                        except Exception as e:
                            st.error(f"Failed to ingest {uploaded_file.name}: {e}")
                            
                    status.update(label=f"Processed {count} files!", state="complete", expanded=False)
                    st.success(f"Successfully ingested {count} files.")

# Manage Page
elif choice == "Manage":
    st.markdown('<div class="main-header">System Management</div>', unsafe_allow_html=True)
    
    status, color = get_db_status()
    st.markdown(f"**Database Status:** :{color}[{status}]")
    st.markdown(f"**Storage Path:** `{STORAGE_PATH}`")
    
    st.markdown("---")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Initialization")
        if st.button("Initialize Database"):
            with st.spinner("Initializing..."):
                try:
                    ensure_dirs()
                    init_db()
                    st.success("Database initialized successfully!")
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"Initialization failed: {e}")
    
    with col2:
        st.subheader("Danger Zone")
        if st.button("Reset Database (Delete All Data)", type="primary"):
            # Use session state to confirm
            st.session_state['confirm_reset'] = True
            
        if st.session_state.get('confirm_reset'):
            st.warning("Are you sure? This action cannot be undone.")
            col_confirm, col_cancel = st.columns(2)
            with col_confirm:
                if st.button("Yes, I'm sure"):
                    try:
                        if os.path.exists(DB_PATH):
                            os.remove(DB_PATH)
                        ensure_dirs()
                        init_db()
                        st.success("Database reset successfully!")
                        st.session_state['confirm_reset'] = False
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Reset failed: {e}")
            with col_cancel:
                if st.button("Cancel"):
                    st.session_state['confirm_reset'] = False
                    st.rerun()

# Footer
st.sidebar.markdown("---")
st.sidebar.caption("v0.2.0 | Powered by Streamlit")
