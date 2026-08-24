"""Orchestrator: runs the cognitive loop for chat turns."""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel

from assistant.backend.config import settings
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import ChatMessage, OllamaClient, build_system_prompt
from assistant.backend.pipeline.reasoner import Action, classify_intent, format_plan_for_prompt
from assistant.backend.pipeline.search import SearchResult, WebSearchTool
from assistant.backend.pipeline.task_router import TaskType, route
from assistant.backend.pipeline.tools import builtin_tools, run_tool_loop

logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    user_id: int
    message: str
    session_id: str | None = None  # if None, generate one
    # Client-generated id for live stage progress (see /chat/status/{turn_id}).
    turn_id: str | None = None


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
    3. Classify task type + search intent (router, single LLM pass when needed)
    4. Retrieve memory context (frames/slots/episodes)
    5. Reason: assess memory sufficiency and decide action (reasoner);
       router's wants_search judgment vetoes search for storage-style turns
    5b. Handle correction intent: extract + validate + apply
    6. Extract user-stated facts from the turn and store them (utility model)
    7. Build system prompt with memory context + just-stored facts
    7b. Execute search if still needed; store search facts (deduped vs step 6)
    8. Call LLM; log assistant episode
    9. Return response with extraction summary so learning is visible
    """

    def __init__(self, deps: OrchestratorDeps):
        self.store = deps.store
        self.retriever = deps.retriever
        self.llm_client = deps.llm_client
        self.search_tool = deps.search_tool

    def _embed_fn(self):
        """Embedding callable for canonical frame resolution (Phase 9A)."""

        async def get_embedding(text: str) -> list[float]:
            resp = await self.llm_client.embed(text)
            return resp.embedding

        return get_embedding

    def embed_fn(self):
        """Public alias — scheduler consolidation reuses the hot-path embedder."""
        return self._embed_fn()

    @staticmethod
    async def _report(
        progress: "Callable[[str, str], Awaitable[None]] | None",
        stage: str,
        detail: str,
    ) -> None:
        """Notify a live stage listener; never let progress break the turn."""
        if progress is None:
            return
        try:
            await progress(stage, detail)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("progress callback failed: %s", e)

    async def _get_self_context(self) -> str:
        """The agent's own identity facts, for grounding every response.

        Reads the identity frame's slots (name, working agreements, traits).
        Cheap deterministic DB reads — no LLM involved.
        """
        from assistant.backend.pipeline.extractor import IDENTITY_FRAME

        try:
            frame = await self.store.get_frame_by_name(IDENTITY_FRAME)
        except Exception:
            return ""
        if not frame:
            return ""
        slots = await self.store.get_slots_for_frame(frame.id)
        lines = [
            f"- {s.key}: {s.value}"
            for s in sorted(slots, key=lambda s: s.key)
            if s.value
        ]
        return "\n".join(lines)

    async def chat(
        self,
        request: ChatRequest,
        progress: "Callable[[str, str], Awaitable[None]] | None" = None,
    ) -> ChatResponse:
        """Run the full cognitive loop for a chat turn.

        progress: optional async callback (stage, detail) for live UI status;
        stage is a stable key, detail is human-readable phrasing.
        """
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

        # 3. Classify task type + search intent (single LLM pass when needed)
        await self._report(progress, "routing", "reading your message")
        classification = await route(request.message, self.llm_client)
        task_type = classification.task_type

        # 3b. Handle scheduled task intent
        if task_type == TaskType.SCHEDULED:
            return await self._handle_scheduled_task(request, session_id)

        # 4. Retrieve memory context
        await self._report(progress, "recall", "checking my memory")
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

        # 5a. Storage statements must not trigger external search: the user is
        # giving information, not requesting a lookup. The router's wants_search
        # judgment vetoes the reasoner's memory-sufficiency heuristic here.
        if (
            plan.search_needed
            and task_type != TaskType.SEARCH
            and classification.wants_search is False
        ):
            logger.info("Search vetoed by router for storage-style turn")
            plan.action = Action.ANSWER
            plan.search_needed = False

        # 5b. Handle correction intent: extract + validate + apply
        if plan.action == Action.CORRECT:
            from assistant.backend.pipeline.extractor import (
                apply_correction,
                extract_correction,
                validate_correction,
            )

            await self._report(progress, "correcting", "updating what I know")
            correction = await extract_correction(request.message, self.llm_client)
            correction_summary: dict = {}

            if (
                correction
                and correction.frame_name
                and correction.slot_key
                and correction.new_value is not None
            ):
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

        # 6. Extract user-stated facts BEFORE generation so the reply can
        # acknowledge them truthfully (no "sure, I remember" over empty stores).
        await self._report(progress, "learning", "learning from our conversation")
        extraction_summary: dict = {}
        try:
            extraction_summary = await store_turn_memory(
                user_message=request.message,
                assistant_response="",
                store=self.store,
                llm_client=self.llm_client,
                source_episode_id=user_episode.id,
            )
        except Exception as e:
            logger.error("Extraction failed: %s", e)

        stored_slots = extraction_summary.get("slots") or []

        # 7. Build system prompt with memory context + reasoner guidance
        plan_instructions = format_plan_for_prompt(plan)
        system_prompt = build_system_prompt(
            memory_context=memory_context.formatted,
            task_type=task_type.value,
            planinstructions=plan_instructions,
            self_context=await self._get_self_context(),
        )

        if stored_slots:
            lines = [f"- {s['frame_name']}.{s['key']} = {s['value']}" for s in stored_slots]
            system_prompt += (
                "\n\n**Facts you just stored this turn:**\n"
                + "\n".join(lines)
                + "\nAcknowledge these naturally, in your own words."
            )

        # 7b. Execute search if reasoner says it's needed
        search_results: list[SearchResult] = []
        search_extraction_summary: dict = {}
        if plan.search_needed:
            await self._report(progress, "searching", "searching the web")
            # Prefer the router's keyword query; fall back to a sanitized
            # version of the raw message (never raw conversational text).
            from assistant.backend.pipeline.search import (
                filter_relevant,
                sanitize_query,
            )

            query = classification.search_query or sanitize_query(request.message)
            logger.info("Reasoner triggered search for: %s", query[:80])
            try:
                search_results = await self.search_tool.search(query, num_results=5)
            except Exception as e:
                logger.warning("Search failed, continuing without results: %s", e)
                search_results = []

            # Relevance gate: drop links that don't belong to the query
            # before they can pollute the system prompt or citations.
            search_results = await filter_relevant(
                search_results, query, self._embed_fn()
            )

            if search_results:
                search_text = "\n".join(
                    f"- [{r.title}]({r.url}) - {r.snippet}" for r in search_results
                )
                system_prompt += f"\n\n**Search Results:**\n{search_text}"

                # Extract facts from search results and store in memory.
                # Drop slots duplicating what conversational extraction just
                # stored (same fact often lands under a different key).
                from assistant.backend.pipeline.extractor import (
                    apply_search_extraction,
                    extract_facts_from_search,
                    filter_duplicate_slots,
                )

                search_extraction = filter_duplicate_slots(
                    await extract_facts_from_search(
                        request.message, search_results, self.llm_client
                    ),
                    stored_slots,
                )
                search_extraction_summary = await apply_search_extraction(
                    search_extraction,
                    search_results,
                    self.store,
                    embed_fn=self._embed_fn(),
                )
                logger.info(
                    "Search extraction: %d slots, %d assocs",
                    search_extraction_summary.get("slots_applied", 0),
                    search_extraction_summary.get("associations_created", 0),
                )

                if search_extraction_summary.get("frame_ids"):

                    async def get_embedding(text: str) -> list[float]:
                        resp = await self.llm_client.embed(text)
                        return resp.embedding

                    await self.store.embed_frames(
                        search_extraction_summary["frame_ids"],
                        get_embedding,
                    )

                if search_extraction_summary.get("slots_applied", 0) > 0:
                    conflicts = search_extraction_summary.get("conflicts_created", 0)
                    if conflicts > 0:
                        fact_word = "fact was" if conflicts == 1 else "facts were"
                        conflict_note = (
                            f" {conflicts} conflicting {fact_word} auto-resolved — "
                            "the new value is stored and the old is preserved in history."
                        )
                    else:
                        conflict_note = ""
                    system_prompt += (
                        f"\n\n**Learned from search:** "
                        f"{search_extraction_summary['slots_applied']} new facts stored in memory."
                        f"{conflict_note}"
                    )
            else:
                system_prompt += (
                    "\n\n**Search Status: No results or SearXNG unavailable.**\n"
                    "You MUST NOT fabricate facts. Say you couldn't fetch current information "
                    "and offer to try again later or answer from memory only."
                )

        # Collect citations from search results only (not from memory slots).
        # Memory source_urls may not be verifiable - only cite from search.
        citations: list[str] = []
        for result in search_results:
            if result.url:
                citations.append(result.url)

        # Build conversation history: up to 6 prior turns from this session
        history_messages: list[ChatMessage] = []
        if session_id:
            session_episodes = await self.store.get_episodes_for_session(session_id)
            prior_turns = session_episodes[:-1]  # exclude current user episode
            max_turns = min(len(prior_turns), 6)
            prior_turns = prior_turns[-max_turns:] if max_turns > 0 else []
            for ep in prior_turns:
                history_messages.append(ChatMessage(role=ep.role, content=ep.content))

        # Call LLM — fast path uses configured default (think off for
        # thinking-capable models); escalated plans flip thinking on with a
        # token cap (Phase 6 plan §6.2). With tools enabled, the model may
        # call local tools (datetime, calculator, private web search) before
        # answering (Phase 6 M5).
        messages = [ChatMessage(role="system", content=system_prompt)]
        messages.extend(history_messages)
        messages.append(ChatMessage(role="user", content=request.message))
        think = True if plan.think else settings.chat_think_default
        num_predict = settings.think_num_predict_cap if plan.think else None
        if think:
            await self._report(progress, "reasoning", "thinking it through")
        else:
            await self._report(progress, "responding", "writing a reply")
        try:
            if settings.tools_enabled:
                tools = builtin_tools(self.search_tool)
                llm_response = await run_tool_loop(
                    self.llm_client,
                    messages,
                    tools,
                    think=think,
                    num_predict=num_predict,
                )
            else:
                llm_response = await self.llm_client.chat(
                    messages,
                    think=think,
                    num_predict=num_predict,
                )
        except Exception as e:
            # Local inference can be slow (large prefill, model load) or the
            # backend briefly unreachable — degrade gracefully instead of 500.
            logger.error("Generation failed: %s", e)
            fallback = (
                "I'm having trouble reaching my language model right now. "
                "It may still be loading or thinking through a long answer — "
                "please try again in a moment."
            )
            await self.store.create_episode(
                user_id=request.user_id,
                session_id=session_id,
                role="assistant",
                content=fallback,
                frame_ids=[],
            )
            return ChatResponse(
                response=fallback,
                session_id=session_id,
                task_type=task_type.value,
                memory_context=memory_context.formatted,
                extraction_summary=extraction_summary,
                search_extraction_summary=search_extraction_summary,
                citations=[],
            )

        # 8. Log assistant episode
        await self.store.create_episode(
            user_id=request.user_id,
            session_id=session_id,
            role="assistant",
            content=llm_response.content,
            frame_ids=[],
        )

        # 9. Append sources to response — only for informational/search tasks
        response_text = llm_response.content
        if citations and task_type.value == "search":
            unique_citations = list(dict.fromkeys(citations))
            sources_block = "\n\n**Sources:**\n" + "\n".join(f"- {url}" for url in unique_citations)
            response_text += sources_block

        # Memory source indicator: show for introspective/recall when frames were retrieved
        show_memory_source = (
            task_type.value == "introspective"
            and memory_context.retrieved_frames
        )
        if show_memory_source:
            frame_count = len(memory_context.retrieved_frames)
            fact_word = "facts" if frame_count != 1 else "fact"
            memory_block = (
                f"\n\n<small>_(Answered from memory"
                f" · {frame_count} {fact_word} retrieved)_</small>"
            )
            response_text += memory_block

        # Return response
        return ChatResponse(
            response=response_text,
            session_id=session_id,
            task_type=task_type.value,
            memory_context=memory_context.formatted,
            extraction_summary=extraction_summary or None,
            search_extraction_summary=search_extraction_summary or None,
            citations=citations,
        )

    async def execute_task(self, prompt: str, user_id: int) -> str:
        """Execute a scheduled task: run the prompt through the cognitive loop.

        Synthesizes a session, runs retrieval + search + LLM + extraction,
        then returns the response text. Results are stored as an assistant episode
        so they become memory normally.
        """
        session_id = f"scheduled-{uuid.uuid4().hex[:8]}"

        task_type_val = "functional"
        memory_context = await self.retriever.retrieve(
            query=prompt,
            user_id=user_id,
            session_id=session_id,
        )

        plan = classify_intent(
            query=prompt,
            task_type=task_type_val,
            memory=memory_context,
        )

        plan_instructions = format_plan_for_prompt(plan)
        system_prompt = build_system_prompt(
            memory_context=memory_context.formatted,
            task_type=task_type_val,
            planinstructions=plan_instructions,
            self_context=await self._get_self_context(),
        )

        search_results: list[SearchResult] = []
        if plan.search_needed:
            from assistant.backend.pipeline.search import (
                filter_relevant,
                sanitize_query,
            )

            query = sanitize_query(prompt)
            logger.info("Scheduled task triggering search: %s", query[:80])
            try:
                search_results = await self.search_tool.search(query, num_results=5)
            except Exception as e:
                logger.warning("Task search failed: %s", e)
                search_results = []

            search_results = await filter_relevant(
                search_results, query, self._embed_fn()
            )

            if search_results:
                search_text = "\n".join(
                    f"- [{r.title}]({r.url}) - {r.snippet}" for r in search_results
                )
                system_prompt += f"\n\n**Search Results:**\n{search_text}"

                from assistant.backend.pipeline.extractor import (
                    apply_search_extraction,
                    extract_facts_from_search,
                )
                try:
                    extraction = await extract_facts_from_search(
                        prompt, search_results, self.llm_client
                    )
                    await apply_search_extraction(
                        extraction, search_results, self.store,
                        embed_fn=self._embed_fn(),
                    )
                except Exception as e:
                    logger.error("Search extraction failed: %s", e)

        messages = [ChatMessage(role="system", content=system_prompt)]
        messages.append(ChatMessage(role="user", content=prompt))
        # Scheduled-task execution is latency-tolerant background work with
        # multi-constraint prompts — always run it in thinking mode (§6.2).
        llm_response = await self.llm_client.chat(
            messages,
            think=True,
            num_predict=settings.think_num_predict_cap,
        )

        await self.store.create_episode(
            user_id=user_id,
            session_id=session_id,
            role="assistant",
            content=llm_response.content,
            frame_ids=[],
        )

        return llm_response.content

    async def _handle_scheduled_task(
        self, request: ChatRequest, session_id: str
    ) -> ChatResponse:
        """Handle scheduled task requests: create, list, delete, pause, run-now."""

        from assistant.backend.pipeline.extractor import extract_scheduled_task_fields

        try:
            fields = await extract_scheduled_task_fields(request.message, self.llm_client)
        except Exception as e:
            logger.error("Failed to extract scheduled task fields: %s", e)
            return ChatResponse(
                response="I couldn't understand the task details. Try phrasing it like: "
                         "'set up a daily briefing on AI news at 9am' or "
                         "'list my scheduled tasks'.",
                session_id=session_id,
                task_type="scheduled",
                memory_context="",
                extraction_summary=None,
                search_extraction_summary=None,
                citations=[],
            )

        intent = fields.get("intent", "create")
        response_text = ""

        if intent == "list":
            tasks = await self.store.get_scheduled_tasks(
                owner_user_id=request.user_id
            )
            if not tasks:
                response_text = (
                    "You don't have any scheduled tasks yet. "
                    "Say something like 'set up a daily AI news briefing' to create one."
                )
            else:
                from ..scheduler.schedule import format_next_run

                lines = ["Your scheduled tasks:"]
                for t in tasks:
                    enabled = "enabled" if t["enabled"] else "paused"
                    last = t.get("last_run") or "never run"
                    next_ts = _parse_iso_ts_safe(t.get("next_run"))
                    next_r = format_next_run(next_ts) if next_ts else "unknown"
                    lines.append(
                        f"- **{t['name']}** ({t.get('schedule_cron', '?')}, {enabled})\n"
                        f"  Last: {last}  |  Next: {next_r}"
                    )
                response_text = "\n".join(lines)

        elif intent == "delete":
            task_name = fields.get("task_name") or fields.get("name", "")
            if task_name:
                tasks = await self.store.get_scheduled_tasks(
                    owner_user_id=request.user_id
                )
                for t in tasks:
                    if t["name"] == task_name:
                        await self.store.delete_scheduled_task(t["id"])
                        response_text = f"Deleted task '{task_name}'."
                        break
                else:
                    response_text = f"I couldn't find a task named '{task_name}'."
            else:
                response_text = "Which task do you want to delete?"

        elif intent == "pause":
            task_name = fields.get("task_name") or fields.get("name", "")
            if task_name:
                tasks = await self.store.get_scheduled_tasks(
                    owner_user_id=request.user_id
                )
                for t in tasks:
                    if t["name"] == task_name:
                        await self.store.upsert_scheduled_task(
                            name=task_name,
                            description=t.get("description", ""),
                            schedule_cron=t.get("schedule_cron", "0 9 * * *"),
                            prompt=t.get("prompt", ""),
                            enabled=False,
                            owner_user_id=request.user_id,
                        )
                        response_text = (
                            f"Paused task '{task_name}'. "
                            f"Say 'resume {task_name}' to enable it again."
                        )
                        break
                else:
                    response_text = f"I couldn't find a task named '{task_name}'."
            else:
                response_text = "Which task do you want to pause?"

        elif intent == "run_now":
            task_name = fields.get("task_name") or fields.get("name", "")
            if task_name:
                tasks = await self.store.get_scheduled_tasks(
                    owner_user_id=request.user_id
                )
                for t in tasks:
                    if t["name"] == task_name:
                        response_text = await self.execute_task(t["prompt"], request.user_id)
                        break
                else:
                    response_text = f"I couldn't find a task named '{task_name}'."
            else:
                response_text = "Which task do you want to run now?"

        else:
            name = fields.get("name") or f"task_{uuid.uuid4().hex[:6]}"
            description = fields.get("description", "")
            repeat = bool(fields.get("repeat", True))
            frequency = "daily" if repeat else "once"
            prompt = fields.get("prompt") or request.message

            from ..scheduler.schedule import format_next_run, next_daily_run

            next_tick = next_daily_run()
            await self.store.upsert_scheduled_task(
                name=name,
                description=description,
                schedule_cron=frequency,
                prompt=prompt,
                enabled=True,
                owner_user_id=request.user_id,
                next_run=next_tick.astimezone(UTC).isoformat(),
            )

            when = format_next_run(next_tick)
            if repeat:
                response_text = (
                    f"Added **{name}** to your daily list. I'll take care of it "
                    f"every morning — first run {when}. Say the word anytime if "
                    "you want it off the list."
                )
            else:
                response_text = (
                    f"Got it — I'll handle **{name}** once, at my next daily "
                    f"run ({when}), and then it's done."
                )

        return ChatResponse(
            response=response_text,
            session_id=session_id,
            task_type="scheduled",
            memory_context="",
            extraction_summary=None,
            search_extraction_summary=None,
            citations=[],
        )


def _parse_iso_ts_safe(value: str | None):
    """Best-effort ISO parse for display; returns None on failure."""
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts


async def store_turn_memory(
    user_message: str,
    assistant_response: str,
    store: MemoryStore,
    llm_client: OllamaClient,
    source_episode_id: int,
) -> dict:
    """Extract facts from a turn and store them in memory.

    Called synchronously before response generation so the chat model can
    acknowledge what was actually stored. Returns the extraction summary
    ({}, e.g. slots_applied/frame_ids); empty dict if extraction failed
    or found nothing.
    """
    from assistant.backend.pipeline.extractor import extract_and_apply

    result = await extract_and_apply(
        user_message,
        assistant_response,
        store,
        llm_client,
        source_episode_id=source_episode_id,
    )
    if result.get("frame_ids"):
        await store.update_episode_frame_ids(source_episode_id, result["frame_ids"])
    return result
