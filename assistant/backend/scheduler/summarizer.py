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
        # Fetch episodes for this session
        episodes = await self.store.get_episodes_for_session(session_id)
        user_episodes = [e for e in episodes if e.user_id == user_id]

        if len(user_episodes) < settings.summarization_min_turns:
            logger.debug(
                "Session %s has %d turns, below minimum %d; skipping",
                session_id, len(user_episodes), settings.summarization_min_turns
            )
            return None

        # Build conversation text
        episodes_text = self._format_episodes(user_episodes)
        if len(episodes_text) > settings.summarization_max_chars:
            episodes_text = episodes_text[:settings.summarization_max_chars]

        # Generate summary via utility model
        summary_data = await self._generate_summary(
            session_id=session_id,
            turn_count=len(user_episodes),
            first_ts=user_episodes[0].timestamp,
            last_ts=user_episodes[-1].timestamp,
            episodes_text=episodes_text,
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

    def _format_episodes(self, episodes: list) -> str:
        """Format episodes into conversation text."""
        lines = []
        for ep in episodes:
            prefix = "User" if ep.role == "user" else "Assistant"
            content = ep.content[:500]  # cap per-turn
            lines.append(f"{prefix}: {content}")
        return "\n".join(lines)

    async def _generate_summary(
        self,
        session_id: str,
        turn_count: int,
        first_ts: str,
        last_ts: str,
        episodes_text: str,
    ) -> dict | None:
        """Call utility model to generate structured summary."""
        prompt = f"""Summarize the following conversation session into a structured summary.

Session ID: {session_id}
Turns: {turn_count}
Date Range: {first_ts} to {last_ts}

Conversation:
{episodes_text}

Produce a JSON object with these fields:
- "summary": 3-5 sentence narrative capturing key topics, decisions, and outcomes
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
        """Upsert the summary frame. Returns True if created, False if updated."""
        frame_name = f"conversation_summary_{session_id}"

        # Check if frame exists
        existing = await self.store.get_frame_by_name(frame_name)

        slots = {
            "summary": summary,
            "key_entities": ", ".join(key_entities) if key_entities else "",
            "open_questions": ", ".join(open_questions) if open_questions else "",
            "session_id": session_id,
            "turn_count": str(turn_count),
            "date_start": date_range[0],
            "date_end": date_range[1],
        }

        if existing:
            # Update existing frame
            frame_id = existing.id
            for key, value in slots.items():
                await self.store.upsert_slot(
                    frame_id=frame_id,
                    key=key,
                    value=value,
                    essential=0,
                    priority=0.5,
                    source_type="summarization",
                    source_reliability=0.8,
                )
            # Update embedding
            await self.store.embed_frames(
                [existing.id], self.llm_client.embed, settings.embedding_model
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
            for key, value in slots.items():
                await self.store.upsert_slot(
                    frame_id=frame.id,
                    key=key,
                    value=value,
                    essential=0,
                    priority=0.5,
                    source_type="summarization",
                    source_reliability=0.8,
                )
            # Generate embedding for new summary frame
            await self.store.embed_frames(
                [frame.id], self.llm_client.embed, settings.embedding_model
            )
            logger.info("Created summary frame for session %s", session_id)
            return True