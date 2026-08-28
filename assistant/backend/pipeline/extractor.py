import json
import logging
import re
import sqlite3
from collections import defaultdict
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from assistant.backend.config import settings

if TYPE_CHECKING:
    from assistant.backend.memory.store import MemoryStore
    from assistant.backend.pipeline.llm_client import OllamaClient
    from assistant.backend.pipeline.search import SearchBackend, SearchResult

logger = logging.getLogger(__name__)

INITIAL_SEARCH_RELIABILITY = 0.5
CORROBORATION_BONUS = 0.15
MAX_SOURCE_RELIABILITY = 0.99

# Canonical frame/slot holding the assistant's own name (see /assistant/name).
IDENTITY_FRAME = "identity_name"
IDENTITY_NAME_SLOT = "full_name"


class ExtractedSlot(BaseModel):
    frame_name: str
    frame_type: str = "entity"  # 'entity' | 'concept' | 'event' | 'household'
    key: str
    value: str | None = None  # Optional — some facts may not have a simple value


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
- Facts about the ASSISTANT ITSELF: the agent's name, identity, or description.

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
- frame_name MUST be a specific proper noun or title ("mount_rainier",
  "dark_side_of_the_moon"). NEVER use a bare type word as the name
  ("song", "movie", "entity", "thing", "concept").
- Emit at most 4 associations per turn — only the most meaningful relations.
- relation_type is a short snake_case verb phrase (e.g. "related_to",
  "part_of", "created_by", "located_in", "inspired_by").
- Don't extract transient conversational content ("hello", "thanks").
- AGENT IDENTITY (the assistant's own traits) may only come from USER speech.
  If the user names, renames, or chooses a name for the assistant — including
  imperatives like "your name is now Echo", "I'll call you X", "let's name you
  X" — emit: {"frame_name": "identity_name", "frame_type": "entity",
  "key": "full_name", "value": "<the name>"}.
  If the user states how they want the assistant to behave or work with them
  ("always ask before acting", "keep answers short", "we work best when you
  confirm first"), emit identity_name slots with a descriptive snake_case key:
  e.g. {"key": "working_agreement", "value": "always ask before acting"}.
  Do NOT create a separate working_agreement frame — these belong directly on
  identity_name.
  A user QUOTING a name back ("you said your name was Hermes") still counts: the
  value came from the user's message.
  NEVER take the assistant's identity from the Assistant side of the transcript.
  Generic self-descriptions ("my full name is cognitive digital assistant",
  "I am an AI language model") are not facts and must never be extracted.
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
- NEVER extract facts that come from the user query itself — anything the user
  stated about themselves is stored separately by another channel. Extract only
  what the search results add beyond the query.
- frame_type is one of: entity, concept, event, household.
- Use snake_case for frame_name (e.g. "sam_altman", "openai").
- frame_name MUST be a specific proper noun or title. NEVER use a bare type
  word as the name ("song", "movie", "entity", "thing", "concept").
- Emit at most 4 associations per turn — only the most meaningful relations.
- relation_type is a short snake_case verb phrase (e.g. "founded", "located_in",
  "created_by", "related_to").
- Don't extract opinions, commentary, or vague statements.
- If no reliable facts to extract, return {"slots": [], "associations": []}.
- A fact confirmed by multiple sources should appear once in the slots list.
- When results are from a high-confidence source and include full page content:
  extract up to 8 slots, focusing on all concrete facts explicitly stated.
- For snippet-only results: stay conservative, aim for up to 4-5 slots.

Respond with ONLY the JSON object, no commentary."""


CORRECTION_EXTRACTION_PROMPT = """Parse a user correction message.

The user is pointing out that something in memory is wrong. Extract the correction:

1. frame_name: Identify the entity/concept being corrected (e.g. "guitar", "meeting", "alice").
   Use snake_case. If the user refers to something implicitly ("it's not 6, it's 12" referring
   to a guitar they mentioned), infer the frame from context.

2. slot_key: The specific attribute/field that is wrong. If the user says "the guitar has 12 strings
   not 6", the slot_key is "strings". If they don't specify which field, infer from what they're
   correcting.

3. new_value: The correct value the user is providing.

Rules:
- Return nulls if the user doesn't identify what is wrong (e.g. just says "that's wrong"
  without specifying what).
