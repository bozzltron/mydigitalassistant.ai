import json
import re
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from assistant.backend.pipeline.llm_client import OllamaClient


class TaskType(StrEnum):
    FUNCTIONAL = "functional"  # goal-directed: search, explain, remind, do X
    INTROSPECTIVE = "introspective"  # reflective: what do you know/remember/think about X
    CORRECTION = "correction"  # user is correcting a stored fact
    SEARCH = "search"  # explicit request to search the web
    SCHEDULED = "scheduled"  # create or manage a scheduled task


class ClassificationResult(BaseModel):
    """Routing decision from a single classifier pass.

    wants_search: the LLM's judgment on whether answering requires external
    lookup. None when decided heuristically (no LLM involved).
    """

    task_type: TaskType
    wants_search: bool | None = None
    search_query: str | None = None


# Heuristic patterns for clearly introspective queries (narrow fast-path only)
# These are patterns that are unambiguously introspective with near-100% precision.
# The LLM is the primary classifier; heuristic is a minor optimization for latency.
INTROSPECTIVE_PATTERNS = [
    r"\bwhat do you (know|remember|recall)\b",
    r"\bdo you (remember|recall)\b",
    r"\bwhat have we (talked|discussed)\b",
    r"\bwhat (have you|do you) learned\b",
]

_COMPILED_INTROSPECTIVE = [re.compile(p, re.IGNORECASE) for p in INTROSPECTIVE_PATTERNS]


def _compile_patterns():
    """Re-compile patterns (useful for testing when patterns are modified)."""
    global _COMPILED_INTROSPECTIVE
    _COMPILED_INTROSPECTIVE = [re.compile(p, re.IGNORECASE) for p in INTROSPECTIVE_PATTERNS]


_compile_patterns()


def classify_heuristic(text: str) -> TaskType | None:
    """Return TaskType.INTROSPECTIVE if a heuristic match is found.

    SCHEDULED intent is not heuristic-matched — the LLM classifies it with full
    context to avoid false positives from ambiguous words like "schedule" or
    "remind" in everyday sentences.
    """
    for pattern in _COMPILED_INTROSPECTIVE:
        if pattern.search(text):
            return TaskType.INTROSPECTIVE
    return None


