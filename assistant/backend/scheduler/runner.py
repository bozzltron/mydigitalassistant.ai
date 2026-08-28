"""Daily-list task runner — background loop inside the backend.

The agent wakes once a day at the configured tick (DAILY_TASKS_TIME) and runs
every enabled "daily" task; "once" tasks run at the next tick and disable
themselves. Housekeeping (heartbeat every 30 min, memory GC weekly, memory
consolidation twice daily) is plain timer logic here — not tasks, no LLM calls
beyond embeddings for consolidation.

User tasks execute through the same Orchestrator instance as chat so results
become memory normally. Started via `start_scheduler(store, orchestrator)`
from the FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from assistant.backend.config import settings
from assistant.backend.db.sqlcipher import aiosqlite_connect
from assistant.backend.memory.gc import run_gc
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

SHUTDOWN = False

POLL_SECONDS = 20
HEARTBEAT_INTERVAL_S = 30 * 60
GC_INTERVAL_S = 7 * 24 * 60 * 60
CONSOLIDATION_BACKUPS_TO_KEEP = 5


def _signal_handler(signum, frame):
    global SHUTDOWN
    logger.info("Shutdown signal received, finishing current task...")
    SHUTDOWN = True


async def _execute_task(
    store: MemoryStore,
    orchestrator: Orchestrator,
    task_frame_id: int,
    task_name: str,
    task_prompt: str,
    owner_user_id: int,
) -> tuple[bool, str]:
    """Run a single scheduled task through the orchestrator.

    Calls run_scheduled_task (full cognitive loop), then:
    - Records last_run / next_run in slots.
    - Creates a daily_run_YYYY_MM_DD event frame.
    - Creates associations: task --ran_in--> daily_run, task --produced_output--> episode.
    """
    now_str = datetime.now(UTC).isoformat()
    date_str = datetime.now(UTC).strftime("%Y_%m_%d")
    session_id = f"scheduled-{task_name}-{date_str}"

    try:
        result = await orchestrator.run_scheduled_task(
            prompt=task_prompt,
            user_id=owner_user_id,
            task_name=task_name,
        )

        full = result[:2000] if result else ""

        await store.update_scheduled_task_run(
            frame_id=task_frame_id,
            last_run=now_str,
            last_result_summary=full,
        )

        episode = await store.get_last_assistant_episode(owner_user_id, session_id)

        daily_run_frame_id = await store.get_or_create_daily_run_frame(
            date_str, owner_user_id=owner_user_id
        )

        await store.associate_frames(
            task_frame_id, daily_run_frame_id, "ran_in", confidence=0.9
        )

        if episode:
            await store.upsert_scheduled_task_slot(
                task_frame_id, "last_output_episode_id", str(episode.id)
            )
        await store.associate_frames(
            daily_run_frame_id, task_frame_id, "includes_task", confidence=0.9
        )

        await store.update_daily_run_frame(daily_run_frame_id, [task_name], "completed")

        logger.info("Task '%s' completed successfully", task_name)
        return True, full

    except Exception as exc:
        logger.exception("Task '%s' failed: %s", task_name, exc)
        await store.update_scheduled_task_run(
            frame_id=task_frame_id,
            last_run=now_str,
            last_result_summary=f"failed: {exc}",
        )
        return False, f"failed: {exc}"


async def _run_heartbeat(store: MemoryStore) -> None:
    """Update heartbeat slot so health checks and users know scheduler is alive."""
    try:
        await store.upsert_scheduler_heartbeat(
            timestamp=datetime.now(UTC).isoformat()
        )
        logger.debug("Heartbeat updated")
    except Exception as exc:
        logger.warning("Heartbeat update failed: %s", exc)


async def _run_memory_gc(store: MemoryStore) -> None:
    """Run memory garbage collection: slot decay + stale-frame soft-delete."""
    try:
        report = await run_gc(store.db_path)
        logger.info(
            "Memory GC completed: %d slots decayed, %d soft-deleted, "
            "%d frames soft-deleted",
            report.decayed,
            report.soft_deleted,
            report.frames_soft_deleted,
        )
    except Exception as exc:
        logger.warning("Memory GC failed: %s", exc)


def _is_new_week(last: datetime | None, now: datetime) -> bool:
    """ISO-week comparison so GC runs once per calendar week."""
    if last is None:
        return True
    local_last = last.astimezone()
    local_now = now.astimezone()
    return local_last.isocalendar()[:2] != local_now.isocalendar()[:2]


async def _backup_db(db_path: str, label: str) -> Path:
    """Snapshot the brain before a mutation pass; keep a bounded ring.

    The DB runs in WAL mode, so a raw file copy can be stale or torn.
    Checkpoint the WAL (TRUNCATE) first so the main DB file is complete
    and self-consistent at copy time.
    """
    import uuid

    src = Path(db_path)
    async with aiosqlite_connect(db_path) as db:
        cursor = await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        row = await cursor.fetchone()
        # row = (busy, wal_pages, checkpointed_pages). busy != 0 means readers
        # (e.g. an in-flight chat turn) pinned the WAL — the copy below would
        # silently miss recent commits, so refuse rather than fake a snapshot.
        if row and row[0] != 0:
            raise RuntimeError(
                f"wal_checkpoint(TRUNCATE) busy ({row[0]}) — retry next cycle"
            )
    backup_dir = src.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    dst = backup_dir / f"{src.stem}-pre-{label}-{stamp}-{uuid.uuid4().hex[:6]}{src.suffix}"
    # Prune before writing so the backup we're about to create can never be
    # the one deleted (also sidesteps mtime-tie ordering on fast writes).
    ring = sorted(
        backup_dir.glob(f"{src.stem}-pre-{label}-*{src.suffix}"),
        key=lambda p: p.stat().st_mtime,
    )
    for old in ring[: max(0, len(ring) - CONSOLIDATION_BACKUPS_TO_KEEP + 1)]:
        old.unlink(missing_ok=True)

    shutil.copy2(src, dst)
    return dst


async def _run_consolidation(
    store: MemoryStore,
    orchestrator: Orchestrator,
) -> None:
    """Twice-daily dreaming: backup -> strengthen + merge with circuit breaker."""
    from assistant.backend.memory.consolidate import run_consolidation

    try:
        backup = await _backup_db(store.db_path, "consolidation")
        logger.info("Consolidation backup written: %s", backup.name)
    except Exception as exc:
        logger.warning("Consolidation aborted (backup failed): %s", exc)
        return

    # Episode top-up is independent of frame merging — run it even when the
    # consolidation breaker trips below.
    try:
        done = await store.embed_missing_episodes(
            orchestrator.embed_fn(),
            embedding_model=settings.embedding_model,
            cap=100,
        )
        if done:
            logger.info("Episode embedding top-up: %d turns indexed", done)
    except Exception as exc:
        logger.warning("Episode embedding top-up failed: %s", exc)

    try:
        # Plan first: embeddings may be filled during clustering either way.
        plan = await run_consolidation(
            store.db_path, dry_run=True, embed_fn=orchestrator.embed_fn()
        )
        max_merges = settings.consolidation_max_merges_per_run
        if plan.capped or len(plan.planned_merges) > max_merges:
            logger.warning(
                "Consolidation skipped (%d merges > cap %d): %s",
                len(plan.planned_merges),
                max_merges,
                plan.summary(),
            )
            return

        report = await run_consolidation(
            store.db_path,
            dry_run=False,
            embed_fn=orchestrator.embed_fn(),
            max_merges=max_merges,
        )
        logger.info("Memory consolidation completed: %s", report.summary())
    except Exception as exc:
        logger.warning("Memory consolidation failed: %s", exc)


async def _scheduler_loop(store: MemoryStore, orchestrator: Orchestrator) -> None:
    """Main scheduler loop: poll for due tasks + housekeeping timers."""
    logger.info("Scheduler loop started (daily tick at %s %s)",
                settings.daily_tasks_time, settings.daily_tasks_tz or "local")

    await _run_heartbeat(store)
    await _run_memory_gc(store)
    last_hb = datetime.now(UTC)
    last_gc = last_hb
    consolidation_interval_s = max(0, settings.consolidation_interval_hours) * 3600
    last_consolidation: datetime | None = None

    while not SHUTDOWN:
        try:
            now = datetime.now(UTC)

            if (now - last_hb).total_seconds() >= HEARTBEAT_INTERVAL_S:
                await _run_heartbeat(store)
                last_hb = now
            if _is_new_week(last_gc, now):
                await _run_memory_gc(store)
                last_gc = now
            if (
                consolidation_interval_s
                and (
                    last_consolidation is None
                    or (now - last_consolidation).total_seconds()
                    >= consolidation_interval_s
                )
            ):
                await _run_consolidation(store, orchestrator)
                last_consolidation = datetime.now(UTC)

            due = await store.get_due_scheduled_tasks()
            if due:
                logger.info("Daily list firing: %d task(s)", len(due))
                for task in due:
                    if SHUTDOWN:
                        break
                    # Catch-up guard: skip anything wildly overdue that was
                    # already handled (e.g. clock jump), fire the rest.
                    await _execute_task(
                        store=store,
                        orchestrator=orchestrator,
                        task_frame_id=task["id"],
                        task_name=task["name"],
                        task_prompt=task["prompt"],
                        owner_user_id=task["owner_user_id"] or 1,
                    )

            await asyncio.sleep(POLL_SECONDS)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Scheduler loop error: %s", exc)
            await asyncio.sleep(30)


async def start_scheduler(store: MemoryStore, orchestrator: Orchestrator) -> None:
    """Start the scheduler loop. Called from FastAPI lifespan as a background task.

    Signal handlers are set in the lifespan (main thread), not here.
    """
    try:
        await _scheduler_loop(store, orchestrator)
    finally:
        logger.info("Scheduler loop stopped")