- If the correction is about something you didn't mention, still parse it — the user knows
  what they told you.
- Be specific: "12" is better than "twelve". Prefer the user's exact wording for values.

Output ONLY valid JSON with this schema:
{"frame_name": "...", "slot_key": "...", "new_value": "..."}

Use null for any field you cannot determine."""


def filter_duplicate_slots(
    candidate: ExtractionResult,
    stored_slots: list[dict],
) -> ExtractionResult:
    """Drop candidate slots that duplicate already-stored slots.

    Match is (frame_name, value) case-insensitive — the same fact arriving from
    two channels (conversational + search extraction) often lands under
    different keys ("strings" vs "number_of_strings"), so key equality would
    miss it. Keeps the earlier channel's key vocabulary intact.
    """
    if not stored_slots:
        return candidate
    seen = {
        (str(s.get("frame_name", "")).lower(), str(s.get("value", "")).strip().lower())
        for s in stored_slots
        if s.get("value") is not None
    }
    slots = [
        s
        for s in candidate.slots
        if s.value is None or (s.frame_name.lower(), str(s.value).strip().lower()) not in seen
    ]
    return ExtractionResult(slots=slots, associations=candidate.associations)


def merge_extractions(
    base: ExtractionResult, *others: ExtractionResult
) -> ExtractionResult:
    """Merge extraction results, keeping first occurrence of each (frame_name, value).

    Uses the same (frame_name, value) deduplication key as filter_duplicate_slots.
    The first occurrence wins — subsequent identical facts from other channels
    (e.g. snippet vs full-page extraction) are dropped to avoid inflation.
    """
    seen: set[tuple[str, str]] = set()
    merged_slots: list[ExtractedSlot] = []
    for extraction in (base, *others):
        for slot in extraction.slots:
            if slot.value is None:
                merged_slots.append(slot)
                continue
            key = (slot.frame_name.lower(), str(slot.value).strip().lower())
            if key not in seen:
                seen.add(key)
                merged_slots.append(slot)

    merged_assocs: list[ExtractedAssociation] = []
    assoc_seen: set[tuple[str, str, str]] = set()
    for extraction in (base, *others):
        for assoc in extraction.associations:
            key = (assoc.from_frame.lower(), assoc.to_frame.lower(), assoc.relation_type.lower())
            if key not in assoc_seen:
                assoc_seen.add(key)
                merged_assocs.append(assoc)

    return ExtractionResult(slots=merged_slots, associations=merged_assocs)


# Words ignored when building a frame's canonical form. Mid-name articles and
# conjunctions are the main source of cosmetic near-duplicates
# ("The Mountain & the Wolf" vs "the mountain and the wolf").
_NAME_STOP_WORDS = {"the", "a", "an", "and", "of"}


def normalize_frame_name(name: str) -> str:
    """Lowercase, drop punctuation/stop words, collapse whitespace.

    "The Mountain & the Wolf" -> "mountain wolf"; used for exact-normalized
    duplicate detection ahead of the embedding-similarity check.
    """
    return " ".join(
        token for token in re.findall(r"[a-z0-9]+", name.lower()) if token not in _NAME_STOP_WORDS
    )


# Fuzzy reuse is only allowed when both types match, or one side is "entity"
# (the extractor's catch-all). concept/event/household stay strict so a
# household item never absorbs into an unrelated entity by name similarity.
_ENTITY_WILDCARD = "entity"

MIN_FUZZY_NAME_LENGTH = 4  # skip fuzzy matching for short names ("bo" vs "bob")


async def resolve_or_create_frame(
    store: "MemoryStore",
    name: str,
    ftype: str,
    *,
    source_type: str | None = None,
    source_url: str | None = None,
    source_reliability: float | None = None,
    embed_fn=None,
    known: dict[str, tuple[int, str]] | None = None,
) -> int:
    """Resolve a frame name to an existing frame id, or create a new frame.

    Canonical resolution order (Phase 9A/B):
      1. exact name match;
      2. normalized-name match (case/punctuation/stop-word-insensitive);
      3. consolidation alias map (names of merged duplicate frames);
      4. embedding similarity within settings.canonical_name_distance — shared
         (owner-less) frames with compatible types only; skipped when no
         embedder is available or the normalized name is under
         MIN_FUZZY_NAME_LENGTH chars;
      5. create a new frame (registering it in ``known`` for later lookups).
    """
    existing = await store.get_frame_by_name(name)
    if existing:
        return existing.id

    if known is None:
        known = {
            normalize_frame_name(name_): (frame_id, type_)
            for frame_id, name_, type_ in await store.list_live_frame_stubs()
        }

    normalized = normalize_frame_name(name)
    if known is not None and normalized:
        hit = known.get(normalized)
        if hit is not None:
            return hit[0]

    if normalized:
        alias_id = await store.get_alias_frame_id(normalized)
        if alias_id is not None:
            logger.info("Resolved %r through alias to frame %d", name, alias_id)
            return alias_id

    if embed_fn is not None and len(normalized) >= MIN_FUZZY_NAME_LENGTH:
        try:
            query_embedding = await embed_fn(normalized)
            matches = await store.search_similar_frames(
                query_embedding,
                user_id=None,
                limit=5,
                min_distance=settings.canonical_name_distance,
            )
        except Exception as exc:
            logger.warning("Canonicalization embedding lookup failed: %s", exc)
        else:
            for frame, _slots, similarity in matches:
                if frame.owner_user_id is not None:
                    continue
                if (
                    frame.type != ftype
                    and ftype != _ENTITY_WILDCARD
                    and frame.type != _ENTITY_WILDCARD
                ):
                    continue
                logger.info(
                    "Canonicalized %r onto existing frame %d (%r, d=%.3f)",
                    name,
                    frame.id,
                    frame.name,
                    1 - similarity,
                )
                return frame.id

    new_frame = await store.create_frame(
        name,
        ftype,
        source_type=source_type,
        source_url=source_url,
        source_reliability=source_reliability,
    )
    if known is not None and normalized:
        known[normalized] = (new_frame.id, ftype)
    return new_frame.id


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
                think=False,
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
                think=False,
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


DOCUMENT_EXTRACTION_PROMPT = """You extract structured facts from a web page or document.

