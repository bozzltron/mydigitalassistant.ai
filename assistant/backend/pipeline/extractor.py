import asyncio
import json
import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from assistant.backend.memory.store import MemoryStore
    from assistant.backend.pipeline.llm_client import OllamaClient


logger = logging.getLogger(__name__)


class ExtractedSlot(BaseModel):
    frame_name: str
    frame_type: str = "entity"  # 'entity' | 'concept' | 'event' | 'household'
    key: str
    value: str


class ExtractedAssociation(BaseModel):
    from_frame: str
    to_frame: str
    relation_type: str = "related_to"


class ExtractionResult(BaseModel):
    slots: list[ExtractedSlot] = Field(default_factory=list)
    associations: list[ExtractedAssociation] = Field(default_factory=list)


EXTRACTION_PROMPT = """You extract structured knowledge from a conversation turn.

Given the user message and assistant response, identify:
- Concrete facts about entities, concepts, events, or household items.
- Relationships between them.

Output a JSON object with this exact schema:
{
  "slots": [
    {"frame_name": "guitar", "frame_type": "entity", "key": "strings", "value": "6"}
  ],
  "associations": [
    {"from_frame": "guitar", "to_frame": "music", "relation_type": "related_to"}
  ]
}

Rules:
- Only extract facts that are EXPLICITLY stated or strongly implied.
- frame_type is one of: entity, concept, event, household.
- Use snake_case for frame_name (e.g. "fender_stratocaster").
- Don't extract transient conversational content ("hello", "thanks").
- If no facts to extract, return {"slots": [], "associations": []}.

Respond with ONLY the JSON object, no commentary."""


async def extract_facts(
    user_message: str,
    assistant_response: str,
    llm_client: "OllamaClient",
) -> ExtractionResult:
    """Call LLM to extract structured facts from a turn. Returns parsed result."""
    from assistant.backend.pipeline.llm_client import ChatMessage

    system = ChatMessage(role="system", content=EXTRACTION_PROMPT)
    user = ChatMessage(
        role="user",
        content=f"User: {user_message}\n\nAssistant: {assistant_response}",
    )

    # Retry once on parse failure with a stricter prompt.
    for attempt in range(2):
        try:
            response = await llm_client.chat(
                [system, user],
                model=llm_client.utility_model,
                format="json",
                temperature=0.0,
            )
            data = json.loads(response.content)
            return ExtractionResult.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Extraction parse failed (attempt %d): %s", attempt + 1, e)
            if attempt == 0:
                extra = "\n\nIMPORTANT: Output ONLY valid JSON. No markdown, no preamble."
                system = ChatMessage(role="system", content=EXTRACTION_PROMPT + extra)
            else:
                logger.error("Extraction failed after retry for message: %s", user_message[:100])
                return ExtractionResult()
    return ExtractionResult()


async def apply_extraction(
    extraction: ExtractionResult,
    store: "MemoryStore",
    source_episode_id: int | None = None,
) -> dict:
    """Apply an ExtractionResult to the MemoryStore.

    For each slot: ensure frame exists (create if needed), then upsert_slot.
    For each association: ensure both frames exist, then create_association.

    Returns a summary dict with counts.
    """
    # First pass: ensure all referenced frames exist.
    frame_ids: dict[str, int] = {}

    all_frame_names = {slot.frame_name for slot in extraction.slots}
    for assoc in extraction.associations:
        all_frame_names.add(assoc.from_frame)
        all_frame_names.add(assoc.to_frame)

    for name in all_frame_names:
        existing = await store.get_frame_by_name(name)
        if existing:
            frame_ids[name] = existing.id
        else:
            # Determine type from first slot mentioning this frame, default 'entity'.
            ftype = next(
                (slot.frame_type for slot in extraction.slots if slot.frame_name == name),
                "entity",
            )
            new_frame = await store.create_frame(name, ftype)
            frame_ids[name] = new_frame.id

    # Second pass: upsert slots.
    slots_applied = 0
    conflicts_created = 0
    for slot in extraction.slots:
        frame_id = frame_ids[slot.frame_name]
        _, conflict = await store.upsert_slot(
            frame_id=frame_id,
            key=slot.key,
            value=slot.value,
            source_episode_id=source_episode_id,
        )
        slots_applied += 1
        if conflict is not None:
            conflicts_created += 1

    # Third pass: create associations.
    assocs_created = 0
    for assoc in extraction.associations:
        from_id = frame_ids[assoc.from_frame]
        to_id = frame_ids[assoc.to_frame]
        if from_id == to_id:
            continue  # skip self-loops
        try:
            await store.create_association(
                from_frame_id=from_id,
                to_frame_id=to_id,
                relation_type=assoc.relation_type,
                confidence=0.5,
            )
            assocs_created += 1
        except Exception as e:
            # Likely a unique-constraint violation on duplicate associations.
            logger.debug("Association likely duplicate: %s", e)

    return {
        "slots_applied": slots_applied,
        "associations_created": assocs_created,
        "conflicts_created": conflicts_created,
    }


async def extract_and_apply(
    user_message: str,
    assistant_response: str,
    store: "MemoryStore",
    llm_client: "OllamaClient",
    source_episode_id: int | None = None,
) -> dict:
    """Full extraction pipeline: extract facts + apply to memory store.

    Designed to be called as a fire-and-forget asyncio task.
    Returns summary dict.
    """
    try:
        extraction = await extract_facts(user_message, assistant_response, llm_client)
        if not extraction.slots and not extraction.associations:
            return {"slots_applied": 0, "associations_created": 0, "conflicts_created": 0}
        return await apply_extraction(extraction, store, source_episode_id)
    except Exception as e:
        logger.error("extract_and_apply failed: %s", e)
        return {}


def fire_and_forget(
    user_message: str,
    assistant_response: str,
    store: "MemoryStore",
    llm_client: "OllamaClient",
    source_episode_id: int | None = None,
) -> asyncio.Task:
    """Schedule extraction as a background task. Returns the task handle."""
    return asyncio.create_task(
        extract_and_apply(
            user_message, assistant_response, store, llm_client, source_episode_id
        )
    )
