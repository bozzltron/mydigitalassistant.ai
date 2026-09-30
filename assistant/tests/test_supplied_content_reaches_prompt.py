"""Regression: supplied content must reach the prompt, not just memory.

Plan A Phase 2, second attempt. The first attempt fixed the *router* — clearing
`plan.search_needed` when a turn supplied content — and passed its tests. It did not
work in production.

Measured on the live backend, 2026-09-30, with the real 7-URL paste:

    Registered user-supplied content: frame=4546 name=manual_submission_links
    context_stats: prompt_chars=11988 frames=10 search_results=0 truncated=False
    tool loop: web_search("WPRB music submission impact indie rock Austin")
               web_search("WKDU radio station college format reach Austin")
               web_search("Rock Rage Radio submission indie rock coverage")

The content was registered and then never seen. Retrieval runs *concurrently* with
registration, so a frame written during this turn is never among the frames
retrieval returns — the model held the list in memory and had nothing to answer
from, so it searched each link instead. The answer was an essay about one station.

The first attempt's tests asserted on the routing flag, which was genuinely cleared
and therefore passed while the bug was untouched. These tests assert on the two
things that actually decide the outcome:

1. the assembled system prompt contains the content, in the order given;
2. the tool loop is not the only thing the turn can answer from.

The routing change is kept — it is correct and prevents search from being *planned*
— but it was necessary and not sufficient.
"""

from __future__ import annotations

import pytest

from assistant.backend.pipeline.user_content import detect_user_content

PASTE = """Here are all my manual submission links. Let's search about them if we need
to and rank them by which would be most impactful.

https://wprb.com/contact/executive-director/
https://wprb.com/contact/music-submissions/
https://wkdu.org/contact
https://bookingagentinfo.com/indie-music-blogs-contact-list/
https://atxlibrary.musicat.co/submit
https://www.rockrageradio.com/
https://www.kgpc969.org/music-submission
"""


async def _orchestrator(store, stub_llm, stub_search):
    from assistant.backend.memory.retrieval import Retriever
    from assistant.backend.pipeline.orchestrator import Orchestrator, OrchestratorDeps

    retriever = Retriever(store=store, llm_client=stub_llm)
    return Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=stub_llm,
            search_tool=stub_search,
        )
    )


class TestSuppliedContentReachesThePrompt:
    """The fix: what the user supplied this turn is in the prompt, in order."""

    @pytest.mark.asyncio
    async def test_supplied_content_is_injected_in_order(self, store):
        """The list must be in the prompt, whole, in the order given."""
        from assistant.backend.pipeline.orchestrator import Orchestrator

        user = await store.create_user("alice")
        episode = await store.create_episode(
            user_id=user.id, session_id="s1", role="user", content=PASTE
        )

        class _StubLLM:
            embedding_model = "stub"

            async def embed(self, text):
                from assistant.backend.pipeline.llm_client import EmbeddingResponse

                return EmbeddingResponse(embedding=[0.0] * 1024, model="stub")

            async def chat(self, *a, **kw):
                from assistant.backend.pipeline.llm_client import ChatResponse

                return ChatResponse(content="", model="stub")

        from assistant.backend.pipeline.orchestrator import store_turn_memory

        summ = await store_turn_memory(
            user_message=PASTE,
            assistant_response="",
            store=store,
            llm_client=_StubLLM(),
            source_episode_id=episode.id,
        )
        supplied = summ.get("user_content")
        assert supplied, "the paste was not registered"

        orch = Orchestrator.__new__(Orchestrator)
        orch.store = store
        rendered = await orch._render_supplied_content("PREFIX", summ)

        urls = detect_user_content(PASTE).urls
        # Every URL present, and in the order the user gave them.
        positions = [rendered.index(u) for u in urls]
        assert positions == sorted(positions), "URL order was not preserved"
        # And the instruction that makes it the subject.
        assert "subject of the request" in rendered
        assert "in the order given" in rendered

    @pytest.mark.asyncio
    async def test_nothing_injected_when_no_content_supplied(self, store):
        from assistant.backend.pipeline.orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch.store = store
        rendered = await orch._render_supplied_content("PREFIX", {})
        assert rendered == "PREFIX"
        rendered = await orch._render_supplied_content("PREFIX", {"user_content": None})
        assert rendered == "PREFIX"

    @pytest.mark.asyncio
    async def test_full_content_not_truncated(self, store):
        """A preview would silently change a ranking or sort answer."""
        from assistant.backend.pipeline.orchestrator import Orchestrator
        from assistant.backend.pipeline.user_content import register_user_content

        d = detect_user_content(PASTE)
        assert d is not None
        out = await register_user_content(d, store)
        summ = {"user_content": out}

        orch = Orchestrator.__new__(Orchestrator)
        orch.store = store
        rendered = await orch._render_supplied_content("PREFIX", summ)

        # Every one of the 7 URLs present, none elided.
        assert rendered.count("https://") == len(d.urls)


class TestChatWithPasteProducesTheList:
    """End to end through chat(): the decisive assertion is that the model was
    given the user's list, which is what the live failure lacked."""

    @pytest.mark.asyncio
    async def test_prompt_carrying_the_list_is_assembled(self, store, stub_llm, stub_search):
        from assistant.backend.pipeline.orchestrator import ChatRequest

        orch = await _orchestrator(store, stub_llm, stub_search)
        user = await store.create_user("alice")
        resp = await orch.chat(
            ChatRequest(message=PASTE, user_id=user.id, session_id="s_paste_e2e")
        )

        assert resp.response
        # Every system prompt the turn issued, so the check does not depend on
        # which call happened to be first.
        prompts = "\n\n".join(stub_llm.system_prompts)
        assert "Content the user supplied this turn" in prompts, (
            "the supplied content never reached any prompt the turn issued"
        )
        assert "https://wprb.com/contact/executive-director/" in prompts

    @pytest.mark.asyncio
    async def test_episode_is_linked_to_the_content_frame(self, store, stub_llm, stub_search):
        """`episode 2653.frame_ids` was `[]`. It must not be, now."""
        from assistant.backend.pipeline.orchestrator import ChatRequest

        orch = await _orchestrator(store, stub_llm, stub_search)
        user = await store.create_user("alice")
        await orch.chat(
            ChatRequest(message=PASTE, user_id=user.id, session_id="s_paste_link")
        )

        episodes = await store.get_episodes_for_session("s_paste_link", user_id=user.id)
        user_turns = [e for e in episodes if e.role == "user"]
        assert user_turns, "no user episode recorded"
        frame_ids = user_turns[0].frame_ids
        assert frame_ids and frame_ids != "[]", (
            f"the paste turn stored no frame link: {frame_ids!r}"
        )

    @pytest.mark.asyncio
    async def test_plain_turn_gets_no_content_section(self, store, stub_llm, stub_search):
        """The injection must be specific to supplied content."""
        from assistant.backend.pipeline.orchestrator import ChatRequest

        orch = await _orchestrator(store, stub_llm, stub_search)
        user = await store.create_user("alice")
        await orch.chat(
            ChatRequest(
                message="what is the capital of France?",
                user_id=user.id,
                session_id="s_plain_e2e",
            )
        )
        prompts = "\n\n".join(stub_llm.system_prompts)
        assert "Content the user supplied this turn" not in prompts