Given the full text content of a web page, identify:
- Concrete facts about entities, concepts, events, or household items.
- Relationships between them.

Output a JSON object with this exact schema:
{
  "slots": [
    {"frame_name": "mozworth", "frame_type": "entity",
     "key": "genre", "value": "indie alternative rock"}
  ],
  "associations": [
    {"from_frame": "mozworth", "to_frame": "austin_tx",
     "relation_type": "located_in"}
  ]
}

Rules:
- Only extract facts explicitly stated in the document content.
- frame_type is one of: entity, concept, event, household.
- Use snake_case for frame_name (e.g. "mozworth", "austin_tx").
- frame_name MUST be a specific proper noun or title. NEVER use a bare type
  word as the name ("song", "movie", "entity", "thing", "concept").
- Emit at most 4 associations per turn — only the most meaningful relations.
- relation_type is a short snake_case verb phrase (e.g. "located_in", "founded",
  "created_by", "related_to").
- Don't extract opinions, commentary, or vague statements.
- If no reliable facts to extract, return {"slots": [], "associations": []}.
- A fact confirmed by multiple parts of the document should appear once.

Respond with ONLY the JSON object, no commentary."""


async def extract_facts_from_document(
    document_content: str,
    source_url: str,
    llm_client: "OllamaClient",
) -> ExtractionResult:
    """Extract structured facts from a fetched document using the LLM."""
    from assistant.backend.pipeline.llm_client import ChatMessage

    if not document_content.strip():
        return ExtractionResult()

    user_content = f"Source URL: {source_url}\n\nDocument content:\n{document_content[:8000]}"

    system = ChatMessage(role="system", content=DOCUMENT_EXTRACTION_PROMPT)
    user = ChatMessage(role="user", content=user_content)

    for attempt in range(2):
        try:
            response = await llm_client.chat(
                [system, user],
                model=llm_client.utility_model,
                format="json",
                temperature=0.0,
                think=False,
            )
            data = json.loads(response.content)
            return ExtractionResult.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Document extraction parse failed (attempt %d): %s", attempt + 1, e)
            if attempt == 0:
                extra = "\n\nIMPORTANT: Output ONLY valid JSON. No markdown, no preamble."
                system = ChatMessage(role="system", content=DOCUMENT_EXTRACTION_PROMPT + extra)
            else:
                logger.error("Document extraction failed after retry for URL: %s", source_url)
                return ExtractionResult()
    return ExtractionResult()


async def apply_extraction(
    extraction: ExtractionResult,
    store: "MemoryStore",
    source_episode_id: int | None = None,
    source_type: str | None = None,
    source_url: str | None = None,
    source_reliability: float | None = None,
    embed_fn=None,
) -> dict:
    """Apply an ExtractionResult to the MemoryStore.

    For each slot: resolve or create its frame (canonicalized), then upsert_slot.
    For each association: ensure both frames exist, then create_association
    (duplicates bump the existing edge's confidence instead of erroring).
    """
    frame_ids: dict[str, int] = {}

    all_frame_names = {slot.frame_name for slot in extraction.slots}
    for assoc in extraction.associations:
        all_frame_names.add(assoc.from_frame)
        all_frame_names.add(assoc.to_frame)

    ftype_hints = {slot.frame_name: slot.frame_type for slot in extraction.slots}
    known = {
        normalize_frame_name(name_): (frame_id, type_)
        for frame_id, name_, type_ in await store.list_live_frame_stubs()
    }
    for name in all_frame_names:
        frame_ids[name] = await resolve_or_create_frame(
            store,
            name,
            ftype_hints.get(name, "entity"),
            source_type=source_type,
            source_url=source_url,
            source_reliability=source_reliability,
            embed_fn=embed_fn,
            known=known,
        )

    slots_applied = 0
    conflicts_created = 0
    applied_slots: list[dict] = []
    for slot in extraction.slots:
        if slot.value is None:
            continue
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
        applied_slots.append(
            {
                "frame_name": slot.frame_name,
                "key": slot.key,
                "value": slot.value,
                "conflict": conflict is not None,
            }
        )

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
        except sqlite3.IntegrityError:
            # Concurrent turn inserted the same edge first; it exists now.
            continue
        assocs_created += 1

    return {
        "slots_applied": slots_applied,
        "associations_created": assocs_created,
        "conflicts_created": conflicts_created,
        "frame_ids": list(frame_ids.values()),
        "slots": applied_slots,
    }


async def apply_search_extraction(
    extraction: ExtractionResult,
    search_results: list["SearchResult"],
    store: "MemoryStore",
    embed_fn=None,
    backend_name: str = "searxng",
) -> dict:
    """Apply search extraction with corroboration support.

    Groups facts by (frame_name, key, value) to detect corroboration.
    Facts appearing in multiple independent sources get bumped source_reliability.
    Per-slot source_url is the corroborating URL (prefer .edu, Wikipedia, major news).
    """
    # The agent's name is never a web fact — drop any identity slots outright.
    extraction.slots = [s for s in extraction.slots if s.frame_name != IDENTITY_FRAME]
    if not extraction.slots and not extraction.associations:
        return {
            "slots_applied": 0,
            "associations_created": 0,
            "conflicts_created": 0,
            "frame_ids": [],
            "slots": [],
        }

    fact_key_to_urls: dict[tuple, set[str]] = defaultdict(set)
    for result in search_results:
        snippet_lower = result.snippet.lower()
        for slot in extraction.slots:
            if not slot.value:
                continue
            if slot.value.lower() in snippet_lower or slot.key.lower() in snippet_lower:
                fact_key_to_urls[(slot.frame_name, slot.key, slot.value)].add(result.url)

    initial_reliability = (
        INITIAL_SEARCH_RELIABILITY + 0.15 if backend_name == "brave" else INITIAL_SEARCH_RELIABILITY
    )

    reliability_map: dict[tuple, float] = {}
    for fact_key, urls in fact_key_to_urls.items():
        count = len(urls)
        if count >= 3:
            reliability = min(
                initial_reliability + CORROBORATION_BONUS * 2,
                MAX_SOURCE_RELIABILITY,
            )
        elif count >= 2:
            reliability = min(
                initial_reliability + CORROBORATION_BONUS,
                MAX_SOURCE_RELIABILITY,
            )
        else:
            reliability = initial_reliability
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

    ftype_hints = {slot.frame_name: slot.frame_type for slot in deduped_slots}
    known = {
        normalize_frame_name(name_): (frame_id, type_)
        for frame_id, name_, type_ in await store.list_live_frame_stubs()
    }
    for name in all_frame_names:
        frame_ids[name] = await resolve_or_create_frame(
            store,
            name,
            ftype_hints.get(name, "entity"),
            source_type="search",
            embed_fn=embed_fn,
            known=known,
        )

    slots_applied = 0
    conflicts_created = 0
    applied_slots: list[dict] = []
    for slot in deduped_slots:
        if slot.value is None:
            continue
        frame_id = frame_ids[slot.frame_name]
        fact_key = (slot.frame_name, slot.key, slot.value)
        urls = fact_key_to_urls.get(fact_key, set())
        slot_url = _best_url(urls)
        slot_reliability = reliability_map.get(fact_key, initial_reliability)
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
        applied_slots.append(
            {
                "frame_name": slot.frame_name,
                "key": slot.key,
                "value": slot.value,
                "conflict": conflict is not None,
            }
        )

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
                source_reliability=initial_reliability,
            )
        except sqlite3.IntegrityError:
            # Concurrent turn inserted the same edge first; it exists now.
            continue
        assocs_created += 1

    return {
        "slots_applied": slots_applied,
        "associations_created": assocs_created,
        "conflicts_created": conflicts_created,
        "frame_ids": list(frame_ids.values()),
        "slots": applied_slots,
    }


def _tokens(text: str) -> list[str]:
    """Lowercase alphanumeric tokens for membership checks."""
    return re.findall(r"[a-z0-9]+", text.lower())


def value_stated_by_user(value: str | None, user_message: str) -> bool:
    """True if the slot value appears verbatim (token-wise) in the user's message.

    Quote marks and punctuation are ignored, so 'Your name is now "Echo"'
    matches a value of "Echo".
    """
    if not value:
        return False
    v = _tokens(value)
    if not v:
        return False
    u = _tokens(user_message)
    n = len(v)
    return any(u[i : i + n] == v for i in range(len(u) - n + 1))


# Minimum fraction of a value's tokens that must trace back to the user's own
# words for non-name identity slots (working agreements, traits). Names demand
# verbatim; longer values may be lightly normalized by the extractor.
IDENTITY_TRACEABILITY_THRESHOLD = 0.7


def value_traced_to_user(value: str | None, user_message: str) -> bool:
    """True if enough of the value's tokens appear in the user's message.

    Order-independent overlap: an extracted working agreement like
    "always ask before acting" survives light paraphrasing of the user's
    "please always confirm with me before you act", but text invented from
    the assistant's own side of the transcript does not.
    """
    if not value:
        return False
    v = set(_tokens(value))
    if not v:
        return False
    u = set(_tokens(user_message))
    return len(v & u) / len(v) >= IDENTITY_TRACEABILITY_THRESHOLD


# Frame names the model may invent for the agent's own traits; their slots are
# folded onto identity_name so self-context stays in one place.
SELF_FRAME_ALIASES = {"working_agreement", "agent_preferences", "assistant_identity"}


def normalize_self_frames(slots: list[ExtractedSlot]) -> list[ExtractedSlot]:
    """Fold alias frames (e.g. working_agreement) onto the identity frame."""
    out: list[ExtractedSlot] = []
    for slot in slots:
        if slot.frame_name in SELF_FRAME_ALIASES:
            out.append(slot.model_copy(update={"frame_name": IDENTITY_FRAME}))
        else:
            out.append(slot)
    return out


def drop_unstated_identity_slots(
    slots: list[ExtractedSlot], user_message: str
) -> list[ExtractedSlot]:
    """Guard against the assistant's self-descriptions becoming "facts".

    The extractor sees both sides of the transcript. Nothing may land on the
    identity frame unless it traces back to the USER's message:
    - full_name demands a contiguous verbatim match;
    - other keys (working agreements, traits) demand >=70% token overlap,
      tolerating extractor normalization while still rejecting text mined
      from the Assistant side.
    Non-identity frames pass through untouched.
    """
    kept: list[ExtractedSlot] = []
    for slot in slots:
        if slot.frame_name != IDENTITY_FRAME:
            kept.append(slot)
            continue
        if slot.key == IDENTITY_NAME_SLOT:
            stated = value_stated_by_user(slot.value, user_message)
        else:
            stated = value_traced_to_user(slot.value, user_message)
        if not stated:
            logger.info(
                "Dropped identity slot %s=%r: value not stated by user",
                slot.key,
                slot.value,
            )
            continue
        kept.append(slot)
    return kept


async def extract_and_apply(
    user_message: str,
    assistant_response: str,
    store: "MemoryStore",
    llm_client: "OllamaClient",
    source_episode_id: int | None = None,
) -> dict:
    """Full extraction pipeline: extract facts + apply to memory store + embed frames."""
    try:
        extraction = await extract_facts(user_message, assistant_response, llm_client)
        extraction.slots = normalize_self_frames(extraction.slots)
        extraction.slots = drop_unstated_identity_slots(extraction.slots, user_message)
        if not extraction.slots and not extraction.associations:
            return {
                "slots_applied": 0,
                "associations_created": 0,
                "conflicts_created": 0,
                "frame_ids": [],
                "slots": [],
            }

        async def get_embedding(text: str) -> list[float]:
            resp = await llm_client.embed(text)
            return resp.embedding

        result = await apply_extraction(
            extraction, store, source_episode_id, embed_fn=get_embedding
        )
        if result.get("frame_ids"):
            await store.embed_frames(result["frame_ids"], get_embedding)
        return result
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
                think=False,
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
                system = ChatMessage(role="system", content=CORRECTION_EXTRACTION_PROMPT + extra)
            else:
                logger.error("Correction extraction failed after retry for: %s", user_message[:100])
                return None
    return None


async def extract_scheduled_task_fields(
    user_message: str,
    llm_client: "OllamaClient",
) -> dict:
    """Parse a scheduled task request to extract intent and parameters.

    Intents: create | list | delete | pause | resume | run_now

    Returns a dict with intent and task fields.
    """
    from assistant.backend.pipeline.llm_client import ChatMessage

    system = ChatMessage(
        role="system",
        content="""You are parsing a request about the agent's daily task list.

The agent runs its task list once a day at a fixed morning time. Tasks repeat
every day until the user asks to stop them, unless the user wants something
done just once.

Determine the user's intent and extract the relevant fields.

Intents:
- "create": user wants to add a task to the daily list
- "list": user wants to see their daily tasks
- "delete": user wants to remove a task (stop doing it)
- "pause": user wants to temporarily stop a task
- "resume": user wants to re-enable a paused task
- "run_now": user wants a task executed immediately

Extract these fields for create:
- name: short identifier (slug-style, e.g. "ai_news_briefing")
- description: human-readable purpose (1-2 sentences)
- prompt: the instruction the agent should execute each day
- repeat: true if the task should run every day; false if the user asked for
  it to happen once ("tomorrow", "just this once", "on Friday", "remind me
  Saturday"). Default true when unclear.

For delete/pause/resume/run_now: only intent and name are needed.
For list: only intent is needed.

Examples:
- "add an AI news briefing to my mornings" → intent=create, name="ai_news_briefing",
  repeat=true
- "remind me to call mom tomorrow" → intent=create, name="call_mom", repeat=false,
  prompt="Remind Alice to call her mom"
- "what's on my daily list?" → intent=list
- "stop doing the weather check" → intent=delete, name="weather_check"
- "run my briefing right now" → intent=run_now, name="briefing"

Respond with ONLY valid JSON:
{"intent": "create"|"list"|"delete"|"pause"|"resume"|"run_now", "name": "...",
 "description": "...", "prompt": "...", "repeat": true|false}""",  # noqa: E501
    )
    user = ChatMessage(role="user", content=user_message)

    for attempt in range(2):
        try:
            response = await llm_client.chat(
                [system, user],
                model=llm_client.utility_model,
                format="json",
                temperature=0.0,
                think=False,
            )
            return json.loads(response.content)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Scheduled task parse failed (attempt %d): %s", attempt + 1, e)
            if attempt == 0:
                extra = "\n\nIMPORTANT: Output ONLY valid JSON. No markdown, no preamble."
                system = ChatMessage(role="system", content=system.content + extra)
            else:
                raise
    raise ValueError("Failed to parse scheduled task fields")


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
