import logging

from assistant.backend.db.sqlcipher import patch_sqlite_for_sqlcipher

patch_sqlite_for_sqlcipher()

import aiosqlite

logger = logging.getLogger(__name__)


async def _load_sqlite_vec(db):
    """Load the sqlite-vec extension on a connection.

    Uses sqlite_vec.loadable_path() which works across platforms and
    handles ABI compatibility internally.
    """
    try:
        import sqlite_vec

        await db.enable_load_extension(True)
        await db.load_extension(sqlite_vec.loadable_path())
        logger.debug("sqlite-vec extension loaded")
    except ImportError:
        logger.warning("sqlite-vec package not installed — vector search unavailable")
    except Exception as exc:
        logger.warning("sqlite-vec extension not loaded: %s — vector search unavailable", exc)

SCHEMA_SQL = """
-- Users (household members)
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Frames (entities, concepts, events)
CREATE TABLE IF NOT EXISTS frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    essential INTEGER NOT NULL DEFAULT 0,
    priority REAL NOT NULL DEFAULT 0.5,
    owner_user_id INTEGER,
    source_type TEXT,
    source_url TEXT,
    source_reliability REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL
);

-- Slots (key/value pairs on a frame)
CREATE TABLE IF NOT EXISTS slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    frame_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    essential INTEGER NOT NULL DEFAULT 0,
    priority REAL NOT NULL DEFAULT 0.5,
    source_type TEXT,
    source_url TEXT,
    source_reliability REAL,
    source_episode_id INTEGER,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(frame_id, key),
    FOREIGN KEY (frame_id) REFERENCES frames(id) ON DELETE CASCADE
);

-- Slot history (audit trail for error correction)
CREATE TABLE IF NOT EXISTS slot_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id INTEGER NOT NULL,
    frame_id INTEGER NOT NULL,
    slot_key TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    reason TEXT NOT NULL,
    source_episode_id INTEGER,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (slot_id) REFERENCES slots(id) ON DELETE CASCADE,
    FOREIGN KEY (frame_id) REFERENCES frames(id) ON DELETE CASCADE
);

-- Associations (typed relations between frames — the graph)
CREATE TABLE IF NOT EXISTS associations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_frame_id INTEGER NOT NULL,
    to_frame_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    essential INTEGER NOT NULL DEFAULT 0,
    priority REAL NOT NULL DEFAULT 0.5,
    source_type TEXT,
    source_url TEXT,
    source_reliability REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(from_frame_id, to_frame_id, relation_type),
    FOREIGN KEY (from_frame_id) REFERENCES frames(id) ON DELETE CASCADE,
    FOREIGN KEY (to_frame_id) REFERENCES frames(id) ON DELETE CASCADE
);

-- Episodes (conversation turns, per-user)
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    frame_ids TEXT NOT NULL DEFAULT '[]',
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Conflicts (log of all conflicts, resolved or pending)
CREATE TABLE IF NOT EXISTS conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    frame_id INTEGER NOT NULL,
    slot_key TEXT NOT NULL,
    existing_value TEXT,
    new_value TEXT,
    resolved_value TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT,
    FOREIGN KEY (frame_id) REFERENCES frames(id) ON DELETE CASCADE
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_slots_frame ON slots(frame_id);
CREATE INDEX IF NOT EXISTS idx_associations_from ON associations(from_frame_id);
CREATE INDEX IF NOT EXISTS idx_associations_to ON associations(to_frame_id);
CREATE INDEX IF NOT EXISTS idx_episodes_user ON episodes(user_id);
CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id);
CREATE INDEX IF NOT EXISTS idx_slot_history_slot ON slot_history(slot_id);
CREATE INDEX IF NOT EXISTS idx_conflicts_frame ON conflicts(frame_id);
CREATE INDEX IF NOT EXISTS idx_conflicts_status ON conflicts(status);
CREATE INDEX IF NOT EXISTS idx_frames_owner ON frames(owner_user_id);

-- Frame embeddings (via nomic-embed-text, stored as sqlite-vec vectors)
CREATE TABLE IF NOT EXISTS frame_embeddings (
    frame_id INTEGER PRIMARY KEY,
    embedding vec_f32 NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (frame_id) REFERENCES frames(id) ON DELETE CASCADE
);

-- Index for efficient vector search
CREATE INDEX IF NOT EXISTS idx_frame_embeddings_embedding ON frame_embeddings(embedding);
CREATE INDEX IF NOT EXISTS idx_frame_embeddings_frame ON frame_embeddings(frame_id);
"""


async def init_db(db_path: str) -> None:
    """Open connection, apply schema, enable foreign keys + WAL, load sqlite-vec."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("PRAGMA journal_mode = WAL")

        await _load_sqlite_vec(db)

        await db.executescript(SCHEMA_SQL)
        await db.commit()
