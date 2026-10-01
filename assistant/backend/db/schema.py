import logging

from assistant.backend.db.sqlcipher import aiosqlite_connect

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
    embedding_model TEXT,
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
    last_strengthened_at TEXT,
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
    embedding_model TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(from_frame_id, to_frame_id, relation_type),
    FOREIGN KEY (from_frame_id) REFERENCES frames(id) ON DELETE CASCADE,
    FOREIGN KEY (to_frame_id) REFERENCES frames(id) ON DELETE CASCADE
);

-- Sessions (conversation sessions per user)
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    title TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    deleted_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
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
    -- The provenance each side carried at decision time.
    --
    -- These exist because the conflict_ladder_value experiment could not answer
    -- whether the confidence ladder was doing anything: slot_history records the
    -- old and new *values* for every conflict but no reliability, so the inputs to
    -- a past decision were unreconstructable for 100% of the sample. Without them
    -- the brain cannot account for its own belief changes after the fact.
    existing_source_reliability REAL,
    new_source_reliability REAL,
    existing_confidence REAL,
    new_confidence REAL,
    existing_priority REAL,
    new_priority REAL,
    FOREIGN KEY (frame_id) REFERENCES frames(id) ON DELETE CASCADE
);

-- Feedback (user reactions to assistant responses)
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id TEXT,
    message_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    comment TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_slots_frame ON slots(frame_id);
