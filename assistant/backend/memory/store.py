import json
import shutil
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from assistant.backend.db.schema import _load_sqlite_vec
from assistant.backend.db.sqlcipher import aiosqlite_connect
from assistant.backend.memory.belief_revision import OperationType, revise
from assistant.backend.memory.confidence import (
    ConflictResolution,
    bump_confidence,
    default_source_reliability,
    forget_priority,
    initial_confidence,
    lower_confidence,
)
from assistant.backend.memory.models import (
    Association,
    Conflict,
    Episode,
    Feedback,
    Frame,
    Slot,
    User,
)


class MemoryStore:
    def __init__(self, db_path: str):
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        """Open a DB connection with sqlite-vec extension loaded."""
        db = await aiosqlite_connect(self.db_path)
        await db.execute("PRAGMA foreign_keys = ON")
        await _load_sqlite_vec(db)
        try:
            yield db
        finally:
            await db.close()

    # Users
    async def create_user(self, name: str) -> User:
        async with self._connect() as db:
            cursor = await db.execute(
                "INSERT INTO users (name) VALUES (?)",
                (name,),
            )
            await db.commit()
            row = await db.execute_fetchall(
                "SELECT id, name, created_at FROM users WHERE id = ?",
                (cursor.lastrowid,),
            )
            id_, name_, created_at = row[0]
            return User(id=id_, name=name_, created_at=created_at)

    async def get_user(self, user_id: int) -> User | None:
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT id, name, created_at FROM users WHERE id = ?",
                (user_id,),
            )
            if not row:
                return None
            id_, name_, created_at = row[0]
            return User(id=id_, name=name_, created_at=created_at)

    async def get_user_by_name(self, name: str) -> User | None:
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT id, name, created_at FROM users WHERE name = ?",
                (name,),
            )
            if not row:
                return None
            id_, name_, created_at = row[0]
            return User(id=id_, name=name_, created_at=created_at)

    async def list_users(self) -> list[User]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, name, created_at FROM users ORDER BY id"
            )
            return [
                User(id=id_, name=name_, created_at=created_at)
                for id_, name_, created_at in rows
            ]

    # Frames
    async def create_frame(
        self,
        name: str,
        type: str,
        confidence: float = 0.5,
        essential: int = 0,
        priority: float = 0.5,
        owner_user_id: int | None = None,
        source_type: str | None = None,
        source_url: str | None = None,
        source_reliability: float | None = None,
    ) -> Frame:
        async with self._connect() as db:
            cursor = await db.execute(
                "INSERT INTO frames "
                "(name, type, confidence, essential, priority, owner_user_id, "
                "source_type, source_url, source_reliability) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    type,
                    confidence,
                    essential,
                    priority,
                    owner_user_id,
                    source_type,
                    source_url,
                    source_reliability,
                ),
            )
            await db.commit()
            return await self._get_frame_row(db, cursor.lastrowid)

    async def get_frame(self, frame_id: int) -> Frame | None:
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT id, name, type, confidence, essential, priority, "
                "owner_user_id, source_type, source_url, source_reliability, "
                "embedding_model, created_at, updated_at FROM frames WHERE id = ?",
                (frame_id,),
            )
            if not row:
                return None
            return Frame(**self._frame_dict(row[0]))

    async def get_frame_by_name(self, name: str) -> Frame | None:
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT id, name, type, confidence, essential, priority, "
                "owner_user_id, source_type, source_url, source_reliability, "
                "embedding_model, created_at, updated_at FROM frames WHERE name = ?",
                (name,),
            )
            if not row:
                return None
            return Frame(**self._frame_dict(row[0]))

    async def list_frames(
        self, type: str | None = None, owner_user_id: int | None = None
    ) -> list[Frame]:
        async with self._connect() as db:
            if type and owner_user_id is not None:
                rows = await db.execute_fetchall(
                    "SELECT id, name, type, confidence, essential, priority, "
                    "owner_user_id, source_type, source_url, source_reliability, "
                    "embedding_model, created_at, updated_at FROM frames "
                    "WHERE type = ? AND (owner_user_id = ? OR owner_user_id IS NULL) "
                    "ORDER BY id",
                    (type, owner_user_id),
                )
            elif type:
                rows = await db.execute_fetchall(
                    "SELECT id, name, type, confidence, essential, priority, "
                    "owner_user_id, source_type, source_url, source_reliability, "
                    "embedding_model, created_at, updated_at "
                    "FROM frames WHERE type = ? ORDER BY id",
                    (type,),
                )
            elif owner_user_id is not None:
                rows = await db.execute_fetchall(
                    "SELECT id, name, type, confidence, essential, priority, "
                    "owner_user_id, source_type, source_url, source_reliability, "
                    "embedding_model, created_at, updated_at FROM frames "
                    "WHERE owner_user_id = ? OR owner_user_id IS NULL ORDER BY id",
                    (owner_user_id,),
                )
            else:
                rows = await db.execute_fetchall(
                    "SELECT id, name, type, confidence, essential, priority, "
                    "owner_user_id, source_type, source_url, source_reliability, "
                    "embedding_model, created_at, updated_at FROM frames ORDER BY id"
                )
            return [Frame(**self._frame_dict(row)) for row in rows]
    async def update_frame(self, frame_id: int, **kwargs) -> Frame:
        allowed = {
            "name",
            "type",
            "confidence",
            "essential",
            "priority",
            "owner_user_id",
            "source_type",
            "source_url",
            "source_reliability",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            raise ValueError("No valid fields to update")
        async with self._connect() as db:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [frame_id]
            await db.execute(
                f"UPDATE frames SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
                values,
            )
            await db.commit()
            return await self._get_frame_row(db, frame_id)

    async def merge_frames(self, primary_id: int, secondary_id: int) -> Frame:
        async with self._connect() as db:
            # Move slots from secondary to primary, logging merges to history.
            secondary_slots = await db.execute_fetchall(
                "SELECT id, key, value, confidence, source_episode_id "
                "FROM slots WHERE frame_id = ?",
                (secondary_id,),
            )
            for slot_id, key, value, conf, src in secondary_slots:
                # Log merge before moving slot.
                await db.execute(
                    """
                    INSERT INTO slot_history (
                        slot_id, frame_id, slot_key, old_value, new_value, reason, source_episode_id
                    )
                    VALUES (?, ?, ?, ?, ?, 'merge', ?)
                    """,
                    (slot_id, primary_id, key, value, value, src),
                )
                # Upsert into primary using update to avoid unique conflict.
                existing = await db.execute_fetchall(
                    "SELECT id, value FROM slots WHERE frame_id = ? AND key = ?",
                    (primary_id, key),
                )
                if existing:
                    pri_slot_id, _ = existing[0]
                    await db.execute(
                        "UPDATE slots SET value = ?, confidence = ?, "
                        "source_episode_id = ?, updated_at = datetime('now') "
                        "WHERE id = ?",
                        (value, conf, src, pri_slot_id),
                    )
                    await db.execute(
                        "DELETE FROM slots WHERE id = ?",
                        (slot_id,),
                    )
                else:
                    await db.execute(
                        "UPDATE slots SET frame_id = ? WHERE id = ?",
                        (primary_id, slot_id),
                    )

            # Move associations from secondary to primary.
            await db.execute(
                "UPDATE associations SET from_frame_id = ? "
                "WHERE from_frame_id = ? AND to_frame_id != ?",
                (primary_id, secondary_id, primary_id),
            )
            await db.execute(
                "UPDATE associations SET to_frame_id = ? "
                "WHERE to_frame_id = ? AND from_frame_id != ?",
                (primary_id, secondary_id, primary_id),
            )
            # Delete any now self-referential or duplicate associations.
            await db.execute(
                "DELETE FROM associations "
                "WHERE from_frame_id = to_frame_id "
                "AND (from_frame_id = ? OR from_frame_id = ?)",
                (primary_id, secondary_id),
            )
            await db.execute(
                "DELETE FROM frames WHERE id = ?",
                (secondary_id,),
            )
            await db.commit()
            return await self._get_frame_row(db, primary_id)

    async def set_frame_priority(
        self, frame_id: int, priority: float
    ) -> Frame:
        """Set frame priority. Use bump_priority() or max_priority() from confidence module."""
        async with self._connect() as db:
            await db.execute(
                "UPDATE frames SET priority = ?, updated_at = datetime('now') "
                "WHERE id = ? AND essential = 0",
                (priority, frame_id),
            )
            await db.commit()
            return await self._get_frame_row(db, frame_id)

    async def forget_frame(self, frame_id: int) -> Frame:
        """Soft-delete a frame by setting priority to 0. Does not affect essential frames."""
        return await self.set_frame_priority(frame_id, forget_priority())

    async def set_slot_priority(self, slot_id: int, priority: float) -> Slot:
        """Set slot priority."""
        async with self._connect() as db:
            await db.execute(
                "UPDATE slots SET priority = ?, updated_at = datetime('now') "
                "WHERE id = ? AND essential = 0",
                (priority, slot_id),
            )
            await db.commit()
            return await self._get_slot_row(db, slot_id)

    async def forget_slot(self, slot_id: int) -> Slot:
        """Soft-delete a slot by setting priority to 0. Does not affect essential slots."""
        return await self.set_slot_priority(slot_id, forget_priority())

    # Embeddings
    async def store_frame_embedding(
        self,
        frame_id: int,
        embedding: list[float],
        embedding_model: str = "nomic-embed-text",
    ) -> None:
        """Store embedding as sqlite-vec vector for a specific embedding model."""
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO frame_embeddings (frame_id, embedding_model, embedding, updated_at)
                VALUES (?, ?, vec_f32(?), datetime('now'))
                ON CONFLICT(frame_id, embedding_model) DO UPDATE SET
                    embedding = vec_f32(excluded.embedding),
                    updated_at = excluded.updated_at
                """,
                (frame_id, embedding_model, json.dumps(embedding)),
            )
            await db.commit()

    async def embed_frames(
        self,
        frame_ids: list[int],
        embed_fn,  # async callable: (text) -> list[float]
        embedding_model: str = "nomic-embed-text",
    ) -> None:
        """Generate and store embeddings for a list of frames. Skips frames that fail."""
        for frame_id in frame_ids:
            try:
                frame = await self.get_frame(frame_id)
                if not frame:
                    continue
                slots = await self.get_slots_for_frame(frame_id)
                text = self._frame_to_embed_text(frame, slots)
                embedding = await embed_fn(text)
                await self.store_frame_embedding(frame_id, embedding, embedding_model)
            except Exception:
                continue

    @staticmethod
    def _frame_to_embed_text(frame: Frame, slots: list[Slot]) -> str:
        """Build embeddable text from a frame and its slots."""
        parts = [f"{frame.type}: {frame.name}"]
        for slot in slots:
            parts.append(f"  {slot.key} = {slot.value}")
        return "\n".join(parts)

    async def get_frame_embedding(
        self, frame_id: int, embedding_model: str = "nomic-embed-text"
    ) -> list[float] | None:
        """Retrieve embedding for a frame and embedding model."""
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT vec_to_json(embedding) FROM frame_embeddings "
                "WHERE frame_id = ? AND embedding_model = ?",
                (frame_id, embedding_model),
            )
            if not row:
                return None
            return json.loads(row[0][0])

    async def get_all_frame_embeddings(
        self, embedding_model: str = "nomic-embed-text"
    ) -> list[tuple[int, list[float]]]:
        """Get all (frame_id, embedding) pairs for a specific embedding model."""
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT frame_id, vec_to_json(embedding) FROM frame_embeddings "
                "WHERE embedding_model = ? ORDER BY frame_id",
                (embedding_model,),
            )
            return [(frame_id, json.loads(embedding)) for frame_id, embedding in rows]

    async def search_similar_frames(
        self,
        embedding: list[float],
        user_id: int,
        embedding_model: str = "nomic-embed-text",
        limit: int = 10,
        min_distance: float = 0.7,
    ) -> list[tuple[Frame, list[Slot], float]]:
        """Search frames by vector similarity using sqlite-vec vec_distance_cosine.

        Returns list of (frame, slots, distance) tuples ordered by similarity.
        Distance is 0.0 to 1.0+; lower is more similar.
        Filters to frames owned by user_id or with no owner (shared household frame).
        Uses the specified embedding_model for the search.
        """
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                """
                -- MATERIALIZED barrier: filter by embedding_model BEFORE any
                -- vec_distance_cosine() call. Mixed-dimension rows from other
                -- models would otherwise crash distance computation depending
                -- on row visitation order.
                WITH candidate AS MATERIALIZED (
                    SELECT frame_id, embedding
                    FROM frame_embeddings
                    WHERE embedding_model = ?
                )
                SELECT f.id, f.name, f.type, f.confidence, f.essential, f.priority,
                       f.owner_user_id, f.source_type, f.source_url, f.source_reliability,
                       f.created_at, f.updated_at, f.embedding_model,
                       vec_distance_cosine(candidate.embedding, ?) as distance
                FROM candidate
                JOIN frames f ON candidate.frame_id = f.id
                WHERE vec_distance_cosine(candidate.embedding, ?) <= ?
                  AND (f.owner_user_id = ? OR f.owner_user_id IS NULL)
                ORDER BY distance ASC
                LIMIT ?
                """,
                (
                    embedding_model,
                    json.dumps(embedding),
                    json.dumps(embedding),
                    min_distance,
                    user_id,
                    limit,
                ),
            )

            results: list[tuple[Frame, list[Slot], float]] = []
            for row in rows:
                frame = Frame(
                    id=row[0],
                    name=row[1],
                    type=row[2],
                    confidence=row[3],
                    essential=row[4],
                    priority=row[5],
                    owner_user_id=row[6],
                    source_type=row[7],
                    source_url=row[8],
                    source_reliability=row[9],
                    created_at=row[10],
                    updated_at=row[11],
                    embedding_model=row[12],
                )
                slots = await self.get_slots_for_frame(frame.id)
                results.append((frame, slots, 1.0 - row[13]))

            return results

    async def clear_frame_embedding(
        self, frame_id: int, embedding_model: str = "nomic-embed-text"
    ) -> None:
        """Remove embedding for a frame and embedding model."""
        async with self._connect() as db:
            await db.execute(
                "DELETE FROM frame_embeddings WHERE frame_id = ? AND embedding_model = ?",
                (frame_id, embedding_model),
            )
            await db.commit()

    # Slots
    async def upsert_slot(
        self,
        frame_id: int,
        key: str,
        value: str,
        source_episode_id: int | None = None,
        essential: int = 0,
        priority: float = 0.5,
        source_type: str | None = None,
        source_url: str | None = None,
        source_reliability: float | None = None,
    ) -> tuple[Slot, Conflict | None]:
        async with self._connect() as db:
            existing = await db.execute_fetchall(
                "SELECT id, value, confidence, essential, source_reliability, priority FROM slots "
                "WHERE frame_id = ? AND key = ?",
                (frame_id, key),
            )
            if not existing:
                eff_rel = (
                    source_reliability
                    if source_reliability is not None
                    else default_source_reliability(source_type)
                )
                cursor = await db.execute(
                    "INSERT INTO slots "
                    "(frame_id, key, value, confidence, essential, priority, "
                    "source_type, source_url, source_reliability, source_episode_id, "
                    "last_strengthened_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                    (
                        frame_id,
                        key,
                        value,
                        initial_confidence(),
                        essential,
                        priority,
                        source_type,
                        source_url,
                        eff_rel,
                        source_episode_id,
                    ),
                )
                await db.commit()
                slot_id = cursor.lastrowid
                await db.execute(
                    """
                    INSERT INTO slot_history (
                        slot_id, frame_id, slot_key, old_value, new_value, reason, source_episode_id
                    )
                    VALUES (?, ?, ?, NULL, ?, ?, ?)
                    """,
                    (slot_id, frame_id, key, value, OperationType.INITIAL.value, source_episode_id),
                )
                await db.commit()
                slot = await self._get_slot_row(db, slot_id)
                return slot, None

            (
                slot_id,
                existing_value,
                existing_confidence,
                existing_essential,
                existing_rel,
                existing_pri,
            ) = existing[0]

            if existing_value == value:
                new_conf = bump_confidence(existing_confidence)
                await db.execute(
                    "UPDATE slots "
                    "SET confidence = ?, essential = ?, updated_at = datetime('now'), "
                    "last_strengthened_at = datetime('now') "
                    "WHERE id = ?",
                    (new_conf, existing_essential, slot_id),
                )
                await db.execute(
                    """
                    INSERT INTO slot_history (
                        slot_id, frame_id, slot_key, old_value, new_value, reason, source_episode_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        slot_id, frame_id, key, existing_value, value,
                        OperationType.EXPAND.value, source_episode_id,
                    ),
                )
                await db.commit()
                slot = await self._get_slot_row(db, slot_id)
                return slot, None

            existing_slot = {
                "value": existing_value,
                "confidence": existing_confidence,
                "source_reliability": existing_rel,
                "priority": existing_pri,
            }
            revision_result = revise(
                existing_slot=existing_slot,
                new_value=value,
                source_type=source_type,
                new_source_reliability=source_reliability,
                new_priority=priority,
            )

            conflict: Conflict | None = None

            if revision_result.operation == OperationType.REVISE:
                if revision_result.resolution == ConflictResolution.NEW_WINS:
                    await db.execute(
                        "UPDATE slots "
                        "SET value = ?, confidence = ?, essential = ?, source_episode_id = ?, "
                        "source_type = ?, source_url = ?, source_reliability = ?, "
                        "updated_at = datetime('now'), last_strengthened_at = datetime('now') "
                        "WHERE id = ?",
                        (value, initial_confidence(), existing_essential, source_episode_id,
                         source_type, source_url, source_reliability, slot_id),
                    )
                    await db.execute(
                        """
                        INSERT INTO slot_history (
                            slot_id, frame_id, slot_key, old_value, new_value,
                            reason, source_episode_id
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            slot_id, frame_id, key, existing_value, value,
                            OperationType.REVISE.value, source_episode_id,
                        ),
                    )
                    cursor = await db.execute(
                        """
                        INSERT INTO conflicts (
                            frame_id, slot_key, existing_value, new_value,
                            resolved_value, status, resolved_at
                        )
                        VALUES (?, ?, ?, ?, ?, 'auto_resolved', datetime('now'))
                        """,
                        (frame_id, key, existing_value, value, value),
                    )
                    await db.commit()
                    conflict = await self._get_conflict_row(db, cursor.lastrowid)
                else:
                    cursor = await db.execute(
                        """
                        INSERT INTO conflicts (
                            frame_id, slot_key, existing_value, new_value, status
                        )
                        VALUES (?, ?, ?, ?, 'pending')
                        """,
                        (frame_id, key, existing_value, value),
                    )
                    await db.commit()
                    conflict = await self._get_conflict_row(db, cursor.lastrowid)
                    await db.execute(
                        """
                        INSERT INTO slot_history (
                            slot_id, frame_id, slot_key, old_value, new_value,
                            reason, source_episode_id
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            slot_id, frame_id, key, existing_value, value,
                            OperationType.REVISE.value, source_episode_id,
                        ),
                    )
                    await db.commit()

            slot = await self._get_slot_row(db, slot_id)
            return slot, conflict

    async def get_slot(self, frame_id: int, key: str) -> Slot | None:
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT id, frame_id, key, value, confidence, essential, priority, "
                "source_type, source_url, source_reliability, source_episode_id, "
                "updated_at, last_strengthened_at FROM slots WHERE frame_id = ? AND key = ?",
                (frame_id, key),
            )
            if not row:
                return None
            return Slot(**self._slot_dict(row[0]))

    async def get_slot_by_id(self, slot_id: int) -> Slot | None:
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT id, frame_id, key, value, confidence, essential, priority, "
                "source_type, source_url, source_reliability, source_episode_id, "
                "updated_at, last_strengthened_at FROM slots WHERE id = ?",
                (slot_id,),
            )
            if not row:
                return None
            return Slot(**self._slot_dict(row[0]))

    async def get_slots_for_frame(self, frame_id: int) -> list[Slot]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, frame_id, key, value, confidence, essential, priority, "
                "source_type, source_url, source_reliability, source_episode_id, "
                "updated_at, last_strengthened_at FROM slots WHERE frame_id = ? ORDER BY id",
                (frame_id,),
            )
            return [Slot(**self._slot_dict(row)) for row in rows]

    async def get_slot_history(self, slot_id: int) -> list[dict]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, slot_id, frame_id, slot_key, old_value, new_value, "
                "reason, source_episode_id, timestamp "
                "FROM slot_history WHERE slot_id = ? ORDER BY id",
                (slot_id,),
            )
            return [
                {
                    "id": r[0],
                    "slot_id": r[1],
                    "frame_id": r[2],
                    "slot_key": r[3],
                    "old_value": r[4],
                    "new_value": r[5],
                    "reason": r[6],
                    "source_episode_id": r[7],
                    "timestamp": r[8],
                }
                for r in rows
            ]

    # Associations
    async def create_association(
        self,
        from_frame_id: int,
        to_frame_id: int,
        relation_type: str,
        confidence: float = 0.5,
        essential: int = 0,
        priority: float = 0.5,
        source_type: str | None = None,
        source_url: str | None = None,
        source_reliability: float | None = None,
    ) -> Association:
        async with self._connect() as db:
            cursor = await db.execute(
                "INSERT INTO associations "
                "(from_frame_id, to_frame_id, relation_type, confidence, essential, "
                "priority, source_type, source_url, source_reliability) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    from_frame_id,
                    to_frame_id,
                    relation_type,
                    confidence,
                    essential,
                    priority,
                    source_type,
                    source_url,
                    source_reliability,
                ),
            )
            await db.commit()
            return await self._get_association_row(db, cursor.lastrowid)

    async def get_associations_to(self, frame_id: int) -> list[Association]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, from_frame_id, to_frame_id, relation_type, confidence, "
                "essential, priority, source_type, source_url, source_reliability, "
                "embedding_model, created_at FROM associations "
                "WHERE to_frame_id = ? ORDER BY id",
                (frame_id,),
            )
            return [Association(**self._association_dict(row)) for row in rows]

    async def get_associations_from(self, frame_id: int) -> list[Association]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, from_frame_id, to_frame_id, relation_type, confidence, "
                "essential, priority, source_type, source_url, source_reliability, "
                "embedding_model, created_at FROM associations WHERE from_frame_id = ? ORDER BY id",
                (frame_id,),
            )
            return [Association(**self._association_dict(row)) for row in rows]

    async def get_all_associations_for_frame(self, frame_id: int) -> list[Association]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT id, from_frame_id, to_frame_id, relation_type, confidence,
                       essential, priority, source_type, source_url, source_reliability,
                       embedding_model, created_at
                FROM associations
                WHERE from_frame_id = ? OR to_frame_id = ?
                ORDER BY id
                """,
                (frame_id, frame_id),
            )
            return [Association(**self._association_dict(row)) for row in rows]

    async def get_all_associations(self) -> list[Association]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                """SELECT id, from_frame_id, to_frame_id, relation_type, confidence,
                          essential, priority, source_type, source_url, source_reliability,
                          embedding_model, created_at
                   FROM associations ORDER BY id"""
            )
            return [Association(**self._association_dict(row)) for row in rows]

    # Episodes
    async def create_episode(
        self,
        user_id: int,
        session_id: str,
        role: str,
        content: str,
        frame_ids: list[int] | None = None,
    ) -> Episode:
        frame_ids = frame_ids or []
        async with self._connect() as db:
            cursor = await db.execute(
                "INSERT INTO episodes (user_id, session_id, role, content, frame_ids) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, session_id, role, content, json.dumps(frame_ids)),
            )
            await db.commit()
            # Fetch the inserted row directly using the lastrowid
            row = await db.execute_fetchall(
                "SELECT id, user_id, session_id, role, content, frame_ids, timestamp "
                "FROM episodes WHERE id = ?",
                (cursor.lastrowid,),
            )
            if not row:
                raise ValueError("Failed to retrieve created episode")
            return Episode(**self._episode_dict(row[0]))

    async def get_episodes_for_user(self, user_id: int, limit: int = 50) -> list[Episode]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, user_id, session_id, role, content, frame_ids, timestamp "
                "FROM episodes WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            )
            return [Episode(**self._episode_dict(row)) for row in rows]

    async def get_episodes_for_session(self, session_id: str) -> list[Episode]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, user_id, session_id, role, content, frame_ids, timestamp "
                "FROM episodes WHERE session_id = ? ORDER BY id",
                (session_id,),
            )
            return [Episode(**self._episode_dict(row)) for row in rows]

    async def get_episodes_for_frame(self, frame_id: int, limit: int = 20) -> list[Episode]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, user_id, session_id, role, content, frame_ids, timestamp "
                "FROM episodes ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return [
                Episode(**self._episode_dict(row))
                for row in rows
                if frame_id in self._episode_dict(row)["frame_ids"]
            ]

    async def update_episode_frame_ids(self, episode_id: int, frame_ids: list[int]) -> None:
        """Update the frame_ids for an episode after extraction completes."""
        async with self._connect() as db:
            await db.execute(
                "UPDATE episodes SET frame_ids = ? WHERE id = ?",
                (json.dumps(frame_ids), episode_id),
            )
            await db.commit()

    # Conflicts
    async def get_conflicts(self, status: str | None = None) -> list[Conflict]:
        async with self._connect() as db:
            if status:
                rows = await db.execute_fetchall(
                    "SELECT id, frame_id, slot_key, existing_value, new_value, "
                    "resolved_value, status, created_at, resolved_at "
                    "FROM conflicts WHERE status = ? ORDER BY id",
                    (status,),
                )
            else:
                rows = await db.execute_fetchall(
                    "SELECT id, frame_id, slot_key, existing_value, new_value, "
                    "resolved_value, status, created_at, resolved_at "
                    "FROM conflicts ORDER BY id"
                )
            return [Conflict(**self._conflict_dict(row)) for row in rows]

    async def get_conflicts_for_frame(self, frame_id: int) -> list[Conflict]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, frame_id, slot_key, existing_value, new_value, "
                "resolved_value, status, created_at, resolved_at "
                "FROM conflicts WHERE frame_id = ? ORDER BY id",
                (frame_id,),
            )
            return [Conflict(**self._conflict_dict(row)) for row in rows]

    async def manual_override_conflict(self, conflict_id: int, value: str) -> Slot:
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT frame_id, slot_key, existing_value, new_value "
                "FROM conflicts WHERE id = ?",
                (conflict_id,),
            )
            if not row:
                raise ValueError(f"Conflict {conflict_id} not found")
            frame_id, slot_key, existing_value, new_value = row[0]

            existing_slot = await db.execute_fetchall(
                "SELECT id, value FROM slots WHERE frame_id = ? AND key = ?",
                (frame_id, slot_key),
            )
            if not existing_slot:
                raise ValueError(f"Slot not found for conflict {conflict_id}")
            slot_id, old_value = existing_slot[0]

            await db.execute(
                "UPDATE slots SET value = ?, confidence = ?, "
                "updated_at = datetime('now'), last_strengthened_at = datetime('now') WHERE id = ?",
                (value, initial_confidence(), slot_id),
            )
            await db.execute(
                """
                INSERT INTO slot_history (
                    slot_id, frame_id, slot_key, old_value, new_value, reason, source_episode_id
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    slot_id, frame_id, slot_key, old_value, value,
                    OperationType.MANUAL_OVERRIDE.value,
                ),
            )
            await db.execute(
                """
                UPDATE conflicts
                SET resolved_value = ?, status = 'manual_override', resolved_at = datetime('now')
                WHERE id = ?
                """,
                (value, conflict_id),
            )
            await db.commit()
            return await self._get_slot_row(db, slot_id)

    # Feedback
    async def create_feedback(
        self,
        episode_id: str | None,
        message_id: str,
        kind: str,
        comment: str | None = None,
    ) -> Feedback:
        async with self._connect() as db:
            cursor = await db.execute(
                "INSERT INTO feedback (episode_id, message_id, kind, comment) VALUES (?, ?, ?, ?)",
                (episode_id, message_id, kind, comment),
            )
            await db.commit()
            row = await db.execute_fetchall(
                "SELECT id, episode_id, message_id, kind, comment, created_at "
                "FROM feedback WHERE id = ?",
                (cursor.lastrowid,),
            )
            if not row:
                raise ValueError("Failed to retrieve created feedback")
            id_, ep_id, msg_id, k, c, created_at = row[0]
            return Feedback(
                id=id_,
                episode_id=ep_id,
                message_id=msg_id,
                kind=k,
                comment=c,
                created_at=created_at,
            )

    async def get_feedback_for_episode(self, episode_id: str) -> list[Feedback]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, episode_id, message_id, kind, comment, created_at "
                "FROM feedback WHERE episode_id = ? ORDER BY id",
                (episode_id,),
            )
            return [
                Feedback(
                    id=r[0], episode_id=r[1], message_id=r[2],
                    kind=r[3], comment=r[4], created_at=r[5]
                )
                for r in rows
            ]

    async def apply_positive_feedback(self, episode_id: str | None) -> int:
        """Boost confidence of frames/slots touched in an episode.

        Returns number of slots updated.
        """
        if not episode_id:
            return 0
        async with self._connect() as db:
            episode_rows = await db.execute_fetchall(
                "SELECT frame_ids FROM episodes WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                (episode_id,),
            )
            if not episode_rows:
                return 0
            frame_ids = json.loads(episode_rows[0][0] or "[]")
            if not frame_ids:
                return 0

            updated = 0
            for frame_id in frame_ids:
                slots = await db.execute_fetchall(
                    "SELECT id, confidence FROM slots WHERE frame_id = ?",
                    (frame_id,),
                )
                for slot_id, conf in slots:
                    new_conf = bump_confidence(conf)
                    await db.execute(
                        "UPDATE slots SET confidence = ?, updated_at = datetime('now'), "
                        "last_strengthened_at = datetime('now') WHERE id = ?",
                        (new_conf, slot_id),
                    )
                    updated += 1

                frame_rows = await db.execute_fetchall(
                    "SELECT id, confidence FROM frames WHERE id = ?",
                    (frame_id,),
                )
                for frame_id_row, conf in frame_rows:
                    new_conf = bump_confidence(conf)
                    await db.execute(
                        "UPDATE frames SET confidence = ?, "
                        "updated_at = datetime('now') WHERE id = ?",
                        (new_conf, frame_id_row),
                    )
            await db.commit()
            return updated

    async def apply_negative_feedback(self, episode_id: str | None) -> int:
        """Lower confidence of frames/slots touched in an episode.

        Returns number of slots updated.
        """
        if not episode_id:
            return 0
        async with self._connect() as db:
            episode_rows = await db.execute_fetchall(
                "SELECT frame_ids FROM episodes WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                (episode_id,),
            )
            if not episode_rows:
                return 0
            frame_ids = json.loads(episode_rows[0][0] or "[]")
            if not frame_ids:
                return 0

            updated = 0
            for frame_id in frame_ids:
                slots = await db.execute_fetchall(
                    "SELECT id, confidence FROM slots WHERE frame_id = ?",
                    (frame_id,),
                )
                for slot_id, conf in slots:
                    new_conf = lower_confidence(conf)
                    await db.execute(
                        "UPDATE slots SET confidence = ?, "
                        "updated_at = datetime('now') WHERE id = ?",
                        (new_conf, slot_id),
                    )
                    updated += 1

                frame_rows = await db.execute_fetchall(
                    "SELECT id, confidence FROM frames WHERE id = ?",
                    (frame_id,),
                )
                for frame_id_row, conf in frame_rows:
                    new_conf = lower_confidence(conf)
                    await db.execute(
                        "UPDATE frames SET confidence = ?, "
                        "updated_at = datetime('now') WHERE id = ?",
                        (new_conf, frame_id_row),
                    )
            await db.commit()
            return updated

    async def apply_correction_feedback(
        self,
        episode_id: str | None,
        comment: str,
    ) -> tuple[int, list[Conflict]]:
        """Treat a correction as a new fact with high reliability.

        DEPRECATED: Use the LLM-based /correction endpoint instead.
        This regex-based parser only handles 'key: value' patterns and
        creates correction_* frames — it does not update the correct frame
        or use belief revision. Prefer POST /correction which routes through
        extract_correction → validate_correction → apply_correction.

        Parses the correction text for 'key: value' patterns and upserts them.
        Returns (slots_updated, conflicts_created).
        """
        conflicts: list[Conflict] = []
        slots_updated = 0

        correction_text = comment.strip()
        if not correction_text:
            return 0, []

        import re
        pattern = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*[=:]\s*(.+)$', re.MULTILINE)
        matches = pattern.findall(correction_text)

        if not matches:
            return 0, []

        async with self._connect() as db:
            episode_rows = await db.execute_fetchall(
                "SELECT frame_ids FROM episodes WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                (episode_id,),
            )
            source_episode_id = None
            if episode_rows:
                frame_ids = json.loads(episode_rows[0][0] or "[]")
                if frame_ids:
                    source_episode_id = frame_ids[0]

            for key, value in matches:
                key = key.strip()
                value = value.strip()
                if not key or not value:
                    continue

                frame_name = f"correction_{key}"
                existing_frame = await db.execute_fetchall(
                    "SELECT id FROM frames WHERE name = ?", (frame_name,)
                )
                if existing_frame:
                    frame_id = existing_frame[0][0]
                else:
                    cursor = await db.execute(
                        "INSERT INTO frames (name, type, confidence, source_type) "
                        "VALUES (?, 'correction', ?, 'user_correction')",
                        (frame_name, initial_confidence()),
                    )
                    frame_id = cursor.lastrowid
                    await db.commit()

                slot_cursor = await db.execute(
                    "INSERT INTO slots "
                    "(frame_id, key, value, confidence, source_type, "
                    "source_reliability, source_episode_id, last_strengthened_at) "
                    "VALUES (?, ?, ?, ?, 'user_correction', 0.95, ?, datetime('now'))",
                    (frame_id, key, value, initial_confidence(), source_episode_id),
                )
                await db.execute(
                    "INSERT INTO slot_history "
                    "(slot_id, frame_id, slot_key, old_value, new_value, "
                    "reason, source_episode_id) "
                    "VALUES (?, ?, ?, NULL, ?, 'initial', ?)",
                    (slot_cursor.lastrowid, frame_id, key, value, source_episode_id),
                )
                await db.commit()
                slots_updated += 1

        return slots_updated, conflicts

    # Helpers
    async def _get_frame_row(self, db: aiosqlite.Connection, frame_id: int) -> Frame:
        row = await db.execute_fetchall(
            "SELECT id, name, type, confidence, essential, priority, "
            "owner_user_id, source_type, source_url, source_reliability, "
            "embedding_model, created_at, updated_at FROM frames WHERE id = ?",
            (frame_id,),
        )
        if not row:
            raise ValueError(f"Frame {frame_id} not found")
        return Frame(**self._frame_dict(row[0]))

    async def _get_slot_row(self, db: aiosqlite.Connection, slot_id: int) -> Slot:
        row = await db.execute_fetchall(
            "SELECT id, frame_id, key, value, confidence, essential, priority, "
            "source_type, source_url, source_reliability, source_episode_id, "
            "updated_at, last_strengthened_at FROM slots WHERE id = ?",
            (slot_id,),
        )
        if not row:
            raise ValueError(f"Slot {slot_id} not found")
        return Slot(**self._slot_dict(row[0]))

    async def _get_association_row(
        self, db: aiosqlite.Connection, association_id: int
    ) -> Association:
        row = await db.execute_fetchall(
            "SELECT id, from_frame_id, to_frame_id, relation_type, confidence, "
            "essential, priority, source_type, source_url, source_reliability, "
            "embedding_model, created_at FROM associations WHERE id = ?",
            (association_id,),
        )
        if not row:
            raise ValueError(f"Association {association_id} not found")
        return Association(**self._association_dict(row[0]))

    @staticmethod
    def _frame_dict(row: tuple) -> dict:
        return {
            "id": row[0],
            "name": row[1],
            "type": row[2],
            "confidence": row[3],
            "essential": row[4],
            "priority": row[5],
            "owner_user_id": row[6],
            "source_type": row[7],
            "source_url": row[8],
            "source_reliability": row[9],
            "embedding_model": row[10] if len(row) > 10 else None,
            "created_at": row[11] if len(row) > 11 else None,
            "updated_at": row[12] if len(row) > 12 else None,
        }

    @staticmethod
    def _slot_dict(row: tuple) -> dict:
        return {
            "id": row[0],
            "frame_id": row[1],
            "key": row[2],
            "value": row[3],
            "confidence": row[4],
            "essential": row[5],
            "priority": row[6],
            "source_type": row[7],
            "source_url": row[8],
            "source_reliability": row[9],
            "source_episode_id": row[10],
            "updated_at": row[11],
            "last_strengthened_at": row[12] if len(row) > 12 else None,
        }

    @staticmethod
    def _association_dict(row: tuple) -> dict:
        return {
            "id": row[0],
            "from_frame_id": row[1],
            "to_frame_id": row[2],
            "relation_type": row[3],
            "confidence": row[4],
            "essential": row[5],
            "priority": row[6],
            "source_type": row[7],
            "source_url": row[8],
            "source_reliability": row[9],
            "embedding_model": row[10] if len(row) > 10 else None,
            "created_at": row[11] if len(row) > 11 else None,
        }

    @staticmethod
    def _episode_dict(row: tuple) -> dict:
        return {
            "id": row[0],
            "user_id": row[1],
            "session_id": row[2],
            "role": row[3],
            "content": row[4],
            "frame_ids": json.loads(row[5]),
            "timestamp": row[6],
        }

    async def _get_conflict_row(self, db: aiosqlite.Connection, conflict_id: int) -> Conflict:
        row = await db.execute_fetchall(
            "SELECT id, frame_id, slot_key, existing_value, new_value, "
            "resolved_value, status, created_at, resolved_at "
            "FROM conflicts WHERE id = ?",
            (conflict_id,),
        )
        if not row:
            raise ValueError(f"Conflict {conflict_id} not found")
        return Conflict(**self._conflict_dict(row[0]))

    @staticmethod
    def _conflict_dict(row: tuple) -> dict:
        return {
            "id": row[0],
            "frame_id": row[1],
            "slot_key": row[2],
            "existing_value": row[3],
            "new_value": row[4],
            "resolved_value": row[5],
            "status": row[6],
            "created_at": row[7],
            "resolved_at": row[8],
        }

    async def export_brain(self) -> dict:
        """Export all memory to a portable JSON dict.

        Embeddings are NOT exported (they can be re-derived on import).
        Returns: {version, exported_at, frames, slots, associations, episodes, feedback}
        """
        frames = await self.list_frames()
        exported_frames = []

        for frame in frames:
            slots = await self.get_slots_for_frame(frame.id)
            exported_slots = []
            for slot in slots:
                exported_slots.append({
                    "frame_name": frame.name,
                    "key": slot.key,
                    "value": slot.value,
                    "confidence": slot.confidence,
                    "essential": slot.essential,
                    "priority": slot.priority,
                    "source_type": slot.source_type,
                    "source_url": slot.source_url,
                    "source_reliability": slot.source_reliability,
                    "last_strengthened_at": slot.last_strengthened_at,
                })
            exported_frames.append({
                "name": frame.name,
                "type": frame.type,
                "confidence": frame.confidence,
                "essential": frame.essential,
                "priority": frame.priority,
                "owner_user_id": frame.owner_user_id,
                "source_type": frame.source_type,
                "source_url": frame.source_url,
                "source_reliability": frame.source_reliability,
                "slots": exported_slots,
            })

        associations = await self.get_all_associations()
        exported_assocs = []
        for assoc in associations:
            from_frame = await self.get_frame(assoc.from_frame_id)
            to_frame = await self.get_frame(assoc.to_frame_id)
            if from_frame and to_frame:
                exported_assocs.append({
                    "from_frame_name": from_frame.name,
                    "to_frame_name": to_frame.name,
                    "relation_type": assoc.relation_type,
                    "confidence": assoc.confidence,
                    "essential": assoc.essential,
                    "priority": assoc.priority,
                    "source_type": assoc.source_type,
                    "source_url": assoc.source_url,
                    "source_reliability": assoc.source_reliability,
                })

        async with self._connect() as db:
            episode_rows = await db.execute_fetchall(
                "SELECT id, user_id, session_id, role, content, frame_ids, timestamp "
                "FROM episodes ORDER BY id"
            )
            episodes = []
            for row in episode_rows:
                episodes.append({
                    "id": row[0],
                    "user_id": row[1],
                    "session_id": row[2],
                    "role": row[3],
                    "content": row[4],
                    "frame_ids": json.loads(row[5]) if row[5] else [],
                    "timestamp": row[6],
                })

            feedback_rows = await db.execute_fetchall(
                "SELECT id, episode_id, message_id, kind, comment, created_at "
                "FROM feedback ORDER BY id"
            )
            feedbacks = []
            for row in feedback_rows:
                feedbacks.append({
                    "id": row[0],
                    "episode_id": row[1],
                    "message_id": row[2],
                    "kind": row[3],
                    "comment": row[4],
                    "created_at": row[5],
                })

            conflict_rows = await db.execute_fetchall(
                "SELECT id, frame_id, slot_key, existing_value, new_value, "
                "resolved_value, status, created_at, resolved_at FROM conflicts ORDER BY id"
            )
            conflicts = []
            for row in conflict_rows:
                frame = await self.get_frame(row[1])
                frame_name = frame.name if frame else str(row[1])
                conflicts.append({
                    "frame_name": frame_name,
                    "slot_key": row[2],
                    "existing_value": row[3],
                    "new_value": row[4],
                    "resolved_value": row[5],
                    "status": row[6],
                    "created_at": row[7],
                    "resolved_at": row[8],
                })

        return {
            "version": 1,
            "exported_at": datetime.now(UTC).isoformat(),
            "frames": exported_frames,
            "associations": exported_assocs,
            "episodes": episodes,
            "feedbacks": feedbacks,
            "conflicts": conflicts,
        }

    async def import_brain(
        self,
        data: dict,
        mode: str = "merge",
    ) -> dict:
        """Import memory from an exported brain dict.

        Modes:
        - merge: upsert frames by name, add slots (existing data preserved)
        - overwrite: delete all existing memory, then re-import (creates backup first)

        Returns counts of imported items.
        """
        if data.get("version") != 1:
            raise ValueError(f"Unsupported brain export version: {data.get('version')}")

        imported = {
            "frames": 0,
            "slots": 0,
            "associations": 0,
            "episodes": 0,
            "feedbacks": 0,
            "conflicts": 0,
        }

        if mode == "overwrite":
            backup_path = await self._create_backup()
            imported["backup_path"] = str(backup_path)

            async with self._connect() as db:
                await db.execute("DELETE FROM slot_history")
                await db.execute("DELETE FROM slots")
                await db.execute("DELETE FROM associations")
                await db.execute("DELETE FROM episodes")
                await db.execute("DELETE FROM conflicts")
                await db.execute("DELETE FROM feedback")
                await db.execute("DELETE FROM frames")
                await db.commit()

        # Import frames and slots
        name_to_frame_id: dict[str, int] = {}
        for frame_data in data.get("frames", []):
            existing = await self.get_frame_by_name(frame_data["name"])
            if existing:
                frame_id = existing.id
                await self.update_frame(frame_id, confidence=frame_data.get("confidence", 0.5))
            else:
                frame = await self.create_frame(
                    name=frame_data["name"],
                    type=frame_data.get("type", "entity"),
                    source_type=frame_data.get("source_type"),
                    source_url=frame_data.get("source_url"),
                )
                frame_id = frame.id
                imported["frames"] += 1
            name_to_frame_id[frame_data["name"]] = frame_id

            for slot_data in frame_data.get("slots", []):
                _, _ = await self.upsert_slot(
                    frame_id=frame_id,
                    key=slot_data["key"],
                    value=slot_data["value"],
                    source_type=slot_data.get("source_type"),
                    source_url=slot_data.get("source_url"),
                    source_reliability=slot_data.get("source_reliability"),
                )
                imported["slots"] += 1

        # Import associations
        for assoc_data in data.get("associations", []):
            from_id = name_to_frame_id.get(assoc_data.get("from_frame_name"))
            to_id = name_to_frame_id.get(assoc_data.get("to_frame_name"))
            if from_id and to_id:
                try:
                    await self.create_association(
                        from_frame_id=from_id,
                        to_frame_id=to_id,
                        relation_type=assoc_data.get("relation_type", "related_to"),
                        confidence=assoc_data.get("confidence", 0.5),
                        source_type=assoc_data.get("source_type"),
                        source_url=assoc_data.get("source_url"),
                        source_reliability=assoc_data.get("source_reliability"),
                    )
                    imported["associations"] += 1
                except Exception:
                    pass  # Skip duplicate associations

        # Import episodes
        for ep_data in data.get("episodes", []):
            async with self._connect() as db:
                await db.execute(
                    "INSERT INTO episodes "
                    "(user_id, session_id, role, content, frame_ids, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        ep_data["user_id"],
                        ep_data.get("session_id", ""),
                        ep_data["role"],
                        ep_data["content"],
                        json.dumps(ep_data.get("frame_ids", [])),
                        ep_data.get("timestamp"),
                    ),
                )
                await db.commit()
                imported["episodes"] += 1

        # Import feedback
        for fb_data in data.get("feedbacks", []):
            async with self._connect() as db:
                await db.execute(
                    "INSERT INTO feedback (episode_id, message_id, kind, comment, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        fb_data.get("episode_id"),
                        fb_data.get("message_id", ""),
                        fb_data.get("kind", "correction"),
                        fb_data.get("comment"),
                        fb_data.get("created_at"),
                    ),
                )
                await db.commit()
                imported["feedbacks"] += 1

        # Import conflicts
        for cf_data in data.get("conflicts", []):
            frame_id = name_to_frame_id.get(cf_data.get("frame_name"))
            if not frame_id:
                continue
            async with self._connect() as db:
                await db.execute(
                    "INSERT INTO conflicts "
                    "(frame_id, slot_key, existing_value, new_value, "
                    "resolved_value, status, created_at, resolved_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        frame_id,
                        cf_data.get("slot_key"),
                        cf_data.get("existing_value"),
                        cf_data.get("new_value"),
                        cf_data.get("resolved_value"),
                        cf_data.get("status", "imported"),
                        cf_data.get("created_at"),
                        cf_data.get("resolved_at"),
                    ),
                )
                await db.commit()
                imported["conflicts"] += 1

        return imported

    async def upsert_scheduled_task(
        self,
        name: str,
        description: str,
        schedule_cron: str,
        prompt: str,
        enabled: bool = True,
        owner_user_id: int | None = None,
        next_run: str | None = None,
    ) -> int:
        """Create or update a scheduled_task frame.

        `schedule_cron` holds the frequency tag "daily" or "once" (column name
        kept for schema compatibility). User tasks all fire at the shared daily
        tick; `next_run` defaults to that.

        Returns the frame id.
        """
        from ..scheduler.schedule import next_daily_run

        if schedule_cron not in ("daily", "once"):
            schedule_cron = "daily"
        if next_run is None:
            if enabled:
                next_run = next_daily_run().isoformat()

        async with self._connect() as db:
            existing = await db.execute_fetchall(
                "SELECT id FROM frames WHERE name = ? AND type = 'scheduled_task' "
                "AND deleted_at IS NULL",
                (name,),
            )
            if existing:
                frame_id = existing[0][0]
                await db.execute(
                    "UPDATE frames SET "
                    "description=?, schedule_cron=?, prompt=?, enabled=?, "
                    "next_run=?, updated_at=datetime('now') "
                    "WHERE id = ?",
                    (description, schedule_cron, prompt, 1 if enabled else 0,
                     next_run, frame_id),
                )
                await db.commit()
                return frame_id
            else:
                cursor = await db.execute(
                    "INSERT INTO frames "
                    "(name, type, confidence, essential, priority, owner_user_id, "
                    "source_type, description, schedule_cron, prompt, enabled, next_run) "
                    "VALUES (?, 'scheduled_task', 1.0, 1, 0.8, ?, 'user', ?, ?, ?, ?, ?)",
                    (name, owner_user_id, description, schedule_cron, prompt,
                     1 if enabled else 0, next_run),
                )
                await db.commit()
                return cursor.lastrowid

    async def get_scheduled_tasks(
        self,
        owner_user_id: int | None = None,
        include_system: bool = False,
    ) -> list[dict]:
        """List all scheduled tasks (optionally filtered by user)."""
        async with self._connect() as db:
            if owner_user_id is not None:
                rows = await db.execute_fetchall(
                    "SELECT id, name, description, schedule_cron, prompt, "
                    "enabled, last_run, next_run, last_result_summary, "
                    "owner_user_id, source_type, created_at, updated_at "
                    "FROM frames "
                    "WHERE type = 'scheduled_task' AND deleted_at IS NULL "
                    "AND owner_user_id = ? "
                    "ORDER BY created_at DESC",
                    (owner_user_id,),
                )
            elif include_system:
                rows = await db.execute_fetchall(
                    "SELECT id, name, description, schedule_cron, prompt, "
                    "enabled, last_run, next_run, last_result_summary, "
                    "owner_user_id, source_type, created_at, updated_at "
                    "FROM frames "
                    "WHERE type = 'scheduled_task' AND deleted_at IS NULL "
                    "ORDER BY created_at DESC",
                )
            else:
                rows = await db.execute_fetchall(
                    "SELECT id, name, description, schedule_cron, prompt, "
                    "enabled, last_run, next_run, last_result_summary, "
                    "owner_user_id, source_type, created_at, updated_at "
                    "FROM frames "
                    "WHERE type = 'scheduled_task' AND deleted_at IS NULL "
                    "AND (source_type != 'system' OR source_type IS NULL) "
                    "ORDER BY created_at DESC",
                )
            cols = [
                "id", "name", "description", "schedule_cron", "prompt",
                "enabled", "last_run", "next_run", "last_result_summary",
                "owner_user_id", "source_type", "created_at", "updated_at",
            ]
            return [dict(zip(cols, r, strict=True)) for r in rows]

    async def get_due_scheduled_tasks(self) -> list[dict]:
        """Get tasks that are enabled and whose next_run <= now (UTC)."""
        from datetime import datetime
        async with self._connect() as db:
            now = datetime.now(UTC).isoformat()
            rows = await db.execute_fetchall(
                "SELECT id, name, description, schedule_cron, prompt, "
                "enabled, last_run, next_run, last_result_summary, "
                "owner_user_id, source_type, created_at, updated_at "
                "FROM frames "
                "WHERE type = 'scheduled_task' AND deleted_at IS NULL "
                "AND enabled = 1 AND next_run IS NOT NULL AND next_run <= ?",
                (now,),
            )
            cols = [
                "id", "name", "description", "schedule_cron", "prompt",
                "enabled", "last_run", "next_run", "last_result_summary",
                "owner_user_id", "source_type", "created_at", "updated_at",
            ]
            return [dict(zip(cols, r, strict=True)) for r in rows]

    async def get_nearest_scheduled_task_run(self) -> datetime | None:
        """Get the nearest next_run datetime among all enabled tasks (UTC)."""
        from datetime import datetime
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT MIN(next_run) FROM frames "
                "WHERE type = 'scheduled_task' AND deleted_at IS NULL "
                "AND enabled = 1 AND next_run IS NOT NULL",
            )
            if row and row[0][0]:
                return datetime.fromisoformat(row[0][0].replace("Z", "+00:00"))
            return None

    async def update_scheduled_task_run(
        self,
        frame_id: int,
        last_run: str,
        last_result_summary: str,
    ) -> None:
        """Record a run: reschedule daily tasks, disable one-shot tasks."""
        from ..scheduler.schedule import next_daily_run

        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT schedule_cron FROM frames WHERE id = ?",
                (frame_id,),
            )
            if not row:
                return
            frequency = row[0][0]
            if frequency == "once":
                await db.execute(
                    "UPDATE frames SET "
                    "last_run=?, last_result_summary=?, enabled=0, next_run=NULL, "
                    "updated_at=datetime('now') "
                    "WHERE id = ?",
                    (last_run, last_result_summary[:2000], frame_id),
                )
            else:
                # "daily" (and any legacy value) repeats at the next daily tick.
                next_run = next_daily_run().isoformat()
                await db.execute(
                    "UPDATE frames SET "
                    "last_run=?, last_result_summary=?, next_run=?, "
                    "updated_at=datetime('now') "
                    "WHERE id = ?",
                    (last_run, last_result_summary[:2000], next_run, frame_id),
                )
            await db.commit()

    async def delete_scheduled_task(self, frame_id: int) -> None:
        """Soft-delete a scheduled task frame."""
        async with self._connect() as db:
            await db.execute(
                "UPDATE frames SET deleted_at=datetime('now') WHERE id = ?",
                (frame_id,),
            )
            await db.commit()

    async def upsert_scheduler_heartbeat(self, timestamp: str) -> None:
        """Update the scheduler heartbeat slot on the system frame."""
        async with self._connect() as db:
            now = datetime.now(UTC).isoformat()
            cursor = await db.execute(
                "INSERT OR REPLACE INTO frames "
                "(name, type, confidence, essential, priority, source_type, updated_at) "
                "VALUES ('scheduler_heartbeat', 'system', 1.0, 0, 0.0, 'system', ?)",
                (now,),
            )
            frame_id = cursor.lastrowid
            await db.execute(
                "INSERT OR REPLACE INTO slots "
                "(frame_id, key, value, updated_at) "
                "VALUES (?, 'last_heartbeat', ?, ?)",
                (frame_id, timestamp, now),
            )
            await db.commit()

    async def gc(self) -> int:
        """Garbage collect: soft-delete forgotten frames and decay stale slots.

        Removes frames that have:
          - priority < 0.2
          - not been accessed in working_memory for > 30 days

        Decays priority of slots with:
          - priority < 0.3
          - last_accessed_at > 60 days ago

        Returns the count of frames soft-deleted.
        """
        from datetime import datetime, timedelta

        async with self._connect() as db:
            cutoff = (
                datetime.now(UTC) - timedelta(days=30)
            ).isoformat()
            stale_cutoff = (
                datetime.now(UTC) - timedelta(days=60)
            ).isoformat()

            stale_frames = await db.execute_fetchall(
                "SELECT f.id FROM frames f "
                "LEFT JOIN working_memory wm ON f.id = wm.frame_id "
                "WHERE f.type != 'scheduled_task' AND f.deleted_at IS NULL "
                "AND f.priority < 0.2 "
                "AND (wm.last_accessed_at IS NULL OR wm.last_accessed_at < ?)",
                (cutoff,),
            )
            removed = 0
            for (fid,) in stale_frames:
                await db.execute(
                    "UPDATE frames SET deleted_at=datetime('now') WHERE id = ?",
                    (fid,),
                )
                removed += 1

            await db.execute(
                "UPDATE slots SET "
                "priority = MAX(priority * 0.9, 0.1), "
                "updated_at = datetime('now') "
                "WHERE frame_id IN ("
                "  SELECT id FROM frames WHERE priority < 0.3 AND deleted_at IS NULL"
                ") AND last_accessed_at < ?",
                (stale_cutoff,),
            )
            await db.commit()
            return removed

    async def _create_backup(self) -> Path:
        """Create a backup of the current DB before overwrite import."""

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        backup_name = f"brain-overwrite-{timestamp}.db"
        backup_path = Path(self.db_path).parent / backup_name
        shutil.copy2(self.db_path, str(backup_path))
        return backup_path
