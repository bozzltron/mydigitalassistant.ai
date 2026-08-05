"""Migration script to upgrade from JSON embeddings to sqlite-vec vectors.

Run this once to migrate existing assistant.db to use proper vector storage.
"""

import asyncio
import json
import sys
from pathlib import Path

import aiosqlite


async def migrate_embeddings(db_path: str) -> int:
    """Migrate frame_embeddings from JSON to vec_f32 format.
    
    Returns number of frames migrated.
    """
    db = await aiosqlite.connect(db_path)
    await db.enable_load_extension(True)
    
    try:
        import sqlite_vec
        await db.load_extension(sqlite_vec.loadable_path())
        print("✓ sqlite-vec extension loaded")
    except Exception as e:
        print(f"✗ Failed to load sqlite-vec: {e}")
        await db.close()
        return 0
    
    # Get all existing embeddings
    async with db.execute(
        "SELECT frame_id, embedding FROM frame_embeddings"
    ) as cursor:
        rows = await cursor.fetchall()
    
    migrated = 0
    for frame_id, embedding_json in rows:
        try:
            embedding = json.loads(embedding_json)
            await db.execute(
                "UPDATE frame_embeddings SET embedding = vec_f32(?) WHERE frame_id = ?",
                (json.dumps(embedding), frame_id),
            )
            migrated += 1
        except (json.JSONDecodeError, TypeError) as e:
            print(f"  ⚠ Skipped frame {frame_id}: {e}")
    
    await db.commit()
    await db.close()
    
    return migrated


def main():
    db_path = Path(__file__).parent.parent / "assistant.db"
    if not db_path.exists():
        print(f"✗ Database not found at {db_path}")
        sys.exit(1)
    
    print(f"⚡ Migrating {db_path} to sqlite-vec...")
    migrated = asyncio.run(migrate_embeddings(str(db_path)))
    print(f"✓ Migrated {migrated} frame embeddings to vec_f32")


if __name__ == "__main__":
    main()
