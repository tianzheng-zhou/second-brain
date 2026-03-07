"""
admin.py — Streamlit admin dashboard for PersonalBrain.
Run: streamlit run personal_brain/admin.py
"""
from __future__ import annotations

import sys
import json
from pathlib import Path

# Allow running via `streamlit run personal_brain/admin.py` (no package context)
if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import personal_brain  # noqa: F401 — ensures package is importable

import streamlit as st

# Initialize DB on startup
@st.cache_resource
def _init():
    from personal_brain import database as db
    db.init_db()
    return db

db = _init()

st.set_page_config(page_title="PersonalBrain Admin", layout="wide")
st.title("PersonalBrain Admin")

tab_ingest, tab_manage, tab_config = st.tabs(["Ingest", "Manage", "Config"])

# ---------------------------------------------------------------------------
# Ingest Tab
# ---------------------------------------------------------------------------
with tab_ingest:
    st.header("Import Files")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Import from Path")
        path_input = st.text_input("File or folder path")
        if st.button("Ingest Path"):
            if path_input:
                p = Path(path_input)
                if not p.exists():
                    st.error(f"Path not found: {path_input}")
                else:
                    with st.spinner("Ingesting..."):
                        try:
                            from personal_brain.ingestion import process_directory, process_file
                            if p.is_dir():
                                result = process_directory(p)
                                st.success(
                                    f"Done: {result['success']} success, "
                                    f"{result['skip']} skip, {result['fail']} fail"
                                )
                                if result["failures"]:
                                    with st.expander("Failures"):
                                        for f in result["failures"]:
                                            st.error(f"{f['path']}: {f['error']}")
                            else:
                                result = process_file(p)
                                st.success(f"Ingested: {result}")
                        except Exception as e:
                            st.error(str(e))
            else:
                st.warning("Enter a path")

    with col2:
        st.subheader("Upload Files")
        SUPPORTED = "png,jpg,jpeg,webp,gif,mp3,wav,ogg,m4a,pdf,txt,md,markdown,json,csv,py,js,html,css,yaml,yml,xml"
        uploaded = st.file_uploader(
            "Upload files",
            accept_multiple_files=True,
            type=SUPPORTED.split(","),
        )
        if uploaded and st.button("Process Uploaded Files"):
            import tempfile
            from personal_brain.ingestion import process_file
            success = 0
            errors = []
            for uf in uploaded:
                with tempfile.NamedTemporaryFile(
                    suffix=Path(uf.name).suffix, delete=False
                ) as tmp:
                    tmp.write(uf.read())
                    tmp_path = Path(tmp.name)
                    tmp_path_renamed = tmp_path.parent / uf.name

                try:
                    tmp_path.rename(tmp_path_renamed)
                    with st.spinner(f"Processing {uf.name}..."):
                        result = process_file(tmp_path_renamed)
                    success += 1
                    st.success(f"{uf.name}: {result.get('status', 'ok')}")
                except Exception as e:
                    errors.append(f"{uf.name}: {e}")
                finally:
                    if tmp_path_renamed.exists():
                        tmp_path_renamed.unlink()

            if errors:
                for err in errors:
                    st.error(err)