CREATE INDEX IF NOT EXISTS idx_associations_from ON associations(from_frame_id);
CREATE INDEX IF NOT EXISTS idx_associations_to ON associations(to_frame_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_episodes_user ON episodes(user_id);
CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id);
CREATE INDEX IF NOT EXISTS idx_slot_history_slot ON slot_history(slot_id);
CREATE INDEX IF NOT EXISTS idx_conflicts_frame ON conflicts(frame_id);
CREATE INDEX IF NOT EXISTS idx_conflicts_status ON conflicts(status);
CREATE INDEX IF NOT EXISTS idx_frames_owner ON frames(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_feedback_episode ON feedback(episode_id);
CREATE INDEX IF NOT EXISTS idx_feedback_message ON feedback(message_id);

-- Frame embeddings (via nomic-embed-text, stored as sqlite-vec vectors)
-- embedding_model is part of the PK to support model migration:
-- multiple embeddings per frame (one per model) are retained during re-embed.
CREATE TABLE IF NOT EXISTS frame_embeddings (
    frame_id INTEGER NOT NULL,
    embedding_model TEXT NOT NULL,
    -- A frame can carry several vectors: chunk 0 is the frame's own name, and
    -- slot-rich frames get one vector per slot. A single vector per frame
    -- averages every slot into one point in space, which measurably buries the
    -- frame -- the live `why_not` frame (24 slots) ranked 453rd of 500 against
    -- its own name, while a 1-slot frame named why_not_page ranked 1st.
    chunk_index INTEGER NOT NULL DEFAULT 0,
    embedding vec_f32 NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (frame_id, embedding_model, chunk_index),
    FOREIGN KEY (frame_id) REFERENCES frames(id) ON DELETE CASCADE
);

-- Index for efficient vector search
CREATE INDEX IF NOT EXISTS idx_frame_embeddings_embedding ON frame_embeddings(embedding);

-- Episode embeddings: semantic recall over raw conversation turns ("what did
-- we say about X last month?"). Mirrors frame_embeddings; embedding_model is
-- part of the PK to support model migration.
CREATE TABLE IF NOT EXISTS episode_embeddings (
    episode_id INTEGER NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding vec_f32 NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (episode_id, embedding_model),
    FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_episode_embeddings_embedding ON episode_embeddings(embedding);

-- Frame aliases: canonical names recorded when consolidation merges a
-- duplicate frame. Extraction resolves through this map so a merged name
-- lands on the surviving frame instead of recreating the duplicate.
CREATE TABLE IF NOT EXISTS frame_aliases (
    alias_norm TEXT PRIMARY KEY,
    frame_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (frame_id) REFERENCES frames(id) ON DELETE CASCADE
);

-- Metadata table: key/value store for schema versioning and embedding model info
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Working memory: recently accessed frames for retrieval bias and LRU eviction
CREATE TABLE IF NOT EXISTS working_memory (
    frame_id INTEGER PRIMARY KEY,
    access_count INTEGER NOT NULL DEFAULT 1,
    entered_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_accessed_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (frame_id) REFERENCES frames(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_wm_last_accessed ON working_memory(last_accessed_at);
CREATE INDEX IF NOT EXISTS idx_wm_access_count ON working_memory(access_count);

-- An alert is a frame of type 'alert', not a table. See store.ALERT_FRAME_TYPE
-- and plans/2026-09-30-alerts-as-memory.md.
"""


async def _migrate_add_last_strengthened_at(db) -> None:
    """Add last_strengthened_at column to slots if it doesn't exist.

    Checks PRAGMA table_info to determine if the column already exists
    (present in databases created with the new schema that already has
    the column, as well as to make the migration idempotent for databases
    where the CREATE TABLE did not yet include the column).
    """
    rows = await db.execute_fetchall("PRAGMA table_info(slots)")
    existing_columns = {row[1] for row in rows}
    if "last_strengthened_at" not in existing_columns:
        await db.execute(
            "ALTER TABLE slots ADD COLUMN last_strengthened_at TEXT"
        )
        await db.commit()
        logger.debug("Migration: last_strengthened_at column added to slots")


async def _migrate_add_embedding_model_and_metadata(db) -> None:
    """Add embedding_model columns and metadata table.

    Adds embedding_model to frames, associations, and frame_embeddings.
    Creates the metadata table if it doesn't exist.
    """
    frames_info = await db.execute_fetchall("PRAGMA table_info(frames)")
    frame_cols = {r[1] for r in frames_info}
    if "embedding_model" not in frame_cols:
        await db.execute(
            "ALTER TABLE frames ADD COLUMN embedding_model TEXT"
        )
        logger.debug("Migration: embedding_model column added to frames")

    assoc_info = await db.execute_fetchall("PRAGMA table_info(associations)")
    assoc_cols = {r[1] for r in assoc_info}
    if "embedding_model" not in assoc_cols:
        await db.execute(
            "ALTER TABLE associations ADD COLUMN embedding_model TEXT"
        )
        logger.debug("Migration: embedding_model column added to associations")

    emb_info = await db.execute_fetchall("PRAGMA table_info(frame_embeddings)")
    emb_cols = {r[1] for r in emb_info}
    if "embedding_model" not in emb_cols:
        await db.execute(
            "ALTER TABLE frame_embeddings ADD COLUMN embedding_model TEXT"
            " DEFAULT 'nomic-embed-text'"
        )
        logger.debug("Migration: embedding_model column added to frame_embeddings")

    tables = await db.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='metadata'"
    )
    if not tables:
        await db.execute(
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        logger.debug("Migration: metadata table created")

    await db.commit()


async def _migrate_frame_embedding_chunks(db) -> None:
    """Let a frame hold more than one vector.

    `frame_embeddings` was keyed on (frame_id, embedding_model), so a frame had
    exactly one vector no matter how many slots it had, and that vector was the
    average of all of them. Splitting it needs the key to change, and SQLite
    cannot alter a primary key in place -- the table is rebuilt and the existing
    vectors are carried across as chunk 0, so this is a no-op for the data.
    """
    info = await db.execute_fetchall("PRAGMA table_info(frame_embeddings)")
    if any(r[1] == "chunk_index" for r in info):
        return
    pk = {(r[1], r[5]) for r in info if r[5]}  # (column, position-in-pk)
    if pk and len(pk) > 2:
        return  # already keyed by more than the two original columns

    logger.info(
        "Migration: rebuilding frame_embeddings to allow multiple vectors per frame"
    )
    await db.execute("PRAGMA foreign_keys=OFF")
    await db.execute(
        """
        CREATE TABLE frame_embeddings_chunked (
            frame_id INTEGER NOT NULL,
            embedding_model TEXT NOT NULL,
            chunk_index INTEGER NOT NULL DEFAULT 0,
            embedding vec_f32 NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (frame_id, embedding_model, chunk_index),
            FOREIGN KEY (frame_id) REFERENCES frames(id) ON DELETE CASCADE
        )
        """
    )
    await db.execute(
        """
        INSERT INTO frame_embeddings_chunked
            (frame_id, embedding_model, chunk_index, embedding, updated_at)
        SELECT frame_id, embedding_model, 0, embedding, updated_at
        FROM frame_embeddings
        """
    )
    await db.execute("DROP TABLE frame_embeddings")
    await db.execute(
        "ALTER TABLE frame_embeddings_chunked RENAME TO frame_embeddings"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_frame_embeddings_embedding "
        "ON frame_embeddings(embedding)"
    )
    await db.execute("PRAGMA foreign_keys=ON")
    await db.commit()
    logger.info("Migration: frame_embeddings now keyed by (frame_id, model, chunk)")


async def _migrate_add_feedback(db) -> None:
    """Add feedback table if it doesn't exist (for existing databases)."""
    tables = await db.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='feedback'"
    )
    if not tables:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id TEXT,
                message_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_episode ON feedback(episode_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_message ON feedback(message_id)"
        )
        await db.commit()
        logger.debug("Migration: feedback table created")


async def _migrate_add_deleted_at_and_last_accessed(db) -> None:
    """Add deleted_at to frames, last_accessed_at to slots, and scheduled task columns."""
    frames_info = await db.execute_fetchall("PRAGMA table_info(frames)")
    frame_cols = {r[1] for r in frames_info}

    new_frame_cols = {
        "deleted_at": "ALTER TABLE frames ADD COLUMN deleted_at TEXT",
        "description": "ALTER TABLE frames ADD COLUMN description TEXT",
        "schedule_cron": "ALTER TABLE frames ADD COLUMN schedule_cron TEXT",
        "prompt": "ALTER TABLE frames ADD COLUMN prompt TEXT",
        "enabled": "ALTER TABLE frames ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1",
        "last_run": "ALTER TABLE frames ADD COLUMN last_run TEXT",
        "next_run": "ALTER TABLE frames ADD COLUMN next_run TEXT",
        "last_result_summary": "ALTER TABLE frames ADD COLUMN last_result_summary TEXT",
    }
    for col, sql in new_frame_cols.items():
        if col not in frame_cols:
            await db.execute(sql)
    await db.commit()

    slots_info = await db.execute_fetchall("PRAGMA table_info(slots)")
    slot_cols = {r[1] for r in slots_info}
    if "last_accessed_at" not in slot_cols:
        await db.execute("ALTER TABLE slots ADD COLUMN last_accessed_at TEXT")
        await db.commit()
        logger.debug("Migration: last_accessed_at column added to slots")

    logger.debug("Migration: scheduled task columns added to frames")

    rows = await db.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_frames_next_run'"
    )
    if not rows:
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_frames_next_run ON frames(next_run)"
        )
        await db.commit()
        logger.debug("Migration: idx_frames_next_run index created")


async def _migrate_add_sessions_table(db) -> None:
    """Add sessions table if it doesn't exist (for existing databases)."""
    tables = await db.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
    )
    if not tables:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                deleted_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)"
        )
        await db.commit()
        logger.debug("Migration: sessions table created")


