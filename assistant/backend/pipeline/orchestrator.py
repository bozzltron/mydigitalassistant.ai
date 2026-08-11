"""Orchestrator: runs the cognitive loop for chat turns."""

import asyncio
import logging
import uuid
from dataclasses import dataclass

from pydantic import BaseModel

from assistant.backend.memory.retrieval import Retriever
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import ChatMessage, OllamaClient, build_system_prompt
from assistant.backend.pipeline.reasoner import Action, classify_intent, format_plan_for_prompt
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
    extraction_summary: dict | None = None  # conversation extraction (async, may be None)
    search_extraction_summary: dict | None = None  # search extraction (sync, available immediately)
    citations: list[str] = []  # source URLs for the response


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
    5. Reason: assess memory sufficiency and decide action (reasoner)
    6. Build system prompt with memory context + reasoner guidance
    7. Call LLM with optional search results
    8. Log assistant episode
    9. Fire-and-forget: extract facts from conversation turn, update memory
    10. Return response to user
    """

    def __init__(self, deps: OrchestratorDeps):
        self.store = deps.store
        self.retriever = deps.retriever
        self.llm_client = deps.llm_client
        self.search_tool = deps.search_tool

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
            query=request.message,
            user_id=request.user_id,
            session_id=session_id,
        )

        # 5. Reason: decide action based on memory sufficiency
        plan = classify_intent(
            query=request.message,
            task_type=task_type.value,
            memory=memory_context,
        )

        # 5b. Handle correction intent: extract + validate + apply
        if plan.action == Action.CORRECT:
            from assistant.backend.pipeline.extractor import (
                apply_correction,
                extract_correction,
                validate_correction,
            )

            correction = await extract_correction(request.message, self.llm_client)
            correction_summary: dict = {}
            response_text = "I acknowledge your correction."

            if correction:
                frame = await self.store.get_frame_by_name(correction.frame_name)
                current_slot = (
                    await self.store.get_slot(frame.id, correction.slot_key) if frame else None
                )
                current_value = current_slot.value if current_slot else None

                validation = await validate_correction(
                    correction=correction,
                    current_value=current_value,
                    store=self.store,
                    search_tool=self.search_tool,
                    llm_client=self.llm_client,
                )

                if validation.contradicted:
                    response_text = (
                        f"I checked third-party sources and found evidence that "
                        f"{correction.frame_name}.{correction.slot_key} may still be "
                        f"'{current_value}' rather than '{correction.new_value}'. "
                        f"I've flagged this for your review rather than updating automatically."
                    )
                    logger.info(
                        "Correction contradicted by third party: frame=%s slot=%s "
                        "current=%s attempted=%s",
                        correction.frame_name,
                        correction.slot_key,
                        current_value,
                        correction.new_value,
                    )
                else:
                    correction_summary = await apply_correction(
                        correction, self.store, source_episode_id=user_episode.id
                    )
                    logger.info(
                        "Correction applied: frame=%s slot=%s value=%s "
                        "corroborated=%s",
                        correction_summary.get("frame_name"),
                        correction_summary.get("slot_key"),
                        correction_summary.get("new_value"),
                        validation.corroborated,
                    )
                    if validation.corroborated:
                        response_text = (
                            f"Got it — I've updated {correction.frame_name}.{correction.slot_key} "
                            f"to '{correction.new_value}'. "
                            f"(Third-party sources corroborate this.)"
                        )
                    else:
                        response_text = (
                            f"Got it — I've updated {correction.frame_name}.{correction.slot_key} "
                            f"to '{correction.new_value}'. Thanks for the correction!"
                        )
            else:
                logger.info("Correction detected but could not be parsed")
                response_text = (
                    "I understand you're saying something was wrong, but I couldn't "
                    "parse exactly what needs to be corrected. Could you rephrase? "
                    "(e.g. 'Actually, the guitar has 12 strings, not 6')"
                )

            await self.store.create_episode(
                user_id=request.user_id,
                session_id=session_id,
                role="assistant",
                content=response_text,
                frame_ids=[],
            )

            return ChatResponse(
                response=response_text,
                session_id=session_id,
                task_type="correction",
                memory_context=memory_context.formatted,
                extraction_summary=None,
                search_extraction_summary=None,
                citations=[],
            )

        # 6. Build system prompt with memory context + reasoner guidance
        plan_instructions = format_plan_for_prompt(plan)
        system_prompt = build_system_prompt(
            memory_context=memory_context.formatted,
            task_type=task_type.value,
            planinstructions=plan_instructions,
        )

        # 7. Execute search if reasoner says it's needed
        search_results: list[SearchResult] = []
        search_extraction_summary: dict = {}
        if plan.search_needed:
            logger.info("Reasoner triggered search for: %s", request.message[:50])
            try:
                search_results = await self.search_tool.search(request.message, num_results=5)
            except Exception as e:
                logger.warning("Search failed, continuing without results: %s", e)
                search_results = []

            if search_results:
                search_text = "\n".join(
                    f"- [{r.title}]({r.url}) - {r.snippet}" for r in search_results
                )
                system_prompt += f"\n\n**Search Results:**\n{search_text}"

                # Extract facts from search results and store in memory
                from assistant.backend.pipeline.extractor import (
                    apply_search_extraction,
                    extract_facts_from_search,
                )

                search_extraction_summary = await apply_search_extraction(
                    await extract_facts_from_search(
                        request.message, search_results, self.llm_client
                    ),
                    search_results,
                    self.store,
                )
                logger.info(
                    "Search extraction: %d slots, %d assocs",
                    search_extraction_summary.get("slots_applied", 0),
                    search_extraction_summary.get("associations_created", 0),
                )

                if search_extraction_summary.get("slots_applied", 0) > 0:
                    system_prompt += (
                        f"\n\n**Learned from search:** "
                        f"{search_extraction_summary['slots_applied']} new facts stored in memory."
                    )
            else:
                system_prompt += (
                    "\n\n**Search Status: No results or SearXNG unavailable.**\n"
                    "You MUST NOT fabricate facts. Say you couldn't fetch current information "
                    "and offer to try again later or answer from memory only."
                )

        # Collect citations from search results and slot source URLs
        citations: list[str] = []
        for result in search_results:
            if result.url:
                citations.append(result.url)
        for rf in memory_context.retrieved_frames:
            for slot in rf.slots:
                if slot.source_url and slot.source_url not in citations:
                    citations.append(slot.source_url)

        # Build conversation history: up to 6 prior turns from this session
        history_messages: list[ChatMessage] = []
        if session_id:
            session_episodes = await self.store.get_episodes_for_session(session_id)
            prior_turns = session_episodes[:-1]  # exclude current user episode
            max_turns = min(len(prior_turns), 6)
            prior_turns = prior_turns[-max_turns:] if max_turns > 0 else []
            for ep in prior_turns:
                history_messages.append(ChatMessage(role=ep.role, content=ep.content))

        # Call LLM
        messages = [ChatMessage(role="system", content=system_prompt)]
        messages.extend(history_messages)
        messages.append(ChatMessage(role="user", content=request.message))
        llm_response = await self.llm_client.chat(messages)

        # 8. Log assistant episode
        await self.store.create_episode(
            user_id=request.user_id,
            session_id=session_id,
            role="assistant",
            content=llm_response.content,
            frame_ids=[],
        )

        # 9. Fire-and-forget extraction
        extraction_task = asyncio.create_task(
            fire_and_forget(
                user_message=request.message,
                assistant_response=llm_response.content,
                store=self.store,
                llm_client=self.llm_client,
                source_episode_id=user_episode.id,
            )
        )
        extraction_task.add_done_callback(
            lambda t: logger.error("Extraction failed: %s", t.exception())
            if t.exception()
            else None
        )

        # 10. Append sources to response
        response_text = llm_response.content
        if citations:
            unique_citations = list(dict.fromkeys(citations))
            sources_block = "\n\n**Sources:**\n" + "\n".join(f"- {url}" for url in unique_citations)
            response_text += sources_block

        # Return response
        return ChatResponse(
            response=response_text,
            session_id=session_id,
            task_type=task_type.value,
            memory_context=memory_context.formatted,
            extraction_summary=None,
            search_extraction_summary=search_extraction_summary or None,
            citations=citations,
        )


async def fire_and_forget(
    user_message: str,
    assistant_response: str,
    store: MemoryStore,
    llm_client: OllamaClient,
    source_episode_id: int,
) -> None:
    """Extract facts from conversation and store in memory (fire-and-forget)."""
    from assistant.backend.pipeline.extractor import extract_and_apply

    await extract_and_apply(
        user_message,
        assistant_response,
        store,
        llm_client,
        source_episode_id=source_episode_id,
    )
