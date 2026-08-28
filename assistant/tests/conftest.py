import hashlib
import json
import random
from pathlib import Path

import pytest
import pytest_asyncio

from assistant.backend.db.schema import init_db
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import ChatResponse, EmbeddingResponse, OllamaClient
from assistant.backend.pipeline.search import WebSearchTool


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> MemoryStore:
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    return MemoryStore(db_path)


@pytest.fixture
def stub_llm() -> "StubLLMClient":
    return StubLLMClient()


@pytest.fixture
def stub_search() -> WebSearchTool:
    return WebSearchTool(enabled=False)


_EMBEDDING_CLUSTERS = {
    "guitar", "fender", "stratocaster", "strings", "household", "my guitar",
}


def add_embedding_cluster(*keywords: str) -> None:
    """Register extra keywords that cluster at [1, 0, 0, ...] for tests.

    The relevance gate compares query vs result embeddings; stubbed search
    results must share a cluster with their query to survive it.
    """
    for kw in keywords:
        _EMBEDDING_CLUSTERS.add(kw.lower())


def deterministic_embedding(text: str, dim: int = 768) -> list[float]:
    """Return a deterministic embedding for integration tests.

    Keyword-clustered text (guitar/household by default, extendable via
    add_embedding_cluster) maps to [1, 0, 0, ...] so retrieval and the
    search-relevance gate can find matches without a real embedding model.
    """
    lower = text.lower()
    if any(k in lower for k in _EMBEDDING_CLUSTERS):
        return [1.0] + [0.0] * (dim - 1)
    sha = hashlib.sha256(text.encode()).digest()
    seed = int.from_bytes(sha[:8], "big")
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(dim)]


class StubLLMClient(OllamaClient):
    """Deterministic Ollama stub for end-to-end tests.

    Mocks at the LLM boundary: classify, extract and embed all return
    controllable, deterministic outputs. No network calls.
    """

    def __init__(self) -> None:
        super().__init__()
        self._next_extraction_result: list[dict] | None = None
        self._next_association_result: list[dict] | None = None
        self.system_prompts: list[str] = []

    def set_extraction_result(
        self,
        slots: list[dict] | None = None,
        associations: list[dict] | None = None,
    ) -> None:
        """Override the next extraction response."""
        self._next_extraction_result = slots
        self._next_association_result = associations

    async def chat(
        self,
        messages,
        model: str | None = None,
        temperature: float = 0.7,
        format: str | None = None,
        stream: bool = False,
        think: bool | None = None,
        num_predict: int | None = None,
        tools: list[dict] | None = None,
    ) -> ChatResponse:
        system = messages[0].content
        self.system_prompts.append(system)
        user = messages[1].content if len(messages) > 1 else ""
        system_lower = system.lower()
        user_lower = user.lower()

        if "classify" in system_lower:
            if any(
                k in user_lower
                for k in ("remember", "what do you know", "tell me about what you")
            ):
                task = "introspective"
            elif any(k in user_lower for k in (
                "actually", "that's wrong", "you got it wrong",
                "i meant", "correction", "not right",
            )):
                task = "correction"
            elif any(
                user_lower.startswith(f"{vb} ") or f" {vb}" in user_lower
                for vb in ("search for", "search", "look up", "look it up",
                           "google", "find", "find out")
            ):
                task = "search"
            else:
                task = "functional"
            return ChatResponse(
                content=f'{{"task_type": "{task}"}}',
                model=self.utility_model,
                done=True,
            )

        if "parse a user correction" in system_lower:
            # Correction-extraction expects CorrectionResult JSON
            # ({frame_name, slot_key, new_value}), not slot batches.
            slots = self._next_extraction_result or []
            self._next_extraction_result = None
            payload: dict = {}
            if slots:
                first = slots[0]
                payload = {
                    "frame_name": first.get("frame_name"),
                    "slot_key": first.get("slot_key"),
                    "new_value": first.get("value"),
                }
            return ChatResponse(
                content=json.dumps(payload), model=self.utility_model, done=True
            )

        if "extract" in system_lower:
            slots: list[dict] = []
            associations: list[dict] = []
            if (
                self._next_extraction_result is not None
                or self._next_association_result is not None
            ):
                slots = self._next_extraction_result or []
                associations = self._next_association_result or []
                self._next_extraction_result = None
                self._next_association_result = None
            else:
                content = self._extraction_response(user_lower)
                return ChatResponse(content=content, model=self.utility_model, done=True)
            return ChatResponse(
                content=json.dumps({"slots": slots, "associations": associations}),
                model=self.utility_model,
                done=True,
            )

        if (
            "search" in user_lower or "apnews" in user_lower or "headline" in user_lower
        ) and "Recent Search Results" not in system:
            return ChatResponse(
                content="I don't have that capability right now — web search is unavailable.",
                model=self.chat_model,
                done=True,
            )

        if "daily task list" in system_lower:
            # Management response: the orchestrator already processed the operation.
            # Return a simple confirmation based on op_details in the user message.
            user_msg = messages[1].content if len(messages) > 1 else ""
            if "resumed" in user_msg.lower():
                content = "Done — weather_check is back on your list."
            elif "paused" in user_msg.lower():
                content = "Paused — say 'resume weather_check' to bring it back."
            elif "deleted" in user_msg.lower():
                content = "Deleted."
            elif "created" in user_msg.lower():
                content = "Added to your daily list."
            elif "not found" in user_msg.lower():
                content = "I couldn't find that task."
            elif "no task" in user_msg.lower():
                content = "You don't have any scheduled tasks yet."
            else:
                content = "Got it."
            return ChatResponse(content=content, model=self.chat_model, done=True)

        return ChatResponse(
            content="Got it — tell me more.",
            model=self.chat_model,
            done=True,
        )

    @staticmethod
    def _extraction_response(user_lower: str) -> str:
        if any(
            k in user_lower for k in ("remember", "what do you know", "what do you remember")
        ):
            slots: list[dict] = []
        elif any(k in user_lower for k in ("guitar", "fender", "stratocaster", "strings")):
            slots = [
                {
                    "frame_name": "fender_stratocaster",
                    "frame_type": "entity",
                    "key": "brand",
                    "value": "Fender",
                },
                {
                    "frame_name": "fender_stratocaster",
                    "frame_type": "entity",
                    "key": "model",
                    "value": "Stratocaster",
                },
                {
                    "frame_name": "fender_stratocaster",
                    "frame_type": "entity",
                    "key": "strings",
                    "value": "6",
                },
            ]
        else:
            slots = []
        return json.dumps({"slots": slots, "associations": []})

    async def embed(self, text: str, model: str | None = None) -> EmbeddingResponse:
        return EmbeddingResponse(
            embedding=deterministic_embedding(text),
            model=self.embedding_model,
        )
