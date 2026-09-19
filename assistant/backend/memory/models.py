from pydantic import BaseModel, Field


class Frame(BaseModel):
    id: int | None = None
    name: str
    type: str
    confidence: float = 0.5
    essential: int = 0
    priority: float = 0.5
    owner_user_id: int | None = None
    source_type: str | None = None
    source_url: str | None = None
    source_reliability: float | None = None
    embedding_model: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    deleted_at: str | None = None  # set = GC-tombstoned; excluded from retrieval


class Slot(BaseModel):
    id: int | None = None
    frame_id: int
    key: str
    value: str
    confidence: float = 0.5
    essential: int = 0
    priority: float = 0.5
    source_type: str | None = None
    source_url: str | None = None
    source_reliability: float | None = None
    source_episode_id: int | None = None
    updated_at: str | None = None
    last_strengthened_at: str | None = None


class Association(BaseModel):
    id: int | None = None
    from_frame_id: int
    to_frame_id: int
    relation_type: str
    confidence: float = 0.5
    essential: int = 0
    priority: float = 0.5
    source_type: str | None = None
    source_url: str | None = None
    source_reliability: float | None = None
    embedding_model: str | None = None
    created_at: str | None = None


class Episode(BaseModel):
    id: int | None = None
    user_id: int
    session_id: str
    role: str
    content: str
    frame_ids: list[int] = Field(default_factory=list)
    timestamp: str | None = None


class Conflict(BaseModel):
    id: int | None = None
    frame_id: int
    slot_key: str
    existing_value: str | None
    new_value: str | None
    resolved_value: str | None
    status: str = "pending"
    created_at: str | None = None
    resolved_at: str | None = None


class User(BaseModel):
    id: int | None = None
    name: str
    created_at: str | None = None


class Feedback(BaseModel):
    id: int | None = None
    episode_id: str | None = None
    message_id: str
    kind: str
    comment: str | None = None
    created_at: str | None = None


class Alert(BaseModel):
    id: int | None = None
    user_id: int
    type: str  # "learning", "task_result", "conflict", "correction", "search_result"
    title: str
    message: str
    source_frame_id: int | None = None
    source_episode_id: int | None = None
    severity: str = "info"  # "info", "warning", "important"
    is_read: bool = False
    created_at: str | None = None
    read_at: str | None = None
