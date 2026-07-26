"""Orchestrator: runs the cognitive loop for chat turns."""

import logging
import uuid
from dataclasses import dataclass

from pydantic import BaseModel

from assistant.backend.memory.retrieval import Retriever
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import ChatMessage, OllamaClient, build_system_prompt
from assistant.backend.pipeline.search import SearchResult, WebSearchTool
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
    search_tool: WebSearchTool  # Always present (required feature)


class Orchestrator:
    """Runs the full cognitive loop for a chat turn.

    Flow:
    1. Get/create session_id
    2. Log user episode (conversation)
    3. Classify task type (router)
    4. Retrieve memory context (frames/slots/episodes)
    5. Build system prompt with memory context + task type
    6. Call LLM with optional search results
    7. Log assistant episode
    8. Fire-and-forget: extract facts from conversation turn, update memory
    9. Return response to user
    """

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Run the full cognitive loop for a chat turn."""
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
            store=self.store,
            query=request.message,
            user_id=request.user_id,
            session_id=session_id,
            limit=10,
        )

        # 5. Build system prompt with memory context
        system_prompt = build_system_prompt(
            memory_context=memory_context.formatted,
            task_type=task_type.value,
        )

        # 6. Call LLM with optional search results
        # Detect if query is about current information (simple heuristic)
        search_results: list[SearchResult] = []
        if task_type.value == "functional" and any(
            keyword in request.message.lower()
            for keyword in ["news", "headline", "current", "today", "now", "recent"]
        ):
            logger.info("Searching for current information: %s", request.message[:50])
            search_results = await self.search_tool.search(request.message, num_results=5)
            logger.info("Found %d search results", len(search_results))

            # Inject search results into system prompt
            if search_results:
                search_text = "\n".join(
                    f"- [{r.title}]({r.url}) - {r.snippet}" for r in search_results
                )
                system_prompt += f"\n\n**Recent Search Results:**\n{search_text}"

        # Call LLM
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


async def fire_and_forget(
    user_message: str,
    assistant_response: str,
    store: MemoryStore,
    llm_client: OllamaClient,
    source_episode_id: int,
) -> None:
    """Extract facts from conversation and store in memory (fire-and-forget)."""
    # TODO: Implement fact extraction
    pass
