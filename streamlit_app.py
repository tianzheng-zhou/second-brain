import streamlit as st
import os
import time
from pathlib import Path
from personal_brain.core.database import init_db
from personal_brain.config import ensure_dirs, STORAGE_PATH, DB_PATH
from personal_brain.core.ingestion import ingest_path
from personal_brain.core.search import search_files

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
    .sub-header {
        font-size: 1.5rem;
        font-weight: 600;
        color: #333;
        margin-top: 2rem;
        margin-bottom: 1rem;
    }
    .card {
        background-color: #f9f9f9;
        padding: 1.5rem;
        border-radius: 10px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        margin-bottom: 1rem;
    }
    .card-title {
        font-size: 1.2rem;
        font-weight: 600;
        margin-bottom: 0.5rem;
    }
    .card-meta {
        font-size: 0.9rem;
        color: #666;
        margin-bottom: 0.5rem;
    }
    .card-snippet {
        font-size: 1rem;
        color: #333;
        background-color: #fff;
        padding: 0.5rem;
        border-radius: 5px;
        border: 1px solid #eee;
    }
</style>
""", unsafe_allow_html=True)

# Sidebar
st.sidebar.title("🧠 PersonalBrain")
st.sidebar.markdown("Your second brain for managing personal information.")

menu = ["Search", "Ingest", "Manage"]
choice = st.sidebar.selectbox("Navigation", menu)

# Helper functions
def get_db_status():
    if os.path.exists(DB_PATH):
        return "Active", "green"
    return "Not Initialized", "red"

# Search Page
if choice == "Search":
    st.markdown('<div class="main-header">Semantic Search</div>', unsafe_allow_html=True)
    
    col1, col2 = st.columns([3, 1])
    with col1:
        query = st.text_input("What are you looking for?", placeholder="e.g., 'notes about machine learning' or 'invoice from last month'")
    with col2:
        limit = st.slider("Limit results", min_value=1, max_value=20, value=5)
    
    if query:
        with st.spinner("Searching your brain..."):
            try:
                # Check DB status first
                if not os.path.exists(DB_PATH):
                    st.error("Database not initialized. Please go to 'Manage' tab and initialize first.")
                else:
                    results = search_files(query, limit)
                    
                    if not results:
                        st.info("No matching results found.")
                    else:
                        st.markdown(f"Found **{len(results)}** results:")
                        
                        for res in results:
                            score = res.get('trash_score', 0)
                            dist = res.get('distance', 0)
                            file_type = res.get('type', 'unknown')
                            filename = res.get('filename', 'Unknown')
                            path = res.get('path', '')
                            ocr_text = res.get('ocr_text', '')
                            
                            with st.container():
                                st.markdown(f"""
                                <div class="card">
                                    <div class="card-title">{filename}</div>
                                    <div class="card-meta">
                                        Type: {file_type} | Distance: {dist:.4f} | Trash Score: {score:.2f}
                                    </div>
                                    <div class="card-meta">Path: {path}</div>
                                </div>
                                """, unsafe_allow_html=True)
                                
                                if ocr_text:
                                    with st.expander("View Content Snippet"):
                                        st.text(ocr_text[:500] + "..." if len(ocr_text) > 500 else ocr_text)

            except Exception as e:
                st.error(f"An error occurred during search: {str(e)}")

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
st.sidebar.caption("v0.1.0 | Powered by Streamlit")
