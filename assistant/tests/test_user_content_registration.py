"""Regression: content the user supplies must become memory.

On 2026-09-30 the user pasted 45 submission URLs and asked, across three turns, for
them ranked and sorted. `episode 2653.frame_ids` was `[]` — a 3.5 kB first-person
artefact produced no memory at all, so no later turn could reach it, and each
follow-up answered from a fresh web search instead of from the user's own list.

The cause is structural, not a one-off. A body of supplied content is not a fact
about an entity, so `EXTRACTION_PROMPT` has no shape for it and extraction stores
nothing. Nothing in the pipeline treated *user-supplied content that is the subject
of the request* as memory.

These tests pin the fix, and the boundary that keeps it from misfiring:

- a URL list at or above the threshold registers as one frame, order preserved;
- prose mentioning a link or two does NOT register (the common case);
- the frame carries user-supplied provenance, so the authority rule can use it;
- registration is keyed on a stable name, so a re-paste updates rather than
  creating a sibling;
- the episode is linked to the frame, which is what makes it reachable next turn.
"""

from __future__ import annotations

import pytest

from assistant.backend.pipeline.user_content import (
    CONTENT_SLOT_KEY,
    MIN_URLS_FOR_PASTE,
    SOURCE_TYPE_USER_SUPPLIED,
    USER_CONTENT_FRAME_TYPE,
    detect_user_content,
    extract_urls,
    register_user_content,
)

# The real message, trimmed to its shape: prose, then a block of links.
REAL_PASTE = """Here are all my manual submission links. Let's search about them if
we need to and rank them by which would be most impactful to mozworth.

https://wprb.com/contact/executive-director/
https://wprb.com/contact/music-submissions/
https://wkdu.org/contact
https://bookingagentinfo.com/indie-music-blogs-contact-list/
https://atxlibrary.musicat.co/submit
https://www.rockrageradio.com/
https://www.kgpc969.org/music-submission
"""


def _urls(n: int, host: str = "example") -> str:
    return "\n".join(f"https://{host}{i}.com/x" for i in range(n))


class TestDetection:
    """Model-free signal, so it can run on every turn."""

    def test_real_paste_is_detected(self):
        d = detect_user_content(REAL_PASTE)
        assert d is not None
        assert d.kind == "url_list"
        assert len(d.urls) == 7

    def test_order_is_preserved(self):
        """A ranking request operates on the sequence the user gave."""
        d = detect_user_content(REAL_PASTE)
        assert d is not None
        assert d.urls == extract_urls(REAL_PASTE)
        assert d.urls[0] == "https://wprb.com/contact/executive-director/"

    def test_threshold_is_exact(self):
        just_under = f"links:\n{_urls(MIN_URLS_FOR_PASTE - 1)}"
        at_threshold = f"links:\n{_urls(MIN_URLS_FOR_PASTE)}"
        assert detect_user_content(just_under) is None
        assert detect_user_content(at_threshold) is not None

    @pytest.mark.parametrize(
        "message",
        [
            "I read this article: https://example.com/a and liked it.",
            "what do you remember about my guitar?",
            "hey how are you",
            "",
            "   ",
            f"{_urls(3)}",
        ],
    )
    def test_ordinary_prose_is_not_detected(self, message):
        assert detect_user_content(message) is None

    def test_trailing_punctuation_is_stripped(self):
        urls = extract_urls("see https://example.com/a, and https://example.com/b.")
        assert urls == ["https://example.com/a", "https://example.com/b"]

    def test_duplicates_collapse(self):
        """A list with a repeated link is still one item per unique URL."""
        msg = _urls(6) + "\nhttps://example0.com/x"
        d = detect_user_content(msg)
        assert d is not None
        assert len(d.urls) == 6
        assert len(d.urls) == len(set(d.urls))


class TestNaming:
    """The name decides whether a re-paste updates or duplicates."""

    def test_name_describes_the_content(self):
        d = detect_user_content(REAL_PASTE)
        assert d is not None
        assert d.name == "manual_submission_links"

    def test_name_is_stable_across_phrasings(self):
        """Same content described two ways must resolve to one frame, or a second
        paste creates a sibling and the user's list forks."""
        a = detect_user_content(
            "Here are all my manual submission links. Let's rank them.\n" + _urls(6, "a")
        )
        b = detect_user_content("Here are all my manual submission links.\n" + _urls(8, "b"))
        assert a is not None and b is not None
        assert a.name == b.name

    def test_name_stops_at_instruction_words(self):
        d = detect_user_content("these are my favourite pizza places please rank them\n" + _urls(5))
        assert d is not None
        assert d.name == "favourite_pizza_places"


