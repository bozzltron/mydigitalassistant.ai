"""Metadata table helpers for tracking schema version and embedding model."""


async def get_metadata(db_path: str, key: str) -> str | None:
    """Get a metadata value by key. Returns None if not found."""
    from assistant.backend.db.sqlcipher import open_checked_db

    async with open_checked_db(db_path) as db:
        rows = await db.execute_fetchall(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        )
        return rows[0][0] if rows else None


async def set_metadata(db_path: str, key: str, value: str) -> None:
    """Set a metadata value (upsert)."""
    from assistant.backend.db.sqlcipher import open_checked_db

    async with open_checked_db(db_path) as db:
        await db.execute(
            "INSERT INTO metadata (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


METADATA_KEY_EMBEDDING_MODEL = "embedding_model"
METADATA_KEY_EMBEDDING_DIMENSION = "embedding_dimension"
METADATA_KEY_SCHEMA_VERSION = "schema_version"
CURRENT_SCHEMA_VERSION = "1"