async def _migrate_add_deleted_at_to_sessions(db) -> None:
    """Add deleted_at column to sessions table for soft delete."""
    sessions_info = await db.execute_fetchall("PRAGMA table_info(sessions)")
    session_cols = {r[1] for r in sessions_info}
    if "deleted_at" not in session_cols:
        await db.execute("ALTER TABLE sessions ADD COLUMN deleted_at TEXT")
        await db.commit()
        logger.debug("Migration: deleted_at column added to sessions")


async def _migrate_add_reasoning_trace(db) -> None:
    """Add reasoning_trace column to episodes table."""
    episodes_info = await db.execute_fetchall("PRAGMA table_info(episodes)")
    episode_cols = {r[1] for r in episodes_info}
    if "reasoning_trace" not in episode_cols:
        await db.execute("ALTER TABLE episodes ADD COLUMN reasoning_trace TEXT")
        await db.commit()
        logger.debug("Migration: reasoning_trace column added to episodes")


async def _migrate_add_conflict_provenance(db) -> None:
    """Add the decision-input columns to `conflicts` if they are missing.

    The conflict_ladder_value experiment could not answer whether the confidence
    ladder was doing anything, because the inputs to a past decision are not
    retained: `slot_history` records old and new *values* for every conflict but no
    reliability, so the sample was 100% unreconstructable. Without these columns the
    brain cannot account for its own belief changes after the fact.

    Existing rows keep NULL — their provenance is genuinely gone and inventing it
    would be worse than admitting the gap.
    """
    cols = {r[1] for r in await db.execute_fetchall("PRAGMA table_info(conflicts)")}
    added = False
    for name, kind in (
        ("existing_source_reliability", "REAL"),
        ("new_source_reliability", "REAL"),
        ("existing_confidence", "REAL"),
        ("new_confidence", "REAL"),
        ("existing_priority", "REAL"),
        ("new_priority", "REAL"),
    ):
        if name not in cols:
            await db.execute(f"ALTER TABLE conflicts ADD COLUMN {name} {kind}")
            added = True
    if added:
        await db.commit()
        logger.debug("Migration: conflict provenance columns added")


