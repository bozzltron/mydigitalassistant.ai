"""Orchestrator: runs the cognitive loop for chat turns."""

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from assistant.backend.config import settings
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import ChatMessage, OllamaClient, build_system_prompt
from assistant.backend.pipeline.reasoner import Action, classify_intent, format_plan_for_prompt
from assistant.backend.pipeline.search import SearchInfo, SearchResult, WebSearchTool
from assistant.backend.pipeline.task_router import TaskType, route
from assistant.backend.pipeline.tools import builtin_tools, run_tool_loop

logger = logging.getLogger(__name__)

MAX_FETCH_BYTES = 500_000
FETCH_TIMEOUT_SECONDS = 4.0


class _HTMLTextExtractor(HTMLParser):
    """Strip HTML tags and return plain text."""

    def __init__(self) -> None:
        super().__init__()
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("br", "hr", "p", "div", "li"):
            self._text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("p", "div"):
            self._text.append("\n")

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self._text.append(text)

    @property
    def text(self) -> str:
        joined = "".join(self._text)
        return " ".join(
            " ".join(line.split())
            for line in joined.split("\n")
            if line.strip()
        )


def _strip_html(html: str) -> str:
    try:
        extractor = _HTMLTextExtractor()
        extractor.feed(html)
        text = extractor.text
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def _fetch_url_body(url: str) -> str | None:
    """Fetch a URL and return stripped plain text. Returns None on failure."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return None
    except Exception:
        return None

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(FETCH_TIMEOUT_SECONDS, read=8.0),
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AssistantBot/1.0)"},
        ) as client:
            r = await client.get(url)
            content_type = r.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return r.text[:2000]

            raw = r.content[:MAX_FETCH_BYTES]
            try:
                raw = raw.decode(r.encoding or "utf-8", errors="replace")
            except Exception:
                raw = raw.decode("utf-8", errors="replace")

            text = _strip_html(raw)
            if not text.strip():
                return None
            return text[:8000]
    except Exception as e:
        logger.warning("Failed to fetch %s: %s", url[:80], e)
        return None


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
    search_info: SearchInfo | None = None  # which backend + query + results (for UI transparency)


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

    async def _log_episode(
        self,
        user_id: int,
        session_id: str,
        role: str,
        content: str,
        frame_ids: list[int] | None = None,
    ):
        """Persist a conversation turn and index it for semantic recall.

        Embedding is best-effort: a failed vector write never breaks the chat
        path — the twice-daily consolidation tops up missing embeddings.
        """
        episode = await self.store.create_episode(
            user_id=user_id,
            session_id=session_id,
            role=role,
            content=content,
            frame_ids=frame_ids or [],
        )
        try:
            embedding = await self.embed_fn()(
                f"{role}: {content[:4000]}"
            )
            await self.store.store_episode_embedding(
                episode.id, embedding, settings.embedding_model
            )
        except Exception as exc:
            logger.warning("Episode embedding deferred (id=%s): %s", episode.id, exc)
        return episode

    async def _acknowledge_correction(
        self,
        frame_name: str,
        slot_key: str,
        new_value: str,
        contradicted: bool,
        current_value: str | None,
    ) -> str:
        """Generate a natural correction acknowledgment via the model."""
        if contradicted:
            prompt = (
                f"The user corrected a stored fact but third-party evidence contradicts the new value.\n"
                f"Frame: {frame_name}\nSlot: {slot_key}\n"
                f"User's claimed value: {new_value}\n"
                f"Current stored value (confirmed by sources): {current_value}\n"
                f"Generate a brief, honest response that:\n"
                f"1. Acknowledges the user's correction attempt\n"
                f"2. Explains that sources suggest the current value is still accurate\n"
                f"3. Notes the correction has been flagged for review\n"
                f"Keep it to 1-2 sentences. Do not use a template like 'Got it — I've updated...'."
            )
        else:
            prompt = (
                f"The user corrected a stored fact and it has been accepted.\n"
                f"Frame: {frame_name}\nSlot: {slot_key}\n"
                f"New value: {new_value}\n"
                f"Generate a brief, natural acknowledgment. "
                f"Keep it to 1 sentence. Do not use a template like 'Got it — I've updated...'."
            )
        messages = [ChatMessage(role="user", content=prompt)]
        resp = await self.llm_client.chat(
            messages,
            model=self.llm_client.chat_model,
            temperature=0.6,
            think=False,
        )
        return resp.content or f"Updated {frame_name}.{slot_key} to '{new_value}'."

    async def _generate_fallback_response(self, original_message: str) -> str:
        """Generate a fallback response when the model returned empty."""
        for _attempt in range(2):
            resp = await self.llm_client.chat(
                [ChatMessage(role="user", content=f"I need to respond to: {original_message[:200]}")],
                model=self.llm_client.chat_model,
                temperature=0.7,
                think=False,
            )
            if resp.content and resp.content.strip():
                return resp.content
        return "I'm not sure how to respond to that."

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
        skip_route: bool = False,
    ) -> ChatResponse:
        """Run the full cognitive loop for a chat turn.

        progress: optional async callback (stage, detail) for live UI status;
        stage is a stable key, detail is human-readable phrasing.

        skip_route: when True, skip the task-type router and treat the message
        as functional. Used by internal callers (e.g. scheduled-task management
        responses) that already know the intent.
        """
        # 1. Session
        session_id = request.session_id or str(uuid.uuid4())

        # 2. Log user episode
        user_episode = await self._log_episode(
            request.user_id,
            session_id,
            role="user",
            content=request.message,
        )

        # 3. Classify task type + search intent (single LLM pass when needed)
        await self._report(progress, "routing", "reading your message")
        if skip_route:
            task_type = TaskType.FUNCTIONAL
        else:
            classification = await route(request.message, self.llm_client)
            task_type = classification.task_type

        # 3b. Handle scheduled task intent
        if task_type == TaskType.SCHEDULED and not skip_route:
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
                    logger.info(
                        "Correction contradicted by third party: frame=%s slot=%s "
                        "current=%s attempted=%s",
                        correction.frame_name,
                        correction.slot_key,
                        current_value,
                        correction.new_value,
                    )
                    response_text = await self._acknowledge_correction(
                        correction.frame_name,
                        correction.slot_key,
                        correction.new_value,
                        contradicted=True,
                        current_value=current_value,
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
                    response_text = await self._acknowledge_correction(
                        correction.frame_name,
                        correction.slot_key,
                        correction.new_value,
                        contradicted=False,
                        current_value=None,
                    )
            else:
                logger.info("Correction could not be parsed — generating natural response")
                natural_response = await self.llm_client.chat(
                    [ChatMessage(role="user", content=request.message)],
                    model=self.llm_client.chat_model,
                    temperature=0.7,
                    think=True,
                )
                response_text = natural_response.content

            await self._log_episode(
                request.user_id,
                session_id,
                role="assistant",
                content=response_text,
            )

            return ChatResponse(
                response=response_text,
                session_id=session_id,
                task_type="correction",
                memory_context=memory_context.formatted,
                extraction_summary=None,
                search_extraction_summary=None,
                citations=[],
                search_info=None,
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
        search_info: SearchInfo | None = None
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
            backend_name = self.search_tool.backend_name
            extraction_budget = self.search_tool.max_results_for_extraction
            relevance_threshold = (
                0.20 if backend_name == "brave" else settings.search_min_relevance
            )
            try:
                search_results, search_info = await self.search_tool.search_with_info(
                    query, num_results=extraction_budget
                )
            except Exception as e:
                logger.warning("Search failed, continuing without results: %s", e)
                search_results = []
                search_info = None

            # Relevance gate: drop links that don't belong to the query
            # before they can pollute the system prompt or citations.
            search_results = await filter_relevant(
                search_results, query, self._embed_fn(), min_relevance=relevance_threshold
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
                    extract_facts_from_document,
                    extract_facts_from_search,
                    filter_duplicate_slots,
                    merge_extractions,
                )

                snippet_extraction = await extract_facts_from_search(
                    request.message, search_results, self.llm_client
                )

                # Brave: fetch top 3 result bodies in parallel for richer extraction
                if backend_name == "brave" and search_results:
                    try:
                        bodies = await asyncio.gather(
                            *[
                                _fetch_url_body(r.url)
                                for r in search_results[:3]
                            ],
                            return_exceptions=True,
                        )
                        document_extractions: list = []
                        for result, body in zip(search_results[:3], bodies, strict=True):
                            if isinstance(body, Exception) or not body:
                                continue
                            doc_extraction = await extract_facts_from_document(
                                body, result.url, self.llm_client
                            )
                            document_extractions.append(doc_extraction)
                        if document_extractions:
                            snippet_extraction = merge_extractions(
                                snippet_extraction, *document_extractions
                            )
                    except Exception as e:
                        logger.warning("Brave full-page fetch failed: %s", e)

                search_extraction = filter_duplicate_slots(
                    snippet_extraction,
                    stored_slots,
                )
                search_extraction_summary = await apply_search_extraction(
                    search_extraction,
                    search_results,
                    self.store,
                    embed_fn=self._embed_fn(),
                    backend_name=backend_name,
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

        # Build conversation history: up to 6 prior turns from this session.
        # Session ids are client-supplied — filter by owner so two household
        # members sharing a session string never see each other's turns.
        history_messages: list[ChatMessage] = []
        if session_id:
            session_episodes = await self.store.get_episodes_for_session(session_id)
            prior_turns = [
                ep for ep in session_episodes if ep.user_id == request.user_id
            ][:-1]  # exclude current user episode
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
                tools = builtin_tools(
                    self.search_tool,
                    store=self.store,
                    llm_client=self.llm_client,
                    embed_fn=self._embed_fn,
                )
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
            await self._log_episode(
                request.user_id,
                session_id,
                role="assistant",
                content=fallback,
            )
            return ChatResponse(
                response=fallback,
                session_id=session_id,
                task_type=task_type.value,
                memory_context=memory_context.formatted,
                extraction_summary=extraction_summary,
                search_extraction_summary=search_extraction_summary,
                citations=[],
                search_info=None,
            )

        # 8. Log assistant episode
        await self._log_episode(
            request.user_id,
            session_id,
            role="assistant",
            content=llm_response.content,
        )

        # 9. Append sources to response — only for informational/search tasks
        response_text = llm_response.content or llm_response.thinking or ""
        if not response_text:
            response_text = "I'm not sure how to respond to that."
            logger.warning("Empty LLM response for: " + repr(request.message[:50]))
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
            search_info=search_info,
        )

    async def _handle_scheduled_task(
        self, request: ChatRequest, session_id: str
    ) -> ChatResponse:
        """Handle scheduled task requests: create, list, delete, pause, run-now.

        All responses are generated by the model — no hardcoded response strings.
        """
        from assistant.backend.pipeline.extractor import extract_scheduled_task_fields

        try:
            fields = await extract_scheduled_task_fields(request.message, self.llm_client)
        except Exception as e:
            logger.error("Failed to extract scheduled task fields: %s", e)
            request.message = (
                "I couldn't understand the task details. Try phrasing it like: "
                "'set up a daily briefing on AI news at 9am' or "
                "'list my scheduled tasks'."
            )
            return await self.chat(request, skip_route=True)

        intent = fields.get("intent", "create")
        task_name = fields.get("task_name") or fields.get("name", "")

        if intent == "run_now":
            if task_name:
                tasks = await self.store.get_scheduled_tasks(
                    owner_user_id=request.user_id
                )
                for t in tasks:
                    if t["name"] == task_name:
                        result = await self.run_scheduled_task(
                            t["prompt"], request.user_id, t["name"]
                        )
                        now_str = datetime.now(UTC).isoformat()
                        summary = result[:2000] if result else ""
                        await self.store.update_scheduled_task_run(
                            frame_id=t["id"],
                            last_run=now_str,
                            last_result_summary=summary,
                        )
                        return ChatResponse(
                            response=result,
                            session_id=str(uuid.uuid4()),
                            task_type="scheduled",
                            memory_context="",
                            extraction_summary=None,
                            search_extraction_summary=None,
                            citations=[],
                        )
                else:
                    request.message = f"I couldn't find a task named '{task_name}'."
                    return await self.chat(request, skip_route=True)
            else:
                request.message = "Which task do you want to run now?"
                return await self.chat(request, skip_route=True)

        elif intent in ("list", "delete", "pause", "resume", "create"):
            return await self._scheduled_task_management_response(
                request, intent, task_name, fields, session_id
            )

        else:
            request.message = (
                "I'm not sure what to do with that. Try something like "
                "'set up a daily AI news briefing' or 'list my scheduled tasks'."
            )
            return await self.chat(request, skip_route=True)

    async def _scheduled_task_management_response(
        self,
        request: ChatRequest,
        intent: str,
        task_name: str,
        fields: dict,
        session_id: str,
    ) -> ChatResponse:
        """Generate a natural-language response for scheduled-task management ops.

        Performs the operation (list/delete/pause/resume/create) and asks the
        model to describe what happened — no hardcoded strings.
        """
        from ..scheduler.schedule import format_next_run, next_daily_run
        op_details = ""

        if intent == "list":
            tasks = await self.store.get_scheduled_tasks(
                owner_user_id=request.user_id
            )
            if not tasks:
                op_details = "no tasks"
            else:
                lines = []
                for t in tasks:
                    enabled = "enabled" if t["enabled"] else "paused"
                    last = t.get("last_run") or "never run"
                    next_ts = _parse_iso_ts_safe(t.get("next_run"))
                    next_r = format_next_run(next_ts) if next_ts else "unknown"
                    lines.append(
                        f"- {t['name']} ({t.get('schedule_cron', '?')}, {enabled}; "
                        f"last: {last}, next: {next_r})"
                    )
                op_details = "tasks:\n" + "\n".join(lines)

        elif intent == "delete":
            if task_name:
                tasks = await self.store.get_scheduled_tasks(
                    owner_user_id=request.user_id
                )
                for t in tasks:
                    if t["name"] == task_name:
                        await self.store.delete_scheduled_task(t["id"])
                        op_details = f"deleted {task_name}"
                        break
                else:
                    op_details = f"not found: {task_name}"
            else:
                op_details = "no task name provided"

        elif intent == "pause":
            if task_name:
                tasks = await self.store.get_scheduled_tasks(
                    owner_user_id=request.user_id
                )
                for t in tasks:
                    if t["name"] == task_name:
                        await self.store.upsert_scheduled_task(
                            name=task_name,
                            description=t.get("description", ""),
                            schedule_cron=t.get("schedule_cron", "daily"),
                            prompt=t.get("prompt", ""),
                            enabled=False,
                            owner_user_id=request.user_id,
                        )
                        op_details = f"paused {task_name}"
                        break
                else:
                    op_details = f"not found: {task_name}"
            else:
                op_details = "no task name provided"

        elif intent == "resume":
            if task_name:
                tasks = await self.store.get_scheduled_tasks(
                    owner_user_id=request.user_id
                )
                for t in tasks:
                    if t["name"] == task_name:
                        next_tick = next_daily_run()
                        await self.store.upsert_scheduled_task(
                            name=task_name,
                            description=t.get("description", ""),
                            schedule_cron=t.get("schedule_cron", "daily"),
                            prompt=t.get("prompt", ""),
                            enabled=True,
                            owner_user_id=request.user_id,
                            next_run=next_tick.astimezone(UTC).isoformat(),
                        )
                        when = format_next_run(next_tick)
                        op_details = f"resumed {task_name}, next run {when}"
                        break
                else:
                    op_details = f"not found: {task_name}"
            else:
                op_details = "no task name provided"

        elif intent == "create":
            name = fields.get("name") or f"task_{uuid.uuid4().hex[:6]}"
            description = fields.get("description", "")
            repeat = bool(fields.get("repeat", True))
            frequency = "daily" if repeat else "once"
            prompt = fields.get("prompt") or request.message
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
            repeat_word = "daily" if repeat else "once"
            op_details = (
                f"created {name} ({repeat_word}): {description[:100]}. "
                f"Prompt: {prompt[:80]}. Next run: {when}."
            )

        prompt_text = (
            f"The user asked to manage their scheduled task list: '{request.message}'.\n"
            f"The operation was: {intent}.\n"
            f"Details: {op_details}.\n"
            f"Write a natural, conversational response telling the user what happened. "
            f"Be concise but informative. If a task wasn't found, say so clearly."
        )

        system_msg = (
            "You are a helpful assistant. The user is managing their daily task list. "
            "Their request has already been processed. Write a brief, natural response "
            "confirming what happened. Do not add unnecessary details."
        )

        from assistant.backend.pipeline.llm_client import ChatMessage

        messages = [
            ChatMessage(role="system", content=system_msg),
            ChatMessage(role="user", content=prompt_text),
        ]
        llm_resp = await self.llm_client.chat(
            messages,
            model=self.llm_client.chat_model,
            think=False,
        )
        response_text = llm_resp.content or ""

        await self._log_episode(
            request.user_id, session_id, role="assistant", content=response_text
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

    async def run_scheduled_task(
        self, prompt: str, user_id: int, task_name: str
    ) -> str:
        """Execute a scheduled task: full cognitive loop, output as string.

        Called by the scheduler for due tasks and by _handle_scheduled_task
        for run-now requests. Logs an assistant episode so the output is
        queryable memory.
        """
        date_str = datetime.now(UTC).strftime("%Y_%m_%d")
        session_id = f"scheduled-{task_name}-{date_str}"

        memory_context = await self.retriever.retrieve(
            query=prompt,
            user_id=user_id,
            session_id=session_id,
        )

        plan = classify_intent(
            query=prompt,
            task_type="functional",
            memory=memory_context,
        )

        plan_instructions = format_plan_for_prompt(plan)
        system_prompt = build_system_prompt(
            memory_context=memory_context.formatted,
            task_type="functional",
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
                search_results, _search_info = await self.search_tool.search_with_info(
                    query, num_results=5
                )
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
        llm_response = await self.llm_client.chat(
            messages,
            think=True,
            num_predict=settings.think_num_predict_cap,
        )

        response_text = (
            llm_response.content
            or llm_response.thinking
            or "Task completed."
        )

        await self._log_episode(
            user_id,
            session_id,
            role="assistant",
            content=response_text,
        )

        try:
            from assistant.backend.pipeline.extractor import (
                apply_extraction,
                extract_facts_from_document,
            )

            extraction = await extract_facts_from_document(
                response_text,
                f"scheduled_task: {prompt[:100]}",
                self.llm_client,
            )
            if extraction.slots or extraction.associations:
                await apply_extraction(
                    extraction,
                    self.store,
                    source_type="scheduled_task",
                    source_url=None,
                    source_reliability=0.6,
                )
                logger.info(
                    "run_scheduled_task: extracted %d slots, %d assocs",
                    len(extraction.slots),
                    len(extraction.associations),
                )
        except Exception as e:
            logger.warning("Scheduled task extraction failed: %s", e)

        return response_text


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
