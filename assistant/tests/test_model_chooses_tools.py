"""Regression: the model decides whether to search; the system verifies the output.

This file replaces `test_web_search_withheld.py`, which pinned a fix that was
reverted, and the history is worth keeping because the mistake is instructive.

Three attempts were made at "make a transform turn answer from the user's list
instead of the web", and all three were **static rules standing where the model's
judgement belongs**:

1. clear the router's search flag — correct, but the model called `web_search` itself
   inside the tool loop;
2. inject the supplied content into the prompt — verified present on both paths, and
   the model still searched;
3. remove `web_search` from the tool list on any turn that supplied content.

Attempt 3 worked on its own test and **broke the actual use case.** The user pasted
45 bare URLs precisely so the agent would research them ("I was hoping the agent
would take the knowledge of which url mapped to high-impact sources and sort them").
Withholding the tool produced a correctly-ranked list *with no research at all* —
the symptom was gone because the capability was gone.

That is the failure mode this file exists to prevent. AGENTS.md is explicit: *route
ambiguous inputs via the model rather than heuristic classifiers*. A transform turn
may want research or may not; that is a per-turn judgement about a particular list
and a particular request, and a pattern can only encode what someone thought of in
advance.

So the design is: **freedom where the judgement lives, a guarantee where correctness
is checkable.**

- `web_search` is available on every turn. The model chooses.
- The *output* is where the system asserts itself: every supplied item must survive,
  and anything added must be labelled as an addition rather than silently substituted
  for an item.

These tests pin the first half. The invariant is measured by
`experiments/transform_decomposition` before any of it is built, which is the other
lesson from the three attempts: measure before building the fourth.
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
https://atxlibrary.musicat.co/submit/
https://www.rockrageradio.com/
https://www.kgpc969.org/music-submission/
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
    """Search that reports enabled, so `web_search` is genuinely on the table.

    The default fixture is `WebSearchTool(enabled=False)` and `builtin_tools` gates
    the tool on that flag, so with the default fixture `web_search` is absent for
    every turn and its availability cannot be observed at all.
    """
    from assistant.backend.pipeline.search import WebSearchTool

    tool = WebSearchTool(enabled=True)

    async def _no_results(query, *a, **kw):
        return [], None

    tool.search_with_info = _no_results  # type: ignore[method-assign]
    return tool


async def _tools_for(store, stub_llm, message: str, user=None) -> list[str]:
    """The tool names offered for one turn.

    `user` may be passed in so a test that calls this more than once against the
    same store does not try to create the same user twice.
    """
    import assistant.backend.pipeline.streaming as streaming
    from assistant.backend.pipeline.orchestrator import ChatRequest

    orch = await _orch(store, stub_llm, await _enabled_search())
    if user is None:
        user = await store.create_user("alice")

    captured: dict = {}

    async def _spy(*args, **kwargs):
        tools = args[2] if len(args) > 2 else kwargs.get("tools")
        captured["tools"] = [t["function"]["name"] for t in tools]
        # `_run_turn` calls `stream_tool_loop` (an async generator), not the old
        # dict-returning `run_tool_loop`; yield one finalize so the turn completes.
        yield streaming.serialize_event(streaming.FinalizeEvent("test", ""))

    original = streaming.stream_tool_loop
    streaming.stream_tool_loop = _spy
    try:
        await orch.chat(
            ChatRequest(message=message, user_id=user.id, session_id="s_tools")
        )
    finally:
        streaming.stream_tool_loop = original
    return captured.get("tools", [])


class TestSearchStaysAvailable:
    """The model chooses; the system does not pre-empt it with a rule."""

    @pytest.mark.asyncio
    async def test_search_available_on_a_paste_turn(self, store, stub_llm):
        """The regression. A turn that supplies content must still be able to
        research it — that is what the user asked for, and the withheld-tool fix
        removed it."""
        tools = await _tools_for(store, stub_llm, PASTE)
        assert tools, "the tool loop was never reached"
        assert "web_search" in tools, (
            "web_search was withheld from a turn that supplied content; the model "
            "cannot research the links the user handed over"
        )

    @pytest.mark.asyncio
    async def test_search_available_on_an_ordinary_turn(self, store, stub_llm):
        tools = await _tools_for(store, stub_llm, "search for austin music festivals")
        assert "web_search" in tools

    @pytest.mark.asyncio
    async def test_no_content_gating_remains(self, store, stub_llm):
        """Whether a turn supplied content must not change its tool list.

        If these ever differ, a static rule has crept back in — which is the exact
        class of change this file guards.
        """
        user = await store.create_user("alice")
        paste_tools = set(await _tools_for(store, stub_llm, PASTE, user=user))
        plain_tools = set(
            await _tools_for(
                store, stub_llm, "what is the capital of France?", user=user
            )
        )
        assert paste_tools == plain_tools, (
            "the tool list varies with supplied content, so a rule is deciding "
            f"what the model may do: only-in-paste={paste_tools - plain_tools}, "
            f"only-in-plain={plain_tools - paste_tools}"
        )


class TestSuppliedContentStillReachesMemory:
    """Phase 1 is kept: the content must be memory and must reach the prompt.

    Reverting Phase 2 must not quietly undo Phase 1, which is the part that fixed
    the actual data loss.
    """

    @pytest.mark.asyncio
    async def test_paste_registers_and_links_the_episode(self, store, stub_llm, stub_search):
        from assistant.backend.pipeline.orchestrator import ChatRequest

        orch = await _orch(store, stub_llm, stub_search)
        user = await store.create_user("alice")
        await orch.chat(
            ChatRequest(message=PASTE, user_id=user.id, session_id="s_phase1")
        )

        episodes = await store.get_episodes_for_session("s_phase1", user_id=user.id)
        user_turns = [e for e in episodes if e.role == "user"]
        assert user_turns, "no user episode recorded"
        assert user_turns[0].frame_ids not in (None, "", "[]"), (
            "the paste turn stored no frame link — Phase 1 regressed"
        )

    @pytest.mark.asyncio
    async def test_prompt_carries_the_list(self, store, stub_llm, stub_search):
        from assistant.backend.pipeline.orchestrator import ChatRequest

        orch = await _orch(store, stub_llm, stub_search)
        user = await store.create_user("alice")
        await orch.chat(
            ChatRequest(message=PASTE, user_id=user.id, session_id="s_prompt")
        )

        prompts = "\n\n".join(stub_llm.system_prompts)
        urls = detect_user_content(PASTE).urls
        missing = [u for u in urls if u not in prompts]
        assert not missing, f"list missing from the prompt: {missing}"
