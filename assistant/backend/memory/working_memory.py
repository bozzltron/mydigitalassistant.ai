"""Working memory module — tracks recently accessed frames for retrieval bias.

Working memory is a bounded cache of frame_ids that were recently retrieved.
It serves two purposes:
1. **Retrieval bias**: frames in working memory are boosted in the scoring step
   so recently-accessed facts surface first.
2. **LRU eviction**: when the cache is full, least-recently-used entries
   are evicted to keep the cache bounded.

Schema: working_memory(frame_id, access_count, entered_at, last_accessed_at)
"""

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from assistant.backend.db.sqlcipher import aiosqlite_connect

logger = logging.getLogger(__name__)

DEFAULT_MAX_SIZE = 50
DEFAULT_BOOST = 1.5  # relevance multiplier for working memory frames


@dataclass
class WorkingMemoryEntry:
    frame_id: int
    access_count: int
    entered_at: str
    last_accessed_at: str


class WorkingMemory:
    """Bounded LRU cache of recently accessed frame IDs."""

    def __init__(
        self,
        db_path: str,
        max_size: int = DEFAULT_MAX_SIZE,
        boost: float = DEFAULT_BOOST,
    ):
        self.db_path = db_path
        self.max_size = max_size
        self.boost = boost

    @asynccontextmanager
    async def _connect(self):
        db = await aiosqlite_connect(self.db_path)
        try:
            yield db
        finally:
            await db.close()

    async def touch_frame(self, frame_id: int) -> None:
        """Record a frame access: insert or update access_count + last_accessed_at."""
        async with self._connect() as db:
            existing = await db.execute_fetchall(
                "SELECT access_count FROM working_memory WHERE frame_id = ?",
                (frame_id,),
            )
            if existing:
                await db.execute(
                    "UPDATE working_memory "
                    "SET access_count = access_count + 1, "
                    "last_accessed_at = datetime('now') "
                    "WHERE frame_id = ?",
                    (frame_id,),
                )
            else:
                await db.execute(
                    "INSERT INTO working_memory (frame_id) VALUES (?)",
                    (frame_id,),
                )
            await db.commit()

        await self._evict_if_needed()

    async def touch_frames(self, frame_ids: list[int]) -> None:
        """Batch version of touch_frame — updates all frame_ids."""
        if not frame_ids:
            return
        async with self._connect() as db:
            for fid in frame_ids:
                existing = await db.execute_fetchall(
                    "SELECT access_count FROM working_memory WHERE frame_id = ?",
                    (fid,),
                )
                if existing:
                    await db.execute(
                        "UPDATE working_memory "
                        "SET access_count = access_count + 1, "
                        "last_accessed_at = datetime('now') "
                        "WHERE frame_id = ?",
                        (fid,),
                    )
                else:
                    await db.execute(
                        "INSERT INTO working_memory (frame_id) VALUES (?)",
                        (fid,),
                    )
            await db.commit()

        await self._evict_if_needed()

    async def is_in_working_memory(self, frame_id: int) -> bool:
        """Return True if the frame is currently in working memory."""
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT 1 FROM working_memory WHERE frame_id = ?",
                (frame_id,),
            )
            return len(row) > 0

    async def get_boost_map(self) -> dict[int, float]:
        """Return a dict of frame_id -> boost multiplier for all working memory frames.

        All entries get the same boost factor (DEFAULT_BOOST). Future work could
        scale boost by access_count or recency.
        """
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT frame_id FROM working_memory",
            )
            return {row[0]: self.boost for row in rows}

    async def get_entries(self) -> list[WorkingMemoryEntry]:
        """Return all working memory entries, most-recently-accessed first."""
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT frame_id, access_count, entered_at, last_accessed_at "
                "FROM working_memory "
                "ORDER BY last_accessed_at DESC",
            )
            return [
                WorkingMemoryEntry(
                    frame_id=r[0],
                    access_count=r[1],
                    entered_at=r[2],
                    last_accessed_at=r[3],
                )
                for r in rows
            ]

    async def get_frame_ids(self) -> set[int]:
        """Return the set of frame_ids currently in working memory."""
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT frame_id FROM working_memory",
            )
            return {row[0] for row in rows}

    async def size(self) -> int:
        """Return current number of entries in working memory."""
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT COUNT(*) FROM working_memory",
            )
            return row[0][0] if row else 0

    async def _evict_if_needed(self) -> None:
        """Remove oldest entries when cache exceeds max_size."""
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT COUNT(*) FROM working_memory",
            )
            count = row[0][0] if row else 0
            if count <= self.max_size:
                return

            excess = count - self.max_size
            await db.execute(
                f"""
                DELETE FROM working_memory
                WHERE frame_id IN (
                    SELECT frame_id FROM working_memory
                    ORDER BY last_accessed_at ASC
                    LIMIT {excess}
                )
                """
            )
            await db.commit()
            logger.debug("Evicted %d entries from working memory", excess)

    async def clear(self) -> None:
        """Clear all working memory entries."""
        async with self._connect() as db:
            await db.execute("DELETE FROM working_memory")
            await db.commit()
