import json
import logging
from collections import defaultdict
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from assistant.backend.memory.store import MemoryStore
    from assistant.backend.pipeline.llm_client import OllamaClient
    from assistant.backend.pipeline.search import SearchBackend, SearchResult

logger = logging.getLogger(__name__)

INITIAL_SEARCH_RELIABILITY = 0.5
CORROBORATION_BONUS = 0.15
MAX_SOURCE_RELIABILITY = 0.99


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


SEARCH_EXTRACTION_PROMPT = """You extract structured facts from web search results.

Given a user query and a list of search results (title, URL, snippet), identify:
- Concrete facts about entities, concepts, events, or household items.
- Relationships between them.

Output a JSON object with this exact schema:
{
  "slots": [
    {"frame_name": "ai", "frame_type": "concept", "key": "founder", "value": "Sam Altman"}
  ],
  "associations": [
    {"from_frame": "sam_altman", "to_frame": "openai", "relation_type": "founded"}
  ]
}

Rules:
- Only extract facts that are explicitly stated in the snippets.
- frame_type is one of: entity, concept, event, household.
- Use snake_case for frame_name (e.g. "sam_altman", "openai").
- Don't extract opinions, commentary, or vague statements.
- If no reliable facts to extract, return {"slots": [], "associations": []}.
- A fact confirmed by multiple sources should appear once in the slots list.

Respond with ONLY the JSON object, no commentary."""