# ---------------------------------------------------------------------------
# Manage Tab
# ---------------------------------------------------------------------------
with tab_manage:
    st.header("Database Management")

    col_stats, col_actions = st.columns([2, 1])

    with col_stats:
        try:
            stats = db.get_stats()
            st.metric("Total Files", stats["total_files"])
            st.metric("Total Notes", stats["total_entries"])
            st.metric("Total Chunks", stats["total_chunks"])
            st.metric("Total Vectors", stats["total_vectors"])
            st.metric("DB Size (MB)", stats["db_size_mb"])
        except Exception as e:
            st.warning(f"Stats unavailable: {e}")

    with col_actions:
        if st.button("Initialize Database"):
            db.init_db()
            st.success("Database initialized")

        if st.button("Reset Database (DANGER)", type="secondary"):
            st.session_state["reset_confirm"] = True

        if st.session_state.get("reset_confirm"):
            st.warning("This will delete ALL data!")
            if st.button("Confirm Reset"):
                db.reset_db()
                st.success("Database reset complete")
                del st.session_state["reset_confirm"]
            if st.button("Cancel"):
                del st.session_state["reset_confirm"]

    st.divider()
    st.subheader("Scan Orphan Files")
    if st.button("Scan"):
        orphans = db.scan_orphan_files()
        if orphans:
            st.warning(f"Found {len(orphans)} orphan files")
            for op in orphans:
                col_a, col_b, col_c = st.columns([3, 1, 1])
                col_a.text(str(op))
                if col_b.button("Re-ingest", key=f"re_{op}"):
                    from personal_brain.ingestion import process_file
                    try:
                        result = process_file(op)
                        st.success(f"Ingested: {result['file_id']}")
                    except Exception as e:
                        st.error(str(e))
                if col_c.button("Delete", key=f"del_{op}"):
                    op.unlink()
                    st.success(f"Deleted {op.name}")
        else:
            st.success("No orphan files found")

    st.divider()
    st.subheader("File List")
    try:
        files, total = db.list_files(limit=100)
        if files:
            import pandas as pd
            rows = []
            for f in files:
                rows.append({
                    "ID": f.id,
                    "Filename": f.filename,
                    "Type": f.type,
                    "Status": f.status,
                    "Enrichment": f.enrichment_status,
                    "Size (KB)": round(f.size_bytes / 1024, 1),
                    "Created": f.created_at.strftime("%Y-%m-%d %H:%M") if f.created_at else "",
                })
            df = pd.DataFrame(rows)
            st.dataframe(df, width="stretch")

            st.subheader("Single File Operations")
            file_ids = [f.id for f in files]
            selected_id = st.selectbox("Select file ID", file_ids)
            if selected_id:
                col1, col2, col3, col4, col5 = st.columns(5)
                if col1.button("Refresh Index"):
                    from personal_brain.ingestion import refresh_index_for_file
                    try:
                        result = refresh_index_for_file(selected_id)
                        st.success(f"Index refreshed: {result}")
                    except Exception as e:
                        st.error(str(e))

                if col2.button("Archive"):
                    db.archive_file(selected_id)
                    st.success("Archived")

                if col3.button("Restore"):
                    db.restore_file(selected_id)
                    st.success("Restored")

                if col4.button("Delete"):
                    st.session_state[f"del_confirm_{selected_id}"] = True

                if col5.button("View Details"):
                    fobj = db.get_file(selected_id)
                    if fobj:
                        st.json(fobj.model_dump(mode="json"))

                st.divider()
                st.subheader("Chunk Details")
                chunks = db.get_chunks_for_file(selected_id)
                if chunks:
                    import pandas as pd
                    missing_vec = sum(1 for c in chunks if not c["has_vector"])
                    col_m1, col_m2, col_m3 = st.columns(3)
                    col_m1.metric("Chunks", len(chunks))
                    col_m2.metric("Missing Vector", missing_vec)
                    avg_chars = sum(c["char_count"] for c in chunks) // len(chunks)
                    col_m3.metric("Avg Chars / Chunk", avg_chars)

                    df_chunks = pd.DataFrame([
                        {
                            "Index": c["chunk_index"],
                            "Chars": c["char_count"],
                            "Page": c["page_number"] or "-",
                            "Has Vector": "✓" if c["has_vector"] else "✗",
                            "ID": c["id"],
                        }
                        for c in chunks
                    ])
                    st.dataframe(df_chunks, use_container_width=True)

                    selected_chunk_idx = st.selectbox(
                        "View chunk content",
                        options=[c["chunk_index"] for c in chunks],
                        format_func=lambda i: f"Chunk {i} ({chunks[i]['char_count']} chars, page {chunks[i]['page_number'] or '-'})",
                    )
                    if selected_chunk_idx is not None:
                        chunk = chunks[selected_chunk_idx]
                        st.text_area(
                            f"Chunk {chunk['chunk_index']} content",
                            value=chunk["content"],
                            height=300,
                        )
                else:
                    st.info("No chunks found for this file.")

                if st.session_state.get(f"del_confirm_{selected_id}"):
                    st.warning("Confirm deletion?")
                    if st.button("Yes, Delete", key=f"yes_del_{selected_id}"):
                        db.delete_file(selected_id)
                        st.success("Deleted")
                        del st.session_state[f"del_confirm_{selected_id}"]
        else:
            st.info("No files in database")
    except Exception as e:
        st.error(str(e))

# ---------------------------------------------------------------------------
# Config Tab
# ---------------------------------------------------------------------------
with tab_config:
    st.header("Runtime Configuration")

    from personal_brain import config_manager

    config = config_manager.get_all()

    st.subheader("Current Config")
    st.json(config)

    st.subheader("Modify Config")
    config_key = st.selectbox("Key", list(config.keys()))
    current_val = config.get(config_key, "")
    new_val_str = st.text_input("New value", value=str(current_val))

    if st.button("Save"):
        # Type coerce
        original = config.get(config_key)
        if isinstance(original, bool):
            new_val = new_val_str.lower() in ("true", "1", "yes")
        elif isinstance(original, int):
            new_val = int(new_val_str)
        elif isinstance(original, float):
            new_val = float(new_val_str)
        else:
            new_val = new_val_str
        config_manager.set(config_key, new_val)
        st.success(f"Saved {config_key} = {new_val}")

    st.subheader("Database Schema (Debug)")
    if st.button("Show Schema"):
        try:
            conn = db._get_conn()
            rows = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name"
            ).fetchall()
            for name, sql in rows:
                with st.expander(name):
                    st.code(sql or "(virtual table)", language="sql")
        except Exception as e:
            st.error(str(e))
