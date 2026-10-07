"""Orchestrator: runs the cognitive loop for chat turns."""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from pydantic import BaseModel

from assistant.backend.config import settings
from assistant.backend.memory.retrieval import (
    MemoryContext,
    Retriever,
    format_memory_context,
)
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import (
    EMPTY_GENERATION_FALLBACK,
    ChatMessage,
    OllamaClient,
    build_system_prompt,
    system_prompt_overhead,
)
from assistant.backend.pipeline.reasoner import (
    Action,
    Plan,
    classify_intent,
    format_plan_for_prompt,
)
from assistant.backend.pipeline.search import (
    SearchInfo,
    SearchResult,
    WebSearchTool,
    search_info_payload,
)
from assistant.backend.pipeline.task_router import TaskType, route
from assistant.backend.pipeline.tools import builtin_tools
from assistant.backend.pipeline.user_content import CONTENT_SLOT_KEY

logger = logging.getLogger(__name__)

# One wording, shared by chat() and chat_stream(). Local inference can be slow
# (large prefill, model load) or the backend briefly unreachable; both paths must
# degrade to the same readable sentence rather than raising.
_GENERATION_FAILURE_MESSAGE = (
    "I'm having trouble reaching my language model right now. "
    "It may still be loading or thinking through a long answer — "
    "please try again in a moment."
)


def _finalize_event(answer: str, reasoning_trace: str | None = None) -> str:
    """A `finalize` SSE frame. Kept in one place so the wire shape cannot drift."""
    return (
        "data: "
        + json.dumps(
            {
                "type": "finalize",
                "answer": answer,
                "reasoning_trace": reasoning_trace,
            }
        )
        + "\n\n"
    )


def _meta_event_from_response(session_id: str | None, response: "ChatResponse"):
    """A `meta` SSE event carrying a ChatResponse's full transparency payload.

    Used by the early-return branches (scheduled task, correction) that produce a
    ChatResponse via a non-streaming handler: without this they returned after
    `finalize` and never emitted `meta`, so a stream consumer got no summaries,
    search_info, confidence, or citations for those turns. Imports lazily to avoid
    the orchestrator<->streaming circular import.
    """
    from assistant.backend.pipeline.streaming import MetaEvent

    return MetaEvent(
        session_id=session_id,
        task_type=response.task_type,
        extraction_summary=response.extraction_summary,
        search_extraction_summary=response.search_extraction_summary,
        search_info=response.search_info,
        confidence=response.confidence,
        confidence_basis=response.confidence_basis,
        citations=response.citations,
        memory_context=response.memory_context,
    )


async def _fetch_url_body(url: str) -> str | None:
    """Fetch a URL and return stripped plain text. Returns None on failure.

    Delegates to the shared `_fetch_page`, so search enrichment gets the same
    robots.txt check and per-hop SSRF check as the `fetch_url` tool -- this path
    used to fetch with `follow_redirects=True` and no robots check, a second,
    weaker implementation of "fetch a page".
    """
    from assistant.backend.pipeline.tools import _fetch_page

    text, _raw, error = await _fetch_page(url)
    if error:
        logger.warning("Failed to fetch %s: %s", url[:80], error)
        return None
    if not text.strip():
        return None
    return text[:8000]


class ChatRequest(BaseModel):
    user_id: int
    message: str
    session_id: str | None = None  # if None, generate one
    # Client-generated id for live stage progress (see /chat/status/{turn_id}).
    turn_id: str | None = None
    # Optional attached files from file upload UI
    attached_files: list[dict] = []
    # User consent for sensitive search queries (Brave)
    search_consent: bool = False
    # Explicit user "Max" toggle (UI) — escalate generation to MAX_MODEL.
    max_intelligence: bool = False
    # Scheduled-task mode: a standing task always checks for new information, so
    # search is forced and the router's storage-style veto must not suppress it.
    force_search: bool = False
    # Scheduled-task mode: the directive delivered as the user turn (the task's
    # script plus the ALERT contract), while `message` (the script) drives routing
    # and retrieval. None for ordinary turns.
    user_turn_override: str | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    task_type: str  # "functional" | "introspective"
    memory_context: str  # for --trace mode
    extraction_summary: dict | None = None  # conversation extraction (async, may be None)
    search_extraction_summary: dict | None = None  # search extraction (sync, available immediately)
    citations: list[str] = []  # source URLs for the response
    search_info: SearchInfo | None = None  # which backend + query + results (for UI transparency)
    confidence: float = 0.0  # 0-1 answer confidence (memory-grounded)
    confidence_basis: str = "none"  # "memory" | "search" | "none"


def compute_answer_confidence(
    retrieved_frames: list,
    search_info: "SearchInfo | None",
    *,
    min_relevance: float = 0.3,
) -> tuple[float, str]:
    """Memory-grounded confidence (0-1) and its basis ('memory'|'search'|'none').

    Memory answers score on the average confidence of the frames that actually
    cleared the relevance bar; search answers score on source corroboration
    (independent result domains). No model call, no extra latency.
    """
    relevant = [
        rf for rf in retrieved_frames if getattr(rf, "relevance", 0.0) >= min_relevance
    ]
    mem_conf = (
        sum(rf.frame.confidence for rf in relevant) / len(relevant)
        if relevant
        else 0.0
    )

    results = getattr(search_info, "results", None) if search_info else None
    if results:
        hosts = {
            host
            for host in (urlparse(getattr(r, "url", "") or "").hostname for r in results)
            if host
        }
        # One source is a claim; several independent sources are corroboration.
        search_conf = min(0.9, 0.5 + 0.1 * max(0, len(hosts) - 1))
        if search_conf >= mem_conf:
            return round(search_conf, 2), "search"

    if mem_conf > 0:
        return round(mem_conf, 2), "memory"
    return 0.0, "none"


@dataclass
class OrchestratorDeps:
    """Dependencies for the orchestrator. Injected for testability."""

    store: MemoryStore
    retriever: Retriever
    llm_client: OllamaClient
    search_tool: WebSearchTool  # Always present (required feature)


SCHEDULED_TASK_ALERT_PREFIX = "ALERT:"