CORRECTION_EXTRACTION_PROMPT = """You parse a user correction about a stored fact.

The user is saying that something stored in memory is wrong.
Extract:
- The frame_name (the entity/concept being corrected) — use snake_case
- The slot key that is wrong
- The correct new value

Output a JSON object with this exact schema:
{
  "frame_name": "guitar",
  "slot_key": "strings",
  "new_value": "12"
}

If the correction does not specify a particular frame name, return nulls.
If the correction is vague (e.g. "that's wrong" without specifying what), return nulls.

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


async def extract_facts_from_search(
    query: str,
    search_results: list["SearchResult"],
    llm_client: "OllamaClient",
) -> ExtractionResult:
    """Extract structured facts from search results using the LLM."""
    from assistant.backend.pipeline.llm_client import ChatMessage

    if not search_results:
        return ExtractionResult()

    results_text = "\n\n".join(
        f"Result {i + 1}:\nTitle: {r.title}\nURL: {r.url}\nSnippet: {r.snippet}"
        for i, r in enumerate(search_results)
    )
    user_content = f"User query: {query}\n\n{results_text}"

    system = ChatMessage(role="system", content=SEARCH_EXTRACTION_PROMPT)
    user = ChatMessage(role="user", content=user_content)

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
            logger.warning("Search extraction parse failed (attempt %d): %s", attempt + 1, e)
            if attempt == 0:
                extra = "\n\nIMPORTANT: Output ONLY valid JSON. No markdown, no preamble."
                system = ChatMessage(role="system", content=SEARCH_EXTRACTION_PROMPT + extra)
            else:
                logger.error("Search extraction failed after retry for query: %s", query[:100])
                return ExtractionResult()
    return ExtractionResult()


async def apply_extraction(
    extraction: ExtractionResult,
    store: "MemoryStore",
    source_episode_id: int | None = None,
    source_type: str | None = None,
    source_url: str | None = None,
    source_reliability: float | None = None,
) -> dict:
    """Apply an ExtractionResult to the MemoryStore.

    For each slot: ensure frame exists (create if needed), then upsert_slot.
    For each association: ensure both frames exist, then create_association.
    """
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
            ftype = next(
                (slot.frame_type for slot in extraction.slots if slot.frame_name == name),
                "entity",
            )
            new_frame = await store.create_frame(
                name,
                ftype,
                source_type=source_type,
                source_url=source_url,
                source_reliability=source_reliability,
            )
            frame_ids[name] = new_frame.id

    slots_applied = 0
    conflicts_created = 0
    for slot in extraction.slots:
        frame_id = frame_ids[slot.frame_name]
        _, conflict = await store.upsert_slot(
            frame_id=frame_id,
            key=slot.key,
            value=slot.value,
            source_episode_id=source_episode_id,
            source_type=source_type,
            source_url=source_url,
            source_reliability=source_reliability,
        )
        slots_applied += 1
        if conflict is not None:
            conflicts_created += 1

    assocs_created = 0
    for assoc in extraction.associations:
        from_id = frame_ids[assoc.from_frame]
        to_id = frame_ids[assoc.to_frame]
        if from_id == to_id:
            continue
        try:
            await store.create_association(
                from_frame_id=from_id,
                to_frame_id=to_id,
                relation_type=assoc.relation_type,
                confidence=0.5,
                source_type=source_type,
                source_url=source_url,
                source_reliability=source_reliability,
            )
            assocs_created += 1
        except Exception as e:
            logger.debug("Association likely duplicate: %s", e)

    return {
        "slots_applied": slots_applied,
        "associations_created": assocs_created,
        "conflicts_created": conflicts_created,
    }


async def apply_search_extraction(
    extraction: ExtractionResult,
    search_results: list["SearchResult"],
    store: "MemoryStore",
) -> dict:
    """Apply search extraction with corroboration support.

    Groups facts by (frame_name, key, value) to detect corroboration.
    Facts appearing in multiple independent sources get bumped source_reliability.
    Per-slot source_url is the corroborating URL (prefer .edu, Wikipedia, major news).
    """
    if not extraction.slots and not extraction.associations:
        return {"slots_applied": 0, "associations_created": 0, "conflicts_created": 0}

    fact_key_to_urls: dict[tuple, set[str]] = defaultdict(set)
    for result in search_results:
        snippet_lower = result.snippet.lower()
        for slot in extraction.slots:
            if slot.value.lower() in snippet_lower or slot.key.lower() in snippet_lower:
                fact_key_to_urls[(slot.frame_name, slot.key, slot.value)].add(result.url)

    reliability_map: dict[tuple, float] = {}
    for fact_key, urls in fact_key_to_urls.items():
        count = len(urls)
        if count >= 3:
            reliability = min(
                INITIAL_SEARCH_RELIABILITY + CORROBORATION_BONUS * 2,
                MAX_SOURCE_RELIABILITY,
            )
        elif count >= 2:
            reliability = min(
                INITIAL_SEARCH_RELIABILITY + CORROBORATION_BONUS,
                MAX_SOURCE_RELIABILITY,
            )
        else:
            reliability = INITIAL_SEARCH_RELIABILITY
        reliability_map[fact_key] = reliability

    def _best_url(urls: set[str]) -> str | None:
        prefs = (".edu", "wikipedia.org", "github.com", "nytimes.com", "reuters.com", "bbc.com")
        for pref in prefs:
            for url in urls:
                if pref in url:
                    return url
        return next(iter(urls)) if urls else None

    seen: set[tuple] = set()
    deduped_slots: list[ExtractedSlot] = []
    for slot in extraction.slots:
        key = (slot.frame_name, slot.key, slot.value)
        if key not in seen:
            seen.add(key)
            deduped_slots.append(slot)

    frame_ids: dict[str, int] = {}
    all_frame_names = {slot.frame_name for slot in deduped_slots}
    for assoc in extraction.associations:
        all_frame_names.add(assoc.from_frame)
        all_frame_names.add(assoc.to_frame)

    for name in all_frame_names:
        existing = await store.get_frame_by_name(name)
        if existing:
            frame_ids[name] = existing.id
        else:
            ftype = next(
                (slot.frame_type for slot in deduped_slots if slot.frame_name == name),
                "entity",
            )
            new_frame = await store.create_frame(name, ftype, source_type="search")
            frame_ids[name] = new_frame.id

    slots_applied = 0
    conflicts_created = 0
    for slot in deduped_slots:
        frame_id = frame_ids[slot.frame_name]
        fact_key = (slot.frame_name, slot.key, slot.value)
        urls = fact_key_to_urls.get(fact_key, set())
        slot_url = _best_url(urls)
        slot_reliability = reliability_map.get(fact_key, INITIAL_SEARCH_RELIABILITY)
        _, conflict = await store.upsert_slot(
            frame_id=frame_id,
            key=slot.key,
            value=slot.value,
            source_type="search",
            source_url=slot_url,
            source_reliability=slot_reliability,
        )
        slots_applied += 1
        if conflict is not None:
            conflicts_created += 1

    primary_url = search_results[0].url if search_results else None
    assocs_created = 0
    for assoc in extraction.associations:
        from_id = frame_ids[assoc.from_frame]
        to_id = frame_ids[assoc.to_frame]
        if from_id == to_id:
            continue
        try:
            await store.create_association(
                from_frame_id=from_id,
                to_frame_id=to_id,
                relation_type=assoc.relation_type,
                confidence=0.5,
                source_type="search",
                source_url=primary_url,
                source_reliability=INITIAL_SEARCH_RELIABILITY,
            )
            assocs_created += 1
        except Exception:
            pass

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
    """Full extraction pipeline: extract facts + apply to memory store."""
    try:
        extraction = await extract_facts(user_message, assistant_response, llm_client)
        if not extraction.slots and not extraction.associations:
            return {"slots_applied": 0, "associations_created": 0, "conflicts_created": 0}
        return await apply_extraction(extraction, store, source_episode_id)
    except Exception as e:
        logger.error("extract_and_apply failed: %s", e)
        return {}


class CorrectionResult(BaseModel):
    frame_name: str | None = None
    slot_key: str | None = None
    new_value: str | None = None


class CorrectionValidation(BaseModel):
    corroborated: bool
    contradicted: bool
    summary: str


async def validate_correction(
    correction: CorrectionResult,
    current_value: str | None,
    store: "MemoryStore",
    search_tool: "SearchBackend",
    llm_client: "OllamaClient",
) -> CorrectionValidation:
    """Validate a user correction by checking third-party sources.

    Searches for corroborating evidence about the claimed fact.
    Returns a ValidationResult indicating whether the correction is
    corroborated, contradicted, or inconclusive.
    """
    if not correction.frame_name or not correction.slot_key or not correction.new_value:
        return CorrectionValidation(
            corroborated=False,
            contradicted=False,
            summary="Invalid correction",
        )

    query = f"{correction.frame_name} {correction.slot_key} {correction.new_value}"
    try:
        results = await search_tool.search(query, num_results=5)
    except Exception:
        results = []

    if not results:
        return CorrectionValidation(
            corroborated=False,
            contradicted=False,
            summary="No third-party sources available — accepting your correction",
        )

    corroboration_count = 0
    contradiction_count = 0
    snippet_text = " ".join(r.snippet.lower() for r in results)

    if correction.new_value.lower() in snippet_text:
        corroboration_count += 1
    if current_value and current_value.lower() in snippet_text:
        if current_value.lower() != correction.new_value.lower():
            contradiction_count += 1

    if corroboration_count > 0 and contradiction_count == 0:
        return CorrectionValidation(
            corroborated=True,
            contradicted=False,
            summary=f"Third-party sources corroborate: {correction.new_value}",
        )
    elif contradiction_count > 0 and corroboration_count == 0:
        return CorrectionValidation(
            corroborated=False,
            contradicted=True,
            summary=(
                f"Third-party sources suggest {current_value}, not "
                f"{correction.new_value} — flagging for review"
            ),
        )
    else:
        return CorrectionValidation(
            corroborated=False,
            contradicted=False,
            summary="Third-party sources are inconclusive — accepting your correction",
        )


async def extract_correction(
    user_message: str,
    llm_client: "OllamaClient",
) -> CorrectionResult | None:
    """Parse a user correction message to extract what needs to be corrected.

    Returns CorrectionResult with frame_name, slot_key, and new_value if parseable.
    Returns None if the correction is too vague to act on.
    """
    from assistant.backend.pipeline.llm_client import ChatMessage

    system = ChatMessage(role="system", content=CORRECTION_EXTRACTION_PROMPT)
    user = ChatMessage(role="user", content=f"User correction: {user_message}")

    for attempt in range(2):
        try:
            response = await llm_client.chat(
                [system, user],
                model=llm_client.utility_model,
                format="json",
                temperature=0.0,
            )
            data = json.loads(response.content)
            result = CorrectionResult.model_validate(data)
            if result.frame_name and result.slot_key and result.new_value:
                return result
            return None
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Correction parse failed (attempt %d): %s", attempt + 1, e)
            if attempt == 0:
                extra = "\n\nIMPORTANT: Output ONLY valid JSON. No markdown, no preamble."
                system = ChatMessage(
                    role="system", content=CORRECTION_EXTRACTION_PROMPT + extra
                )
            else:
                logger.error("Correction extraction failed after retry for: %s", user_message[:100])
                return None
    return None


async def apply_correction(
    correction: CorrectionResult,
    store: "MemoryStore",
    source_episode_id: int | None = None,
) -> dict:
    """Apply a user correction to the store.

    The corrected slot is given high source_reliability (0.9) since it comes from the user.
    A conflict may be created if the existing value differs.
    """
    if not correction.frame_name or not correction.slot_key or not correction.new_value:
        return {"slots_corrected": 0}

    frame = await store.get_frame_by_name(correction.frame_name)
    if not frame:
        logger.info("Correction: frame '%s' not found, creating", correction.frame_name)
        frame = await store.create_frame(
            correction.frame_name,
            "entity",
            source_type="user_correction",
        )

    _, conflict = await store.upsert_slot(
        frame_id=frame.id,
        key=correction.slot_key,
        value=correction.new_value,
        source_episode_id=source_episode_id,
        source_type="user_correction",
        source_reliability=0.9,
    )

    return {
        "slots_corrected": 1,
        "frame_name": correction.frame_name,
        "slot_key": correction.slot_key,
        "new_value": correction.new_value,
        "conflict": conflict is not None,
    }