async def classify_with_llm(text: str, llm_client: "OllamaClient") -> ClassificationResult:
    """LLM-based classifier. Uses utility_model + JSON format."""
    from assistant.backend.pipeline.llm_client import ChatMessage

    system = ChatMessage(
        role="system",
        content="""Analyze the user's message and classify its intent.

Classify into exactly one of these five types based on what the user is TRYING to do:

FUNCTIONAL: The user wants information, help with a task, an explanation, or an action.
  Any question that seeks facts, explanations, or help accomplishing something.
  Even casual questions like "what's the capital of France?" are functional.
  This ALSO covers statements that GIVE you information to store: "remember that
  my guitar has 6 strings", "keep in mind Alice lives in Portland", "my car is a
  blue Prius". The user is telling you facts so you can remember them later —
  that is a goal-directed action, NOT a question about your memory.

INTROSPECTIVE: The user is asking about YOUR memory, knowledge, or past interactions.
  Questions that query what you already know or have access to:
  - "what do you know/remember/recall?"
  - "what have we discussed/talked about?"
  - "do you recall X?", "tell me what you learned"
  - "what files/documents/uploads do you have/have access to/can see?"
  - "list/show my/your files"
  The test: an introspective message QUERIES your internal state/memory. A message
  that PROVIDES new information is functional, even if it contains the word
  "remember" ("remember that..." = giving, "do you remember..." = asking).

CORRECTION: The user is asserting that something you said or stored is WRONG and needs correcting.
  Look for: disagreement words ("actually", "no", "wrong", "not right", "mistake"),
  specificity about what is wrong ("it's 12, not 6", "you said X but it's actually Y"),
  self-corrections ("I meant...", "let me rephrase"), or contradiction signals.

SEARCH: The user is explicitly asking you to search the web.
  Direct requests like "search for X", "look up Y", "find info about Z".
  Not: questions that COULD be answered by search, but actual requests to search.

SCHEDULED: The user wants to set up, manage, or ask about a recurring scheduled task.
  Requests like "set up a daily briefing", "remind me to X every day", "schedule weekly check",
  "what scheduled tasks do I have?", "delete my Monday task", "run my AI briefing now".
  Merely mentioning a date or time while sharing a fact ("note for later: dinner
  is booked for June 12") is functional, not scheduled. Scheduled requires the
  user to request automatic/recurring execution.

Examples:
- "Remember that my Fender Stratocaster has 6 strings." → functional (giving a fact)
- "Do you remember how many strings my guitar has?" → introspective (querying memory)
- "Note for later: our anniversary dinner is June 12." → functional (sharing a fact)
- "Remind me every day at 9am to check the news." → scheduled (recurring execution)
- "That's wrong, it has 12 strings not 6." → correction
- "What files do you have access to?" → introspective (querying your memory)
- "List my uploaded documents" → introspective (querying your memory)

IMPORTANT: When uncertain, prefer INTROSPECTIVE for queries about your own memory/knowledge/files.
When uncertain between FUNCTIONAL and CORRECTION, look for explicit disagreement signals.

Also decide wants_search: true ONLY if answering well requires fetching external or
current information from the web. Statements that give information to remember are
wants_search=false. General-knowledge questions you can answer without looking
anything up are also wants_search=false. Questions about your own memory/files are
wants_search=false — the answer is in your local memory, not on the web.

When wants_search is true, also return "search_query": a short keyword query
(3-8 words) for a search engine — strip greetings, filler and personal details,
keep the names and topic words that a search engine needs.

Respond with ONLY valid JSON:
{"task_type": "functional"|"introspective"|"correction"|"search"|"scheduled",
 "wants_search": true|false,
 "search_query": "short keyword query"}""",
    )
    user = ChatMessage(role="user", content=text)
    try:
        response = await llm_client.chat(
            [system, user],
            model=llm_client.utility_model,
            format="json",
            temperature=0.0,
            think=False,  # utility role: structured output, never pay for thinking
        )
        data = json.loads(response.content)
        tt = data.get("task_type", "functional").lower()
        try:
            task_type = TaskType(tt)
        except ValueError:
            task_type = TaskType.FUNCTIONAL
        raw_wants = data.get("wants_search")
        wants_search = bool(raw_wants) if isinstance(raw_wants, bool) else None
        raw_query = data.get("search_query")
        search_query = (
            raw_query.strip() if isinstance(raw_query, str) and raw_query.strip() else None
        )
        return ClassificationResult(
            task_type=task_type, wants_search=wants_search, search_query=search_query
        )
    except (json.JSONDecodeError, AttributeError):
        return ClassificationResult(task_type=TaskType.FUNCTIONAL)
    except Exception:
        return ClassificationResult(task_type=TaskType.FUNCTIONAL)


async def classify(
    text: str,
    llm_client: "OllamaClient",
    use_llm_fallback: bool = True,
) -> TaskType:
    """Classify a user query. Heuristic first; LLM fallback for ambiguous cases.

    Single classification -- no double blocking call. If heuristic matches,
    return immediately. Otherwise (and use_llm_fallback=True), call LLM once.
    """
    return (await route(text, llm_client, use_llm_fallback)).task_type


async def route(
    text: str,
    llm_client: "OllamaClient",
    use_llm_fallback: bool = True,
) -> ClassificationResult:
    """Full routing decision: task type + search intent in one pass.

    Heuristic first; LLM fallback for ambiguous cases. wants_search is None
    when the heuristic path decided (no LLM judgment available).
    """
    heuristic_result = classify_heuristic(text)
    if heuristic_result is not None:
        return ClassificationResult(task_type=heuristic_result)
    if not use_llm_fallback:
        return ClassificationResult(task_type=TaskType.FUNCTIONAL)
    return await classify_with_llm(text, llm_client)
