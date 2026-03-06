"""
models.py — Pydantic data models for inter-module data transfer and MCP serialization.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class FileInfo(BaseModel):
    id: str
    path: str
    processed_text_path: Optional[str] = None
    filename: str
    type: str  # image / audio / text / pdf / unknown
    size_bytes: int
    created_at: datetime
    last_accessed: Optional[datetime] = None
    status: str = "active"  # active / archived
    enrichment_status: str = "pending"  # pending / completed / failed


class FileChunk(BaseModel):
    id: str  # "{file_id}_{chunk_index}"
    file_id: str
    chunk_index: int
    content: str
    start_char: int = 0
    page_number: Optional[int] = None


class Entry(BaseModel):
    id: str  # UUID
    content_text: str
    metadata: Optional[dict[str, Any]] = None
    created_at: datetime
    source: str  # mcp / auto_enrichment / cli
    tags: list[str] = Field(default_factory=list)
    status: str = "active"  # active / archived


class SearchResult(BaseModel):
    score: float
    content: str
    source_type: str  # "chunk" / "entry"
    source_file_id: Optional[str] = None
    source_filename: Optional[str] = None
    chunk_index: Optional[int] = None
    page_number: Optional[int] = None
    entry_id: Optional[str] = None


class TaskInfo(BaseModel):
    id: str  # UUID
    type: str  # ingest / refresh_index
    status: str  # pending / running / completed / failed
    file_path: Optional[str] = None
    result_json: Optional[str] = None
    created_at: datetime
    updated_at: datetime
