"""Background conversation summarization.

Periodically compresses conversation episodes into structured frame summaries
using the utility model. Runs as a scheduled task off the hot path.
"""

import json
import logging
from dataclasses import dataclass

from assistant.backend.config import settings
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import ChatMessage, OllamaClient

logger = logging.getLogger(__name__)


@dataclass
class SummaryResult:
    """Result of a summarization operation."""
    session_id: str
    summary: str
    key_entities: list[str]
    open_questions: list[str]
    turn_count: int
    date_range: tuple[str, str]
    created: bool  # True if new, False if updated


class Summarizer:
    """Summarizes conversation sessions into structured frames."""

    def __init__(
        self,
        store: MemoryStore,
        llm_client: OllamaClient,
    ):
        self.store = store
        self.llm_client = llm_client

    async def summarize_session(
        self,
        session_id: str,
        user_id: int,
    ) -> SummaryResult | None:
        """Summarize a single session's episodes into a structured frame.

        Returns None if session has insufficient turns or doesn't exist.
        """
        # Fetch episodes for this session (all of them: a summary needs the
        # whole session, and the call is owner-scoped in SQL).
        episodes = await self.store.get_episodes_for_session(
            session_id, user_id=user_id
        )
        user_episodes = episodes

        if len(user_episodes) < settings.summarization_min_turns:
            logger.debug(
                "Session %s has %d turns, below minimum %d; skipping",
                session_id, len(user_episodes), settings.summarization_min_turns
            )
            return None

        # Summarize the *recent* turns and fold in the prior summary, so the
        # summary tracks where the conversation is. Truncating the head (the old
        # behaviour) summarized a long session from its opening and never moved:
        # `episodes_text[:max_chars]` on an oldest-first list drops the tail.
        prior_summary = await self._existing_summary(session_id)
        episodes_text = self._recent_episodes_text(user_episodes)

        # Generate summary via utility model
        summary_data = await self._generate_summary(
            session_id=session_id,
            turn_count=len(user_episodes),
            first_ts=user_episodes[0].timestamp,
            last_ts=user_episodes[-1].timestamp,
            episodes_text=episodes_text,
            prior_summary=prior_summary,
        )

        if not summary_data:
            logger.warning("Failed to generate summary for session %s", session_id)
            return None

        # Upsert summary frame
        created = await self._upsert_summary_frame(
            session_id=session_id,
            user_id=user_id,
            summary=summary_data["summary"],
            key_entities=summary_data["key_entities"],
            open_questions=summary_data["open_questions"],
            turn_count=len(user_episodes),
            date_range=(user_episodes[0].timestamp, user_episodes[-1].timestamp),
        )

        return SummaryResult(
            session_id=session_id,
            summary=summary_data["summary"],
            key_entities=summary_data["key_entities"],
            open_questions=summary_data["open_questions"],
            turn_count=len(user_episodes),
            date_range=(user_episodes[0].timestamp, user_episodes[-1].timestamp),
            created=created,
        )

    @staticmethod
    def _episode_line(ep) -> str:
        prefix = "User" if ep.role == "user" else "Assistant"
        return f"{prefix}: {ep.content[:500]}"  # cap per-turn

    def _recent_episodes_text(self, episodes: list) -> str:
        """The most recent turns that fit the budget, oldest-first.

        A long session's *recent* turns are where the conversation is; the prior
        summary (folded in by the caller) carries the earlier context forward.
        The previous head-truncation summarized the opening and never advanced.
        """
        budget = settings.summarization_max_chars
        chosen: list[str] = []
        used = 0
        for ep in reversed(episodes):
            line = self._episode_line(ep)
            if chosen and used + len(line) > budget:
                break
            chosen.append(line)
            used += len(line) + 1
        chosen.reverse()
        return "\n".join(chosen)

    async def _existing_summary(self, session_id: str) -> str | None:
        """The session's current summary prose, or None if there is none."""
        frame = await self.store.get_frame_by_name(
            f"conversation_summary_{session_id}"
        )
        if frame is None:
            return None
        for slot in await self.store.get_slots_for_frame(frame.id):
            if slot.key == "summary" and (slot.value or "").strip():
                return slot.value
        return None

    async def _generate_summary(
        self,
        session_id: str,
        turn_count: int,
        first_ts: str,
        last_ts: str,
        episodes_text: str,
        prior_summary: str | None = None,
    ) -> dict | None:
        """Call utility model to generate structured summary."""
        prior_block = (
            "\nPrevious summary of the earlier part of this conversation "
            "(carry it forward, updating it with what is new):\n"
            f"{prior_summary}\n"
            if prior_summary
            else ""
        )
        prompt = f"""Summarize the following conversation session into a structured summary.

Session ID: {session_id}
Turns: {turn_count}
Date Range: {first_ts} to {last_ts}
{prior_block}
Recent conversation:
{episodes_text}

Produce a JSON object with these fields:
- "summary": 3-5 sentence narrative capturing key topics, decisions, and outcomes so far
- "key_entities": list of important people, projects, concepts mentioned
- "open_questions": list of unresolved topics or follow-ups needed"""

        try:
            resp = await self.llm_client.chat(
                [
                    ChatMessage(role="system", content=(
                        "You are a precise summarizer. Output ONLY valid JSON. "
                        "No markdown, no extra text. Be concise and factual."
                    )),
                    ChatMessage(role="user", content=prompt),
                ],
                model=self.llm_client.utility_model,
                think=False,
            )
        except Exception as e:
            logger.warning("Utility model summarization failed for session %s: %s", session_id, e)
            return None

        if not resp.content:
            logger.warning("Empty summary response for session %s", session_id)
            return None

        try:
            data = json.loads(resp.content)
            # Validate required fields
            required = {"summary", "key_entities", "open_questions"}
            if not all(k in data for k in required):
                logger.warning("Summary missing required fields: %s", data)
                return None
            return data
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse summary JSON for session %s: %s", session_id, e)
            return None

    async def _upsert_summary_frame(
        self,
        session_id: str,
        user_id: int,
        summary: str,
        key_entities: list[str],
        open_questions: list[str],
        turn_count: int,
        date_range: tuple[str, str],
    ) -> bool:
        """Upsert the summary frame. Returns True if created, False if updated.

        Every slot on a summary frame is **derived**, not asserted. The frame is a
        view of the session's episodes: its prose, entities, and questions are
        regenerated from the conversation on each run, so comparing this run's
        output to the last run's output is the summarizer disagreeing with itself,
        not a conflict between sources.

        The first attempt at this split the slots into "bookkeeping" (counters and
        timestamps) and "content" (prose, entities, questions), and put content
        through `upsert_slot` on the reasoning that prose is a belief. That was the
        wrong line: a belief is *asserted by a source*, and a summary is *computed
        from* episodes. Live behaviour settled it -- the first summarization pass
        after the scheduler was re-enabled produced exactly one conflict per
        content slot per session (`summary`, `key_entities`, `open_questions`),
        which is the signature of regeneration, not disagreement.

        So every slot here is written directly. Values still update and `updated_at`
        still records when, but a regenerated summary stops claiming to be a
        disputed fact. The conflicts ledger is left for genuine disagreements.
        """
        frame_name = f"conversation_summary_{session_id}"

        # Check if frame exists
        existing = await self.store.get_frame_by_name(frame_name)

        derived_slots = {
            "summary": summary,
        }
        if key_entities:
            derived_slots["key_entities"] = ", ".join(key_entities)
        if open_questions:
            derived_slots["open_questions"] = ", ".join(open_questions)

        derived_slots.update(
            {
                "session_id": session_id,
                "turn_count": str(turn_count),
                "date_start": date_range[0],
                "date_end": date_range[1],
            }
        )

        if existing:
            # Update existing frame
            frame_id = existing.id
            for key, value in derived_slots.items():
                await self.store.set_derived_slot(
                    frame_id=frame_id,
                    key=key,
                    value=value,
                    source_type="summarization",
                )
            # Update embedding
            await self.store.embed_frames(
                [existing.id], self.llm_client.embed_one, settings.embedding_model
            )
            logger.info("Updated summary frame for session %s", session_id)
            return False
        else:
            # Create new frame
            frame = await self.store.create_frame(
                name=frame_name,
                type="conversation_summary",
                confidence=0.8,
                owner_user_id=user_id,
                source_type="summarization",
                source_reliability=0.8,
            )
            for key, value in derived_slots.items():
                await self.store.set_derived_slot(
                    frame_id=frame.id,
                    key=key,
                    value=value,
                    source_type="summarization",
                )
            # Generate embedding for new summary frame
            await self.store.embed_frames(
                [frame.id], self.llm_client.embed_one, settings.embedding_model
            )
            logger.info("Created summary frame for session %s", session_id)
            return True