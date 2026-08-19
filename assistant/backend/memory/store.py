import json
from contextlib import asynccontextmanager

from assistant.backend.db.sqlcipher import apply_db_key, patch_sqlite_for_sqlcipher

patch_sqlite_for_sqlcipher()

import aiosqlite

from assistant.backend.db.schema import _load_sqlite_vec
from assistant.backend.memory.confidence import (
    ConflictResolution,
    bump_confidence,
    default_source_reliability,
    forget_priority,
    initial_confidence,
    resolve_conflict,
)
from assistant.backend.memory.models import (
    Association,
    Conflict,
    Episode,
    Frame,
    Slot,
    User,
)


class MemoryStore:
    def __init__(self, db_path: str):
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        """Open a DB connection with sqlite-vec extension loaded and SQLCipher key set."""
        db = await aiosqlite.connect(self.db_path)
        apply_db_key(db)
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
                "created_at, updated_at FROM frames WHERE id = ?",
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
                "created_at, updated_at FROM frames WHERE name = ?",
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
                    "created_at, updated_at FROM frames "
                    "WHERE type = ? AND (owner_user_id = ? OR owner_user_id IS NULL) "
                    "ORDER BY id",
                    (type, owner_user_id),
                )
            elif type:
                rows = await db.execute_fetchall(
                    "SELECT id, name, type, confidence, essential, priority, "
                    "owner_user_id, source_type, source_url, source_reliability, "
                    "created_at, updated_at FROM frames WHERE type = ? ORDER BY id",
                    (type,),
                )
            elif owner_user_id is not None:
                rows = await db.execute_fetchall(
                    "SELECT id, name, type, confidence, essential, priority, "
                    "owner_user_id, source_type, source_url, source_reliability, "
                    "created_at, updated_at FROM frames "
                    "WHERE owner_user_id = ? OR owner_user_id IS NULL ORDER BY id",
                    (owner_user_id,),
                )
            else:
                rows = await db.execute_fetchall(
                    "SELECT id, name, type, confidence, essential, priority, "
                    "owner_user_id, source_type, source_url, source_reliability, "
                    "created_at, updated_at FROM frames ORDER BY id"
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
    async def store_frame_embedding(self, frame_id: int, embedding: list[float]) -> None:
        """Store embedding as sqlite-vec vector."""
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO frame_embeddings (frame_id, embedding, updated_at)
                VALUES (?, vec_f32(?), datetime('now'))
                ON CONFLICT(frame_id) DO UPDATE SET
                    embedding = vec_f32(excluded.embedding),
                    updated_at = excluded.updated_at
                """,
                (frame_id, json.dumps(embedding)),
            )
            await db.commit()

    async def embed_frames(
        self,
        frame_ids: list[int],
        embed_fn,  # async callable: (text) -> list[float]
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
                await self.store_frame_embedding(frame_id, embedding)
            except Exception:
                continue

    @staticmethod
    def _frame_to_embed_text(frame: Frame, slots: list[Slot]) -> str:
        """Build embeddable text from a frame and its slots."""
        parts = [f"{frame.type}: {frame.name}"]
        for slot in slots:
            parts.append(f"  {slot.key} = {slot.value}")
        return "\n".join(parts)

    async def get_frame_embedding(self, frame_id: int) -> list[float] | None:
        """Retrieve embedding for a frame."""
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT vec_to_json(embedding) FROM frame_embeddings WHERE frame_id = ?",
                (frame_id,),
            )
            if not row:
                return None
            return json.loads(row[0][0])

    async def get_all_frame_embeddings(self) -> list[tuple[int, list[float]]]:
        """Get all (frame_id, embedding) pairs for similarity search."""
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT frame_id, vec_to_json(embedding) FROM frame_embeddings ORDER BY frame_id"
            )
            return [(frame_id, json.loads(embedding)) for frame_id, embedding in rows]

    async def search_similar_frames(
        self,
        embedding: list[float],
        user_id: int,
        limit: int = 10,
        min_distance: float = 0.7,
    ) -> list[tuple[Frame, list[Slot], float]]:
        """Search frames by vector similarity using sqlite-vec vec_distance_cosine.

        Returns list of (frame, slots, distance) tuples ordered by similarity.
        Distance is 0.0 to 1.0+; lower is more similar.
        Filters to frames owned by user_id or with no owner (shared household frame).
        """
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT f.id, f.name, f.type, f.confidence, f.essential, f.priority,
                       f.owner_user_id, f.source_type, f.source_url, f.source_reliability,
                       f.created_at, f.updated_at,
                       vec_distance_cosine(embedding, ?) as distance
                FROM frame_embeddings fe
                JOIN frames f ON fe.frame_id = f.id
                WHERE vec_distance_cosine(embedding, ?) <= ?
                  AND (f.owner_user_id = ? OR f.owner_user_id IS NULL)
                ORDER BY distance ASC
                LIMIT ?
                """,
                (
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
                )
                slots = await self.get_slots_for_frame(frame.id)
                results.append((frame, slots, 1.0 - row[12]))

            return results

    async def clear_frame_embedding(self, frame_id: int) -> None:
        """Remove embedding for a frame."""
        async with self._connect() as db:
            await db.execute(
                "DELETE FROM frame_embeddings WHERE frame_id = ?",
                (frame_id,),
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
                    "source_type, source_url, source_reliability, source_episode_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                slot = await self._get_slot_row(db, cursor.lastrowid)
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
                    "SET confidence = ?, essential = ?, updated_at = datetime('now') "
                    "WHERE id = ?",
                    (new_conf, existing_essential, slot_id),
                )
                await db.commit()
                slot = await self._get_slot_row(db, slot_id)
                return slot, None

            decision = resolve_conflict(
                existing_value,
                value,
                existing_confidence,
                new_confidence=initial_confidence(),
                existing_source_reliability=existing_rel,
                new_source_reliability=source_reliability,
                existing_priority=existing_pri,
                new_priority=priority,
            )
            conflict: Conflict | None = None

            if decision.resolution == ConflictResolution.NEW_WINS:
                await db.execute(
                    "UPDATE slots "
                    "SET value = ?, confidence = ?, essential = ?, source_episode_id = ?, "
                    "source_type = ?, source_url = ?, source_reliability = ?, "
                    "updated_at = datetime('now') WHERE id = ?",
                    (value, initial_confidence(), existing_essential, source_episode_id,
                     source_type, source_url, source_reliability, slot_id),
                )
                await db.execute(
                    """
                    INSERT INTO slot_history (
                        slot_id, frame_id, slot_key, old_value, new_value, reason, source_episode_id
                    )
                    VALUES (?, ?, ?, ?, ?, 'conflict_resolved', ?)
                    """,
                    (slot_id, frame_id, key, existing_value, value, source_episode_id),
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

            slot = await self._get_slot_row(db, slot_id)
            return slot, conflict

    async def get_slot(self, frame_id: int, key: str) -> Slot | None:
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT id, frame_id, key, value, confidence, essential, priority, "
                "source_type, source_url, source_reliability, source_episode_id, "
                "updated_at FROM slots WHERE frame_id = ? AND key = ?",
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
                "updated_at FROM slots WHERE id = ?",
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
                "updated_at FROM slots WHERE frame_id = ? ORDER BY id",
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
                "created_at FROM associations "
                "WHERE to_frame_id = ? ORDER BY id",
                (frame_id,),
            )
            return [Association(**self._association_dict(row)) for row in rows]

    async def get_associations_from(self, frame_id: int) -> list[Association]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, from_frame_id, to_frame_id, relation_type, confidence, "
                "essential, priority, source_type, source_url, source_reliability, "
                "created_at FROM associations WHERE from_frame_id = ? ORDER BY id",
                (frame_id,),
            )
            return [Association(**self._association_dict(row)) for row in rows]

    async def get_all_associations_for_frame(self, frame_id: int) -> list[Association]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT id, from_frame_id, to_frame_id, relation_type, confidence,
                       essential, priority, source_type, source_url, source_reliability,
                       created_at
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
                          created_at
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
                "updated_at = datetime('now') WHERE id = ?",
                (value, initial_confidence(), slot_id),
            )
            await db.execute(
                """
                INSERT INTO slot_history (
                    slot_id, frame_id, slot_key, old_value, new_value, reason, source_episode_id
                )
                VALUES (?, ?, ?, ?, ?, 'manual_override', NULL)
                """,
                (slot_id, frame_id, slot_key, old_value, value),
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

    # Helpers
    async def _get_frame_row(self, db: aiosqlite.Connection, frame_id: int) -> Frame:
        row = await db.execute_fetchall(
            "SELECT id, name, type, confidence, essential, priority, "
            "owner_user_id, source_type, source_url, source_reliability, "
            "created_at, updated_at FROM frames WHERE id = ?",
            (frame_id,),
        )
        if not row:
            raise ValueError(f"Frame {frame_id} not found")
        return Frame(**self._frame_dict(row[0]))

    async def _get_slot_row(self, db: aiosqlite.Connection, slot_id: int) -> Slot:
        row = await db.execute_fetchall(
            "SELECT id, frame_id, key, value, confidence, essential, priority, "
            "source_type, source_url, source_reliability, source_episode_id, "
            "updated_at FROM slots WHERE id = ?",
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
            "created_at FROM associations WHERE id = ?",
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
            "created_at": row[10],
            "updated_at": row[11],
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
            "created_at": row[10],
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
