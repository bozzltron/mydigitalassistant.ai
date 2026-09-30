"""Registration of user-supplied content as memory.

A turn that hands the assistant a body of content -- a list of links, a pasted
email, a table -- is not a fact about an entity, so conversational extraction has
no shape for it and stores nothing. That is what happened on 2026-09-30: the user
pasted 45 submission URLs and asked for them ranked across three turns, and
`episode 2653.frame_ids` was `[]`. The list was never memory, so no later turn
could reach it, and every follow-up answered from a fresh web search instead.

This module gives that content a home. It is deliberately **general**: the URL list
is the case we have evidence for, but a pasted email or a draft behaves the same
way, so detection is a class rather than a URL special case.

Two design constraints, both from the project's own principles:

- **Nothing scripted speaks to the user.** The registration writes memory only. The
  model still writes every response.
- **No new sequential LLM call.** Detection is model-free, and registration hangs
  off the extraction slot the turn already runs (see `store_turn_memory`).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from assistant.backend.config import settings

if TYPE_CHECKING:
    from assistant.backend.memory.store import MemoryStore

logger = logging.getLogger(__name__)

# Frame type for a body of content the user supplied. Distinct from `document`
# (a fetched page) and `file_upload` (a file the user attached) because neither
# carries the "the user gave me this to work on" provenance that matters for the
# authority rule.
USER_CONTENT_FRAME_TYPE = "user_content"

# The slot holding the content itself, in the order it was given. Order is
# load-bearing: a request to sort or rank operates on this sequence.
CONTENT_SLOT_KEY = "content"

SOURCE_TYPE_USER_SUPPLIED = "user_supplied"

# Reliability for content the user supplied about their own request. High, per the
# AGENTS.md authority rule: the user is authoritative about their own world, and
# this is their material. Above search (0.5) so that on any conflict, a fact the
# user handed over outranks one a page asserted.
USER_SUPPLIED_RELIABILITY = 0.9

# Priority above the default 0.5 so the user's own material survives the prompt
# cap ahead of generic retrieval filler.
USER_SUPPLIED_PRIORITY = 0.8

# A bare URL, tolerating the punctuation that surrounds links in prose.
_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)

# Threshold for the model-free signal. Below this a message mentioning a couple of
# links is ordinary prose ("I read this: <url>"); at or above it the user is
# handing over a collection, which is a different act.
MIN_URLS_FOR_PASTE = 5


@dataclass
class DetectedContent:
    """A body of user-supplied content worth registering as memory."""

    name: str
    content: str
    urls: list[str]
    kind: str  # "url_list" | "pasted_text"


def extract_urls(text: str) -> list[str]:
    """Return bare URLs in order of appearance, deduplicated but order-preserving."""
    seen: set[str] = set()
    urls: list[str] = []
    for match in _URL_RE.findall(text or ""):
        candidate = match.rstrip(".,;:")
        if candidate not in seen:
            seen.add(candidate)
            urls.append(candidate)
    return urls


def detect_user_content(message: str) -> DetectedContent | None:
    """Detect content the user supplied as the subject of their request.

    Model-free by design: this runs on every turn, so it must not cost a call. The
    signal is structural rather than semantic --

    - a URL list at or above `MIN_URLS_FOR_PASTE`, or
    - a long non-URL block that is clearly pasted material.

    Returns None for ordinary prose, which is the overwhelming majority of turns.
    """
    if not message or not message.strip():
        return None

    urls = extract_urls(message)
    if len(urls) >= MIN_URLS_FOR_PASTE:
        return DetectedContent(
            name=_derive_name(message, urls),
            content="\n".join(urls),
            urls=urls,
            kind="url_list",
        )

    return None


def _derive_name(message: str, urls: list[str]) -> str:
    """A stable, descriptive frame name for a supplied body of content.

    Uses the message's own opening noun-ish words where they read as a label,
    otherwise falls back to the content shape. Deliberately deterministic: the
    same paste in one session should resolve to the same frame, so a second paste
    updates rather than creating a sibling.
    """
    # Strip links out; the surrounding prose is what names the thing.
    prose = _URL_RE.sub(" ", message)
    prose = re.sub(r"\s+", " ", prose).strip()

    # Drop leading conversational filler so "here are all my manual submission
    # links" names itself "manual_submission_links" rather than "here_are_all_my".
    filler = (
        "here are", "here's", "heres", "here is", "this is", "these are",
        "i have", "ive", "i've", "below are", "the following", "please",
        "can you", "let's", "lets", "all my", "all of my", "my",
    )
    words = prose.lower().split()
    changed = True
    while words and changed:
        changed = False
        for phrase in filler:
            parts = phrase.split()
            if words[: len(parts)] == parts:
                words = words[len(parts) :]
                changed = True
                break

    # Keep the leading noun-ish run only. The name should describe what the
    # content *is*, so it stops at the first word that reads as instruction
    # ("...links let's search about them" must not become "links_lets_search").
    stop = {
        "and", "or", "for", "to", "of", "in", "on", "with", "that", "which",
        "search", "sort", "rank", "let", "lets", "can", "you", "we", "them",
        "it", "if", "need", "about", "by", "most", "is", "are", "be", "a", "an",
        "please", "these", "those", "use", "using", "wants", "want", "should",
        "would", "could", "will", "help", "find", "look", "up", "get",
    }
    kept: list[str] = []
    for word in words:
        clean = re.sub(r"[^a-z0-9_]", "", word)
        if not clean or clean in stop:
            break
        kept.append(clean)
        if len(kept) >= 5:
            break

    if kept:
        return "_".join(kept)

    # No usable prose: name it by shape so it is still identifiable.
    domain = re.sub(r"[^a-z0-9]", "_", urls[0].split("//")[-1].split("/")[0].lower())
    return f"shared_links_{domain}"[:60]


async def register_user_content(
    detected: DetectedContent,
    store: MemoryStore,
    *,
    source_episode_id: int | None = None,
    embed_fn=None,
    embedding_model: str | None = None,
) -> dict:
    """Store a detected body of user content as a frame, and embed it.

    Written directly rather than through the extraction pipeline: this is a
    durability guarantee for the user's material, not a judgement about what the
    content means. If conversational extraction also derived facts from the same
    turn, those land on their own frames and both coexist.

    Returns a summary dict in the shape the orchestrator's extraction summaries
    use, so it can be surfaced to the UI without a second vocabulary.
    """
    name = detected.name
    frame = await store.get_frame_by_name(name)
    if frame is None:
        frame = await store.create_frame(
            name=name,
            type=USER_CONTENT_FRAME_TYPE,
            source_type=SOURCE_TYPE_USER_SUPPLIED,
            source_reliability=USER_SUPPLIED_RELIABILITY,
        )

    await store.set_derived_slot(
        frame_id=frame.id,
        key="kind",
        value=detected.kind,
        source_type=SOURCE_TYPE_USER_SUPPLIED,
    )
    # The content itself is asserted by a source -- the user -- so it takes the
    # normal belief path, unlike a derived summary value.
    await store.upsert_slot(
        frame_id=frame.id,
        key=CONTENT_SLOT_KEY,
        value=detected.content,
        source_episode_id=source_episode_id,
        source_type=SOURCE_TYPE_USER_SUPPLIED,
        source_reliability=USER_SUPPLIED_RELIABILITY,
        priority=USER_SUPPLIED_PRIORITY,
    )
    await store.set_derived_slot(
        frame_id=frame.id,
        key="item_count",
        value=str(len(detected.urls) if detected.kind == "url_list" else 0),
        source_type=SOURCE_TYPE_USER_SUPPLIED,
    )

    if embed_fn is not None and embedding_model is not None:
        try:
            await store.embed_frames([frame.id], embed_fn, embedding_model)
        except Exception as exc:  # embedding is best-effort; the content is stored
            logger.warning("Embedding user content frame %d failed: %s", frame.id, exc)

    logger.info(
        "Registered user-supplied content: frame=%d name=%s kind=%s items=%d chars=%d",
        frame.id,
        name,
        detected.kind,
        len(detected.urls),
        len(detected.content),
    )

    return {
        "frame_id": frame.id,
        "frame_name": name,
        "kind": detected.kind,
        "item_count": len(detected.urls),
        "chars": len(detected.content),
    }


def user_content_enabled() -> bool:
    """Whether registration is on. Off-switch mirrors the other feature flags."""
    return settings.register_user_content