class TestRegistration:
    """What lands in memory, and with what provenance."""

    @pytest.mark.asyncio
    async def test_content_is_stored_in_order(self, store):
        d = detect_user_content(REAL_PASTE)
        assert d is not None
        out = await register_user_content(d, store)

        frame = await store.get_frame(out["frame_id"])
        assert frame is not None
        slots = {s.key: s.value for s in await store.get_slots_for_frame(frame.id)}
        # Every URL, in the order given.
        assert slots[CONTENT_SLOT_KEY].split("\n") == d.urls

    @pytest.mark.asyncio
    async def test_frame_carries_user_supplied_provenance(self, store):
        """The authority rule needs to know this came from the user, not the web."""
        d = detect_user_content(REAL_PASTE)
        assert d is not None
        out = await register_user_content(d, store)

        frame = await store.get_frame(out["frame_id"])
        assert frame is not None
        assert frame.type == USER_CONTENT_FRAME_TYPE
        assert frame.source_type == SOURCE_TYPE_USER_SUPPLIED

        slots = await store.get_slots_for_frame(frame.id)
        content_slot = next(s for s in slots if s.key == CONTENT_SLOT_KEY)
        assert content_slot.source_type == SOURCE_TYPE_USER_SUPPLIED
        # High reliability: user-stated outranks search (0.5) on any conflict.
        assert (content_slot.source_reliability or 0) > 0.5
        # And above default priority, so it survives the prompt cap.
        assert content_slot.priority > 0.5

    @pytest.mark.asyncio
    async def test_content_slot_records_its_source_episode(self, store):
        """The slot must point back at the turn that supplied it, so the content
        is auditable to the user's own message."""
        user = await store.create_user("alice")
        episode = await store.create_episode(
            user_id=user.id, session_id="s1", role="user", content=REAL_PASTE
        )
        d = detect_user_content(REAL_PASTE)
        assert d is not None
        out = await register_user_content(d, store, source_episode_id=episode.id)

        slots = await store.get_slots_for_frame(out["frame_id"])
        content = next(s for s in slots if s.key == CONTENT_SLOT_KEY)
        assert content.source_episode_id == episode.id

    @pytest.mark.asyncio
    async def test_turn_memory_links_episode_to_the_frame(self, store, stub_llm):
        """The registration is only reachable on a later turn if the episode is
        linked to the frame. That is what made the original failure
        unrecoverable -- `episode 2653.frame_ids` was `[]`."""
        from assistant.backend.pipeline.orchestrator import store_turn_memory

        user = await store.create_user("alice")
        episode = await store.create_episode(
            user_id=user.id, session_id="s1", role="user", content=REAL_PASTE
        )

        result = await store_turn_memory(
            user_message=REAL_PASTE,
            assistant_response="",
            store=store,
            llm_client=stub_llm,
            source_episode_id=episode.id,
        )

        assert result.get("user_content"), "the paste was not registered"
        frame_id = result["user_content"]["frame_id"]
        episodes = await store.get_episodes_for_frames([frame_id])
        assert any(e.id == episode.id for e in episodes), (
            "the episode was not linked to the content frame"
        )

    @pytest.mark.asyncio
    async def test_repaste_updates_the_same_frame(self, store):
        d1 = detect_user_content(REAL_PASTE)
        d2 = detect_user_content(REAL_PASTE + "https://new.example.com/x\n")
        assert d1 is not None and d2 is not None

        a = await register_user_content(d1, store)
        b = await register_user_content(d2, store)

        assert a["frame_id"] == b["frame_id"], "a second paste forked the frame"
        slots = await store.get_slots_for_frame(a["frame_id"])
        content = next(s for s in slots if s.key == CONTENT_SLOT_KEY)
        assert "https://new.example.com/x" in content.value

    @pytest.mark.asyncio
    async def test_registration_does_not_conflict(self, store):
        """A re-paste of the same list must not register as a dispute."""
        d = detect_user_content(REAL_PASTE)
        assert d is not None
        out = await register_user_content(d, store)
        await register_user_content(d, store)
        conflicts = await store.get_conflicts_for_frame(out["frame_id"])
        assert conflicts == []