def build_scheduled_task_directive(prompt: str) -> str:
    """Build the user message delivered to the model for a scheduled task.

    The task's instruction must reach the model as an explicit user turn;
    when it is only folded into the system prompt, a small local model
    drifts and regurgitates whatever is loudest in memory context instead
    of doing the task.

    The trailing ALERT contract lets the model flag genuinely important
    findings; the scheduler parses a final ``ALERT: <title>`` line (with an
    optional one-sentence body) into a high-visibility alert for the user.
    """
    return (
        "You are executing one of the user's standing scheduled tasks.\n\n"
        f"Task: {prompt}\n\n"
        "Complete the task now: use the search results and memory state above, "
        "check for the latest information, and deliver a concrete, useful report "
        "to the user. Do not restate old memories as if they were new findings.\n\n"
        "If something in your findings is important enough that the user should "
        f"see it right away, end your report with a line starting with "
        f"'{SCHEDULED_TASK_ALERT_PREFIX}' followed by a short title, then a "
        "one-sentence reason on the next line. Otherwise end normally."
    )


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

    def embed_fn(self) -> Callable[[str | list[str]], Awaitable[list[float] | list[list[float]]]]:
        """Embedding callable for canonical frame resolution and consolidation.

        Returns a callable that accepts a string or list of strings.
        The underlying OllamaClient.embed handles both single and batch inputs.
        """
        async def get_embedding(text: str | list[str]) -> list[float] | list[list[float]]:
            resp = await self.llm_client.embed(text)
            # For single string, resp is EmbeddingResponse with .embedding
            # For list, resp is list[EmbeddingResponse]
            if isinstance(text, list):
                return [r.embedding for r in resp]
            return resp.embedding
        return get_embedding

    async def _detect_math_intent(self, query: str) -> bool:
        """Detect if query needs mathematical computation."""
        math_keywords = {
            "calculate", "compute", "solve", "derive", "integrate", "differentiate",
            "npv", "irr", "roi", "payback", "amortize", "compound", "present value",
            "future value", "annuity", "bond", "yield", "volatility", "sharpe",
            "regression", "correlation", "covariance", "mean", "median", "std",
            "standard deviation", "variance", "percentile", "quantile", "hypothesis",
            "t-test", "chi-square", "anova", "monte carlo", "simulate", "bootstrap",
            "optimize", "minimize", "maximize", "linear programming", "constraint",
            "derivative", "integral", "limit", "series", "taylor", "fourier",
            "matrix", "eigenvalue", "determinant", "inverse", "decomposition",
            "dimensional analysis", "unit conversion", "significant figures"
        }
        q_lower = query.lower()
        return any(kw in q_lower for kw in math_keywords)

    async def _log_episode(
        self,
        user_id: int,
        session_id: str,
        role: str,
        content: str,
        frame_ids: list[int] | None = None,
        reasoning_trace: str | None = None,
        search_info: str | None = None,
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
            reasoning_trace=reasoning_trace,
            search_info=search_info,
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

    async def _run_correction(
        self,
        request: ChatRequest,
        session_id: str,
        user_episode_id: int,
        memory_context,
        progress: "Callable[[str, str], Awaitable[None]] | None" = None,
    ) -> ChatResponse:
        """Extract, validate, and apply a correction — the whole CORRECT branch.

        Shared by ``chat()`` and ``chat_stream()``. The streaming path used to
        call ``chat()`` for this branch, which re-logged the user episode and
        re-ran routing/extraction: the turn appeared twice in history and two
        redundant LLM calls were paid before the answer. The caller has already
        done steps 1-4, so this takes the resolved session id, episode id, and
        memory context rather than resolving them again.
        """
        from assistant.backend.pipeline.extractor import (
            apply_correction,
            extract_correction,
            validate_correction,
        )

        await self._report(progress, "correcting", "updating what I know")
        correction_start = time.monotonic()
        correction = await extract_correction(request.message, self.llm_client)
        correction_summary: dict = {}

        if (
            correction
            and correction.frame_name
            and correction.slot_key
            and correction.new_value is not None
        ):
            # Route before the current-value read and validation: the subject
            # decides the frame, so a rename must be checked against the identity
            # frame it will actually be written to.
            from assistant.backend.pipeline.extractor import route_correction

            correction = route_correction(correction)
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
                # No alert. The presence rule: an alert is warranted when the
                # agent learned something and the user was **not there to hear
                # it**. This happened in the user's own conversation, and
                # `_acknowledge_correction` below already says it in the reply.
                # The alert was a third copy of something on screen, and it was
                # unclearable: there is nothing to answer in a notice about
                # something that already happened. (Measured before removal: of
                # 111 alert rows, 103 were this class of noise.)
                response_text = await self._acknowledge_correction(
                    correction.frame_name,
                    correction.slot_key,
                    correction.new_value,
                    contradicted=True,
                    current_value=current_value,
                )
            else:
                correction_summary = await apply_correction(
                    correction,
                    self.store,
                    source_episode_id=user_episode_id,
                    embed_fn=self.llm_client.embed_one,
                    embedding_model=self.llm_client.embedding_model,
                )
                logger.info(
                    "Correction applied: frame=%s slot=%s value=%s "
                    "corroborated=%s",
                    correction_summary.get("frame_name"),
                    correction_summary.get("slot_key"),
                    correction_summary.get("new_value"),
                    validation.corroborated,
                )
                # No alert for an applied correction, for the same reason as the
                # contradicted branch above: it happened in this conversation and
                # `_acknowledge_correction` says so in the reply. See that branch
                # for the measured rationale.
                response_text = await self._acknowledge_correction(
                    correction.frame_name,
                    correction.slot_key,
                    correction.new_value,
                    contradicted=False,
                    current_value=None,
                )
        else:
            logger.info("Correction could not be parsed — generating natural response")
            # Provide a system prompt so the model doesn't hallucinate
            system = ChatMessage(
                role="system",
                content=(
                    "You are a helpful cognitive assistant. The user sent a message "
                    "that looked like a correction but couldn't be parsed. Respond "
                    "naturally and ask for clarification if needed."
                ),
            )
            natural_response = await self.llm_client.chat(
                [system, ChatMessage(role="user", content=request.message)],
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

        correction_time = time.monotonic() - correction_start
        logger.debug("Correction pipeline: %.3fs", correction_time)

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
                "The user corrected a stored fact but third-party evidence "
                f"contradicts the new value.\n"
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

    async def _select_generation(
        self,
        request: ChatRequest,
        plan: Plan,
    ) -> tuple[str | None, bool]:
        """Pick the generation model and thinking flag for a chat turn.

        Escalation tiers (Phase 6 M6):
        - Base: the configured default (tool loop runs on tools_model, plain
          generation on chat_model) with thinking per plan/default.
        - Max: plan.max_intelligence (auto by the reasoner) or the user's
          explicit "Max" toggle routes the whole generation phase to
          MAX_MODEL with thinking on — when that model is configured and
          supports thinking (and tools, when the tool loop is active).

        Returns (model, think); model=None means "use the role default".
        """
        escalated = request.max_intelligence or plan.max_intelligence
        max_model = self.llm_client.max_model
        use_max = bool(max_model) and escalated
        if use_max:
            supports = await self.llm_client.supports_thinking(max_model)
            if settings.tools_enabled:
                supports = supports and await self.llm_client.supports_tools(max_model)
            if not supports:
                logger.info(
                    "max_model=%s lacks thinking/tools capability; falling back to chat model",
                    max_model,
                )
                use_max = False
        if use_max:
            return max_model, True

        probe = self.llm_client.chat_model
        supports_thinking = await self.llm_client.supports_thinking(probe)
        think = False
        if plan.think and supports_thinking:
            think = True
        elif settings.chat_think_default and supports_thinking:
            think = True
        return None, think

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

    @staticmethod
    def _memory_char_budget(
        task_type: str, plan_instructions: str, self_context: str
    ) -> int:
        """Chars the memory section may occupy in the system prompt.

        The flat `system_prompt[:max_system_prompt_chars]` cut further down is a
        backstop only. Memory is appended last, so that cut lands mid-frame: the
        frame keeps its "### name" header and so reads as present while the facts
        in its tail are gone. Fitting memory to the space left after the persona
        and plan prefix drops whole frames, least relevant first, instead.

        system_prompt_overhead measures that prefix rather than reserving a
        guessed number of chars, because the prefix grows with the plan and with
        the agent's self context.
        """
        return max(
            0,
            settings.max_system_prompt_chars
            - system_prompt_overhead(task_type, plan_instructions, self_context),
        )

    @staticmethod
    def _log_turn_timings(
        turn_start: float,
        *,
        episode_ms: float | None = None,
        routing_ms: float | None = None,
        recall_ms: float | None = None,
        plan_ms: float | None = None,
        extraction_ms: float | None = None,
        search_ms: float | None = None,
        correction_ms: float | None = None,
        ttft_ms: float | None = None,
    ) -> None:
        """Log one greppable line of per-turn phase timings.

        The numbers were always computed but only at DEBUG, which no deployment
        runs, so latency work had no data to stand on. One structured line per
        turn makes it possible to ask "what is slow" instead of guessing.

        `ttft_ms` is the one that reflects what a user perceives: on the streaming
        path the first token lands long before the turn ends, so total turn time
        is close to irrelevant. Routing, recall, plan and extraction all happen
        *before* any text appears, so they are what TTFT is made of -- which is
        why they are worth watching even when total time looks fine.
        """
        parts = [
            f"turn_total_ms={(time.monotonic() - turn_start) * 1000:.0f}",
        ]
        for label, value in (
            ("episode_ms", episode_ms),
            ("routing_ms", routing_ms),
            ("recall_ms", recall_ms),
            ("plan_ms", plan_ms),
            ("extraction_ms", extraction_ms),
            ("correction_ms", correction_ms),
            ("search_ms", search_ms),
            ("ttft_ms", ttft_ms),
        ):
            if value is not None:
                parts.append(f"{label}={value * 1000:.0f}")
        logger.info("turn_timings: " + " ".join(parts))

    async def _assemble_prompt(
        self,
        plan: Plan,
        memory_context: MemoryContext,
        task_type: str,
    ) -> tuple[str, str, str]:
        """Render the system prompt, returning it and the inputs used to build it.

        Returns `(prompt_with_memory, plan_instructions, self_context)`. The last
        two are needed verbatim by `_fit_prompt_to_cap` later, once the appends
        are known -- measuring the appends against a re-derived prefix would
        reintroduce the accounting gap that let a frame get cut mid-way.

        This exists so the three prompt-building paths -- chat, scheduled and
        streaming -- cannot drift apart. They were near-identical copies that had
        already drifted once: one passed a hardcoded "functional" where the others
        passed the real task type, so the memory budget and the prompt's own task
        guidance could describe different turns. Copy-pasted sizing arithmetic is
        exactly where a silent, user-visible bug hides.
        """
        plan_instructions = format_plan_for_prompt(plan)
        self_context = await self._get_self_context()
        system_prompt = build_system_prompt(
            memory_context=format_memory_context(
                memory_context,
                self._memory_char_budget(task_type, plan_instructions, self_context),
            ),
            task_type=task_type,
            planinstructions=plan_instructions,
            self_context=self_context,
        )
        return system_prompt, plan_instructions, self_context

    async def _render_supplied_content(
        self, system_prompt: str, extraction_summary: dict
    ) -> str:
        """Append content the user supplied this turn, in full, in order.

        Retrieval and extraction run concurrently, so a frame written *during* this
        turn is never among the frames retrieval returns. Without this the model
        holds the user's list in memory and still has nothing to answer from.

        Measured on the Plan A live test (2026-09-30): the 7-URL paste registered
        as frame 4546 and `context_stats` reported the 10 frames retrieval found --
        4546 was not one of them. The answer was then built from search results
        about the links, which is the exact failure Plan A exists to fix.

        Injected whole, for the same reason a file frame renders a content hint
        rather than a preview: a transform request (rank, sort, summarise, compare)
        operates on the sequence, and a truncated view would silently change the
        answer rather than merely shorten it.
        """
        supplied = extraction_summary.get("user_content")
        if not supplied:
            return system_prompt
        frame_id = supplied.get("frame_id")
        if not frame_id:
            return system_prompt

        frame = await self.store.get_frame(frame_id)
        if frame is None:
            return system_prompt
        slots = await self.store.get_slots_for_frame(frame.id)
        content_slot = next((s for s in slots if s.key == CONTENT_SLOT_KEY), None)
        if content_slot is None or not (content_slot.value or "").strip():
            return system_prompt

        item_count = supplied.get("item_count")
        item_note = f" ({item_count} items)" if item_count else ""
        return (
            system_prompt
            + f"\n\n**Content the user supplied this turn{item_note}:**\n"
            + content_slot.value
            + "\nThis content is the subject of the request. Work from it directly "
            "— if the user asked you to rank, sort, summarise, or compare, do that "
            "to THIS content, in the order given. Use search only to add context, "
            "never to replace it."
        )

    def _fit_prompt_to_cap(
        self,
        prompt_with_memory: str,
        final_prompt: str,
        memory_context: MemoryContext,
        task_type: str,
        plan_instructions: str,
        self_context: str,
    ) -> tuple[str, bool]:
        """Re-render memory so the *finished* prompt fits, not just the prefix.

        `_memory_char_budget` can only see `build_system_prompt`'s own overhead.
        Everything appended after the memory section -- stored facts, a computed
        result, search results -- lands outside that budget, so the prompt
        overran the cap and the flat character cut fired. Because memory is
        rendered near the end, that cut sliced through a frame: the frame kept its
        "### name" header and so read as present to the model while the facts in
        its tail were gone. The user saw an answer stop mid-sentence.

        The suffix is the exact set of chars the appends added, so memory gives up
        precisely that much and whole frames are dropped instead. No guessed
        reserve, and it stays correct as the persona, plan and self context grow.

        `prompt_with_memory` is the prompt as `build_system_prompt` returned it and
        `final_prompt` is that plus the appends, so the suffix is the difference.
        """
        cap = settings.max_system_prompt_chars
        if len(final_prompt) <= cap:
            return final_prompt, False

        suffix = final_prompt[len(prompt_with_memory) :]
        budget = max(
            0,
            cap
            - system_prompt_overhead(task_type, plan_instructions, self_context)
            - len(suffix),
        )
        rebuilt = (
            build_system_prompt(
                memory_context=format_memory_context(memory_context, budget),
                task_type=task_type,
                planinstructions=plan_instructions,
                self_context=self_context,
            )
            + suffix
        )
        if len(rebuilt) > cap:
            # Only reachable when the appends alone exceed the cap. Genuinely a
            # last resort, and the cut lands in the appended tail rather than in
            # a frame.
            logger.warning(
                "System prompt truncated from %d to %d chars: post-memory content "
                "alone exceeds the cap",
                len(rebuilt),
                cap,
            )
            return rebuilt[:cap] + "\n\n[... truncated ...]", True
        logger.info(
            "Refitted memory section by %d chars to fit the prompt cap "
            "(appended %d chars after memory)",
            len(final_prompt) - len(rebuilt),
            len(suffix),
        )
        return rebuilt, False

    async def _remember_computation(self, computation_result: str) -> None:
        """Store a `compute` result as a memory slot, best-effort.

        Shared by chat() and chat_stream(). The streaming path used to build the
        same "Computed Result" prompt and then drop the value, so a fact the user
        had just been given vanished at the end of the turn while the
        non-streaming path kept it.
        """
        from assistant.backend.pipeline.extractor import (
            ExtractedSlot,
            ExtractionResult,
            apply_extraction,
        )

        try:
            extraction = ExtractionResult(
                slots=[
                    ExtractedSlot(
                        frame_name=f"computation_{uuid.uuid4().hex[:8]}",
                        key="result",
                        value=computation_result[:5000],  # Truncate if too long
                        confidence=0.9,
                        source_type="computation",
                    )
                ],
                associations=[],
            )
            await apply_extraction(
                extraction,
                self.store,
                source_type="computation",
                source_reliability=0.9,
            )
            logger.info("Stored computation result in memory")
        except Exception as e:
            logger.warning("Failed to store computation result: %s", e)

    def _append_response_footers(
        self,
        answer: str,
        citations: list[str],
        task_type_value: str,
        retrieved_frame_count: int,
    ) -> str:
        """Append the sources list and the "answered from memory" marker.

        Shared by chat() and chat_stream(). The streaming path is the one the
        frontend actually uses, and it never grew either footer: search answers
        arrived with no citations and introspective answers without the memory
        provenance the non-streaming path had shown all along.
        """
        response_text = answer
        if not response_text:
            response_text = EMPTY_GENERATION_FALLBACK
            logger.warning("Empty LLM response")
        # Sources only for informational/search tasks, never for a chat reply
        # that merely happened to run a search.
        if citations and task_type_value == "search":
            unique_citations = list(dict.fromkeys(citations))
            response_text += "\n\n**Sources:**\n" + "\n".join(
                f"- {url}" for url in unique_citations
            )
        # Memory source indicator: show for introspective/recall when frames were
        # retrieved.
        if task_type_value == "introspective" and retrieved_frame_count:
            fact_word = "facts" if retrieved_frame_count != 1 else "fact"
            response_text += (
                f"\n\n<small>_(Answered from memory"
                f" · {retrieved_frame_count} {fact_word} retrieved)_</small>"
            )
        return response_text

    async def _create_learning_alerts(
        self,
        user_id: int,
        extraction_summary: dict,
        search_extraction_summary: dict,
    ) -> None:
        """No-op. Facts learned mid-conversation are not alerts.

        The presence rule (see assistant/AGENTS.md):

            An alert is warranted when the agent learned something and the user was
            not there to hear it.

        This method did the opposite. It raised bell notifications for a conflict
        auto-resolved during the turn, or for facts a search had just stored — both
        of which happened while the user was watching, in the conversation they were
        having. "New facts learned from search" is not news to the person who just
        asked for the search.

        Nothing is lost by removing it. The information was already on screen twice
        over: the response carries `extraction_summary` and
        `search_extraction_summary`, and Message.tsx renders them as "What I learned"
        and "Found from search", itemised per slot with a conflict flag. The bell
        entry was a third copy of something the user could already read.

        Kept as a named method rather than deleting the call sites, so the two
        orchestrator paths stay symmetrical and the reasoning lives where the
        behaviour used to be. Measured on the live brain before this: of 111 alert
        rows, 103 were this class of noise and 8 were real.
        """
        return


    async def chat(
        self,
        request: ChatRequest,
        progress: "Callable[[str, str], Awaitable[None]] | None" = None,
        skip_route: bool = False,
    ) -> ChatResponse:
        """Run the full cognitive loop for a chat turn (non-streaming).

        An adapter over ``_run_turn``, the single implementation of the loop: it
        drains the same SSE events the UI receives and rebuilds a ``ChatResponse``
        from the terminal ``finalize`` and ``meta`` events. This method used to be
        a second, ~600-line copy of the loop that drifted from the streaming path
        six times (see ``test_stream_parity.py``); the copy is gone, so there is
        one loop to change.

        progress: optional async callback (stage, detail) for live UI status.
        skip_route: when True, skip the task-type router and treat the message as
        functional. Used by internal callers (e.g. scheduled-task management
        responses) that already know the intent.
        """
        answer = ""
        meta: dict = {}
        async for frame in self._run_turn(request, progress, skip_route):
            if not frame.startswith("data: "):
                continue
            try:
                event = json.loads(frame[len("data: "):].strip())
            except (json.JSONDecodeError, ValueError):
                continue
            event_type = event.get("type")
            if event_type == "finalize":
                answer = event.get("answer", answer)
            elif event_type == "meta":
                meta = event

        return ChatResponse(
            response=answer,
            session_id=meta.get("session_id") or request.session_id or "",
            task_type=meta.get("task_type") or "functional",
            memory_context=meta.get("memory_context") or "",
            extraction_summary=meta.get("extraction_summary"),
            search_extraction_summary=meta.get("search_extraction_summary"),
            citations=meta.get("citations") or [],
            search_info=meta.get("search_info"),
            confidence=meta.get("confidence") or 0.0,
            confidence_basis=meta.get("confidence_basis") or "none",
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
                        # Echo the resolved session id (the same value used for
                        # the whole turn), not a fresh uuid. The frontend keys
                        # conversation state on the returned session_id, so
                        # minting a new one made the next message start an empty
                        # conversation -- the user asked "run my briefing now"
                        # and silently lost their thread.
                        return ChatResponse(
                            response=result,
                            session_id=session_id,
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
        """Execute a scheduled task through the single cognitive loop.

        The task is memory: a `scheduled_task` frame whose `prompt` slot is the
        script. That script drives routing and retrieval, and is delivered to the
        model as the user turn (via the directive, which carries the ALERT
        contract). Search is forced — a standing task must check for new
        information — and the full loop runs, so the model has its tools.

        This used to be a third, partial copy of the pipeline: no router, no tool
        loop, its own search with the instruction as the query, and facts mined
        from its own report. The instruction-as-query was the measured cause of
        `job_postings_monitor` returning tool documentation instead of postings.

        Returns the assistant's answer. The scheduler parses any `ALERT:` footer
        and records the run (episode, daily-run frame, associations, alerts).
        """
        date_str = datetime.now(UTC).strftime("%Y_%m_%d")
        request = ChatRequest(
            user_id=user_id,
            message=prompt,
            session_id=f"scheduled-{task_name}-{date_str}",
            force_search=True,
            user_turn_override=build_scheduled_task_directive(prompt),
            # Scheduling the task *is* the user's consent to search for it: there
            # is no one present at the daily tick to answer a consent prompt, and
            # the user asked for this search when they created the task. Without
            # this the loop's Brave sensitivity gate would replace the task's
            # output with a "do you want to proceed?" prompt nobody will see.
            search_consent=True,
        )
        response = await self.chat(request)
        return response.response

    async def chat_stream(
        self,
        request: ChatRequest,
        progress: "Callable[[str, str], Awaitable[None]] | None" = None,
        skip_route: bool = False,
    ) -> AsyncGenerator[str, None]:
        """Run the cognitive loop as an SSE event stream (the UI path).

        A thin pass-through over `_run_turn`, the single implementation of the
        loop. Output is byte-identical to what this method produced when it held
        the loop itself.
        """
        async for event in self._run_turn(request, progress, skip_route):
            yield event

    async def _run_turn(
        self,
        request: ChatRequest,
        progress: "Callable[[str, str], Awaitable[None]] | None" = None,
        skip_route: bool = False,
    ) -> AsyncGenerator[str, None]:
        """The single cognitive loop, emitted as serialized SSE events.

        Both public paths consume this: `chat_stream()` re-yields it to the UI,
        and `chat()` drains it to build a `ChatResponse`. It was previously
        duplicated in full by `chat()`, which is how the two drifted six times
        (see test_stream_parity.py).

        Yields SSE-formatted events (`stage` … `finalize` … `meta`) for real-time
        UI updates.
        """
        # Import here to avoid circular imports
        from assistant.backend.pipeline.llm_client import ChatMessage
        from assistant.backend.pipeline.streaming import (
            MetaEvent,
            serialize_event,
            stream_tool_loop,
        )

        # 1. Session
        session_id = request.session_id or str(uuid.uuid4())
        turn_start = time.monotonic()

        # 2. Log user episode
        user_episode = await self._log_episode(
            request.user_id,
            session_id,
            role="user",
            content=request.message,
        )
        episode_log_time = time.monotonic() - turn_start

        # 2b. Deterministic alert backstop (see chat() for the full note). The
        # streaming path is the one the frontend uses, so this is the copy that
        # actually runs in production.
        try:
            await self.store.resolve_alerts_for_session(session_id)
        except Exception as exc:
            logger.warning("Alert backstop failed: %s", exc)

        # 3. Classify task type + search intent AND Retrieve memory context IN PARALLEL
        await self._report(progress, "routing", "reading your message")
        await self._report(progress, "recall", "checking my memory")

        routing_start = time.monotonic()
        recall_start = time.monotonic()

        if skip_route:
            task_type = TaskType.FUNCTIONAL
            classification = None
            # Still need retrieval even when skipping route
            memory_context = await self.retriever.retrieve(
                query=request.message,
                user_id=request.user_id,
                session_id=session_id,
            )
        else:
            # Run routing and retrieval concurrently
            route_task = asyncio.create_task(route(request.message, self.llm_client))
            retrieve_task = asyncio.create_task(
                self.retriever.retrieve(
                    query=request.message,
                    user_id=request.user_id,
                    session_id=session_id,
                )
            )
            classification, memory_context = await asyncio.gather(route_task, retrieve_task)
            task_type = classification.task_type

        routing_time = time.monotonic() - routing_start
        recall_time = time.monotonic() - recall_start
        logger.debug("Routing: %.3fs, Memory recall: %.3fs (parallel)", routing_time, recall_time)

        # 3b. Handle scheduled task intent. Guarded against re-entry: when the
        # router is running *inside* a task's execution (force_search), the script
        # must be executed, not treated as another management request — otherwise
        # run_scheduled_task -> chat -> _handle_scheduled_task -> run_scheduled_task
        # recurses forever.
        if task_type == TaskType.SCHEDULED and not skip_route and not request.force_search:
            # The non-streaming handler does the work; re-emit its response as
            # stream events. The meta event is required (parity gap G2): this
            # branch used to return after finalize, so the response shape never
            # reached a stream consumer.
            response = await self._handle_scheduled_task(request, session_id)
            yield _finalize_event(response.response)
            yield serialize_event(_meta_event_from_response(session_id, response))
            return

        # 5. Reason: decide action based on memory sufficiency
        # 6. Extract user-stated facts BEFORE generation (parallel with reasoner)
        plan_start = time.monotonic()
        extraction_start = time.monotonic()

        plan_task = asyncio.create_task(asyncio.to_thread(
            classify_intent,
            query=request.message,
            task_type=task_type.value,
            memory=memory_context,
        ))
        # Report learning stage before parallel extraction
        await self._report(progress, "learning", "learning from our conversation")
        extraction_task = asyncio.create_task(
            store_turn_memory(
                user_message=request.message,
                assistant_response="",
                store=self.store,
                llm_client=self.llm_client,
                source_episode_id=user_episode.id,
            )
        )

        plan, extraction_summary = await asyncio.gather(plan_task, extraction_task)

        plan_time = time.monotonic() - plan_start
        extraction_time = time.monotonic() - extraction_start
        logger.debug("Reasoner: %.3fs, Extraction: %.3fs (parallel)", plan_time, extraction_time)

        # The streaming path runs search after this point, so pre-generation
        # latency is only final once search is done. `stream_search_s` and
        # `ttft_s` are filled in further down.
        stream_search_s = 0.0
        ttft_s: float | None = None

        # 5a. Storage statements must not trigger external search. A scheduled
        # task is exempt: it is a standing instruction to CHECK FOR NEW
        # information, so its search is forced and the veto must not suppress it.
        if request.force_search:
            plan.search_needed = True
        elif (
            plan.search_needed
            and task_type != TaskType.SEARCH
            and not skip_route
            and classification is not None
            and classification.wants_search is False
        ):
            logger.info("Search vetoed by router for storage-style turn")
            plan.action = Action.ANSWER
            plan.search_needed = False

        # 5a-bis. A turn transforming user-supplied content answers from that
        # content; search must not become the subject (see chat() for the full
        # note, and Plan A for the failure this prevents).
        supplied_content = extraction_summary.get("user_content")
        if plan.search_needed and supplied_content:
            logger.info(
                "Search de-prioritised: turn transforms user-supplied content "
                "(frame=%s, %s items)",
                supplied_content.get("frame_name"),
                supplied_content.get("item_count"),
            )
            plan.search_needed = False

        # 5b. Handle correction intent
        if plan.action == Action.CORRECT:
            # Same handler as chat() -- previously this re-entered chat(), which
            # double-logged the user episode and re-ran routing/extraction.
            response = await self._run_correction(
                request, session_id, user_episode.id, memory_context, progress
            )
            # meta is required here too (parity gap G2), as in the scheduled branch.
            yield _finalize_event(response.response)
            yield serialize_event(_meta_event_from_response(session_id, response))
            return

        stored_slots = extraction_summary.get("slots") or []

        # 7. Build system prompt with memory context + reasoner guidance
        prompt_with_memory, plan_instructions, self_context = await self._assemble_prompt(
            plan, memory_context, task_type=task_type.value
        )
        system_prompt = prompt_with_memory

        if stored_slots:
            lines = [f"- {s['frame_name']}.{s['key']} = {s['value']}" for s in stored_slots]
            system_prompt += (
                "\n\n**Facts you just stored this turn:**\n"
                + "\n".join(lines)
                + "\nAcknowledge these naturally, in your own words."
            )

        system_prompt = await self._render_supplied_content(
            system_prompt, extraction_summary
        )

        # Math computation path
        computation_result = None
        if await self._detect_math_intent(request.message):
            if self.llm_client.math_model:
                try:
                    await self._report(progress, "computing", "running mathematical computation")
                    computation_result = await self.llm_client.execute_python(
                        f"Solve this step by step: {request.message}"
                    )
                    logger.info("Math computation completed: %d chars", len(computation_result))
                except Exception as e:
                    logger.warning("Math computation failed: %s", e)

        if computation_result:
            system_prompt += (
                f"\n\n**Computed Result (verified via Python execution):**\n"
                f"{computation_result}\n"
                f"Incorporate this result into your response. Cite as 'computed'."
            )
            await self._remember_computation(computation_result)

        # 7b. Execute search if reasoner says it's needed
        search_results: list[SearchResult] = []
        search_extraction_summary: dict = {}
        search_info: SearchInfo | None = None
        if plan.search_needed:
            await self._report(progress, "searching", "searching the web")
            stream_search_start = time.monotonic()
            # `classification` is None when skip_route=True; see the identical
            # guard in chat() for why the router veto does not cover it.
            from assistant.backend.pipeline.search import (
                filter_relevant,
                sanitize_query,
            )

            routed_query = classification.search_query if classification else None
            query = routed_query or sanitize_query(request.message)
            logger.info("Reasoner triggered search for: %s", query[:80])
            backend_name = self.search_tool.backend_name
            extraction_budget = self.search_tool.max_results_for_extraction
            relevance_threshold = (
                settings.brave_search_min_relevance
                if backend_name == "brave"
                else settings.search_min_relevance
            )

            user_consent = getattr(request, 'search_consent', False)
            try:
                search_results, search_info = await self.search_tool.search_with_info(
                    query,
                    num_results=extraction_budget,
                    llm_client=self.llm_client,
                    user_consent=user_consent
                )
            except Exception as e:
                logger.warning("Search failed, continuing without results: %s", e)
                search_results = []
                search_info = None

            if search_info and search_info.consent_required:
                sensitivity = search_info.sensitivity
                categories = (
                    ", ".join(sensitivity.categories)
                    if sensitivity.categories
                    else "general"
                )
                consent_msg = (
                    f"This search query may contain sensitive information "
                    f"({sensitivity.level.value}: {sensitivity.reason}). "
                    f"Categories: {categories}. "
                    f"Search via Brave would send this query to their servers. "
                    f"Do you want to proceed?"
                )
                event = {
                    'type': 'finalize',
                    'answer': consent_msg,
                    'reasoning_trace': None
                }
                # Emit metadata first so the UI can open the consent dialog with
                # the search_info transparency record (backend, query, sources).
                yield serialize_event(
                    MetaEvent(
                        session_id=session_id,
                        task_type="search_consent_required",
                        search_info=search_info,
                    )
                )
                yield f"data: {json.dumps(event)}\n\n"
                return

            # Relevance gate: drop links that don't belong to the query before
            # they can pollute the system prompt or citations. Bounded, exactly
            # as in chat(): an embedder that stalls must not hang the live
            # stream, and this is the path the frontend actually uses.
            try:
                search_results = await asyncio.wait_for(
                    filter_relevant(
                        search_results,
                        query,
                        self.embed_fn(),
                        min_relevance=relevance_threshold,
                    ),
                    timeout=settings.search_timeout,
                )
            except TimeoutError:
                logger.warning(
                    "filter_relevant timed out after %.1fs; keeping all results",
                    settings.search_timeout,
                )

            if search_results:
                display_results = search_results[: settings.max_search_results_in_prompt]
                search_text = "\n".join(
                    f"- [{r.title}]({r.url}) - {r.snippet}" for r in display_results
                )
                system_prompt += f"\n\n**Search Results:**\n{search_text}"

                # Extract facts from search results
                from assistant.backend.pipeline.extractor import (
                    apply_search_extraction,
                    extract_facts_from_document,
                    extract_facts_from_search,
                    filter_duplicate_slots,
                    merge_extractions,
                )

                # Extract facts from search snippets with timeout
                try:
                    snippet_extraction = await asyncio.wait_for(
                        extract_facts_from_search(
                            request.message, search_results, self.llm_client
                        ),
                        timeout=settings.search_timeout,
                    )
                except TimeoutError:
                    logger.warning(
                "extract_facts_from_search timed out after %.1fs",
                settings.search_timeout,
            )
                    from assistant.backend.pipeline.extractor import ExtractionResult
                    snippet_extraction = ExtractionResult()

                # Brave: fetch top result bodies in parallel
                if backend_name == "brave" and search_results:
                    try:
                        bodies = await asyncio.gather(
                            *[
                                _fetch_url_body(r.url)
                                for r in search_results[: settings.max_search_results_in_prompt]
                            ],
                            return_exceptions=True,
                        )
                        extraction_tasks = [
                            extract_facts_from_document(body, result.url, self.llm_client)
                            for result, body in zip(
                                search_results[: settings.max_search_results_in_prompt],
                                bodies,
                                strict=True,
                            )
                            if not isinstance(body, Exception) and body
                        ]
                        if extraction_tasks:
                            document_extractions = await asyncio.gather(
                                *extraction_tasks, return_exceptions=True
                            )
                            valid_extractions = [
                                ext for ext in document_extractions
                                if not isinstance(ext, Exception) and ext
                            ]
                            if valid_extractions:
                                snippet_extraction = merge_extractions(
                                    snippet_extraction, *valid_extractions
                                )
                    except Exception as e:
                        logger.warning("Brave full-page fetch failed: %s", e)

                search_extraction = filter_duplicate_slots(
                    snippet_extraction,
                    stored_slots,
                )
                # Apply search extraction with timeout
                try:
                    search_extraction_summary = await asyncio.wait_for(
                        apply_search_extraction(
                            search_extraction,
                            search_results,
                            self.store,
                            embed_fn=self.embed_fn(),
                            backend_name=backend_name,
                            embedding_model=self.llm_client.embedding_model,
                        ),
                        timeout=settings.search_timeout,
                    )
                except TimeoutError:
                    logger.warning(
                "apply_search_extraction timed out after %.1fs",
                settings.search_timeout,
            )
                    search_extraction_summary = {}

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
                        self.llm_client.embedding_model,
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

        # Collect citations from search results only
        citations: list[str] = []
        for result in search_results:
            if result.url:
                citations.append(result.url)

        # Search sits between the plan and generation, so it is dead time in
        # front of the first token. It is also the part that varies most turn to
        # turn, which is why it needs a number of its own.
        if plan.search_needed:
            stream_search_s = time.monotonic() - stream_search_start

        # Build conversation history
        history_messages: list[ChatMessage] = []
        if session_id:
            # Owner-scoped in SQL now; bound to the tail we can use (6 prior
            # turns plus the current one, which `[:-1]` drops).
            session_episodes = await self.store.get_episodes_for_session(
                session_id, user_id=request.user_id, limit=7
            )
            prior_turns = session_episodes[:-1]
            max_turns = min(len(prior_turns), 6)
            prior_turns = prior_turns[-max_turns:] if max_turns > 0 else []
            for ep in prior_turns:
                history_messages.append(ChatMessage(role=ep.role, content=ep.content))

        # Hard limit on system prompt
        system_prompt, truncated = self._fit_prompt_to_cap(
            prompt_with_memory,
            system_prompt,
            memory_context,
            task_type.value,
            plan_instructions,
            self_context,
        )

        # Structured logging
        logger.info(
            "context_stats: prompt_chars=%d frames=%d episodes=%d search_results=%d truncated=%s",
            len(system_prompt),
            len(memory_context.retrieved_frames),
            len(memory_context.recent_episodes),
            len(search_results),
            truncated,
        )

        # Build messages
        messages = [ChatMessage(role="system", content=system_prompt)]
        messages.extend(history_messages)
        messages.append(
            ChatMessage(
                role="user",
                content=request.user_turn_override or request.message,
            )
        )

        # Convert to dict format for streaming
        messages_dict = [m.model_dump() for m in messages]

        # Everything above the generation call is dead time for the user: no text
        # has been emitted yet, so this span is exactly what they wait through
        # before the first word. On the streaming path it is the latency that
        # matters -- total turn time is close to irrelevant once the answer is
        # arriving word by word.
        logger.info(
            "turn_pregen: pregen_ms=%.0f routing_ms=%.0f recall_ms=%.0f "
            "plan_ms=%.0f extraction_ms=%.0f search_ms=%.0f",
            (time.monotonic() - turn_start) * 1000,
            routing_time * 1000, recall_time * 1000,
            plan_time * 1000, extraction_time * 1000,
            stream_search_s * 1000,
        )

        # Initialised before either branch so a generation failure has something
        # to fall back to. The streaming path -- which the frontend actually
        # uses -- used to let a generation exception escape the generator, so
        # the user saw a broken stream instead of the sentence chat() shows.
        final_answer = ""
        final_reasoning: str | None = None

        # Check if tools enabled
        if settings.tools_enabled:
            tools = builtin_tools(
                self.search_tool,
                store=self.store,
                llm_client=self.llm_client,
                embed_fn=self.embed_fn(),
            )
            tool_names = [t["function"]["name"] for t in tools]
            logger.info("DEBUG: Available tools for streaming: %s", tool_names)

            # Determine model, think and num_predict (max-intelligence aware)
            gen_model, think = await self._select_generation(request, plan)
            num_predict = settings.think_num_predict_cap if think else None

            if gen_model:
                await self._report(progress, "reasoning", "applying maximum intelligence")
            elif think:
                await self._report(progress, "reasoning", "thinking it through")
            else:
                await self._report(progress, "responding", "writing a reply")

            # Stream using the tool loop (Phase 4: stream full tool loop including tools)
            # We need to capture the final answer to persist it as an episode
            
            async def _stream_and_capture():
                nonlocal final_answer, final_reasoning, ttft_s
                async for event in stream_tool_loop(
                    self.llm_client,
                    messages_dict,
                    tools,
                    model=gen_model,
                    think=think,
                    num_predict=num_predict,
                    user_id=str(request.user_id),
                    session_id=session_id,
                ):
                    # Parse event to capture final answer
                    try:
                        import json
                        event_data = json.loads(event.replace("data: ", "").strip())
                        etype = event_data.get("type")
                        if etype == "text_delta":
                            final_answer += event_data.get("delta", "")
                        elif etype == "finalize":
                            final_answer = event_data.get("answer", final_answer)
                            final_reasoning = event_data.get("reasoning_trace")
                        # TTFT is the first moment the user can see any of the
                        # answer. `finalize` counts, and on the tool path it is
                        # currently the ONLY one that arrives: stream_tool_loop
                        # uses blocking chat() calls and emits a whole-answer
                        # FinalizeEvent, so `text_delta` never fires there. So
                        # ttft_ms on tool turns ~= total generation time, which
                        # is the honest number -- the user really did wait that
                        # long for any text. Recording only text_delta would
                        # report a satisfying small number for a wait that never
                        # ended early.
                        if etype in ("text_delta", "finalize") and ttft_s is None:
                            ttft_s = time.monotonic() - turn_start
                    except Exception:
                        pass
                    yield event
            
            try:
                async for event in _stream_and_capture():
                    yield event
            except Exception as e:
                logger.error("Streaming generation failed: %s", e)
                final_answer = _GENERATION_FAILURE_MESSAGE
                yield _finalize_event(final_answer)

        else:
            # No tools - just stream the chat response
            gen_model, think = await self._select_generation(request, plan)
            num_predict = settings.think_num_predict_cap if think else None

            if gen_model:
                await self._report(progress, "reasoning", "applying maximum intelligence")
            elif think:
                await self._report(progress, "reasoning", "thinking it through")
            else:
                await self._report(progress, "responding", "writing a reply")

            # Simple streaming without tools - collect deltas
            accumulated_content = ""
            try:
                async for chunk in self.llm_client.chat_stream(
                    messages,
                    model=gen_model or self.llm_client.chat_model,
                    think=think,
                    num_predict=num_predict,
                ):
                    if chunk.content:
                        # First real token: the wait the user actually feels is over.
                        # (No-tools path does stream deltas, so this fires early.)
                        if ttft_s is None:
                            ttft_s = time.monotonic() - turn_start
                        accumulated_content += chunk.content
                        event = {'type': 'text_delta', 'delta': chunk.content}
                        yield f"data: {json.dumps(event)}\n\n"
                    if chunk.done:
                        final_answer = accumulated_content
                        final_reasoning = chunk.thinking
                        event = {
                            'type': 'finalize',
                            'answer': final_answer,
                            'reasoning_trace': chunk.thinking
                        }
                        yield f"data: {json.dumps(event)}\n\n"
                        break
            except Exception as e:
                logger.error("Streaming generation failed: %s", e)
                final_answer = _GENERATION_FAILURE_MESSAGE
                yield _finalize_event(final_answer)

        # Persist assistant episode to database
        if final_answer and session_id:
            try:
                await self._log_episode(
                    request.user_id,
                    session_id,
                    role="assistant",
                    content=final_answer,
                    # G4: the stream captured `final_reasoning` and emitted it to
                    # the client but never stored it, so a streamed turn's
                    # reasoning trace was lost. chat() has always persisted it.
                    reasoning_trace=final_reasoning,
                    search_info=search_info_payload(search_info),
                )
            except Exception as e:
                logger.warning("Failed to log assistant episode: %s", e)

        # Same two footers chat() has always appended: "**Sources:**" for search
        # answers and the memory-provenance marker for introspective ones. The
        # streamed finalize carried the raw answer, so re-emit only when there is
        # something to add; the frontend replaces the message on finalize.
        footered = self._append_response_footers(
            final_answer,
            citations,
            task_type.value,
            len(memory_context.retrieved_frames),
        )
        if footered != final_answer:
            final_answer = footered
            yield _finalize_event(final_answer, final_reasoning)

        # Learning alerts: the same bell notifications chat() raises, so the web
        # UI learns that a search stored facts or a conflict was auto-resolved.
        await self._create_learning_alerts(
            request.user_id, extraction_summary, search_extraction_summary
        )

        self._log_turn_timings(
            turn_start,
            episode_ms=episode_log_time,
            routing_ms=routing_time,
            recall_ms=recall_time,
            plan_ms=plan_time,
            extraction_ms=extraction_time,
            search_ms=stream_search_s,
            ttft_ms=ttft_s,
        )

        # Final metadata: same transparency the non-streaming ChatResponse carries
        # (session id, task type, extraction/search summaries, search info). The UI
        # uses it for the consent dialog, search trace, and "what I learned".
        confidence, confidence_basis = compute_answer_confidence(
            memory_context.retrieved_frames, search_info
        )
        yield serialize_event(
            MetaEvent(
                session_id=session_id,
                task_type=task_type,
                extraction_summary=extraction_summary,
                search_extraction_summary=search_extraction_summary,
                search_info=search_info,
                confidence=confidence,
                confidence_basis=confidence_basis,
                citations=citations,
                memory_context=memory_context.formatted,
            )
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

    Also registers any body of content the user *supplied* (Plan A). That is a
    separate concern from fact extraction: a pasted list is not a fact about an
    entity, so extraction stores nothing for it and the content would otherwise be
    unreachable on the next turn. Detection is model-free and this runs inside the
    task the orchestrator already dispatches, so the registration costs no extra
    call on the hot path.
    """
    from assistant.backend.pipeline.extractor import extract_and_apply
    from assistant.backend.pipeline.user_content import (
        detect_user_content,
        register_user_content,
        user_content_enabled,
    )

    result = await extract_and_apply(
        user_message,
        assistant_response,
        store,
        llm_client,
        source_episode_id=source_episode_id,
    )

    supplied = None
    if user_content_enabled():
        detected = detect_user_content(user_message)
        if detected is not None:
            try:
                supplied = await register_user_content(
                    detected,
                    store,
                    source_episode_id=source_episode_id,
                    embed_fn=llm_client.embed,
                    embedding_model=llm_client.embedding_model,
                )
            except Exception as exc:  # registration must not break the turn
                logger.warning("Registering user-supplied content failed: %s", exc)

    frame_ids = list(result.get("frame_ids") or [])
    if supplied and supplied.get("frame_id"):
        frame_ids.append(supplied["frame_id"])
    if frame_ids:
        await store.update_episode_frame_ids(source_episode_id, frame_ids)

    if supplied:
        # Surfaced in the same summary shape the UI already reads, so the trace
        # panel can show "registered your list" without a second vocabulary.
        result = {**result, "user_content": supplied}
    return result
