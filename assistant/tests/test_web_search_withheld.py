"""Regression: withhold `web_search` when the turn transforms supplied content.

The third attempt at this, and the first two are worth recording because they show
what the measurement has to be.

1. **Routing fix** — cleared `plan.search_needed`. Correct, verified (`search_ms=0`
   in production) and *insufficient*: the model then called `web_search` itself
   inside the tool loop.
2. **Prompt injection** — put the supplied content into the system prompt in full,
   with an instruction to work from it. Verified present on both paths. The live
   turn still searched, because a tool description competes with a prompt line.
3. **This** — remove the tool. The system already knows the turn supplied content
   and that the request is to transform it; withholding a tool that pre-empts that
   decision is a routing choice, not a prompt plea.

`fetch_url` is deliberately kept: fetching a link the user actually handed over is
working from their content, not replacing it.

Measured context: `experiments/decision_routing_value` showed the chat model routes
all three `answer_from_content` turns to search, while every decision model tested
routed them correctly. Withholding the tool reaches the same routing decision with
no second model and no added latency.
"""

from __future__ import annotations

import pytest

from assistant.backend.pipeline.user_content import CONTENT_SLOT_KEY, detect_user_content

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


async def _orch(store, stub_llm, stub_search):
    from assistant.backend.memory.retrieval import Retriever
    from assistant.backend.pipeline.orchestrator import Orchestrator, OrchestratorDeps

    return Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=Retriever(store=store, llm_client=stub_llm),
            llm_client=stub_llm,
            search_tool=stub_search,
        )
    )


async def _enabled_search():
    """A search tool that reports enabled, so `web_search` is actually offered.

    The default test fixture is `WebSearchTool(enabled=False)`, and `builtin_tools`
    gates `web_search` on that flag — so in a default test the tool is absent for
    every turn and a content-based withholding cannot be observed. Search must be
    enabled for the gate under test to be the only variable.
    """
    from assistant.backend.pipeline.search import WebSearchTool

    tool = WebSearchTool(enabled=True)

    async def _no_results(query, *a, **kw):
        return [], None

    tool.search_with_info = _no_results  # type: ignore[method-assign]
    return tool


class TestToolListOmitsSearchForTransformTurns:
    """The mechanism, asserted where it is decided."""

    @pytest.mark.asyncio
    async def test_web_search_withheld_on_a_paste_turn(self, store, stub_llm):
        from assistant.backend.pipeline.orchestrator import ChatRequest

        orch = await _orch(store, stub_llm, await _enabled_search())
        user = await store.create_user("alice")

        captured: dict = {}
        import assistant.backend.pipeline.orchestrator as mod

        orig = mod.run_tool_loop

        async def _spy(*args, **kwargs):
            tools = args[2] if len(args) > 2 else kwargs.get("tools")
            captured["tools"] = [t["function"]["name"] for t in tools]
            return await orig(*args, **kwargs)

        mod.run_tool_loop = _spy
        try:
            await orch.chat(
                ChatRequest(message=PASTE, user_id=user.id, session_id="s_withhold")
            )
        finally:
            mod.run_tool_loop = orig

        names = captured.get("tools")
        assert names is not None, "the tool loop was never reached"
        assert "web_search" not in names, (
            f"web_search was offered on a transform turn: {names}"
        )
        # fetch_url stays: fetching a link the user supplied is working from their
        # content, not replacing it.
        assert "fetch_url" in names
        # And the memory tools remain, so the model can still look things up.
        assert "recall" in names

    @pytest.mark.asyncio
    async def test_web_search_kept_on_an_ordinary_turn(self, store, stub_llm):
        """The withholding must be specific to supplied content. With search
        enabled, an ordinary turn must still be offered `web_search`."""
        from assistant.backend.pipeline.orchestrator import ChatRequest

        orch = await _orch(store, stub_llm, await _enabled_search())
        user = await store.create_user("alice")

        captured: dict = {}
        import assistant.backend.pipeline.orchestrator as mod

        orig = mod.run_tool_loop

        async def _spy(*args, **kwargs):
            tools = args[2] if len(args) > 2 else kwargs.get("tools")
            captured["tools"] = [t["function"]["name"] for t in tools]
            return await orig(*args, **kwargs)

        mod.run_tool_loop = _spy
        try:
            await orch.chat(
                ChatRequest(
                    message="search for austin music festivals",
                    user_id=user.id,
                    session_id="s_ordinary",
                )
            )
        finally:
            mod.run_tool_loop = orig

        names = captured.get("tools")
        assert names is not None
        assert "web_search" in names, (
            "web_search was withheld from a turn that did not supply content"
        )

    @pytest.mark.asyncio
    async def test_five_urls_is_the_boundary(self, store, stub_llm):
        """Four URLs is prose; five is a list. The tool list must follow."""
        from assistant.backend.pipeline.orchestrator import ChatRequest

        orch = await _orch(store, stub_llm, await _enabled_search())
        user = await store.create_user("alice")
        four = "\n".join(f"https://example{i}.com/a" for i in range(4))

        captured: dict = {}
        import assistant.backend.pipeline.orchestrator as mod

        orig = mod.run_tool_loop

        async def _spy(*args, **kwargs):
            tools = args[2] if len(args) > 2 else kwargs.get("tools")
            captured["tools"] = [t["function"]["name"] for t in tools]
            return await orig(*args, **kwargs)

        mod.run_tool_loop = _spy
        try:
            await orch.chat(
                ChatRequest(message=four, user_id=user.id, session_id="s_four")
            )
        finally:
            mod.run_tool_loop = orig

        assert "web_search" in captured.get("tools", [])


class TestAnswerUsesTheSuppliedList:
    """The outcome, not the routing: what the user actually gets."""

    @pytest.mark.asyncio
    async def test_prompt_carries_the_list_and_episode_links_the_frame(
        self, store, stub_llm, stub_search
    ):
        from assistant.backend.pipeline.orchestrator import ChatRequest

        orch = await _orch(store, stub_llm, stub_search)
        user = await store.create_user("alice")
        await orch.chat(
            ChatRequest(message=PASTE, user_id=user.id, session_id="s_answer")
        )

        prompts = "\n\n".join(stub_llm.system_prompts)
        urls = detect_user_content(PASTE).urls
        missing = [u for u in urls if u not in prompts]
        assert not missing, f"list not in the prompt, missing: {missing}"

        episodes = await store.get_episodes_for_session("s_answer", user_id=user.id)
        user_turns = [e for e in episodes if e.role == "user"]
        assert user_turns
        assert user_turns[0].frame_ids not in (None, "", "[]"), (
            "the paste turn stored no frame link"
        )

        # The content frame holds every URL, in order.
        frame_ids = user_turns[0].frame_ids
        ids = [int(x) for x in str(frame_ids).strip("[]").split(",") if x.strip()]
        found = False
        for fid in ids:
            slots = await store.get_slots_for_frame(fid)
            content = next((s for s in slots if s.key == CONTENT_SLOT_KEY), None)
            if content is not None:
                assert content.value.split("\n") == urls
                found = True
        assert found, "no content frame among the episode's links"
