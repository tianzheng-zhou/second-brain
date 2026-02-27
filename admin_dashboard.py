import streamlit as st
import os
import time
import json
import pandas as pd
from pathlib import Path
from personal_brain.core.database import (
    init_db,
    get_all_files,
    delete_file_record,
    get_db_schema,
    get_file_chunks,
    get_all_entities,
    get_all_relations,
    get_entity_types_count,
    get_entity_by_id,
    get_entity_relations,
    get_relations_by_file,
    get_files_with_shared_entities
)
from personal_brain.config import ensure_dirs, STORAGE_PATH, DB_PATH
from personal_brain.core.config_manager import config_manager
from personal_brain.core.ingestion import ingest_path, refresh_index_for_file
from personal_brain.core.search import search_files


def render_entity_graph(nodes, links, graph_type):
    """Render entity-entity relationship graph using D3.js"""
    import streamlit.components.v1 as components

    color_scheme = {
        "person": "#FF6B6B",
        "project": "#4ECDC4",
        "location": "#45B7D1",
        "tech": "#96CEB4",
        "organization": "#FFEAA7",
        "concept": "#DDA0DD"
    }

    graph_data = {"nodes": nodes, "links": links}

    d3_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <script src="https://d3js.org/d3.v7.min.js"></script>
        <style>
            body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
            #graph {{
                width: 100%;
                height: 600px;
                background: #fafafa;
                border-radius: 8px;
                border: 1px solid #e0e0e0;
            }}
            .node circle {{
                cursor: pointer;
                stroke: #fff;
                stroke-width: 2px;
            }}
            .node:hover circle {{
                stroke: #333;
                stroke-width: 3px;
            }}
            .node text {{
                font-size: 12px;
                pointer-events: none;
                fill: #333;
                font-weight: 500;
            }}
            .link {{
                stroke: #999;
                stroke-opacity: 0.6;
                stroke-width: 1.5px;
            }}
            .tooltip {{
                position: absolute;
                padding: 10px;
                background: rgba(0, 0, 0, 0.9);
                color: white;
                border-radius: 6px;
                pointer-events: none;
                font-size: 13px;
                z-index: 1000;
            }}
            .legend {{
                position: absolute;
                top: 10px;
                right: 10px;
                background: white;
                padding: 10px;
                border-radius: 6px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.15);
                font-size: 12px;
            }}
            .legend-item {{
                display: flex;
                align-items: center;
                margin: 4px 0;
            }}
            .legend-color {{
                width: 12px;
                height: 12px;
                border-radius: 50%;
                margin-right: 8px;
            }}
        </style>
    </head>
    <body>
        <div id="graph"></div>
        <div class="legend">
            <strong>Entity Types</strong>
            <div class="legend-item"><div class="legend-color" style="background:#FF6B6B"></div>Person</div>
            <div class="legend-item"><div class="legend-color" style="background:#4ECDC4"></div>Project</div>
            <div class="legend-item"><div class="legend-color" style="background:#45B7D1"></div>Location</div>
            <div class="legend-item"><div class="legend-color" style="background:#96CEB4"></div>Tech</div>
            <div class="legend-item"><div class="legend-color" style="background:#FFEAA7"></div>Organization</div>
            <div class="legend-item"><div class="legend-color" style="background:#DDA0DD"></div>Concept</div>
        </div>

        <script>
            const data = {json.dumps(graph_data)};
            const colorScheme = {json.dumps(color_scheme)};
            const width = document.getElementById('graph').clientWidth;
            const height = 600;

            const svg = d3.select("#graph")
                .append("svg")
                .attr("width", width)
                .attr("height", height)
                .call(d3.zoom().on("zoom", (event) => {{
                    g.attr("transform", event.transform);
                }}));

            const g = svg.append("g");

            const tooltip = d3.select("body").append("div")
                .attr("class", "tooltip")
                .style("opacity", 0);

            const simulation = d3.forceSimulation(data.nodes)
                .force("link", d3.forceLink(data.links).id(d => d.id).distance(120))
                .force("charge", d3.forceManyBody().strength(-300))
                .force("center", d3.forceCenter(width / 2, height / 2))
                .force("collision", d3.forceCollide().radius(d => Math.sqrt(d.mention_count) * 8 + 15));

            const link = g.append("g")
                .selectAll("line")
                .data(data.links)
                .enter().append("line")
                .attr("class", "link")
                .on("mouseover", function(event, d) {{
                    tooltip.transition().duration(200).style("opacity", 1);
                    tooltip.html(`<strong>${{d.type}}</strong><br/>Confidence: ${{d.confidence.toFixed(2)}}`)
                        .style("left", (event.pageX + 10) + "px")
                        .style("top", (event.pageY - 28) + "px");
                }})
                .on("mouseout", function() {{
                    tooltip.transition().duration(500).style("opacity", 0);
                }});

            const node = g.append("g")
                .selectAll("g")
                .data(data.nodes)
                .enter().append("g")
                .attr("class", "node")
                .call(d3.drag()
                    .on("start", dragstarted)
                    .on("drag", dragged)
                    .on("end", dragended));

            node.append("circle")
                .attr("r", d => Math.sqrt(d.mention_count) * 8 + 5)
                .attr("fill", d => colorScheme[d.type] || "#888")
                .on("mouseover", function(event, d) {{
                    tooltip.transition().duration(200).style("opacity", 1);
                    tooltip.html(`<strong>${{d.name}}</strong><br/>Type: ${{d.type}}<br/>Mentions: ${{d.mention_count}}`)
                        .style("left", (event.pageX + 10) + "px")
                        .style("top", (event.pageY - 28) + "px");
                }})
                .on("mouseout", function() {{
                    tooltip.transition().duration(500).style("opacity", 0);
                }});

            node.append("text")
                .attr("dx", d => Math.sqrt(d.mention_count) * 8 + 10)
                .attr("dy", ".35em")
                .text(d => d.name.length > 15 ? d.name.substring(0, 15) + '...' : d.name);

            simulation.on("tick", () => {{
                link
                    .attr("x1", d => d.source.x)
                    .attr("y1", d => d.source.y)
                    .attr("x2", d => d.target.x)
                    .attr("y2", d => d.target.y);
                node.attr("transform", d => `translate(${{d.x}},${{d.y}})`);
            }});

            function dragstarted(event, d) {{
                if (!event.active) simulation.alphaTarget(0.3).restart();
                d.fx = d.x;
                d.fy = d.y;
            }}

            function dragged(event, d) {{
                d.fx = event.x;
                d.fy = event.y;
            }}

            function dragended(event, d) {{
                if (!event.active) simulation.alphaTarget(0);
                d.fx = null;
                d.fy = null;
            }}
        </script>
    </body>
    </html>
    """

    components.html(d3_html, height=650, scrolling=False)


def render_file_connection_graph(file_nodes, file_links):
    """Render file-file connection graph based on shared entities"""
    import streamlit.components.v1 as components

    graph_data = {"nodes": file_nodes, "links": file_links}

    d3_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <script src="https://d3js.org/d3.v7.min.js"></script>
        <style>
            body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
            #graph {{
                width: 100%;
                height: 600px;
                background: #f8f9fa;
                border-radius: 8px;
                border: 1px solid #e0e0e0;
            }}
            .node rect {{
                cursor: pointer;
                stroke: #fff;
                stroke-width: 2px;
            }}
            .node:hover rect {{
                stroke: #333;
                stroke-width: 3px;
            }}
            .node text {{
                font-size: 11px;
                pointer-events: none;
                fill: #333;
                font-weight: 500;
            }}
            .link {{
                stroke: #6C757D;
                stroke-opacity: 0.5;
            }}
            .tooltip {{
                position: absolute;
                padding: 10px;
                background: rgba(0, 0, 0, 0.9);
                color: white;
                border-radius: 6px;
                pointer-events: none;
                font-size: 13px;
                max-width: 300px;
                z-index: 1000;
            }}
            .legend {{
                position: absolute;
                top: 10px;
                right: 10px;
                background: white;
                padding: 10px;
                border-radius: 6px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.15);
                font-size: 12px;
            }}
        </style>
    </head>
    <body>
        <div id="graph"></div>
        <div class="legend">
            <strong>File Connection Strength</strong>
            <div>Line thickness = shared entities</div>
            <div style="margin-top:5px; font-size:11px; color:#666;">
                Hover over lines to see shared entities
            </div>
        </div>

        <script>
            const data = {json.dumps(graph_data)};
            const width = document.getElementById('graph').clientWidth;
            const height = 600;

            const svg = d3.select("#graph")
                .append("svg")
                .attr("width", width)
                .attr("height", height)
                .call(d3.zoom().on("zoom", (event) => {{
                    g.attr("transform", event.transform);
                }}));

            const g = svg.append("g");

            const tooltip = d3.select("body").append("div")
                .attr("class", "tooltip")
                .style("opacity", 0);

            const simulation = d3.forceSimulation(data.nodes)
                .force("link", d3.forceLink(data.links).id(d => d.id).distance(150))
                .force("charge", d3.forceManyBody().strength(-400))
                .force("center", d3.forceCenter(width / 2, height / 2))
                .force("collision", d3.forceCollide().radius(40));

            const link = g.append("g")
                .selectAll("line")
                .data(data.links)
                .enter().append("line")
                .attr("class", "link")
                .attr("stroke-width", d => Math.min(d.shared_count * 2, 10))
                .on("mouseover", function(event, d) {{
                    tooltip.transition().duration(200).style("opacity", 1);
                    const entityList = d.shared_entities.join(', ');
                    tooltip.html(`<strong>Shared Entities: ${{d.shared_count}}</strong><br/>${{entityList}}`)
                        .style("left", (event.pageX + 10) + "px")
                        .style("top", (event.pageY - 28) + "px");
                }})
                .on("mouseout", function() {{
                    tooltip.transition().duration(500).style("opacity", 0);
                }});

            const node = g.append("g")
                .selectAll("g")
                .data(data.nodes)
                .enter().append("g")
                .attr("class", "node")
                .call(d3.drag()
                    .on("start", dragstarted)
                    .on("drag", dragged)
                    .on("end", dragended));

            node.append("rect")
                .attr("width", d => Math.min(30 + d.entity_count * 2, 60))
                .attr("height", 25)
                .attr("x", d => -(Math.min(30 + d.entity_count * 2, 60)) / 2)
                .attr("y", -12.5)
                .attr("rx", 5)
                .attr("fill", "#6C757D")
                .on("mouseover", function(event, d) {{
                    tooltip.transition().duration(200).style("opacity", 1);
                    tooltip.html(`<strong>${{d.name}}</strong><br/>Entities: ${{d.entity_count}}<br/>Type: ${{d.file_type}}`)
                        .style("left", (event.pageX + 10) + "px")
                        .style("top", (event.pageY - 28) + "px");
                }})
                .on("mouseout", function() {{
                    tooltip.transition().duration(500).style("opacity", 0);
                }});

            node.append("text")
                .attr("dy", ".35em")
                .attr("text-anchor", "middle")
                .text(d => d.name.length > 20 ? d.name.substring(0, 20) + '...' : d.name);

            simulation.on("tick", () => {{
                link
                    .attr("x1", d => d.source.x)
                    .attr("y1", d => d.source.y)
                    .attr("x2", d => d.target.x)
                    .attr("y2", d => d.target.y);
                node.attr("transform", d => `translate(${{d.x}},${{d.y}})`);
            }});

            function dragstarted(event, d) {{
                if (!event.active) simulation.alphaTarget(0.3).restart();
                d.fx = d.x;
                d.fy = d.y;
            }}

            function dragged(event, d) {{
                d.fx = event.x;
                d.fy = event.y;
            }}

            function dragended(event, d) {{
                if (!event.active) simulation.alphaTarget(0);
                d.fx = null;
                d.fy = null;
            }}
        </script>
    </body>
    </html>
    """

    components.html(d3_html, height=650, scrolling=False)