async def _migrate_drop_alerts_table(db) -> None:
    """Drop the alerts table if a previous version created one.

    An alert is memory of a type, so the table was a second storage mechanism for
    something the frames model already expresses -- and a second place for the same
    thing to be wrong. It is dropped on boot rather than left in place, because
    `CREATE TABLE IF NOT EXISTS` meant any explicit drop in a migration script was
    silently undone on the next start.

    A pre-migration backup is the caller's responsibility; the rows worth keeping
    (`task_alert`, `correction`) are migrated to alert frames by
    `assistant/scripts/migrate_alerts_to_frames.py`.
    """
    tables = await db.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='alerts'"
    )
    if tables:
        await db.execute("DROP TABLE alerts")
        await db.execute("DROP INDEX IF EXISTS idx_alerts_user")
        await db.execute("DROP INDEX IF EXISTS idx_alerts_unread")
        await db.execute("DROP INDEX IF EXISTS idx_alerts_created")
        await db.commit()
        logger.info("Migration: alerts table dropped (alerts are frames of type 'alert')")


async def init_db(db_path: str) -> None:
    """Open connection, apply schema, enable foreign keys + WAL, load sqlite-vec.

    Uses the same connection type as MemoryStore: encrypted when DB_KEY is set,
    plain otherwise. This ensures the file format matches how the app will open
    it later.
    """
    async with aiosqlite_connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("PRAGMA journal_mode = WAL")

        await _load_sqlite_vec(db)

        await db.executescript(SCHEMA_SQL)
        await db.commit()

        await _migrate_add_last_strengthened_at(db)
        await _migrate_add_embedding_model_and_metadata(db)
        await _migrate_frame_embedding_chunks(db)
        await _migrate_add_feedback(db)
        await _migrate_add_deleted_at_and_last_accessed(db)
        await _migrate_add_sessions_table(db)
        await _migrate_add_deleted_at_to_sessions(db)
        await _migrate_add_reasoning_trace(db)
        await _migrate_add_conflict_provenance(db)
        await _migrate_drop_alerts_table(db)
