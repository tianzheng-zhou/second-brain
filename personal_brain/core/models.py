from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from enum import Enum
from datetime import datetime
import uuid

class FileType(str, Enum):
    IMAGE = "image"
    AUDIO = "audio"
    TEXT = "text"
    PDF = "pdf"
    UNKNOWN = "unknown"

class FileStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"

class EntityType(str, Enum):
    PERSON = "person"
    PROJECT = "project"
    LOCATION = "location"
    TECH = "tech"
    ORGANIZATION = "organization"
    CONCEPT = "concept"

class File(BaseModel):
    id: str = Field(..., description="Content hash SHA256 first 16 chars")
    path: str = Field(..., description="Storage path")
    filename: str
    type: FileType
    size_bytes: int
    created_at: datetime
    last_accessed: datetime
    ocr_text: Optional[str] = None
    trash_score: float = Field(0.0, ge=0.0, le=1.0)
    status: FileStatus = FileStatus.ACTIVE

class Embedding(BaseModel):
    file_id: str
    embedding: List[float] # 768 dimensions for nomic-embed-text

class Entity(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    type: EntityType
    first_seen: datetime
    mention_count: int = 1
    metadata: Dict[str, Any] = {}

class Relation(BaseModel):
    source: str
    target: str
    type: str
    file_id: str
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    created_at: datetime
