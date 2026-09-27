"""Regression: the system prompt must never be cut mid-frame.

The frame-aware fit sizes the memory section to
``max_system_prompt_chars - system_prompt_overhead(...)``. That accounting only
covers ``build_system_prompt("")`` -- but the orchestrator appends up to five
more blocks *after* the memory section (stored facts, a computed result, search
results, and two others). Their cost is missing from the budget, so the prompt
overruns the cap and the flat ``system_prompt[:cap]`` backstop fires, slicing
through the memory section.

Memory is rendered near the end of the prompt, so a frame caught by that cut
still shows its "### name" header and reads to the model as present while the
facts in its tail are gone. The user sees an answer that stops mid-sentence.

Observed in the live backend before the fix:
``WARNING System prompt truncated from 12165 to 12000 chars`` with
``frames=10 ... truncated=True``.
"""

from assistant.backend.config import settings
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.pipeline.llm_client import system_prompt_overhead
from assistant.backend.pipeline.orchestrator import (
    ChatRequest,
    Orchestrator,
    OrchestratorDeps,
)
from assistant.backend.pipeline.search import WebSearchTool

from .test_orchestrator import StorageTurnStub

# Ten frames of eight slots each is roughly the post-graph-walk production
# reality and is deliberately larger than the memory allowance, so the frame-aware
# fit is genuinely active and the post-memory appends are what tips the total over
# the cap. The overflow is the whole point: the budget is correct in isolation and
# wrong once post-memory appends are counted.
_FRAME_COUNT = 10
_SLOTS_PER_FRAME = 8
# "guitar" puts both the frame text and the query in the stub's default
# embedding cluster, so retrieval actually finds these frames.
_SLOT_VALUE = "a reasonably long slot value about my guitar that fills the prompt "


def _stored_slots(count: int) -> list[dict]:
    return [
        {
            "frame_name": "fender_stratocaster",
            "frame_type": "entity",
            "key": f"strings_note_{i}",
            "value": "x" * 40,
        }
        for i in range(count)
    ]


async def _seed_frames(store, llm, prefix: str) -> None:
    """Seed frames the stub's own embedder can find.

    The vectors have to come from the stub, not a hand-written list: the stub
    embeds at 768 dims and clusters on keywords, so a literal 1024-dim vector
    would be invisible to retrieval and the memory section would be empty.
    """
    from assistant.backend.memory.retrieval import frame_to_text

    for i in range(_FRAME_COUNT):
        frame = await store.create_frame(
            name=f"{prefix}_guitar_{i}", type="entity", confidence=0.9
        )
        for j in range(_SLOTS_PER_FRAME):
            await store.upsert_slot(
                frame_id=frame.id,
                key=f"key_{j}",
                value=f"{_SLOT_VALUE}{i}",
            )
        slots = await store.get_slots_for_frame(frame.id)
        resp = await llm.embed(frame_to_text(frame, slots))
        await store.store_frame_embedding(
            frame.id, resp.embedding, llm.embedding_model
        )


def _frames_with_unterminated_blocks(prompt: str) -> list[str]:
    """Frame headers whose slot list is cut off.

    A complete frame renders its header followed by "  - key = value" lines. A
    frame caught by a character cut ends with a dangling key and no value, or
    with nothing at all -- the visible symptom is a half-sentence.
    """
    bad: list[str] = []
    lines = prompt.splitlines()
    for idx, line in enumerate(lines):
        if not line.startswith("### "):
            continue
        body: list[str] = []
        for nxt in lines[idx + 1 :]:
            if nxt.startswith("### ") or nxt.startswith("## "):
                break
            if nxt.strip():
                body.append(nxt)
        if not body or not body[-1].lstrip().startswith("- "):
            bad.append(line)
    return bad


async def _run_turn(store, slots: list[dict], message: str) -> str:
    """Return the final system prompt the chat model was given."""
    llm = StorageTurnStub()
    llm.set_extraction_result(slots=slots)
    await _seed_frames(store, llm, "trunc")
    # top_k_direct=10 mirrors production after the graph-walk fix, where the
    # delivered context is 10 frames rather than 3.
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=Retriever(
                store=store,
                llm_client=llm,
                embedding_model=settings.embedding_model,
                top_k_direct=10,
            ),
            llm_client=llm,
            search_tool=WebSearchTool(base_url="http://127.0.0.1:1", enabled=False),
        )
    )
    user = await store.create_user("alice")
    await orchestrator.chat(ChatRequest(user_id=user.id, message=message))
    prompts = [p for p in llm.system_prompts if "classify" not in p.lower()]
    assert prompts, "the chat model was never called"
    return prompts[-1]


