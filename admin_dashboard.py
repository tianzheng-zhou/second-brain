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
    
    menu = st.radio(
        "Navigation", 
        ["Dashboard", "Knowledge Base", "Vector Search", "Settings"],
        index=0,
    )
    
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
            search_term = st.text_input("🔍 Search files...", "")
            if search_term:
                df = df[df['filename'].str.contains(search_term, case=False)]
            
            # Display as interactive table
            col_widths = [3, 1, 1, 2]
            header_cols = st.columns(col_widths + [2]) # +2 for actions
            header_cols[0].markdown("**Filename**")
            header_cols[1].markdown("**Type**")
            header_cols[2].markdown("**Size**")
            header_cols[3].markdown("**Date**")
            header_cols[4].markdown("**Actions**")
            
            for index, row in df.iterrows():
                cols = st.columns(col_widths + [2])
                cols[0].write(f"📄 {row['filename']}")
                cols[1].write(row['type'])
                cols[2].write(f"{row['size_bytes']/1024:.1f} KB")
                cols[3].write(row['created_at'])
                
                with cols[4]:
                    b1, b2, b3 = st.columns(3)
                    with b1:
                        if st.button("👁️", key=f"view_{row['id']}", help="View Chunks"):
                            st.session_state['view_file_id'] = row['id']
                            st.rerun()
                    with b2:
                        if st.button("🔄", key=f"refresh_{row['id']}", help="Re-index"):
                            with st.spinner("Re-indexing..."):
                                refresh_index_for_file(row['id'])
                                st.toast(f"Refreshed {row['filename']}")
                    with b3:
                        if st.button("🗑️", key=f"del_{row['id']}", help="Delete"):
                            delete_file_record(row['id'])
                            st.toast(f"Deleted {row['filename']}")
                            time.sleep(0.5)
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
                    st.markdown("**Content Chunk:**")
                    st.markdown(f"```text\n{res['content']}\n```")

# --- SETTINGS ---
elif menu == "Settings":
    st.markdown('<div class="main-header">System Settings</div>', unsafe_allow_html=True)
    
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
