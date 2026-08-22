"""Daily-list task runner — background loop inside the backend.

The agent wakes once a day at the configured tick (DAILY_TASKS_TIME) and runs
every enabled "daily" task; "once" tasks run at the next tick and disable
themselves. Housekeeping (heartbeat every 30 min, memory GC weekly) is plain
timer logic here — not tasks, no LLM calls.

User tasks execute through the same Orchestrator as chat so results become
memory normally. Started via `start_scheduler(store)` from the FastAPI
lifespan.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from assistant.backend.config import settings
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import OllamaClient
from assistant.backend.pipeline.orchestrator import Orchestrator, OrchestratorDeps
from assistant.backend.pipeline.search import WebSearchTool
from assistant.backend.scheduler.schedule import next_daily_run

logger = logging.getLogger(__name__)

SHUTDOWN = False

POLL_SECONDS = 20
HEARTBEAT_INTERVAL_S = 30 * 60
GC_INTERVAL_S = 7 * 24 * 60 * 60


def _signal_handler(signum, frame):
    global SHUTDOWN
    logger.info("Shutdown signal received, finishing current task...")
    SHUTDOWN = True


def _new_orchestrator(store: MemoryStore) -> Orchestrator:
    llm = OllamaClient(base_url=settings.ollama_url)
    search = WebSearchTool(base_url=settings.search_base_url, enabled=True)
    retriever = Retriever(store=store, llm_client=llm)
    return Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=llm,
            search_tool=search,
        )
    )


async def _execute_task(
    store: MemoryStore,
    orchestrator: Orchestrator,
    task_frame_id: int,
    task_name: str,
    task_prompt: str,
    owner_user_id: int,
) -> tuple[bool, str]:
    """Run a single scheduled task through the orchestrator.

    Returns (success, result_summary). update_scheduled_task_run reschedules
    daily tasks to the next tick and disables one-shot tasks.
    """
    try:
        result = await orchestrator.execute_task(
            prompt=task_prompt,
            user_id=owner_user_id,
        )

        full = result[:2000] if result else ""

        await store.update_scheduled_task_run(
            frame_id=task_frame_id,
            last_run=datetime.now(UTC).isoformat(),
            last_result_summary=full,
        )

        logger.info("Task '%s' completed successfully", task_name)
        return True, full

    except Exception as exc:
        logger.exception("Task '%s' failed: %s", task_name, exc)
        await store.update_scheduled_task_run(
            frame_id=task_frame_id,
            last_run=datetime.now(UTC).isoformat(),
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
    """Run memory garbage collection: decay low-priority stale slots."""
    try:
        removed = await store.gc()
        logger.info("Memory GC completed: %d stale slots removed", removed)
    except Exception as exc:
        logger.warning("Memory GC failed: %s", exc)


def _is_new_week(last: datetime | None, now: datetime) -> bool:
    """ISO-week comparison so GC runs once per calendar week."""
    if last is None:
        return True
    local_last = last.astimezone()
    local_now = now.astimezone()
    return local_last.isocalendar()[:2] != local_now.isocalendar()[:2]


async def _scheduler_loop(store: MemoryStore) -> None:
    """Main scheduler loop: poll for due tasks + housekeeping timers."""
    logger.info("Scheduler loop started (daily tick at %s %s)",
                settings.daily_tasks_time, settings.daily_tasks_tz or "local")

    await _run_heartbeat(store)
    await _run_memory_gc(store)
    last_hb = datetime.now(UTC)
    last_gc = last_hb

    while not SHUTDOWN:
        try:
            now = datetime.now(UTC)

            if (now - last_hb).total_seconds() >= HEARTBEAT_INTERVAL_S:
                await _run_heartbeat(store)
                last_hb = now
            if _is_new_week(last_gc, now):
                await _run_memory_gc(store)
                last_gc = now

            due = await store.get_due_scheduled_tasks()
            if due:
                orchestrator = _new_orchestrator(store)
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


def next_tick_for_display() -> str:
    """Human-readable next daily tick (for status surfaces/tests)."""
    return next_daily_run().isoformat()


async def start_scheduler(store: MemoryStore) -> None:
    """Start the scheduler loop. Called from FastAPI lifespan as a background task.

    Signal handlers are set in the lifespan (main thread), not here.
    """
    try:
        await _scheduler_loop(store)
    finally:
        logger.info("Scheduler loop stopped")
