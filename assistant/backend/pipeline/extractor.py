import json
import logging
import re
import sqlite3
from collections import defaultdict
from typing import TYPE_CHECKING
from urllib.parse import urlparse

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

# The USER's own name is a separate fact with its own frame. Routing first-person
# self-identification here keeps it off the assistant's frame: measured on the
# live brain, "My name is not Carl. I go by Boz." landed on
# identity_name.full_name and renamed the assistant to the user's own name.
USER_IDENTITY_FRAME = "user_identity"
USER_IDENTITY_NAME_SLOT = "full_name"

# The assistant's name is the user's to choose, so a user-stated name is
# authoritative and must always win over the value already stored -- however
# entrenched that value is. A correction can leave `full_name` at
# source_reliability 0.99, while ordinary conversational extraction writes 0.5;
# without this the ladder returned EXISTING_WINS and the name was effectively
# frozen (measured: "Carl" / "Carl Sagan" were both rejected against "Echo").
IDENTITY_NAME_RELIABILITY = 1.0

# Slot-key namespaces owned by the system, not by the model. `file_safe_name` is
# consumed as a filesystem path by the /files endpoints, so a model-authored value
# in that namespace is a path primitive. Model output is untrusted input, so this
# is enforced wherever a model-supplied slot key becomes a slot write.
RESERVED_SLOT_PREFIXES: tuple[str, ...] = ("file_",)


def _is_blank(value: str | None) -> bool:
    """True when a model-supplied string carries no content.

    Frame 4387 was created with an empty name and zero slots from a single paste
    turn, and blank slot values have been written by the summarizer's empty
    list-joins. A record with no content is not memory; it is pollution that
    later reads have to reason around.
    """
    return not (value or "").strip()


def _drop_degenerate(
    slots: list["ExtractedSlot"], associations: list["ExtractedAssociation"]
) -> tuple[list["ExtractedSlot"], list["ExtractedAssociation"], int]:
    """Drop records that cannot express a fact, returning (slots, assocs, dropped).

    Enforced at the write path because the model is untrusted input: a name or key
    of `""` is not a bad value to be corrected later, it is an object with no
    identity. Surfaced via the caller's summary rather than dropped silently, so a
    model that keeps emitting blanks is visible instead of looking like a quiet
    turn.
    """
    kept_slots: list[ExtractedSlot] = []
    dropped = 0
    for slot in slots:
        if _is_blank(slot.frame_name) or _is_blank(slot.key):
            logger.warning(
                "Dropped degenerate extracted slot (frame_name=%r key=%r)",
                slot.frame_name,
                slot.key,
            )
            dropped += 1
            continue
        kept_slots.append(slot)

    kept_assocs: list[ExtractedAssociation] = []
    for assoc in associations:
        if _is_blank(assoc.from_frame) or _is_blank(assoc.to_frame):
            logger.warning(
                "Dropped degenerate extracted association (from=%r to=%r)",
                assoc.from_frame,
                assoc.to_frame,
            )
            dropped += 1
            continue
        kept_assocs.append(assoc)

    return kept_slots, kept_assocs, dropped


class ExtractedSlot(BaseModel):
    frame_name: str = Field(max_length=200)
    frame_type: str = Field(default="entity", max_length=50)  # entity|concept|event|household
    key: str = Field(max_length=200)
    value: str | None = Field(default=None, max_length=4000)
    source_urls: list[str] = Field(default_factory=list, max_length=50)
    source_domains: list[str] = Field(default_factory=list, max_length=50)


class ExtractedAssociation(BaseModel):
    from_frame: str = Field(max_length=200)
    to_frame: str = Field(max_length=200)
    relation_type: str = Field(default="related_to", max_length=100)