def render_bridge_graph(nodes, links):
    """Render bipartite file-entity bridge graph"""
    import streamlit.components.v1 as components

    color_scheme = {
        "person": "#FF6B6B",
        "project": "#4ECDC4",
        "location": "#45B7D1",
        "tech": "#96CEB4",
        "organization": "#FFEAA7",
        "concept": "#DDA0DD",
        "file": "#6C757D"
    }

    graph_data = {"nodes": nodes, "links": links}

    d3_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <script src="https://d3js.org/d3.v7.min.js"></script>
        <style>
            body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
            #graph {{
                width: 100%;
                height: 600px;
                background: #fafafa;
                border-radius: 8px;
                border: 1px solid #e0e0e0;
            }}
            .node circle {{
                cursor: pointer;
                stroke: #fff;
                stroke-width: 2px;
            }}
            .node rect {{
                cursor: pointer;
                stroke: #fff;
                stroke-width: 2px;
            }}
            .node:hover circle, .node:hover rect {{
                stroke: #333;
                stroke-width: 3px;
            }}
            .node text {{
                font-size: 11px;
                pointer-events: none;
                fill: #333;
                font-weight: 500;
            }}
            .link {{
                stroke: #adb5bd;
                stroke-opacity: 0.4;
                stroke-width: 1px;
            }}
            .tooltip {{
                position: absolute;
                padding: 10px;
                background: rgba(0, 0, 0, 0.9);
                color: white;
                border-radius: 6px;
                pointer-events: none;
                font-size: 13px;
                z-index: 1000;
            }}
            .legend {{
                position: absolute;
                top: 10px;
                right: 10px;
                background: white;
                padding: 10px;
                border-radius: 6px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.15);
                font-size: 12px;
            }}
            .legend-item {{
                display: flex;
                align-items: center;
                margin: 4px 0;
            }}
            .legend-color {{
                width: 12px;
                height: 12px;
                border-radius: 50%;
                margin-right: 8px;
            }}
            .legend-rect {{
                width: 12px;
                height: 12px;
                border-radius: 2px;
                margin-right: 8px;
            }}
        </style>
    </head>
    <body>
        <div id="graph"></div>
        <div class="legend">
            <strong>Nodes</strong>
            <div class="legend-item"><div class="legend-rect" style="background:#6C757D"></div>File</div>
            <div class="legend-item"><div class="legend-color" style="background:#FF6B6B"></div>Person</div>
            <div class="legend-item"><div class="legend-color" style="background:#4ECDC4"></div>Project</div>
            <div class="legend-item"><div class="legend-color" style="background:#45B7D1"></div>Location</div>
            <div class="legend-item"><div class="legend-color" style="background:#96CEB4"></div>Tech</div>
            <div class="legend-item"><div class="legend-color" style="background:#FFEAA7"></div>Organization</div>
            <div class="legend-item"><div class="legend-color" style="background:#DDA0DD"></div>Concept</div>
        </div>

        <script>
            const data = {json.dumps(graph_data)};
            const colorScheme = {json.dumps(color_scheme)};
            const width = document.getElementById('graph').clientWidth;
            const height = 600;

            const svg = d3.select("#graph")
                .append("svg")
                .attr("width", width)
                .attr("height", height)
                .call(d3.zoom().on("zoom", (event) => {{
                    g.attr("transform", event.transform);
                }}));

            const g = svg.append("g");

            const tooltip = d3.select("body").append("div")
                .attr("class", "tooltip")
                .style("opacity", 0);

            const simulation = d3.forceSimulation(data.nodes)
                .force("link", d3.forceLink(data.links).id(d => d.id).distance(80))
                .force("charge", d3.forceManyBody().strength(d => d.nodeType === 'file' ? -300 : -100))
                .force("center", d3.forceCenter(width / 2, height / 2))
                .force("collision", d3.forceCollide().radius(20));

            const link = g.append("g")
                .selectAll("line")
                .data(data.links)
                .enter().append("line")
                .attr("class", "link");

            const node = g.append("g")
                .selectAll("g")
                .data(data.nodes)
                .enter().append("g")
                .attr("class", "node")
                .call(d3.drag()
                    .on("start", dragstarted)
                    .on("drag", dragged)
                    .on("end", dragended));

            // Different shapes for file vs entity
            node.each(function(d) {{
                const el = d3.select(this);
                if (d.nodeType === 'file') {{
                    el.append("rect")
                        .attr("width", 24)
                        .attr("height", 18)
                        .attr("x", -12)
                        .attr("y", -9)
                        .attr("rx", 3)
                        .attr("fill", colorScheme.file);
                }} else {{
                    el.append("circle")
                        .attr("r", d => Math.sqrt(d.mention_count || 1) * 6 + 4)
                        .attr("fill", d => colorScheme[d.type] || "#888");
                }}
            }});

            node.on("mouseover", function(event, d) {{
                tooltip.transition().duration(200).style("opacity", 1);
                if (d.nodeType === 'file') {{
                    tooltip.html(`<strong>${{d.name}}</strong><br/>Type: File`)
                        .style("left", (event.pageX + 10) + "px")
                        .style("top", (event.pageY - 28) + "px");
                }} else {{
                    tooltip.html(`<strong>${{d.name}}</strong><br/>Type: ${{d.type}}`)
                        .style("left", (event.pageX + 10) + "px")
                        .style("top", (event.pageY - 28) + "px");
                }}
            }})
            .on("mouseout", function() {{
                tooltip.transition().duration(500).style("opacity", 0);
            }});

            node.append("text")
                .attr("dx", d => d.nodeType === 'file' ? 16 : 12)
                .attr("dy", ".35em")
                .text(d => d.name.length > 12 ? d.name.substring(0, 12) + '...' : d.name);

            simulation.on("tick", () => {{
                link
                    .attr("x1", d => d.source.x)
                    .attr("y1", d => d.source.y)
                    .attr("x2", d => d.target.x)
                    .attr("y2", d => d.target.y);
                node.attr("transform", d => `translate(${{d.x}},${{d.y}})`);
            }});

            function dragstarted(event, d) {{
                if (!event.active) simulation.alphaTarget(0.3).restart();
                d.fx = d.x;
                d.fy = d.y;
            }}

            function dragged(event, d) {{
                d.fx = event.x;
                d.fy = event.y;
            }}

            function dragended(event, d) {{
                if (!event.active) simulation.alphaTarget(0);
                d.fx = null;
                d.fy = null;
            }}
        </script>
    </body>
    </html>
    """

    components.html(d3_html, height=650, scrolling=False)

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
        "Knowledge Graph": "🧠",
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
        semantic_split_options = ["qwen3.5-flash", "qwen3.5-plus"]  # Two options for comparison

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

        current_semantic_split_model = current_config.get("semantic_split_model")
        if current_semantic_split_model not in semantic_split_options:
            current_semantic_split_model = semantic_split_options[0]

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
                new_semantic_split_model = st.selectbox(
                    "Semantic Split Model (Chunk Segmentation)",
                    options=semantic_split_options,
                    index=semantic_split_options.index(current_semantic_split_model),
                    help="Used for semantically splitting documents into chunks during indexing."
                )

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
                config_manager.set("semantic_split_model", new_semantic_split_model)
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

# --- KNOWLEDGE GRAPH ---
elif menu == "Knowledge Graph":
    st.markdown('<div class="main-header">Knowledge Graph Visualization</div>', unsafe_allow_html=True)
    st.markdown("Explore entities, files, and their cross-file relationships.")

    # Load all data
    entities = get_all_entities(limit=500)
    relations = get_all_relations(limit=2000)
    type_counts = get_entity_types_count()
    files = get_all_files()

    # Create lookups
    files_dict = {f['id']: f for f in files}
    entities_dict = {e['id']: e for e in entities}

    if not entities:
        st.info("No entities found in the knowledge graph. Start by processing some files!")
    else:
        # Statistics row
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Entities", len(entities))
        with col2:
            st.metric("Total Relations", len(relations))
        with col3:
            files_with_entities = len(set(r['file_id'] for r in relations if r['file_id']))
            st.metric("Files with Entities", files_with_entities)
        with col4:
            # Calculate file connections
            file_connections = get_files_with_shared_entities()
            connected_pairs = sum(1 for conn in file_connections if conn['shared_count'] > 0)
            st.metric("File-File Connections", connected_pairs)

        # View mode selector
        st.markdown("---")
        st.markdown("### 📊 View Mode")

        view_mode = st.radio(
            "Select visualization mode",
            options=["entity_view", "file_connection_view", "file_entity_bridge"],
            format_func=lambda x: {
                "entity_view": "🧠 Entity-Entity Relations",
                "file_connection_view": "📁 File-File Connections (via shared entities)",
                "file_entity_bridge": "🌉 File-Entity Bridge (bipartite)"
            }[x],
            horizontal=True
        )

        if view_mode == "entity_view":
            # ===== ENTITY VIEW (Original) =====
            st.markdown("#### Entity-Entity Relationship Graph")

            # File filter for entity view
            col_filter1, col_filter2 = st.columns([2, 2])
            with col_filter1:
                file_options = [("all", "📂 All Files")] + [(f['id'], f"📄 {f['filename']}") for f in files[:50]]
                selected_file_id = st.selectbox(
                    "Filter by source file",
                    options=[f[0] for f in file_options],
                    format_func=lambda x: next(f[1] for f in file_options if f[0] == x),
                    key="entity_file_filter"
                )

            with col_filter2:
                entity_types = ["All"] + [t['type'] for t in type_counts]
                selected_type = st.selectbox("Filter by entity type", entity_types)

            # Apply filters
            if selected_file_id != "all":
                file_relations = get_relations_by_file(selected_file_id)
                file_entity_ids = set()
                for r in file_relations:
                    file_entity_ids.add(r['source'])
                    file_entity_ids.add(r['target'])
                filtered_entities = [e for e in entities if e['id'] in file_entity_ids]
                filtered_relations = file_relations
            else:
                filtered_entities = entities
                filtered_relations = relations

            if selected_type != "All":
                filtered_entities = [e for e in filtered_entities if e['type'] == selected_type]

            # Limit for performance
            visible_entity_ids = {e['id'] for e in filtered_entities}
            final_relations = [r for r in filtered_relations
                              if r['source'] in visible_entity_ids and r['target'] in visible_entity_ids]

            if len(filtered_entities) > 100:
                st.warning(f"Showing top 100 of {len(filtered_entities)} entities for performance.")
                filtered_entities = filtered_entities[:100]
                visible_entity_ids = {e['id'] for e in filtered_entities}
                final_relations = [r for r in final_relations
                                  if r['source'] in visible_entity_ids and r['target'] in visible_entity_ids]

            st.caption(f"Showing {len(filtered_entities)} entities and {len(final_relations)} relations")

            # Build graph data
            nodes = []
            for e in filtered_entities:
                nodes.append({
                    "id": e['id'],
                    "name": e['name'],
                    "type": e['type'],
                    "nodeType": "entity",
                    "mention_count": e.get('mention_count', 1)
                })

            links = []
            for r in final_relations:
                links.append({
                    "source": r['source'],
                    "target": r['target'],
                    "type": r['type'],
                    "confidence": r.get('confidence', 1.0)
                })

            # Render D3 graph
            render_entity_graph(nodes, links, "entity")

        elif view_mode == "file_connection_view":
            # ===== FILE CONNECTION VIEW =====
            st.markdown("#### File-File Connection Graph")
            st.caption("Files are connected if they share common entities. Line thickness = number of shared entities.")

            # Get file connections
            file_connections = get_files_with_shared_entities()

            # Filter connections by minimum shared entities
            min_shared = st.slider("Minimum shared entities to show connection", 1, 10, 1)

            # Build file-file graph
            file_nodes = []
            file_links = []

            # Get files that have entities
            files_with_entities = set()
            for conn in file_connections:
                if conn['shared_count'] >= min_shared:
                    files_with_entities.add(conn['file1_id'])
                    files_with_entities.add(conn['file2_id'])

            # Create file nodes
            for file_id in files_with_entities:
                file_info = files_dict.get(file_id)
                if file_info:
                    # Count entities in this file
                    entity_count = len(set(r['source'] for r in relations if r['file_id'] == file_id) |
                                      set(r['target'] for r in relations if r['file_id'] == file_id))
                    file_nodes.append({
                        "id": file_id,
                        "name": file_info['filename'][:25] + "..." if len(file_info['filename']) > 25 else file_info['filename'],
                        "type": "file",
                        "nodeType": "file",
                        "entity_count": entity_count,
                        "file_type": file_info.get('type', 'unknown')
                    })

            # Create file-file links based on shared entities
            for conn in file_connections:
                if conn['shared_count'] >= min_shared:
                    file_links.append({
                        "source": conn['file1_id'],
                        "target": conn['file2_id'],
                        "shared_count": conn['shared_count'],
                        "shared_entities": conn['shared_entities'][:5]  # Limit for display
                    })

            st.caption(f"Showing {len(file_nodes)} files with {len(file_links)} connections")

            if len(file_nodes) == 0:
                st.info("No file connections found. Try lowering the minimum shared entities threshold.")
            else:
                # Render file connection graph
                render_file_connection_graph(file_nodes, file_links)

        elif view_mode == "file_entity_bridge":
            # ===== FILE-ENTITY BRIDGE VIEW =====
            st.markdown("#### File-Entity Bipartite Graph")
            st.caption("Shows which files contain which entities. Files (squares) connect to Entities (circles).")

            # Select files to display
            selected_files = st.multiselect(
                "Select files to display (or leave empty for all)",
                options=[f['id'] for f in files],
                format_func=lambda x: files_dict.get(x, {}).get('filename', x)[:40],
                default=[]
            )

            if not selected_files:
                # Use files that have entities
                selected_files = list(set(r['file_id'] for r in relations if r['file_id']))[:20]

            # Build bipartite graph
            bridge_nodes = []
            bridge_links = []
            connected_entities = set()

            # Add file nodes
            for file_id in selected_files:
                file_info = files_dict.get(file_id)
                if file_info:
                    bridge_nodes.append({
                        "id": f"file_{file_id}",
                        "name": file_info['filename'][:20] + "..." if len(file_info['filename']) > 20 else file_info['filename'],
                        "type": "file",
                        "nodeType": "file"
                    })

            # Find entities connected to these files
            for r in relations:
                if r['file_id'] in selected_files:
                    connected_entities.add(r['source'])
                    connected_entities.add(r['target'])
                    bridge_links.append({
                        "source": f"file_{r['file_id']}",
                        "target": r['source'],
                        "linkType": "file-entity"
                    })
                    bridge_links.append({
                        "source": f"file_{r['file_id']}",
                        "target": r['target'],
                        "linkType": "file-entity"
                    })

            # Add entity nodes
            for entity_id in connected_entities:
                entity = entities_dict.get(entity_id)
                if entity:
                    bridge_nodes.append({
                        "id": entity_id,
                        "name": entity['name'],
                        "type": entity['type'],
                        "nodeType": "entity",
                        "mention_count": entity.get('mention_count', 1)
                    })

            st.caption(f"Showing {len(selected_files)} files connected to {len(connected_entities)} entities")

            # Render bridge graph
            render_bridge_graph(bridge_nodes, bridge_links)

        # Entity details section (only for entity view)
        if view_mode == "entity_view":
            st.markdown("---")
            st.markdown("### Entity Details")

            col_detail1, col_detail2 = st.columns([1, 2])

            with col_detail1:
                selected_entity_name = st.selectbox(
                    "Select an entity to view details",
                    options=[e['name'] for e in filtered_entities],
                    index=0 if filtered_entities else None
                )

            if selected_entity_name:
                selected_entity = next((e for e in filtered_entities if e['name'] == selected_entity_name), None)

                if selected_entity:
                    with col_detail2:
                        cols = st.columns(3)
                        with cols[0]:
                            st.markdown(f"**Name:** {selected_entity['name']}")
                        with cols[1]:
                            st.markdown(f"**Type:** {selected_entity['type']}")
                        with cols[2]:
                            st.markdown(f"**Mentions:** {selected_entity.get('mention_count', 1)}")

                    # Get entity's relations
                    entity_rels = get_entity_relations(selected_entity['id'])

                    if entity_rels:
                        st.markdown("**Relations:**")
                        rel_data = []
                        for r in entity_rels:
                            direction = "→" if r['source'] == selected_entity['id'] else "←"
                            other_name = r['target_name'] if r['source'] == selected_entity['id'] else r['source_name']
                            file_info = files_dict.get(r['file_id'])
                            source_file = file_info['filename'] if file_info else "Unknown"
                            rel_data.append({
                                "Relation": r['type'],
                                "Direction": direction,
                                "Connected Entity": other_name,
                                "Confidence": f"{r.get('confidence', 1.0):.2f}",
                                "Source File": source_file[:30] + "..." if len(source_file) > 30 else source_file
                            })

                        rel_df = pd.DataFrame(rel_data)
                        st.dataframe(rel_df, use_container_width=True, hide_index=True)
                    else:
                        st.info("No relations found for this entity.")


def render_entity_graph(nodes, links, graph_type):
    """Render entity-entity relationship graph using D3.js"""
    import streamlit.components.v1 as components

    color_scheme = {
        "person": "#FF6B6B",
        "project": "#4ECDC4",
        "location": "#45B7D1",
        "tech": "#96CEB4",
        "organization": "#FFEAA7",
        "concept": "#DDA0DD"
    }

    graph_data = {"nodes": nodes, "links": links}

    d3_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <script src="https://d3js.org/d3.v7.min.js"></script>
        <style>
            body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
            #graph {{
                width: 100%;
                height: 600px;
                background: #fafafa;
                border-radius: 8px;
                border: 1px solid #e0e0e0;
            }}
            .node circle {{
                cursor: pointer;
                stroke: #fff;
                stroke-width: 2px;
            }}
            .node:hover circle {{
                stroke: #333;
                stroke-width: 3px;
            }}
            .node text {{
                font-size: 12px;
                pointer-events: none;
                fill: #333;
                font-weight: 500;
            }}
            .link {{
                stroke: #999;
                stroke-opacity: 0.6;
                stroke-width: 1.5px;
            }}
            .tooltip {{
                position: absolute;
                padding: 10px;
                background: rgba(0, 0, 0, 0.9);
                color: white;
                border-radius: 6px;
                pointer-events: none;
                font-size: 13px;
                z-index: 1000;
            }}
            .legend {{
                position: absolute;
                top: 10px;
                right: 10px;
                background: white;
                padding: 10px;
                border-radius: 6px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.15);
                font-size: 12px;
            }}
            .legend-item {{
                display: flex;
                align-items: center;
                margin: 4px 0;
            }}
            .legend-color {{
                width: 12px;
                height: 12px;
                border-radius: 50%;
                margin-right: 8px;
            }}
        </style>
    </head>
    <body>
        <div id="graph"></div>
        <div class="legend">
            <strong>Entity Types</strong>
            <div class="legend-item"><div class="legend-color" style="background:#FF6B6B"></div>Person</div>
            <div class="legend-item"><div class="legend-color" style="background:#4ECDC4"></div>Project</div>
            <div class="legend-item"><div class="legend-color" style="background:#45B7D1"></div>Location</div>
            <div class="legend-item"><div class="legend-color" style="background:#96CEB4"></div>Tech</div>
            <div class="legend-item"><div class="legend-color" style="background:#FFEAA7"></div>Organization</div>
            <div class="legend-item"><div class="legend-color" style="background:#DDA0DD"></div>Concept</div>
        </div>

        <script>
            const data = {json.dumps(graph_data)};
            const colorScheme = {json.dumps(color_scheme)};
            const width = document.getElementById('graph').clientWidth;
            const height = 600;

            const svg = d3.select("#graph")
                .append("svg")
                .attr("width", width)
                .attr("height", height)
                .call(d3.zoom().on("zoom", (event) => {{
                    g.attr("transform", event.transform);
                }}));

            const g = svg.append("g");

            const tooltip = d3.select("body").append("div")
                .attr("class", "tooltip")
                .style("opacity", 0);

            const simulation = d3.forceSimulation(data.nodes)
                .force("link", d3.forceLink(data.links).id(d => d.id).distance(120))
                .force("charge", d3.forceManyBody().strength(-300))
                .force("center", d3.forceCenter(width / 2, height / 2))
                .force("collision", d3.forceCollide().radius(d => Math.sqrt(d.mention_count) * 8 + 15));

            const link = g.append("g")
                .selectAll("line")
                .data(data.links)
                .enter().append("line")
                .attr("class", "link")
                .on("mouseover", function(event, d) {{
                    tooltip.transition().duration(200).style("opacity", 1);
                    tooltip.html(`<strong>${{d.type}}</strong><br/>Confidence: ${{d.confidence.toFixed(2)}}`)
                        .style("left", (event.pageX + 10) + "px")
                        .style("top", (event.pageY - 28) + "px");
                }})
                .on("mouseout", function() {{
                    tooltip.transition().duration(500).style("opacity", 0);
                }});

            const node = g.append("g")
                .selectAll("g")
                .data(data.nodes)
                .enter().append("g")
                .attr("class", "node")
                .call(d3.drag()
                    .on("start", dragstarted)
                    .on("drag", dragged)
                    .on("end", dragended));

            node.append("circle")
                .attr("r", d => Math.sqrt(d.mention_count) * 8 + 5)
                .attr("fill", d => colorScheme[d.type] || "#888")
                .on("mouseover", function(event, d) {{
                    tooltip.transition().duration(200).style("opacity", 1);
                    tooltip.html(`<strong>${{d.name}}</strong><br/>Type: ${{d.type}}<br/>Mentions: ${{d.mention_count}}`)
                        .style("left", (event.pageX + 10) + "px")
                        .style("top", (event.pageY - 28) + "px");
                }})
                .on("mouseout", function() {{
                    tooltip.transition().duration(500).style("opacity", 0);
                }});

            node.append("text")
                .attr("dx", d => Math.sqrt(d.mention_count) * 8 + 10)
                .attr("dy", ".35em")
                .text(d => d.name.length > 15 ? d.name.substring(0, 15) + '...' : d.name);

            simulation.on("tick", () => {{
                link
                    .attr("x1", d => d.source.x)
                    .attr("y1", d => d.source.y)
                    .attr("x2", d => d.target.x)
                    .attr("y2", d => d.target.y);
                node.attr("transform", d => `translate(${{d.x}},${{d.y}})`);
            }});

            function dragstarted(event, d) {{
                if (!event.active) simulation.alphaTarget(0.3).restart();
                d.fx = d.x;
                d.fy = d.y;
            }}

            function dragged(event, d) {{
                d.fx = event.x;
                d.fy = event.y;
            }}

            function dragended(event, d) {{
                if (!event.active) simulation.alphaTarget(0);
                d.fx = null;
                d.fy = null;
            }}
        </script>
    </body>
    </html>
    """

    components.html(d3_html, height=650, scrolling=False)


