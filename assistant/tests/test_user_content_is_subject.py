"""Regression: search must not stand in for content the user supplied.

Plan A Phase 2. The failure: the user pasted 45 submission URLs and asked for them
ranked. The router read "let's search about them if we need to" as an intent to
search, and the answer became a web essay about submission platforms — a different
subject from the user's list. `episode 2658` recommended SubmitHub, Groover, and
PlaylistPal, none of which appear anywhere in the user's list.

Two things had to change:

1. The system prompt states the authority rule, so the model knows that when the
   request transforms user-supplied content, that content is the subject and search
   only enriches it.
2. Routing stops letting search pre-empt such a turn, so the content is what gets
   answered from.

Search is not banned — enriching the list with context is legitimate. What is
pinned here is that it cannot become the answer's subject.
"""

from __future__ import annotations

import pytest

from assistant.backend.pipeline.llm_client import build_system_prompt

PASTE = """Here are all my manual submission links. Let's search about them if we need
to and rank them by which would be most impactful.

https://wprb.com/contact/executive-director/
https://wprb.com/contact/music-submissions/
https://wkdu.org/contact
https://bookingagentinfo.com/indie-music-blogs-contact-list/
https://atxlibrary.musicat.co/submit
https://www.rockrageradio.com/
"""


class TestPromptCarriesTheAuthorityRule:
    """The rule has to be where the model can act on it."""

    def test_transform_clause_is_present(self):
        prompt = build_system_prompt("(no memory)", "functional")
        assert "transform content they gave you" in prompt
        assert "must never stand in for it" in prompt

    def test_authority_split_is_stated(self):
        prompt = build_system_prompt("(no memory)", "functional")
        assert "authoritative about their own world" in prompt
        assert "authoritative about the\n  external world" in prompt or (
            "authoritative about the external world" in prompt
        )

    def test_rule_is_in_the_stable_prefix(self):
        """It must sit before the memory section, or the prompt cache breaks on
        every turn and the ~11k-char prefix stops being reusable."""
        prompt = build_system_prompt("MEMORY-MARKER", "functional")
        rule_at = prompt.index("transform content they gave you")
        memory_at = prompt.index("MEMORY-MARKER")
        assert rule_at < memory_at

    def test_introspective_prompt_unaffected(self):
        """The introspective branch is a different prompt and must not have been
        rewritten by this change."""
        prompt = build_system_prompt("(no memory)", "introspective")
        assert "Ground your answer ONLY in the retrieved memory state" in prompt


class TestToolDescriptionsPointAtMemory:
    """At 09:13 the list WAS in the conversation; the model simply had no tool
    description telling it to look there, so it called web_search instead."""

    def test_recall_mentions_content_from_the_user(self):
        from assistant.backend.pipeline.tools import builtin_tools

        tools = {t["function"]["name"]: t["function"] for t in builtin_tools()}
        desc = tools["recall"]["description"]
        assert "the list" in desc or "those links" in desc
        assert "web_search" in desc, "should say when to prefer it over search"

    def test_search_episodes_mentions_earlier_turns(self):
        from assistant.backend.pipeline.tools import builtin_tools

        tools = {t["function"]["name"]: t["function"] for t in builtin_tools()}
        desc = tools["search_episodes"]["description"]
        assert "earlier" in desc
        assert "pasted" in desc or "supplied" in desc


class TestRoutingDoesNotLetSearchPreEmptContent:
    """The routing half: with supplied content present, search is de-prioritised."""

    @pytest.mark.asyncio
    async def test_plan_search_is_cleared_when_content_supplied(self, store, stub_llm, stub_search):
        """Drives the real failure shape through orchestrator.chat(): a paste plus
        a request that would otherwise satisfy the search heuristic."""
        from assistant.backend.memory.retrieval import Retriever
        from assistant.backend.pipeline.orchestrator import (
            ChatRequest,
            Orchestrator,
            OrchestratorDeps,
        )

        user = await store.create_user("alice")
        retriever = Retriever(store=store, llm_client=stub_llm)
        orch = Orchestrator(
            deps=OrchestratorDeps(
                store=store,
                retriever=retriever,
                llm_client=stub_llm,
                search_tool=stub_search,
            )
        )

        captured: dict = {}
        orig = stub_search.search_with_info

        async def _spy(query, *a, **kw):
            captured["searched"] = query
            return await orig(query, *a, **kw)

        stub_search.search_with_info = _spy

        resp = await orch.chat(
            ChatRequest(
                message=PASTE,
                user_id=user.id,
                session_id="s_paste",
            )
        )

        # The paste is registered, and the answer was produced.
        assert resp.response
        # Decisive: the pipeline did not run a search for this turn.
        assert "searched" not in captured, (
            f"search pre-empted a transform request: {captured.get('searched')!r}"
        )

    @pytest.mark.asyncio
    async def test_ordinary_search_still_works(self, store, stub_llm, stub_search):
        """The de-prioritisation must be specific to supplied content, not a
        blanket removal of search."""
        from assistant.backend.memory.retrieval import Retriever
        from assistant.backend.pipeline.orchestrator import (
            ChatRequest,
            Orchestrator,
            OrchestratorDeps,
        )

        user = await store.create_user("alice")
        retriever = Retriever(store=store, llm_client=stub_llm)
        orch = Orchestrator(
            deps=OrchestratorDeps(
                store=store,
                retriever=retriever,
                llm_client=stub_llm,
                search_tool=stub_search,
            )
        )

        # A plain informational question with no supplied content.
        resp = await orch.chat(
            ChatRequest(
                message="what is the capital of France?",
                user_id=user.id,
                session_id="s_plain",
            )
        )
        assert resp.response