class ExtractionResult(BaseModel):
    slots: list[ExtractedSlot] = Field(default_factory=list, max_length=200)
    associations: list[ExtractedAssociation] = Field(default_factory=list, max_length=200)


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
- WHO A NAME BELONGS TO is the point of these rules. Two different frames:
  * The ASSISTANT's name goes on identity_name.full_name, and ONLY when the user
    assigns a name TO THE ASSISTANT (second person): "your name is Echo",
    "I'll call you X", "let's name you X", "you are X", "I dub thee X".
    {"frame_name": "identity_name", "frame_type": "entity",
     "key": "full_name", "value": "<the name>"}
  * The USER's own name goes on user_identity.full_name, when the user names
    THEMSELVES in the first person: "my name is X", "I go by X", "I'm X",
    "call me X".
    {"frame_name": "user_identity", "frame_type": "entity",
     "key": "full_name", "value": "<the name>"}
  A first-person self-introduction is NEVER the assistant's name. "My name is
  Boz" sets user_identity, not identity_name.
- Names that appear only inside pasted content -- an email thread, a list, an
  article, other people's names -- are not the assistant's name and not the
  user's name. Emit no identity_name or user_identity slot for them.
- If the user states how they want the assistant to behave or work with them
  ("always ask before acting", "keep answers short", "we work best when you
  confirm first"), emit identity_name slots with a descriptive snake_case key:
  e.g. {"key": "working_agreement", "value": "always ask before acting"}.
  Do NOT create a separate working_agreement frame — these belong directly on
  identity_name.
  A user QUOTING the assistant's name back ("you said your name was Hermes")
  still counts as naming the assistant.
  NEVER take the assistant's identity from the Assistant side of the transcript.
  Generic self-descriptions ("my full name is cognitive digital assistant",
  "I am an AI language model") are not facts and must never be extracted.
- Examples (User message -> slots to emit):
  "My name is not Carl. I go by Boz."
    -> user_identity.full_name = "Boz"  (the user, not the assistant)
  "Your name is now Carl." / "I'm not Carl. You are Carl."
    -> identity_name.full_name = "Carl"
  "Planning a ski trip with Jay Miles and Sam." (or a pasted email)
    -> no identity_name and no user_identity slot
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

# Identity frames resolve by exact name only. Fuzzy canonicalization matches on
# embedding similarity, and "user identity" / "identity name" are close enough
# that one could absorb the other -- which would put the user's name back on the
# assistant's frame through the back door. Stored in normalized form because that
# is what the resolver compares (see normalize_frame_name).
RESERVED_IDENTITY_FRAMES = frozenset(
    normalize_frame_name(name) for name in (IDENTITY_FRAME, USER_IDENTITY_FRAME)
)


async def resolve_or_create_frame(
    store: "MemoryStore",
    name: str,
    ftype: str,
    *,
    source_type: str | None = None,
    source_url: str | None = None,
    source_reliability: float | None = None,
    embed_fn=None,
    embedding_model: str | None = None,
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

    Refuses a blank name outright. Callers filter degenerate records before
    reaching here (see ``_drop_degenerate``); this is the last line of defence at
    the point where a nameless frame would otherwise be created — which is how
    frame 4387, an empty-named entity with zero slots, came to exist.
    """
    if _is_blank(name):
        raise ValueError(
            "resolve_or_create_frame requires a non-blank frame name; "
            "callers must filter degenerate records before resolving"
        )

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

    if (
        embed_fn is not None
        and len(normalized) >= MIN_FUZZY_NAME_LENGTH
        and normalized not in RESERVED_IDENTITY_FRAMES
    ):
        try:
            query_embedding = await embed_fn(normalized)
            matches = await store.search_similar_frames(
                query_embedding,
                user_id=None,
                # sqlite-vec partitions by model name, so the wrong label returns
                # zero rows rather than erroring. Resolve from settings so an
                # omitted argument still finds vectors written by production.
                embedding_model=embedding_model or settings.embedding_model,
                limit=5,
                min_distance=settings.canonical_name_distance,
            )
        except Exception as exc:
            logger.warning("Canonicalization embedding lookup failed: %s", exc)
        else:
            for frame, _slots, similarity in matches:
                if normalize_frame_name(frame.name) in RESERVED_IDENTITY_FRAMES:
                    continue
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

    for attempt in range(3):
        try:
            response = await llm_client.chat(
                [system, user],
                model=llm_client.utility_model,
                format="json",
                temperature=0.0,
                think=False,
            )
            if not response.content or not response.content.strip():
                logger.warning("Extraction returned empty content (attempt %d)", attempt + 1)
                if attempt == 0:
                    extra = (
                        "\n\nIMPORTANT: Output ONLY valid JSON. "
                        "No markdown, no preamble. Do not output empty response."
                    )
                    system = ChatMessage(role="system", content=EXTRACTION_PROMPT + extra)
                continue
            data = json.loads(response.content)
            return ExtractionResult.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Extraction parse failed (attempt %d): %s", attempt + 1, e)
            if attempt < 2:
                extra = "\n\nIMPORTANT: Output ONLY valid JSON. No markdown, no preamble."
                system = ChatMessage(role="system", content=EXTRACTION_PROMPT + extra)
            else:
                logger.error("Extraction failed after retries for message: %s", user_message[:100])
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

    for attempt in range(3):
        try:
            response = await llm_client.chat(
                [system, user],
                model=llm_client.utility_model,
                format="json",
                temperature=0.0,
                think=False,
            )
            if not response.content or not response.content.strip():
                logger.warning("Search extraction returned empty content (attempt %d)", attempt + 1)
                if attempt < 2:
                    extra = (
                        "\n\nIMPORTANT: Output ONLY valid JSON. "
                        "No markdown, no preamble. Do not output empty response."
                    )
                    system = ChatMessage(role="system", content=SEARCH_EXTRACTION_PROMPT + extra)
                continue
            data = json.loads(response.content)
            return ExtractionResult.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Search extraction parse failed (attempt %d): %s", attempt + 1, e)
            if attempt < 2:
                extra = "\n\nIMPORTANT: Output ONLY valid JSON. No markdown, no preamble."
                system = ChatMessage(role="system", content=SEARCH_EXTRACTION_PROMPT + extra)
            else:
                logger.error("Search extraction failed after retries for query: %s", query[:100])
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

    for attempt in range(3):
        try:
            response = await llm_client.chat(
                [system, user],
                model=llm_client.utility_model,
                format="json",
                temperature=0.0,
                think=False,
            )
            if not response.content or not response.content.strip():
                logger.warning(
                    "Document extraction returned empty content (attempt %d)", attempt + 1
                )
                if attempt < 2:
                    extra = (
                        "\n\nIMPORTANT: Output ONLY valid JSON. "
                        "No markdown, no preamble. Do not output empty response."
                    )
                    system = ChatMessage(role="system", content=DOCUMENT_EXTRACTION_PROMPT + extra)
                continue
            data = json.loads(response.content)
            result = ExtractionResult.model_validate(data)
            # Attach source URL to all extracted slots for traceability
            for slot in result.slots:
                slot.source_urls = [source_url]
                domain = urlparse(source_url).netloc if source_url else ""
                slot.source_domains = [domain] if domain else []
            return result
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Document extraction parse failed (attempt %d): %s", attempt + 1, e)
            if attempt < 2:
                extra = "\n\nIMPORTANT: Output ONLY valid JSON. No markdown, no preamble."
                system = ChatMessage(role="system", content=DOCUMENT_EXTRACTION_PROMPT + extra)
            else:
                logger.error("Document extraction failed after retries for URL: %s", source_url)
    return ExtractionResult()


async def apply_extraction(
    extraction: ExtractionResult,
    store: "MemoryStore",
    source_episode_id: int | None = None,
    source_type: str | None = None,
    source_url: str | None = None,
    source_reliability: float | None = None,
    embed_fn=None,
    embedding_model: str | None = None,
    user_authoritative_identity: bool = False,
) -> dict:
    """Apply an ExtractionResult to the MemoryStore.

    For each slot: resolve or create its frame (canonicalized), then upsert_slot.
    For each association: ensure both frames exist, then create_association
    (duplicates bump the existing edge's confidence instead of erroring).

    When ``user_authoritative_identity`` is set, an extracted
    ``identity_name.full_name`` is written as a user-stated fact at
    ``IDENTITY_NAME_RELIABILITY`` so it always supersedes the stored name. Only
    the conversational path sets this: a name must come from the user, and the
    extraction guard has already proven the value traces to the user's message.
    """
    frame_ids: dict[str, int] = {}

    # Degenerate records are dropped before any frame is resolved, so a blank
    # frame name can never reach resolve_or_create_frame (frame 4387's origin).
    extraction.slots, extraction.associations, degenerate_dropped = _drop_degenerate(
        extraction.slots, extraction.associations
    )

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
            embedding_model=embedding_model,
            known=known,
        )

    slots_applied = 0
    conflicts_created = 0
    skipped_reserved = 0
    applied_slots: list[dict] = []
    for slot in extraction.slots:
        if slot.value is None:
            continue
        # A blank value is not a fact. Guarded here as well as at the model's
        # output shape because this is the last point before the write.
        if _is_blank(slot.value):
            degenerate_dropped += 1
            logger.warning(
                "Dropped extracted slot with blank value (frame=%r key=%r)",
                slot.frame_name,
                slot.key,
            )
            continue
        # Model output is untrusted input. `ExtractedSlot.key` is a free-form
        # string with no allowlist, and the `file_*` namespace is consumed as a
        # filesystem path by the /files endpoints -- so an extracted slot key is
        # a path primitive. See test_file_slot_reserved_keys.py.
        if slot.key.startswith(RESERVED_SLOT_PREFIXES):
            skipped_reserved += 1
            logger.warning(
                "Dropped extracted slot with reserved key %r on frame %r",
                slot.key,
                slot.frame_name,
            )
            continue
        frame_id = frame_ids[slot.frame_name]
        slot_source_type = source_type
        slot_reliability = source_reliability
        if (
            user_authoritative_identity
            and slot.frame_name == IDENTITY_FRAME
            and slot.key == IDENTITY_NAME_SLOT
        ):
            slot_source_type = "user"
            slot_reliability = IDENTITY_NAME_RELIABILITY
        stored_slot, conflict = await store.upsert_slot(
            frame_id=frame_id,
            key=slot.key,
            value=slot.value,
            source_episode_id=source_episode_id,
            source_type=slot_source_type,
            source_url=source_url,
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
                # The confidence the slot now carries, for the UI to annotate the
                # statement(s) that rest on it.
                "confidence": stored_slot.confidence,
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
        # Surfaced rather than dropped silently: a model that keeps emitting
        # `file_*` keys is misbehaving, and that should be visible in the
        # extraction summary instead of looking like "nothing was learned".
        "reserved_keys_skipped": skipped_reserved,
        # Same reasoning for blank names/keys/values: a model emitting degenerate
        # records should be visible in the summary, not look like a quiet turn.
        "degenerate_dropped": degenerate_dropped,
    }


async def apply_search_extraction(
    extraction: ExtractionResult,
    search_results: list["SearchResult"],
    store: "MemoryStore",
    embed_fn=None,
    backend_name: str = "searxng",
    embedding_model: str | None = None,
) -> dict:
    """Apply search extraction with corroboration support.

    Groups facts by (frame_name, key, value) to detect corroboration.
    Facts appearing in multiple independent sources get bumped source_reliability.
    Per-slot source_url is the corroborating URL (prefer .edu, Wikipedia, major news).
    High-stakes facts (financial, medical, legal, safety) require ≥2 unique domains.
    """
    # Names are never a web fact — drop any identity slots outright, for both the
    # assistant's frame and the user's.
    extraction.slots = [
        s
        for s in extraction.slots
        if s.frame_name not in (IDENTITY_FRAME, USER_IDENTITY_FRAME)
    ]
    if not extraction.slots and not extraction.associations:
        return {
            "slots_applied": 0,
            "associations_created": 0,
            "conflicts_created": 0,
            "frame_ids": [],
            "slots": [],
            "corroboration_status": {
                "high_stakes_checked": 0,
                "flagged_for_review": 0,
            },
        }

    fact_key_to_urls: dict[tuple, set[str]] = defaultdict(set)
    fact_key_to_domains: dict[tuple, set[str]] = defaultdict(set)
    for result in search_results:
        snippet_lower = result.snippet.lower()
        domain = urlparse(result.url).netloc if result.url else ""
        for slot in extraction.slots:
            if not slot.value:
                continue
            # Match on the value only. An empty or generic key would otherwise
            # match `"" in snippet` for every snippet, so a fact seen in zero
            # sources could still be scored as corroborated and clear the
            # high-stakes gate.
            if slot.value.lower() in snippet_lower:
                fact_key_to_urls[(slot.frame_name, slot.key, slot.value)].add(result.url)
                if domain:
                    fact_key_to_domains[(slot.frame_name, slot.key, slot.value)].add(domain)

    initial_reliability = (
        INITIAL_SEARCH_RELIABILITY + 0.15 if backend_name == "brave" else INITIAL_SEARCH_RELIABILITY
    )

    # Corroboration gate for high-stakes facts
    high_stakes_categories = {"financial", "medical", "legal", "safety", "security"}

    def _categorize_fact(frame_name: str, slot_key: str) -> str:
        # Tokenize on word boundaries. A substring test misfires badly here:
        # "rate" in "fender_stratocaster", "stock" in "stockholm", "law" in
        # "lawrence_fountain" all tagged musical/hobby frames as financial or
        # legal and clamped them to reduced reliability. A trailing "s" is also
        # folded so "prices" still matches "price".
        raw_tokens = re.split(r"[^a-z0-9]+", f"{frame_name} {slot_key}".lower())
        tokens = set(raw_tokens)
        for token in raw_tokens:
            if token.endswith("s") and len(token) > 3:
                tokens.add(token[:-1])

        def _has(keywords: list[str]) -> bool:
            return any(kw in tokens for kw in keywords)

        financial_kw = [
            "price", "cost", "revenue", "profit", "npv", "irr",
            "investment", "stock", "bond", "rate", "yield"
        ]
        if _has(financial_kw):
            return "financial"
        medical_kw = [
            "dose", "medication", "diagnosis", "symptom",
            "treatment", "drug", "therapy"
        ]
        if _has(medical_kw):
            return "medical"
        legal_kw = ["law", "regulation", "compliance", "contract", "liability", "statute"]
        if _has(legal_kw):
            return "legal"
        safety_kw = ["hazard", "danger", "warning", "recall", "toxic", "explosive", "flammable"]
        if _has(safety_kw):
            return "safety"
        return "general"

    reliability_map: dict[tuple, float] = {}
    corroboration_map: dict[tuple, dict] = {}
    for fact_key, urls in fact_key_to_urls.items():
        count = len(urls)
        domains = fact_key_to_domains.get(fact_key, set())
        unique_domains = len(domains)

        # Corroboration gate: high-stakes facts need ≥2 unique domains
        frame_name, slot_key, _ = fact_key
        category = _categorize_fact(frame_name, slot_key)
        needs_corroboration = category in high_stakes_categories and unique_domains < 2

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
        corroboration_map[fact_key] = {
            "unique_domains": unique_domains,
            "category": category,
            "needs_corroboration": needs_corroboration,
            "domains": list(domains),
        }

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

    # Same degenerate-record guard as apply_extraction: search extraction is model
    # output over fetched pages, both untrusted.
    deduped_slots, extraction.associations, degenerate_dropped = _drop_degenerate(
        deduped_slots, extraction.associations
    )

    # Populate per-slot source URLs and domains from corroboration matching
    for slot in deduped_slots:
        fact_key = (slot.frame_name, slot.key, slot.value)
        slot.source_urls = list(fact_key_to_urls.get(fact_key, set()))
        slot.source_domains = sorted(fact_key_to_domains.get(fact_key, set()))

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
            embedding_model=embedding_model,
            known=known,
        )

    slots_applied = 0
    conflicts_created = 0
    skipped_reserved = 0
    applied_slots: list[dict] = []
    for slot in deduped_slots:
        if slot.value is None:
            continue
        if _is_blank(slot.value):
            degenerate_dropped += 1
            logger.warning(
                "Dropped search-extracted slot with blank value (frame=%r key=%r)",
                slot.frame_name,
                slot.key,
            )
            continue
        # Same reserved-namespace guard as apply_extraction: search-derived facts
        # are model output too, and a fetched page is untrusted input.
        if slot.key.startswith(RESERVED_SLOT_PREFIXES):
            skipped_reserved += 1
            logger.warning(
                "Dropped search-extracted slot with reserved key %r on frame %r",
                slot.key,
                slot.frame_name,
            )
            continue
        frame_id = frame_ids[slot.frame_name]
        fact_key = (slot.frame_name, slot.key, slot.value)
        urls = fact_key_to_urls.get(fact_key, set())
        slot_url = _best_url(urls)
        slot_reliability = reliability_map.get(fact_key, initial_reliability)

        # Apply corroboration gate for high-stakes facts
        corrob_info = corroboration_map.get(fact_key, {})
        if corrob_info.get("needs_corroboration"):
            # Reduce confidence and flag for review
            slot_reliability = min(slot_reliability, 0.3)
            logger.warning(
                "High-stakes fact needs corroboration: %s.%s "
                "(domains=%d, category=%s)",
                slot.frame_name,
                slot.key,
                corrob_info.get("unique_domains", 0),
                corrob_info.get("category", "unknown"),
            )

        stored_slot, conflict = await store.upsert_slot(
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
                "confidence": stored_slot.confidence,
                "needs_corroboration": corrob_info.get("needs_corroboration", False),
                "corroboration_domains": corrob_info.get("unique_domains", 0),
                "corroboration_category": corrob_info.get("category", "general"),
                "source_urls": slot.source_urls,
                "source_domains": list(slot.source_domains),
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
        "reserved_keys_skipped": skipped_reserved,
        "degenerate_dropped": degenerate_dropped,
        "corroboration_status": {
            "high_stakes_checked": sum(1 for s in applied_slots if s.get("needs_corroboration")),
            "flagged_for_review": sum(1 for s in applied_slots if s.get("needs_corroboration")),
        },
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


# Words that are never a name on their own. The extractor has mined pronouns out
# of the user's own message ("you" landed on full_name on 2026-10-02) because
# `value_stated_by_user` only checks that the token appears in the message, and
# "you" appears in almost every message. A real name is not made entirely of
# these, so a full_name whose every token is a stopword is not a name.
_IDENTITY_NAME_STOPWORDS = frozenset(
    {
        "you", "your", "yours", "yourself", "me", "my", "mine", "myself",
        "i", "we", "us", "our", "ours", "it", "its", "itself",
        "the", "a", "an", "this", "that", "these", "those",
        "name", "assistant", "agent", "ai", "bot",
    }
)


def _is_name_like(value: str | None) -> bool:
    """False when a full_name value is only function words (pronouns, articles)."""
    tokens = _tokens(value or "")
    return bool(tokens) and not all(t in _IDENTITY_NAME_STOPWORDS for t in tokens)


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
            stated = value_stated_by_user(slot.value, user_message) and _is_name_like(
                slot.value
            )
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


def drop_user_duplicated_identity_slots(
    slots: list[ExtractedSlot],
) -> list[ExtractedSlot]:
    """Drop an identity_name.full_name the same turn also claims as the user's.

    A name cannot be both the user's and the assistant's. When the extractor
    emits the same value as `user_identity.full_name` and `identity_name.full_name`
    (measured: "My name is not Carl. I go by Boz."), the user's own claim wins and
    the assistant-side slot is discarded. This is a cross-check on the model's
    own output, not a phrasing rule.
    """
    user_names = {
        slot.value.strip().lower()
        for slot in slots
        if slot.frame_name == USER_IDENTITY_FRAME
        and slot.key == USER_IDENTITY_NAME_SLOT
        and slot.value
    }
    if not user_names:
        return slots
    return [
        slot
        for slot in slots
        if not (
            slot.frame_name == IDENTITY_FRAME
            and slot.key == IDENTITY_NAME_SLOT
            and slot.value
            and slot.value.strip().lower() in user_names
        )
    ]


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
        extraction.slots = drop_user_duplicated_identity_slots(extraction.slots)
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
            extraction,
            store,
            source_episode_id,
            embed_fn=get_embedding,
            embedding_model=llm_client.embedding_model,
            user_authoritative_identity=True,
        )
        if result.get("frame_ids"):
            # Must be the model that produced the vectors. Omitting it wrote
            # qwen3 vectors under the nomic-embed-text label, which made every
            # search for this frame miss -- see the embedding_model split-brain
            # in docs/EMBEDDING_MODEL_NOTES.md.
            await store.embed_frames(
                result["frame_ids"], get_embedding, llm_client.embedding_model
            )
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
        results, _ = await search_tool.search(query, num_results=5)
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
            parsed = json.loads(response.content)
            if not isinstance(parsed, dict):
                # A non-object (e.g. a bare list or string) would parse fine but
                # crash the caller at `parsed.get(...)`, outside this retry loop.
                raise ValueError(
                    f"scheduled task JSON must be an object, got {type(parsed).__name__}"
                )
            return parsed
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Scheduled task parse failed (attempt %d): %s", attempt + 1, e)
            if attempt == 0:
                extra = "\n\nIMPORTANT: Output ONLY valid JSON. No markdown, no preamble."
                system = ChatMessage(role="system", content=system.content + extra)
            else:
                raise


async def apply_correction(
    correction: CorrectionResult,
    store: "MemoryStore",
    source_episode_id: int | None = None,
    embed_fn=None,
    embedding_model: str | None = None,
) -> dict:
    """Apply a user correction to the store.

    The corrected slot is given high source_reliability (0.9) since it comes from the user.
    A conflict may be created if the existing value differs.

    `embed_fn` and `embedding_model` re-index the corrected frame. A correction
    changes a value without changing how many slots the frame has, so the
    count-based staleness check cannot see it -- only the write path can. Without
    this the frame keeps a vector for the value the user just rejected, and
    search keeps surfacing it by that rejected value. Both are required together:
    a vector written under an unverified model label is worse than none.
    """
    if (
        _is_blank(correction.frame_name)
        or _is_blank(correction.slot_key)
        or _is_blank(correction.new_value)
    ):
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
        source_reliability=(
            IDENTITY_NAME_RELIABILITY
            if correction.frame_name == IDENTITY_FRAME
            and correction.slot_key == IDENTITY_NAME_SLOT
            else 0.9
        ),
    )

    if embed_fn is not None and embedding_model is not None:
        try:
            await store.embed_frames([frame.id], embed_fn, embedding_model)
        except Exception as e:
            logger.warning("Embedding refresh after correction failed: %s", e)

    return {
        "slots_corrected": 1,
        "frame_name": correction.frame_name,
        "slot_key": correction.slot_key,
        "new_value": correction.new_value,
        "conflict": conflict is not None,
    }
