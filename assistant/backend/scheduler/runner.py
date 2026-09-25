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
from assistant.backend.pipeline.orchestrator import SCHEDULED_TASK_ALERT_PREFIX, Orchestrator
from assistant.backend.scheduler.summarizer import Summarizer

logger = logging.getLogger(__name__)

SHUTDOWN = False

POLL_SECONDS = 20
HEARTBEAT_INTERVAL_S = 30 * 60
GC_INTERVAL_S = 7 * 24 * 60 * 60
CONSOLIDATION_BACKUPS_TO_KEEP = 5

# Summarization: runs at configured interval (default 24h)
SUMMARIZATION_INTERVAL_S = 0  # will be set from settings in _scheduler_loop


def _is_new_day(last: datetime | None, now: datetime) -> bool:
    """UTC date comparison so summarization runs once per calendar day."""
    if last is None:
        return True
    local_last = last.astimezone()
    local_now = now.astimezone()
    return local_last.date() != local_now.date()


async def _run_summarization(
    store: MemoryStore,
    orchestrator: Orchestrator,
) -> None:
    """Run conversation summarization for all users with eligible sessions."""
    if not settings.summarization_enabled:
        return

    try:
        Summarizer(
            store=store,
            llm_client=orchestrator.llm_client,
        )

        users = await store.list_users()
        for user in users:
            sessions = await store.get_sessions_for_user(user.id)
            summarized = 0
            for sess in sessions:
                if summarized >= settings.summarization_max_sessions_per_run:
                    break

                # Check if session has enough turns
                episodes = await store.get_episodes_for_session(sess["id"])
                user_episodes = [e for e in episodes if e.user_id == user.id]
                if len(user_episodes) < settings.summarization_min_turns:
                    continue

                try:
                    result = await Summarizer(
                        store=store,
                        llm_client=orchestrator.llm_client,
                    ).summarize_session(
                        session_id=sess["id"],
                        user_id=user.id,
                    )
                    if result:
                        logger.info(
                            "Summarized session %s (created=%s, turns=%d, chars=%d)",
                            sess["id"],
                            result.created,
                            result.turn_count,
                            len(result.summary),
                        )
                except Exception as exc:
                    logger.warning("Summarization failed for session %s: %s", sess["id"], exc)

                summarized += 1

    except Exception as exc:
        logger.warning("Summarization run failed: %s", exc)


def _signal_handler(signum, frame):
    global SHUTDOWN
    logger.info("Shutdown signal received, finishing current task...")
    SHUTDOWN = True


def _extract_agent_alert(
    response: str, task_name: str
) -> tuple[str | None, str | None, str]:
    """Pull an agent-raised alert footer out of a task report.

    The scheduled-task directive tells the model it may end its report with
    ``ALERT: <short title>`` followed by a one-sentence reason. This parser
    treats everything from that line onward as the alert footer: it returns
    the title, the body, and the remaining report text (footer stripped) so
    the alert does not get daisy-chained into the stored task summary.
    """
    prefix = SCHEDULED_TASK_ALERT_PREFIX
    for idx, line in enumerate(response.splitlines()):
        stripped = line.strip()
        if stripped.upper().startswith(prefix):
            title = stripped[len(prefix):].strip().strip(":-— ")
            title = title or f"Important update from {task_name}"
            body_lines = [ln.strip() for ln in response.splitlines()[idx + 1:] if ln.strip()]
            message = " ".join(body_lines) or response[:500]
            cleaned = "\n".join(
                ln for ln in response.splitlines()[:idx] if ln.strip()
            ).strip()
            return title[:200], message[:1000], cleaned
    return None, None, response


async def execute_and_record_task(
    store: MemoryStore,
    orchestrator: Orchestrator,
    task_frame_id: int,
    task_name: str,
    task_prompt: str,
    owner_user_id: int,
) -> tuple[bool, str]:
    """Run a scheduled task and record all memory artifacts.

    Shared by scheduler runner and manual API endpoint.
    Calls run_scheduled_task (full cognitive loop), then:
    - Records last_run / next_run in slots.
    - Creates a daily_run_YYYY_MM_DD event frame.
    - Creates associations: task --ran_in--> daily_run, task --produced_output--> episode.
    - Generates embedding for daily run frame.
    - Creates alert for user about task result.
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

        agent_alert_title, agent_alert_message, clean_text = _extract_agent_alert(
            result or "", task_name
        )
        full = (clean_text or result or "")[:2000] if result else ""

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

        # Generate embedding for daily run frame so it's retrievable immediately
        try:
            await store.embed_frames(
                [daily_run_frame_id],
                orchestrator.embed_fn(),
                embedding_model=settings.embedding_model,
            )
        except Exception as e:
            logger.warning("Failed to embed daily run frame: %s", e)

        # Agent-judgment alert: the model flagged something worth the user's
        # attention during this run (parsed from an ALERT: footer).
        if agent_alert_title:
            try:
                await store.create_alert(
                    user_id=owner_user_id,
                    type="task_alert",
                    title=agent_alert_title,
                    message=agent_alert_message,
                    source_frame_id=daily_run_frame_id,
                    severity="important",
                )
                logger.info(
                    "Task '%s' raised an agent alert: %s",
                    task_name, agent_alert_title,
                )
            except Exception as e:
                logger.warning("Failed to create agent alert: %s", e)

        # Create alert for task completion
        try:
            await store.create_alert(
                user_id=owner_user_id,
                type="task_result",
                title=f"Task completed: {task_name}",
                message=full[:500] if full else "Task completed with no output",
                source_frame_id=daily_run_frame_id,
                severity="info",
            )
        except Exception as e:
            logger.warning("Failed to create task completion alert: %s", e)

        logger.info("Task '%s' completed successfully", task_name)
        return True, full

    except Exception as exc:
        logger.exception("Task '%s' failed: %s", task_name, exc)
        await store.update_scheduled_task_run(
            frame_id=task_frame_id,
            last_run=now_str,
            last_result_summary=f"failed: {exc}",
        )

        # Create alert for task failure
        try:
            await store.create_alert(
                user_id=owner_user_id,
                type="task_result",
                title=f"Task failed: {task_name}",
                message=f"Error: {exc}",
                source_frame_id=task_frame_id,
                severity="warning",
            )
        except Exception as e:
            logger.warning("Failed to create task failure alert: %s", e)

        return False, f"failed: {exc}"


async def _execute_task(
    store: MemoryStore,
    orchestrator: Orchestrator,
    task_frame_id: int,
    task_name: str,
    task_prompt: str,
    owner_user_id: int,
) -> tuple[bool, str]:
    """Run a single scheduled task through the orchestrator (scheduler entry point)."""
    return await execute_and_record_task(
        store, orchestrator, task_frame_id, task_name, task_prompt, owner_user_id
    )


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
    last_summarization: datetime | None = None
    consolidation_interval_s = max(0, settings.consolidation_interval_hours) * 3600
    last_consolidation: datetime | None = None
    summarization_interval_s = settings.summarization_interval_hours * 3600

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
            if (
                summarization_interval_s
                and settings.summarization_enabled
                and (
                    last_summarization is None
                    or (now - last_summarization).total_seconds()
                    >= summarization_interval_s
                )
            ):
                await _run_summarization(store, orchestrator)
                last_summarization = datetime.now(UTC)

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
