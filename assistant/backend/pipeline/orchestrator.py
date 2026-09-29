"""Orchestrator: runs the cognitive loop for chat turns."""

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from assistant.backend.config import settings
from assistant.backend.memory.retrieval import (
    MemoryContext,
    Retriever,
    format_memory_context,
)
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import (
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
    # Optional attached files from file upload UI
    attached_files: list[dict] = []
    # User consent for sensitive search queries (Brave)
    search_consent: bool = False
    # Explicit user "Max" toggle (UI) — escalate generation to MAX_MODEL.
    max_intelligence: bool = False


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
                # Create alert for contradicted correction
                try:
                    await self.store.create_alert(
                        user_id=request.user_id,
                        type="correction",
                        title="Correction contradicted by sources",
                        message=(
                            f"Your correction to '{correction.frame_name}."
                            f"{correction.slot_key}' was contradicted by "
                            f"third-party sources and not applied."
                        ),
                        severity="warning",
                    )
                except Exception as e:
                    logger.warning("Failed to create correction alert: %s", e)
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
                # Create alert for applied correction
                try:
                    corr_msg = (
                        f"Updated '{correction.frame_name}.{correction.slot_key}' "
                        f"to '{correction.new_value}'."
                    )
                    if validation.corroborated:
                        corr_msg += " Corroborated by sources."
                    else:
                        corr_msg += " No third-party sources available."
                    await self.store.create_alert(
                        user_id=request.user_id,
                        type="correction",
                        title="Correction applied",
                        message=corr_msg,
                        severity="info",
                    )
                except Exception as e:
                    logger.warning("Failed to create correction alert: %s", e)
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
            response_text = "I'm not sure how to respond to that."
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
        """Raise the bell notifications for facts learned this turn.

        Shared by chat() and chat_stream(). The streaming path had none of these,
        so a user on the web UI was never told a search had stored facts or that a
        contradiction had been auto-resolved, even when the trace panel said so.
        """
        try:
            conv_conflicts = extraction_summary.get("conflicts_created", 0)
            if conv_conflicts > 0:
                fact_word = "fact" if conv_conflicts == 1 else "facts"
                await self.store.create_alert(
                    user_id=user_id,
                    type="conflict",
                    title="Auto-resolved conflict in learning",
                    message=(
                        f"{conv_conflicts} {fact_word} you mentioned contradicted "
                        "existing memory and were auto-resolved. "
                        "Check the trace panel for details."
                    ),
                    severity="info",
                )

            search_conflicts = search_extraction_summary.get("conflicts_created", 0)
            search_slots = search_extraction_summary.get("slots_applied", 0)
            if search_slots > 0:
                if search_conflicts > 0:
                    fact_word = "fact" if search_conflicts == 1 else "facts"
                    await self.store.create_alert(
                        user_id=user_id,
                        type="conflict",
                        title="Search conflict auto-resolved",
                        message=(
                            f"Search found {search_conflicts} {fact_word} that "
                            "contradicted existing memory and were auto-resolved."
                        ),
                        severity="info",
                    )
                else:
                    await self.store.create_alert(
                        user_id=user_id,
                        type="search_result",
                        title="New facts learned from search",
                        message=f"Search returned {search_slots} new fact(s) stored in memory.",
                        severity="info",
                    )
        except Exception as e:
            logger.warning("Failed to create learning alerts: %s", e)

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
        turn_start = time.monotonic()

        # 2. Log user episode
        user_episode = await self._log_episode(
            request.user_id,
            session_id,
            role="user",
            content=request.message,
        )
        episode_log_time = time.monotonic() - turn_start
        logger.debug("Episode logging: %.3fs", episode_log_time)

        # 3. Classify task type + search intent AND Retrieve memory context IN PARALLEL
        # Router doesn't need memory; retrieval doesn't need router result.
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

        # 3b. Handle scheduled task intent
        if task_type == TaskType.SCHEDULED and not skip_route:
            return await self._handle_scheduled_task(request, session_id)

        # 5. Reason: decide action based on memory sufficiency
        # 6. Extract user-stated facts BEFORE generation (parallel with reasoner)
        # Both need memory_context, but extraction only needs
        # user_message + empty assistant_response
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

        # 5a. Storage statements must not trigger external search: the user is
        # giving information, not requesting a lookup. The router's wants_search
        # judgment vetoes the reasoner's memory-sufficiency heuristic here.
        # giving information, not requesting a lookup. The router's wants_search
        # judgment vetoes the reasoner's memory-sufficiency heuristic here.
        if (
            plan.search_needed
            and task_type != TaskType.SEARCH
            and not skip_route
            and classification is not None
            and classification.wants_search is False
        ):
            logger.info("Search vetoed by router for storage-style turn")
            plan.action = Action.ANSWER
            plan.search_needed = False

        # 5b. Handle correction intent: extract + validate + apply
        if plan.action == Action.CORRECT:
            return await self._run_correction(
                request, session_id, user_episode.id, memory_context, progress
            )

        stored_slots = extraction_summary.get("slots") or []

        # 7. Build system prompt with memory context + reasoner guidance
        prompt_with_memory, plan_instructions, self_context = await self._assemble_prompt(
            plan, memory_context, task_type=task_type.value
        )
        system_prompt = prompt_with_memory

        # DEBUG: Log system prompt for file tools visibility
        logger.info("DEBUG system_prompt contains file tools guidance: %s", 
            "list_files" in system_prompt and "read_file" in system_prompt)
        logger.debug("DEBUG system_prompt (first 500 chars): %s", system_prompt[:500])

        if stored_slots:
            lines = [f"- {s['frame_name']}.{s['key']} = {s['value']}" for s in stored_slots]
            system_prompt += (
                "\n\n**Facts you just stored this turn:**\n"
                + "\n".join(lines)
                + "\nAcknowledge these naturally, in your own words."
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
        search_start = time.monotonic() if plan.search_needed else None
        # Defined up front so the timing log can report it unconditionally; 0.0
        # means "no search ran", which is the common case and worth seeing.
        search_time = 0.0
        if plan.search_needed:
            await self._report(progress, "searching", "searching the web")
            # Prefer the router's keyword query; fall back to a sanitized
            # version of the raw message (never raw conversational text).
            #
            # `classification` is None when skip_route=True, so it must be
            # guarded here. The router's wants_search veto above cannot cover
            # this case: it explicitly requires `classification is not None`,
            # so on a skip_route turn the veto never fires and this line is
            # reached. Four _handle_scheduled_task fallbacks rewrite the message
            # to an agent-authored string and re-enter with skip_route=True;
            # those strings match no _NON_INFO_PATTERNS, so classify_intent
            # always plans a search for them. See test_skip_route_crash.py.
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
            
            # If user gave consent for sensitive search, pass it to skip sensitivity check
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

            # Check if user consent is required for sensitive query
            if search_info and search_info.consent_required:
                # Return early with a response asking for consent
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
                return ChatResponse(
                    session_id=request.session_id or "",
                    response=consent_msg,
                    task_type="search_consent_required",
                    memory_context="",
                    citations=[],
                    extraction_summary=None,
                    search_extraction_summary=None,
                    search_info=search_info,
                )

            # Relevance gate: drop links that don't belong to the query
            # before they can pollute the system prompt or citations.
            # Add overall timeout to prevent search pipeline from hanging
            try:
                search_results = await asyncio.wait_for(
                    filter_relevant(
                        search_results, query, self.embed_fn(), min_relevance=relevance_threshold
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

                # Brave: fetch top result bodies in parallel for richer extraction
                if backend_name == "brave" and search_results:
                    try:
                        bodies = await asyncio.gather(
                            *[
                                _fetch_url_body(r.url)
                                for r in search_results[: settings.max_search_results_in_prompt]
                            ],
                            return_exceptions=True,
                        )
                        # Parallelize document extraction with timeout
                        extraction_tasks = [
                            asyncio.wait_for(
                                extract_facts_from_document(body, result.url, self.llm_client),
                                timeout=settings.search_timeout,
                            )
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

                    try:
                        await asyncio.wait_for(
                            self.store.embed_frames(
                                search_extraction_summary["frame_ids"],
                                get_embedding,
                                self.llm_client.embedding_model,
                            ),
                            timeout=settings.search_timeout,
                        )
                    except TimeoutError:
                        logger.warning(
                "embed_frames timed out after %.1fs",
                settings.search_timeout,
            )

                search_time = (
                time.monotonic() - search_start
                if search_start is not None
                else 0.0
            )
                logger.debug("Search pipeline: %.3fs", search_time)

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

        # Nothing has been emitted to the user yet, so this span is exactly what
        # they wait through before the first word. Search is included because it
        # also happens before generation -- logging before it would understate
        # the wait on precisely the slowest turns.
        logger.info(
            "turn_pregen: pregen_ms=%.0f routing_ms=%.0f recall_ms=%.0f "
            "plan_ms=%.0f extraction_ms=%.0f search_ms=%.0f",
            (time.monotonic() - turn_start) * 1000,
            routing_time * 1000, recall_time * 1000,
            plan_time * 1000, extraction_time * 1000,
            search_time * 1000,
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
            # Owner-scoped in SQL now, so no cross-member filter is needed here.
            # Bound the read to the tail we can actually use: 6 prior turns plus
            # the current one, which `[:-1]` drops.
            session_episodes = await self.store.get_episodes_for_session(
                session_id, user_id=request.user_id, limit=7
            )
            prior_turns = session_episodes[:-1]  # exclude current user episode
            max_turns = min(len(prior_turns), 6)
            prior_turns = prior_turns[-max_turns:] if max_turns > 0 else []
            for ep in prior_turns:
                history_messages.append(ChatMessage(role=ep.role, content=ep.content))

        # Hard limit on system prompt to prevent OOM/timeout
        system_prompt, truncated = self._fit_prompt_to_cap(
            prompt_with_memory,
            system_prompt,
            memory_context,
            task_type.value,
            plan_instructions,
            self_context,
        )

        # Structured logging for context transparency
        logger.info(
            "context_stats: prompt_chars=%d frames=%d episodes=%d search_results=%d truncated=%s",
            len(system_prompt),
            len(memory_context.retrieved_frames),
            len(memory_context.recent_episodes),
            len(search_results),
            truncated,
        )

        # Call LLM — fast path uses configured default (think off for
        # thinking-capable models); escalated plans flip thinking on with a
        # token cap (Phase 6 plan §6.2). With tools enabled, the model may
        # call local tools (datetime, calculator, private web search) before
        # answering (Phase 6 M5).
        messages = [ChatMessage(role="system", content=system_prompt)]
        messages.extend(history_messages)
        messages.append(ChatMessage(role="user", content=request.message))

        gen_model, think = await self._select_generation(request, plan)
        num_predict = settings.think_num_predict_cap if think else None
        if gen_model:
            await self._report(progress, "reasoning", "applying maximum intelligence")
        elif think:
            await self._report(progress, "reasoning", "thinking it through")
        else:
            await self._report(progress, "responding", "writing a reply")
        try:
            if settings.tools_enabled:
                logger.info("DEBUG: tools_enabled=True, building tools list")
                tools = builtin_tools(
                    self.search_tool,
                    store=self.store,
                    llm_client=self.llm_client,
                    embed_fn=self.embed_fn(),
                )
                tool_names = [t["function"]["name"] for t in tools]
                logger.info("DEBUG: Available tools: %s", tool_names)
                llm_response = await run_tool_loop(
                    self.llm_client,
                    messages,
                    tools,
                    user_id=str(request.user_id),
                    session_id=request.session_id,
                    model=gen_model,
                    think=think,
                    num_predict=num_predict,
                )
            else:
                logger.info("DEBUG: tools_enabled=False, skipping tool loop")
                llm_response = await self.llm_client.chat(
                    messages,
                    model=gen_model,
                    think=think,
                    num_predict=num_predict,
                )
        except Exception as e:
            # Local inference can be slow (large prefill, model load) or the
            # backend briefly unreachable — degrade gracefully instead of 500.
            logger.error("Generation failed: %s", e)
            fallback = _GENERATION_FAILURE_MESSAGE
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
        # run_tool_loop returns dict, llm_client.chat returns ChatResponse
        if isinstance(llm_response, dict):
            answer = llm_response.get("answer", "")
            reasoning_trace = llm_response.get("reasoning_trace")
            _ = llm_response.get("memory_updated", False)
        else:
            answer = llm_response.content
            reasoning_trace = llm_response.thinking

        await self._log_episode(
            request.user_id,
            session_id,
            role="assistant",
            content=answer,
            reasoning_trace=reasoning_trace,
        )

        # 9. Append sources / memory provenance to the response
        response_text = self._append_response_footers(
            answer,
            citations,
            task_type.value,
            len(memory_context.retrieved_frames),
        )

        # Create alerts for learning events
        await self._create_learning_alerts(
            request.user_id, extraction_summary, search_extraction_summary
        )

        # Return response
        self._log_turn_timings(
            turn_start,
            episode_ms=episode_log_time,
            routing_ms=routing_time,
            recall_ms=recall_time,
            plan_ms=plan_time,
            extraction_ms=extraction_time,
            # search_time only exists when the search branch ran; 0.0 otherwise.
            search_ms=search_time,
            ttft_ms=None,  # non-streaming: the response arrives whole, so there
                           # is no first token to measure. pregen_ms governs.
        )
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
        """Execute a scheduled task: full cognitive loop, output as string.

        Called by the scheduler for due tasks and by _handle_scheduled_task
        for run-now requests. Logs an assistant episode so the output is
        queryable memory. The task's own instruction is delivered to the
        model as the user message — never just folded into the system prompt,
        where it gets drowned by memory context.
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
        # A scheduled task is a standing instruction to CHECK FOR NEW
        # information. Never let memory sufficiency suppress the search —
        # an AI-news monitor must not answer purely from yesterday's frames.
        plan.search_needed = True

        prompt_with_memory, plan_instructions, self_context = await self._assemble_prompt(
            plan, memory_context, task_type="functional"
        )
        system_prompt = prompt_with_memory

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
                    query, num_results=5, llm_client=self.llm_client
                )
            except Exception as e:
                logger.warning("Task search failed: %s", e)
                search_results = []

            # Timed out like the other two paths: a stalled embedder must not
            # hang the scheduler's daily tick either.
            try:
                search_results = await asyncio.wait_for(
                    filter_relevant(search_results, query, self.embed_fn()),
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
                        embed_fn=self.embed_fn(),
                        embedding_model=self.llm_client.embedding_model,
                    )
                except Exception as e:
                    logger.error("Search extraction failed: %s", e)

        system_prompt, truncated = self._fit_prompt_to_cap(
            prompt_with_memory,
            system_prompt,
            memory_context,
            "functional",
            plan_instructions,
            self_context,
        )

        logger.info(
            "context_stats: prompt_chars=%d frames=%d episodes=%d search_results=%d truncated=%s",
            len(system_prompt),
            0,  # no frames in this path
            0,  # no episodes in this path
            len(search_results),
            truncated,
        )

        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=build_scheduled_task_directive(prompt)),
        ]
        use_thinking = await self.llm_client.supports_thinking(self.llm_client.chat_model)
        llm_response = await self.llm_client.chat(
            messages,
            think=use_thinking,
            num_predict=settings.think_num_predict_cap if use_thinking else None,
        )

        response_text = (
            llm_response.content
            or llm_response.thinking
            or "Task completed."
        )

        episode = await self._log_episode(
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
                result = await apply_extraction(
                    extraction,
                    self.store,
                    source_type="scheduled_task",
                    source_url=None,
                    source_reliability=0.6,
                )
                await self.store.update_episode_frame_ids(
                    episode.id, result.get("frame_ids", [])
                )
                logger.info(
                    "run_scheduled_task: extracted %d slots, %d assocs",
                    len(extraction.slots),
                    len(extraction.associations),
                )
        except Exception as e:
            logger.warning("Scheduled task extraction failed: %s", e)

        return response_text

    async def chat_stream(
        self,
        request: ChatRequest,
        progress: "Callable[[str, str], Awaitable[None]] | None" = None,
        skip_route: bool = False,
    ) -> AsyncGenerator[str, None]:
        """Run the cognitive loop with streaming response (SSE format).

        Yields SSE-formatted events for real-time UI updates.
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

        # 3b. Handle scheduled task intent
        if task_type == TaskType.SCHEDULED and not skip_route:
            # For scheduled tasks, fall back to non-streaming
            response = await self._handle_scheduled_task(request, session_id)
            event = {
                'type': 'finalize',
                'answer': response.response,
                'reasoning_trace': None
            }
            yield f"data: {json.dumps(event)}\n\n"
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

        # 5a. Storage statements must not trigger external search
        if (
            plan.search_needed
            and task_type != TaskType.SEARCH
            and not skip_route
            and classification is not None
            and classification.wants_search is False
        ):
            logger.info("Search vetoed by router for storage-style turn")
            plan.action = Action.ANSWER
            plan.search_needed = False

        # 5b. Handle correction intent
        if plan.action == Action.CORRECT:
            # Same handler as chat() -- previously this re-entered chat(), which
            # double-logged the user episode and re-ran routing/extraction.
            response = await self._run_correction(
                request, session_id, user_episode.id, memory_context, progress
            )
            event = {
                'type': 'finalize',
                'answer': response.response,
                'reasoning_trace': None
            }
            yield f"data: {json.dumps(event)}\n\n"
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
        messages.append(ChatMessage(role="user", content=request.message))

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
        yield serialize_event(
            MetaEvent(
                session_id=session_id,
                task_type=task_type,
                extraction_summary=extraction_summary,
                search_extraction_summary=search_extraction_summary,
                search_info=search_info,
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