def render_file_connection_graph(file_nodes, file_links):
    """Render file-file connection graph based on shared entities"""
    import streamlit.components.v1 as components

    graph_data = {"nodes": file_nodes, "links": file_links}

    d3_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <script src="https://d3js.org/d3.v7.min.js"></script>
        <style>
            body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
            #graph {{
                width: 100%;
                height: 600px;
                background: #f8f9fa;
                border-radius: 8px;
                border: 1px solid #e0e0e0;
            }}
            .node rect {{
                cursor: pointer;
                stroke: #fff;
                stroke-width: 2px;
            }}
            .node:hover rect {{
                stroke: #333;
                stroke-width: 3px;
            }}
            .node text {{
                font-size: 11px;
                pointer-events: none;
                fill: #333;
                font-weight: 500;
            }}
            .link {{
                stroke: #6C757D;
                stroke-opacity: 0.5;
            }}
            .tooltip {{
                position: absolute;
                padding: 10px;
                background: rgba(0, 0, 0, 0.9);
                color: white;
                border-radius: 6px;
                pointer-events: none;
                font-size: 13px;
                max-width: 300px;
                z-index: 1000;
            }}
            .legend {{
                position: absolute;
                top: 10px;
                right: 10px;
                background: white;
                padding: 10px;
                border-radius: 6px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.15);
                font-size: 12px;
            }}
        </style>
    </head>
    <body>
        <div id="graph"></div>
        <div class="legend">
            <strong>File Connection Strength</strong>
            <div>Line thickness = shared entities</div>
            <div style="margin-top:5px; font-size:11px; color:#666;">
                Hover over lines to see shared entities
            </div>
        </div>

        <script>
            const data = {json.dumps(graph_data)};
            const width = document.getElementById('graph').clientWidth;
            const height = 600;

            const svg = d3.select("#graph")
                .append("svg")
                .attr("width", width)
                .attr("height", height)
                .call(d3.zoom().on("zoom", (event) => {{
                    g.attr("transform", event.transform);
                }}));

            const g = svg.append("g");

            const tooltip = d3.select("body").append("div")
                .attr("class", "tooltip")
                .style("opacity", 0);

            const simulation = d3.forceSimulation(data.nodes)
                .force("link", d3.forceLink(data.links).id(d => d.id).distance(150))
                .force("charge", d3.forceManyBody().strength(-400))
                .force("center", d3.forceCenter(width / 2, height / 2))
                .force("collision", d3.forceCollide().radius(40));

            const link = g.append("g")
                .selectAll("line")
                .data(data.links)
                .enter().append("line")
                .attr("class", "link")
                .attr("stroke-width", d => Math.min(d.shared_count * 2, 10))
                .on("mouseover", function(event, d) {{
                    tooltip.transition().duration(200).style("opacity", 1);
                    const entityList = d.shared_entities.join(', ');
                    tooltip.html(`<strong>Shared Entities: ${{d.shared_count}}</strong><br/>${{entityList}}`)
                        .style("left", (event.pageX + 10) + "px")
                        .style("top", (event.pageY - 28) + "px");
                }})
                .on("mouseout", function() {{
                    tooltip.transition().duration(500).style("opacity", 0);
                }});

            const node = g.append("g")
                .selectAll("g")
                .data(data.nodes)
                .enter().append("g")
                .attr("class", "node")
                .call(d3.drag()
                    .on("start", dragstarted)
                    .on("drag", dragged)
                    .on("end", dragended));

            node.append("rect")
                .attr("width", d => Math.min(30 + d.entity_count * 2, 60))
                .attr("height", 25)
                .attr("x", d => -(Math.min(30 + d.entity_count * 2, 60)) / 2)
                .attr("y", -12.5)
                .attr("rx", 5)
                .attr("fill", "#6C757D")
                .on("mouseover", function(event, d) {{
                    tooltip.transition().duration(200).style("opacity", 1);
                    tooltip.html(`<strong>${{d.name}}</strong><br/>Entities: ${{d.entity_count}}<br/>Type: ${{d.file_type}}`)
                        .style("left", (event.pageX + 10) + "px")
                        .style("top", (event.pageY - 28) + "px");
                }})
                .on("mouseout", function() {{
                    tooltip.transition().duration(500).style("opacity", 0);
                }});

            node.append("text")
                .attr("dy", ".35em")
                .attr("text-anchor", "middle")
                .text(d => d.name.length > 20 ? d.name.substring(0, 20) + '...' : d.name);

            simulation.on("tick", () => {{
                link
                    .attr("x1", d => d.source.x)
                    .attr("y1", d => d.source.y)
                    .attr("x2", d => d.target.x)
                    .attr("y2", d => d.target.y);
                node.attr("transform", d => `translate(${{d.x}},${{d.y}})`);
            }});

            function dragstarted(event, d) {{
                if (!event.active) simulation.alphaTarget(0.3).restart();
                d.fx = d.x;
                d.fy = d.y;
            }}

            function dragged(event, d) {{
                d.fx = event.x;
                d.fy = event.y;
            }}

            function dragended(event, d) {{
                if (!event.active) simulation.alphaTarget(0);
                d.fx = null;
                d.fy = null;
            }}
        </script>
    </body>
    </html>
    """

    components.html(d3_html, height=650, scrolling=False)


def render_bridge_graph(nodes, links):
    """Render bipartite file-entity bridge graph"""
    import streamlit.components.v1 as components

    color_scheme = {
        "person": "#FF6B6B",
        "project": "#4ECDC4",
        "location": "#45B7D1",
        "tech": "#96CEB4",
        "organization": "#FFEAA7",
        "concept": "#DDA0DD",
        "file": "#6C757D"
    }

    graph_data = {"nodes": nodes, "links": links}

    d3_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <script src="https://d3js.org/d3.v7.min.js"></script>
        <style>
            body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
            #graph {{
                width: 100%;
                height: 600px;
                background: #fafafa;
                border-radius: 8px;
                border: 1px solid #e0e0e0;
            }}
            .node circle {{
                cursor: pointer;
                stroke: #fff;
                stroke-width: 2px;
            }}
            .node rect {{
                cursor: pointer;
                stroke: #fff;
                stroke-width: 2px;
            }}
            .node:hover circle, .node:hover rect {{
                stroke: #333;
                stroke-width: 3px;
            }}
            .node text {{
                font-size: 11px;
                pointer-events: none;
                fill: #333;
                font-weight: 500;
            }}
            .link {{
                stroke: #adb5bd;
                stroke-opacity: 0.4;
                stroke-width: 1px;
            }}
            .tooltip {{
                position: absolute;
                padding: 10px;
                background: rgba(0, 0, 0, 0.9);
                color: white;
                border-radius: 6px;
                pointer-events: none;
                font-size: 13px;
                z-index: 1000;
            }}
            .legend {{
                position: absolute;
                top: 10px;
                right: 10px;
                background: white;
                padding: 10px;
                border-radius: 6px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.15);
                font-size: 12px;
            }}
            .legend-item {{
                display: flex;
                align-items: center;
                margin: 4px 0;
            }}
            .legend-color {{
                width: 12px;
                height: 12px;
                border-radius: 50%;
                margin-right: 8px;
            }}
            .legend-rect {{
                width: 12px;
                height: 12px;
                border-radius: 2px;
                margin-right: 8px;
            }}
        </style>
    </head>
    <body>
        <div id="graph"></div>
        <div class="legend">
            <strong>Nodes</strong>
            <div class="legend-item"><div class="legend-rect" style="background:#6C757D"></div>File</div>
            <div class="legend-item"><div class="legend-color" style="background:#FF6B6B"></div>Person</div>
            <div class="legend-item"><div class="legend-color" style="background:#4ECDC4"></div>Project</div>
            <div class="legend-item"><div class="legend-color" style="background:#45B7D1"></div>Location</div>
            <div class="legend-item"><div class="legend-color" style="background:#96CEB4"></div>Tech</div>
            <div class="legend-item"><div class="legend-color" style="background:#FFEAA7"></div>Organization</div>
            <div class="legend-item"><div class="legend-color" style="background:#DDA0DD"></div>Concept</div>
        </div>

        <script>
            const data = {json.dumps(graph_data)};
            const colorScheme = {json.dumps(color_scheme)};
            const width = document.getElementById('graph').clientWidth;
            const height = 600;

            const svg = d3.select("#graph")
                .append("svg")
                .attr("width", width)
                .attr("height", height)
                .call(d3.zoom().on("zoom", (event) => {{
                    g.attr("transform", event.transform);
                }}));

            const g = svg.append("g");

            const tooltip = d3.select("body").append("div")
                .attr("class", "tooltip")
                .style("opacity", 0);

            const simulation = d3.forceSimulation(data.nodes)
                .force("link", d3.forceLink(data.links).id(d => d.id).distance(80))
                .force("charge", d3.forceManyBody().strength(d => d.nodeType === 'file' ? -300 : -100))
                .force("center", d3.forceCenter(width / 2, height / 2))
                .force("collision", d3.forceCollide().radius(20));

            const link = g.append("g")
                .selectAll("line")
                .data(data.links)
                .enter().append("line")
                .attr("class", "link");

            const node = g.append("g")
                .selectAll("g")
                .data(data.nodes)
                .enter().append("g")
                .attr("class", "node")
                .call(d3.drag()
                    .on("start", dragstarted)
                    .on("drag", dragged)
                    .on("end", dragended));

            // Different shapes for file vs entity
            node.each(function(d) {{
                const el = d3.select(this);
                if (d.nodeType === 'file') {{
                    el.append("rect")
                        .attr("width", 24)
                        .attr("height", 18)
                        .attr("x", -12)
                        .attr("y", -9)
                        .attr("rx", 3)
                        .attr("fill", colorScheme.file);
                }} else {{
                    el.append("circle")
                        .attr("r", d => Math.sqrt(d.mention_count || 1) * 6 + 4)
                        .attr("fill", d => colorScheme[d.type] || "#888");
                }}
            }});

            node.on("mouseover", function(event, d) {{
                tooltip.transition().duration(200).style("opacity", 1);
                if (d.nodeType === 'file') {{
                    tooltip.html(`<strong>${{d.name}}</strong><br/>Type: File`)
                        .style("left", (event.pageX + 10) + "px")
                        .style("top", (event.pageY - 28) + "px");
                }} else {{
                    tooltip.html(`<strong>${{d.name}}</strong><br/>Type: ${{d.type}}`)
                        .style("left", (event.pageX + 10) + "px")
                        .style("top", (event.pageY - 28) + "px");
                }}
            }})
            .on("mouseout", function() {{
                tooltip.transition().duration(500).style("opacity", 0);
            }});

            node.append("text")
                .attr("dx", d => d.nodeType === 'file' ? 16 : 12)
                .attr("dy", ".35em")
                .text(d => d.name.length > 12 ? d.name.substring(0, 12) + '...' : d.name);

            simulation.on("tick", () => {{
                link
                    .attr("x1", d => d.source.x)
                    .attr("y1", d => d.source.y)
                    .attr("x2", d => d.target.x)
                    .attr("y2", d => d.target.y);
                node.attr("transform", d => `translate(${{d.x}},${{d.y}})`);
            }});

            function dragstarted(event, d) {{
                if (!event.active) simulation.alphaTarget(0.3).restart();
                d.fx = d.x;
                d.fy = d.y;
            }}

            function dragged(event, d) {{
                d.fx = event.x;
                d.fy = event.y;
            }}

            function dragended(event, d) {{
                if (!event.active) simulation.alphaTarget(0);
                d.fx = null;
                d.fy = null;
            }}
        </script>
    </body>
    </html>
    """

    components.html(d3_html, height=650, scrolling=False)
