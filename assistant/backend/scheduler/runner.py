"""Scheduled task runner — runs as a background task inside the backend.

Loops: sleeps until the next task's next_run, executes due tasks, updates
last_run/next_run. Uses the same Orchestrator as chat so that task results
become memory normally.

Started via `start_scheduler(store)` from the FastAPI lifespan.
System tasks (gc, heartbeat) run regardless of user-defined tasks.
"""

from __future__ import annotations

import asyncio
import logging
import signal  # noqa: F401
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

logger = logging.getLogger(__name__)

SHUTDOWN = False


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

    Returns (success, result_summary).
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


async def _sleep_until(next_run: datetime) -> None:
    """Sleep until a specific UTC datetime, checking SHUTDOWN flag."""
    now = datetime.now(UTC)
    if next_run <= now:
        return
    total_seconds = (next_run - now).total_seconds()
    logger.debug("Sleeping %.0f seconds until %s", total_seconds, next_run)

    while total_seconds > 0 and not SHUTDOWN:
        await asyncio.sleep(min(total_seconds, 30))
        total_seconds = (next_run - datetime.now(UTC)).total_seconds()


async def _scheduler_loop(store: MemoryStore) -> None:
    """Main scheduler loop."""
    logger.info("Scheduler loop started")

    await _run_heartbeat(store)
    await _run_memory_gc(store)

    while not SHUTDOWN:
        try:
            tasks = await store.get_due_scheduled_tasks()
            if not tasks:
                nearest = await store.get_nearest_scheduled_task_run()
                if nearest:
                    await _sleep_until(nearest)
                else:
                    await asyncio.sleep(60)
                continue

            orchestrator = _new_orchestrator(store)

            for task in tasks:
                if SHUTDOWN:
                    break
                await _execute_task(
                    store=store,
                    orchestrator=orchestrator,
                    task_frame_id=task["id"],
                    task_name=task["name"],
                    task_prompt=task["prompt"],
                    owner_user_id=task["owner_user_id"] or 1,
                )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Scheduler loop error: %s", exc)
            await asyncio.sleep(30)


async def start_scheduler(store: MemoryStore) -> None:
    """Start the scheduler loop. Called from FastAPI lifespan as a background task.

    Signal handlers are set in the lifespan (main thread), not here.
    """
    try:
        await _scheduler_loop(store)
    finally:
        logger.info("Scheduler loop stopped")