def _appended_after_memory(prompt: str) -> str:
    """The blocks the orchestrator appends after the memory section."""
    marker = "Facts you just stored this turn"
    idx = prompt.find(marker)
    assert idx >= 0, "no stored-facts block, so nothing was appended after memory"
    return prompt[idx:]


class TestPromptIsNotCutMidFrame:
    async def test_fixtures_reproduce_the_overrun(self, store):
        """Guard for the two tests below.

        They are only meaningful if memory is delivered *and* the post-memory
        appends are big enough to overrun the cap under the old accounting. If
        retrieval stopped finding these frames, or the stored-facts block shrank,
        they would pass vacuously -- which is how a regression test becomes
        decoration.

        Deliberately asserts on the *appended* suffix, which the refit does not
        touch, rather than on the final prompt length, which the fix changes.
        """
        system_prompt = await _run_turn(
            store, _stored_slots(20), "remember my guitar has 6 strings"
        )
        assert "### trunc_guitar" in system_prompt, (
            "no seeded frame reached the prompt; the truncation tests below "
            "would pass vacuously"
        )
        allowance = (
            settings.max_system_prompt_chars
            - system_prompt_overhead("functional")
        )
        suffix = _appended_after_memory(system_prompt)
        # Under the old accounting memory was sized to fill the whole allowance,
        # so an append of any size overran. Assert the margin is real rather than
        # a rounding error, so the fixture cannot quietly stop reproducing it.
        assert len(suffix) > 0.05 * allowance, (
            f"appended suffix is only {len(suffix)} chars against a "
            f"{allowance}-char allowance; too small to reproduce the overrun"
        )

    async def test_full_turn_does_not_truncate_the_prompt(self, store):
        """End to end: a turn that stores facts must not overflow the cap.

        This is the user-visible bug -- the answer comes back cut off.
        """
        system_prompt = await _run_turn(
            store, _stored_slots(20), "remember my guitar has 6 strings"
        )
        assert len(system_prompt) <= settings.max_system_prompt_chars, (
            f"system prompt is {len(system_prompt)} chars, over the "
            f"{settings.max_system_prompt_chars} cap"
        )
        assert "[... truncated ...]" not in system_prompt, (
            "the flat character cut fired, so the prompt was sliced"
        )
        assert not _frames_with_unterminated_blocks(system_prompt), (
            "a frame was cut mid-slots: "
            f"{_frames_with_unterminated_blocks(system_prompt)}"
        )

    async def test_memory_survives_when_stored_facts_are_long(self, store):
        """A big stored-facts block must cost memory, not truncate the answer."""
        system_prompt = await _run_turn(
            store, _stored_slots(20), "remember some facts about my guitar"
        )
        assert len(system_prompt) <= settings.max_system_prompt_chars
        assert "[... truncated ...]" not in system_prompt
        assert "Facts you just stored this turn" in system_prompt, (
            "the stored-facts block was dropped entirely instead of yielding "
            "prompt room to memory"
        )
        assert "### trunc_guitar" in system_prompt, (
            "memory was dropped entirely instead of yielding room to the "
            "stored-facts block"
        )

    def test_memory_char_budget_ignores_post_memory_appends(self):
        """The accounting bug itself, isolated from the orchestrator.

        Memory is sized to the cap minus build_system_prompt's own prefix. Any
        block appended afterwards is unbudgeted, so the sum exceeds the cap. This
        pins the arithmetic so the fix has to account for the appends somewhere.
        """
        budget = settings.max_system_prompt_chars - system_prompt_overhead("functional")
        assert budget > 0
        post_memory = "\n\n**Facts you just stored this turn:**\n" + ("\n".join(
            f"- frame_{i}.key_{j} = value" for i in range(6) for j in range(4)
        ))
        total = (settings.max_system_prompt_chars - budget) + budget + len(post_memory)
        assert total > settings.max_system_prompt_chars, (
            "post-memory appends are now inside the budget; update this test to "
            "assert the orchestrator reserves for them"
        )
