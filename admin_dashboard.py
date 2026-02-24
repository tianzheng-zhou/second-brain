import streamlit as st
import os
import time
import pandas as pd
from pathlib import Path
from personal_brain.core.database import (
    init_db, 
    get_all_files, 
    delete_file_record, 
    get_db_schema, 
    get_file_chunks
)
from personal_brain.config import ensure_dirs, STORAGE_PATH, DB_PATH
from personal_brain.core.config_manager import config_manager
from personal_brain.core.ingestion import ingest_path, refresh_index_for_file
from personal_brain.core.search import search_files

# Page configuration
st.set_page_config(
    page_title="PersonalBrain Admin",
    page_icon="🛠️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for "Cherry Studio" like feel
st.markdown("""
<style>
    .stApp {
        background-color: #f8f9fa;
    }
    .main-header {
        font-size: 2rem;
        font-weight: 600;
        color: #1f2937;
        margin-bottom: 1rem;
    }
    .card {
        background-color: white;
        padding: 1.5rem;
        border-radius: 0.5rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        margin-bottom: 1rem;
    }
    .stat-value {
        font-size: 1.5rem;
        font-weight: 700;
        color: #4B0082;
    }
    .stat-label {
        font-size: 0.875rem;
        color: #6b7280;
    }
</style>
""", unsafe_allow_html=True)

# Sidebar
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/brain.png", width=50)
    st.title("Admin Console")
    st.markdown("Database Management")
    
    # Custom styling for buttons to make them look like chat topics
    st.markdown("""
        <style>
            div[data-testid="stSidebar"] button {
                width: 100%;
                text-align: left;
                padding-left: 20px;
                border: none;
                margin-bottom: 5px;
            }
            div[data-testid="stSidebar"] button:focus {
                border: none;
                outline: none;
            }
        </style>
    """, unsafe_allow_html=True)

    # Initialize session state for page navigation
    if 'current_page' not in st.session_state:
        st.session_state['current_page'] = "Dashboard"

    # Navigation items
    nav_items = {
        "Dashboard": "📊",
        "Knowledge Base": "📂",
        "Vector Search": "🔍",
        "Settings": "⚙️"
    }

    # Render navigation buttons
    for page_name, icon in nav_items.items():
        is_active = st.session_state['current_page'] == page_name
        # Use primary type for active page, secondary for others
        btn_type = "primary" if is_active else "secondary"
        
        if st.button(f"{icon}  {page_name}", key=f"nav_{page_name}", type=btn_type):
            st.session_state['current_page'] = page_name
            st.rerun()
            
    menu = st.session_state['current_page']
    
    st.markdown("---")
    status = "Active" if os.path.exists(DB_PATH) else "Offline"
    color = "green" if status == "Active" else "red"
    st.markdown(f"Status: :{color}[{status}]")
    st.caption(f"v1.0.0")

# --- DASHBOARD ---
if menu == "Dashboard":
    st.markdown('<div class="main-header">Dashboard Overview</div>', unsafe_allow_html=True)
    
    if not os.path.exists(DB_PATH):
        st.warning("Database not initialized. Go to Settings to initialize.")
    else:
        files = get_all_files()
        total_files = len(files)
        total_size = sum(f['size_bytes'] for f in files) if files else 0
        
        # Calculate chunks (approximation if not storing total chunks count in metadata)
        # We can query DB for exact count if needed, but for now let's just show file stats
        
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(f"""
            <div class="card">
                <div class="stat-label">Total Files</div>
                <div class="stat-value">{total_files}</div>
            </div>
            """, unsafe_allow_html=True)
            
        with c2:
            st.markdown(f"""
            <div class="card">
                <div class="stat-label">Total Storage</div>
                <div class="stat-value">{total_size / 1024 / 1024:.2f} MB</div>
            </div>
            """, unsafe_allow_html=True)
            
        with c3:
            st.markdown(f"""
            <div class="card">
                <div class="stat-label">File Types</div>
                <div class="stat-value">{len(set(f['type'] for f in files))}</div>
            </div>
            """, unsafe_allow_html=True)
            
        st.markdown("### Recent Files")
        if files:
            df = pd.DataFrame(files[:5])
            st.dataframe(
                df[['filename', 'type', 'created_at', 'size_bytes']], 
                column_config={
                    "filename": "Filename",
                    "type": "Type",
                    "created_at": "Created",
                    "size_bytes": "Size"
                },
                use_container_width=True,
                hide_index=True
            )

# --- KNOWLEDGE BASE ---
elif menu == "Knowledge Base":
    st.markdown('<div class="main-header">Knowledge Base Management</div>', unsafe_allow_html=True)
    
    tab_list, tab_upload = st.tabs(["📂 File List", "📤 Upload New"])
    
    with tab_list:
        files = get_all_files()
        if not files:
            st.info("No files found.")
        else:
            # Convert to DataFrame for easier handling
            df = pd.DataFrame(files)
            
            # Search filter
            col_search, col_ai = st.columns([4, 1])
            with col_search:
                search_term = st.text_input("🔍 Search files...", "")
            with col_ai:
                st.write("") # Spacer
                st.write("") # Spacer
                use_ai_search = st.toggle("🤖 AI Search", help="Use vector search to find semantically related files")
            
            if search_term:
                if use_ai_search:
                    with st.spinner("AI is optimizing your search query..."):
                        try:
                            # Use configured AI Search Model to optimize the query
                            from openai import OpenAI
                            from personal_brain.config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL
                            from personal_brain.core.config_manager import config_manager
                            
                            ai_client = OpenAI(
                                api_key=DASHSCOPE_API_KEY,
                                base_url=DASHSCOPE_BASE_URL
                            )
                            
                            ai_model = config_manager.get("ai_search_model", "qwen-plus")
                            
                            response = ai_client.chat.completions.create(
                                model=ai_model,
                                messages=[
                                    {"role": "system", "content": "You are a search query optimizer. Your task is to extract keywords and expand the user's search query to improve retrieval accuracy. Output ONLY the optimized query string, no other text."},
                                    {"role": "user", "content": f"Optimize this search query: {search_term}"}
                                ],
                                temperature=0.3
                            )
                            optimized_query = response.choices[0].message.content.strip()
                            st.caption(f"Original: '{search_term}' -> Optimized: '{optimized_query}' (Model: {ai_model})")
                            search_term = optimized_query
                            
                        except Exception as e:
                            st.error(f"AI Optimization failed: {e}")
                            # Fallback to original term
                            
                    with st.spinner("Searching knowledge base..."):
                        # Use vector search to find relevant chunks/files
                        # Increase limit to cast a wider net
                        search_results = search_files(search_term, limit=20)
                        
                        if search_results:
                            # Extract unique file IDs and their max scores
                            relevant_files = {}
                            for res in search_results:
                                fid = res['file_id']
                                score = res.get('score', 0)
                                if fid not in relevant_files or score > relevant_files[fid]:
                                    relevant_files[fid] = score
                            
                            # Filter dataframe
                            df = df[df['id'].isin(relevant_files.keys())]
                            
                            # Add score column and sort
                            df['relevance'] = df['id'].map(relevant_files)
                            df = df.sort_values('relevance', ascending=False)
                            
                            st.caption(f"Found {len(df)} semantically related files.")
                        else:
                            st.warning("No related files found via AI search.")
                            df = df.iloc[0:0] # Empty dataframe
                else:
                    # Standard substring match
                    df = df[df['filename'].str.contains(search_term, case=False)]
            
            # Display as interactive table
            if 'relevance' in df.columns:
                col_widths = [3, 1, 1, 1, 1, 2]
                header_cols = st.columns(col_widths + [2]) 
                header_cols[0].markdown("**Filename**")
                header_cols[1].markdown("**Score**")
                header_cols[2].markdown("**Type**")
                header_cols[3].markdown("**Size**")
                header_cols[4].markdown("**Date**")
                header_cols[5].markdown("**Actions**")
            else:
                col_widths = [3, 1, 1, 2]
                header_cols = st.columns(col_widths + [2]) 
                header_cols[0].markdown("**Filename**")
                header_cols[1].markdown("**Type**")
                header_cols[2].markdown("**Size**")
                header_cols[3].markdown("**Date**")
                header_cols[4].markdown("**Actions**")
            
            for index, row in df.iterrows():
                if 'relevance' in df.columns:
                    cols = st.columns(col_widths + [2])
                    cols[0].write(f"📄 {row['filename']}")
                    cols[1].write(f"{row['relevance']:.4f}")
                    cols[2].write(row['type'])
                    cols[3].write(f"{row['size_bytes']/1024:.1f} KB")
                    cols[4].write(row['created_at'])
                    action_col = cols[5]
                else:
                    cols = st.columns(col_widths + [2])
                    cols[0].write(f"📄 {row['filename']}")
                    cols[1].write(row['type'])
                    cols[2].write(f"{row['size_bytes']/1024:.1f} KB")
                    cols[3].write(row['created_at'])
                    action_col = cols[4]
                
                with action_col:
                    b1, b2, b3 = st.columns(3)
                    with b1:
                        if st.button("👁️", key=f"view_{row['id']}", help="View Chunks"):
                            st.session_state['view_file_id'] = row['id']
                            st.rerun()
                    with b2:
                        with st.popover("🔄", help="Re-index"):
                            st.write("⚠️ **Confirm Re-index?**")
                            st.caption("This will consume tokens and overwrite existing embeddings.")
                            if st.button("Yes, Re-index", key=f"confirm_refresh_{row['id']}", type="primary"):
                                with st.spinner("Re-indexing..."):
                                    refresh_index_for_file(row['id'])
                                    st.toast(f"Refreshed {row['filename']}")
                                    time.sleep(1)
                                    st.rerun()
                    with b3:
                        with st.popover("🗑️", help="Delete"):
                            st.write("⚠️ **Confirm Delete?**")
                            st.caption("This action cannot be undone.")
                            if st.button("Yes, Delete", key=f"confirm_del_{row['id']}", type="primary"):
                                delete_file_record(row['id'])
                                st.toast(f"Deleted {row['filename']}")
                                time.sleep(1)
                                st.rerun()
            
            st.markdown("---")
            
            # Chunk Viewer (Bottom Sheet style)
            if 'view_file_id' in st.session_state:
                file_id = st.session_state['view_file_id']
                file_info = next((f for f in files if f['id'] == file_id), None)
                
                if file_info:
                    st.markdown(f"### 🧩 Chunks for: `{file_info['filename']}`")
                    chunks = get_file_chunks(file_id)
                    
                    if chunks:
                        st.info(f"Found {len(chunks)} chunks.")
                        for chunk in chunks:
                            with st.expander(f"Chunk {chunk['chunk_index']} (ID: {chunk['id']})"):
                                st.text(chunk['content'])
                    else:
                        st.warning("No chunks found for this file.")
                    
                    if st.button("Close Viewer"):
                        del st.session_state['view_file_id']
                        st.rerun()

    with tab_upload:
        st.markdown("### Upload Files")
        uploaded_files = st.file_uploader("Drag and drop files here", accept_multiple_files=True)
        if uploaded_files:
            if st.button("Start Ingestion", type="primary"):
                upload_dir = STORAGE_PATH / "uploads"
                upload_dir.mkdir(parents=True, exist_ok=True)
                
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                for i, file in enumerate(uploaded_files):
                    status_text.text(f"Processing {file.name}...")
                    file_path = upload_dir / file.name
                    with open(file_path, "wb") as f:
                        f.write(file.getbuffer())
                    
                    try:
                        ingest_path(str(file_path))
                        # Cleanup
                        if file_path.exists():
                            file_path.unlink()
                    except Exception as e:
                        st.error(f"Error processing {file.name}: {e}")
                    
                    progress_bar.progress((i + 1) / len(uploaded_files))
                
                status_text.text("All files processed!")
                st.success("Upload complete.")
                time.sleep(1)
                st.rerun()

# --- VECTOR SEARCH ---
elif menu == "Vector Search":
    st.markdown('<div class="main-header">Vector Search Playground</div>', unsafe_allow_html=True)
    st.markdown("Test your retrieval accuracy without generating an answer.")
    
    col1, col2 = st.columns([3, 1])
    with col1:
        query = st.text_input("Enter test query:", placeholder="e.g. What is the project timeline?")
    with col2:
        top_k = st.number_input("Top K", min_value=1, max_value=20, value=5)
        
    if query:
        st.markdown("### Retrieval Results")
        with st.spinner("Searching..."):
            results = search_files(query, limit=top_k)
            
        if not results:
            st.info("No matching chunks found.")
        else:
            for i, res in enumerate(results):
                score = res.get('score', 0)
                color = "green" if score > 0.7 else "orange" if score > 0.5 else "red"
                
                with st.expander(f"#{i+1} | Score: :{color}[{score:.4f}] | {res['filename']}"):
                    st.markdown(f"**Source File:** `{res['filename']}`")
                    st.markdown(f"**Type:** `{res.get('file_type', 'unknown')}`")
                    
                    # Display content based on type
                    content = res['content']
                    
                    # Check for image content in multimodal chunks
                    # Our multimodal splitter stores images as base64 in the content or as separate metadata?
                    # The current implementation stores text content in 'content' column.
                    # If it's a multimodal chunk, the text might contain image references or the chunk itself is text description.
                    
                    # However, if the chunk comes from an image file (OCR/Description), we should show the image if possible.
                    # We need the file path to show the image.
                    # search_files returns file_id, we can look up the file path?
                    # But search_files doesn't return file path currently.
                    
                    # Let's try to infer if it's an image file
                    file_type = res.get('file_type', '').lower()
                    if file_type in ['image', 'png', 'jpg', 'jpeg', 'webp', 'gif']:
                        # Try to construct file path and show image
                        from pathlib import Path
                        # Assuming STORAGE_PATH is available
                        # We need to know where the file is stored.
                        # Usually in STORAGE_PATH or a subfolder if using ingestion logic.
                        # Since we don't have the path in result, let's query DB for it?
                        # Or just show the description.
                        
                        st.markdown("**Image Description / Text:**")
                        st.info(content)
                        
                        # Attempt to show image if we can find it by filename in storage
                        # This is a heuristic since we don't pass full path
                        # Check root storage and uploads folder
                        possible_paths = [
                            STORAGE_PATH / res['filename'],
                            STORAGE_PATH / "uploads" / res['filename']
                        ]
                        
                        for p in possible_paths:
                            if p.exists():
                                st.image(str(p), caption=res['filename'])
                                break
                    else:
                        # Text content
                        st.markdown("**Content Chunk:**")
                        
                        # Try to find and render images embedded in markdown (e.g. from PDF processing)
                        import re
                        from pathlib import Path
                        
                        # Find all markdown images: ![alt](path)
                        # The path in DB might be relative or absolute. 
                        # MinerU usually outputs relative paths like "images/xxx.jpg"
                        
                        # We need to be careful not to break the layout if there are many images
                        
                        image_matches = re.findall(r'!\[(.*?)\]\((.*?)\)', content)
                        
                        if image_matches:
                            # Render images found in text
                            for alt, img_path in image_matches:
                                # Clean path
                                clean_path = img_path.lstrip('./').lstrip('/').replace('/', os.sep)
                                
                                # Try to resolve path
                                # 1. Try relative to storage/mineru_cache (where PDFs are processed)
                                # We don't know the exact subfolder from here easily without file hash
                                # But we can try to search for the filename in STORAGE_PATH
                                
                                found_img = None
                                img_name = Path(clean_path).name
                                
                                # Heuristic: search in mineru_cache
                                cache_dir = STORAGE_PATH / "mineru_cache"
                                if cache_dir.exists():
                                    # Try to find the file recursively in cache_dir
                                    # This might be slow if cache is huge, but usually okay for admin console
                                    try:
                                        found_imgs = list(cache_dir.rglob(img_name))
                                        if found_imgs:
                                            found_img = found_imgs[0]
                                    except Exception:
                                        pass
                                
                                if found_img:
                                    st.image(str(found_img), caption=f"Image found in text: {alt}")
                                else:
                                    # If not found, maybe just display the text
                                    pass
                        
                        st.markdown(f"```text\n{content}\n```")

# --- SETTINGS ---
elif menu == "Settings":
    st.markdown('<div class="main-header">System Settings</div>', unsafe_allow_html=True)
    
    tab_model, tab_db = st.tabs(["🤖 Model Configuration", "🗄️ Database"])
    
    with tab_model:
        st.subheader("Model Selection")
        st.markdown("Configure the AI models used for different tasks.")
        
        # Load current config
        current_config = config_manager.get_all()
        
        # Define available options
        chat_options = ["qwen3-max", "qwen-plus", "qwen-flash", "qwen3.5-plus"]
        vision_options = ["qwen3-vl-plus", "qwen3-vl-flash", "qwen3.5-plus"]
        
        # Ensure current config values are in options or use default
        current_chat_model = current_config.get("chat_model")
        if current_chat_model not in chat_options:
            current_chat_model = chat_options[0]
            
        current_vision_model = current_config.get("vision_model")
        if current_vision_model not in vision_options:
            current_vision_model = vision_options[0]
            
        current_ai_search_model = current_config.get("ai_search_model")
        if current_ai_search_model not in chat_options:
            current_ai_search_model = chat_options[0]

        with st.form("model_config_form"):
            col1, col2 = st.columns(2)
            
            with col1:
                new_chat_model = st.selectbox(
                    "Chat Model (General Conversation)",
                    options=chat_options,
                    index=chat_options.index(current_chat_model),
                    help="Used for answering user queries and general conversation."
                )
                
                new_ai_search_model = st.selectbox(
                    "AI Search Model (Query Optimization)",
                    options=chat_options,
                    index=chat_options.index(current_ai_search_model),
                    help="Used to optimize search queries in the Admin Console AI Search."
                )
                
                new_vision_model = st.selectbox(
                    "Vision Model (Image Understanding)",
                    options=vision_options,
                    index=vision_options.index(current_vision_model),
                    help="Used for analyzing images in uploaded files."
                )
                
            with col2:
                st.text_input(
                    "Embedding Model (Read-only)",
                    value=current_config.get("embedding_model"),
                    disabled=True,
                    help="Currently fixed to ensure index compatibility."
                )
                
                st.text_input(
                    "Rerank Model (Read-only)",
                    value=current_config.get("rerank_model"),
                    disabled=True,
                    help="Currently fixed to ensure search optimization."
                )
            
            submitted = st.form_submit_button("Save Configuration", type="primary")
            
            if submitted:
                config_manager.set("chat_model", new_chat_model)
                config_manager.set("ai_search_model", new_ai_search_model)
                config_manager.set("vision_model", new_vision_model)
                st.success("Configuration saved successfully!")
                time.sleep(1)
                st.rerun()

    with tab_db:
        st.subheader("Database Configuration")
        st.code(f"DB_PATH = {DB_PATH}", language="python")
        st.code(f"STORAGE_PATH = {STORAGE_PATH}", language="python")
        
        st.markdown("---")
        st.subheader("Maintenance")
        
        if st.button("Initialize / Repair Database"):
            try:
                ensure_dirs()
                init_db()
                st.success("Database structure initialized/repaired.")
            except Exception as e:
                st.error(f"Error: {e}")
                
        st.markdown("---")
        st.subheader("Danger Zone")
        if st.button("🗑️ WIPE DATABASE", type="primary"):
            st.session_state['confirm_wipe'] = True
            
        if st.session_state.get('confirm_wipe'):
            st.warning("⚠️ This will verify delete ALL files and embeddings! Are you sure?")
            if st.button("YES, DELETE EVERYTHING"):
                if os.path.exists(DB_PATH):
                    os.remove(DB_PATH)
                init_db()
                st.success("Database wiped clean.")
                st.session_state['confirm_wipe'] = False
                time.sleep(1)
                st.rerun()
