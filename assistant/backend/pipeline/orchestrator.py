import logging
import uuid
from dataclasses import dataclass

from pydantic import BaseModel

from assistant.backend.memory.retrieval import Retriever
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import ChatMessage, OllamaClient, build_system_prompt
from assistant.backend.pipeline.task_router import classify

logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    user_id: int
    message: str
    session_id: str | None = None  # if None, generate one


class ChatResponse(BaseModel):
    response: str
    session_id: str
    task_type: str  # "functional" | "introspective"
    memory_context: str  # for --trace mode
    extraction_summary: dict | None = None  # for --trace mode (filled async, may be None)


@dataclass
class OrchestratorDeps:
    """Dependencies for the orchestrator. Injected for testability."""

    store: MemoryStore
    retriever: Retriever
    llm_client: OllamaClient


class Orchestrator:
    """Runs the full cognitive loop for a chat turn.

    Flow:
    1. Get/create session_id
    2. Log user episode
    3. Classify task type (router)
    4. Retrieve memory context
    5. Build system prompt with memory context + task type
    6. Call LLM for response
    7. Log assistant episode
    8. Fire-and-forget: extract facts from turn, update memory
    9. Return response to user
    """

    def __init__(self, deps: OrchestratorDeps):
        self.store = deps.store
        self.retriever = deps.retriever
        self.llm_client = deps.llm_client

    async def chat(self, request: ChatRequest) -> ChatResponse:
        # Local import avoids circular dependency; extractor is only needed here.
        from assistant.backend.pipeline.extractor import fire_and_forget

        # 1. Session
        session_id = request.session_id or str(uuid.uuid4())

        # 2. Log user episode
        user_episode = await self.store.create_episode(
            user_id=request.user_id,
            session_id=session_id,
            role="user",
            content=request.message,
            frame_ids=[],
        )

        # 3. Classify task type
        task_type = await classify(request.message, self.llm_client)

        # 4. Retrieve memory context
        memory_context = await self.retriever.retrieve(
            query=request.message,
            user_id=request.user_id,
            session_id=session_id,
        )

        # 5. Build system prompt
        system_prompt = build_system_prompt(
            memory_context=memory_context.formatted,
            task_type=task_type.value,
        )

        # 6. Call LLM
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=request.message),
        ]
        llm_response = await self.llm_client.chat(messages)

        # 7. Log assistant episode
        await self.store.create_episode(
            user_id=request.user_id,
            session_id=session_id,
            role="assistant",
            content=llm_response.content,
            frame_ids=[],
        )

        # 8. Fire-and-forget extraction
        extraction_task = fire_and_forget(
            user_message=request.message,
            assistant_response=llm_response.content,
            store=self.store,
            llm_client=self.llm_client,
            source_episode_id=user_episode.id,
        )
        extraction_task.add_done_callback(
            lambda t: logger.error("Extraction failed: %s", t.exception())
            if t.exception()
            else None
        )

        # 9. Return response
        return ChatResponse(
            response=llm_response.content,
            session_id=session_id,
            task_type=task_type.value,
            memory_context=memory_context.formatted,
            extraction_summary=None,  # async, not yet available
        )
