"""The agent's identity grounds every response, not just identity queries.

build_system_prompt receives a self_context block ("Who you are") sourced from
the identity frame's slots, and the orchestrator injects it on every turn.
"""


from assistant.backend.memory.retrieval import Retriever
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.extractor import IDENTITY_FRAME
from assistant.backend.pipeline.llm_client import build_system_prompt
from assistant.backend.pipeline.orchestrator import ChatRequest, Orchestrator, OrchestratorDeps

from .conftest import StubLLMClient


def test_build_system_prompt_includes_self_block():
    prompt = build_system_prompt(
        memory_context="- guitar.strings = 6",
        task_type="functional",
        self_context="- full_name: Echo",
    )
    assert "Who you are" in prompt
    assert "- full_name: Echo" in prompt
    assert "- guitar.strings = 6" in prompt


def test_build_system_prompt_omits_self_block_when_empty():
    prompt = build_system_prompt(
        memory_context="- guitar.strings = 6",
        task_type="functional",
    )
    assert "Who you are" not in prompt
    assert "- guitar.strings = 6" in prompt


async def test_orchestrator_injects_identity_into_every_turn(store: MemoryStore):
    """Even a neutral query gets the identity block — no name amnesia."""
    llm = StubLLMClient()
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=Retriever(store=store, llm_client=llm),
            llm_client=llm,
            search_tool=WebSearchStub(),
        )
    )
    user = await store.create_user("alice")
    frame = await store.create_frame(name=IDENTITY_FRAME, type="entity")
    await store.upsert_slot(frame.id, "full_name", "Echo")
    await store.upsert_slot(frame.id, "working_agreement", "confirm before deleting")

    await orchestrator.chat(ChatRequest(user_id=user.id, message="what's up?"))

    assert llm.system_prompts, "no system prompts recorded"
    final = llm.system_prompts[-1]
    assert "Who you are" in final
    assert "- full_name: Echo" in final
    assert "- working_agreement: confirm before deleting" in final


class WebSearchStub:
    def __init__(self) -> None:
        self.enabled = False

    async def search(self, query: str, num_results: int = 5):
        return []

    def close(self):
        pass
