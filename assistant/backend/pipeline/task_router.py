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


# Heuristic patterns for introspective queries (case-insensitive)
INTROSPECTIVE_PATTERNS = [
    r"\bwhat do you (know|remember|think|recall)\b",
    r"\bdo you (know|remember|recall|have)\b",
    r"\bcan you (remember|recall)\b",
    r"\btell me about (what you (know|remember|learned)|"
    r"our (previous|past|earlier) (conversations?|talks?))\b",
    r"\bwhat have (we|you|i) (talked|discussed|said|learned)\b",
    r"\bdo you (still )?remember\b",
    r"\bhave you (heard of|learned about|seen)\b",
    r"\bwhat is (your|the) (knowledge|understanding|model)\b",
    r"\bwhat (have you|do you) learned\b",
    r"\bour (previous|earlier|past) (conversation|chat|discussion)\b",
    r"\bremember when\b",
    r"\bwhat (else )?do you know about\b",
    r"\btell me (more )?about the? (?:[\w'-]+ )?(article|story|thing|fact|subject|topic|matter)\b",
    r"\bwhat do you recall (about|regarding) \b",
    r"\bwhat was (discussed|said|mentioned) about\b",
    r"\b(pick )?up (with|on|on) that\b",
    r"\bit was (on|about|related to) \w+\b",
    r"\bthat (was|is) (about|on|related to) \w+\b",
]

# Heuristic patterns for scheduled task intents
SCHEDULED_TASK_PATTERNS = [
    r"\bschedule\b",
    r"\bscheduled\b",
    r"\bset up a daily\b",
    r"\bset up a weekly\b",
    r"\bset up an? \w+ task\b",
    r"\bcreate a (daily|weekly|monthly|recurring)\b",
    r"\bevery \w+ (minute|hour|day|week|month)\b",
    r"\b(remind|notify) me (to|every|at|daily|weekly)\b",
    r"\bautomat(e|ically) (remind|run|check)\b",
    r"\bperiodic(al)?(ly)?\b",
    r"\brun (every|daily|weekly|monthly)\b",
    r"\bhave a (daily|weekly|monthly) (briefing|task|check|report)\b",
    r"\btrack (daily|weekly|monthly)\b",
    r"\bfollow artificial intelligence in the news\b",
]

_COMPILED_INTROSPECTIVE = [re.compile(p, re.IGNORECASE) for p in INTROSPECTIVE_PATTERNS]
_COMPILED_SCHEDULED = [re.compile(p, re.IGNORECASE) for p in SCHEDULED_TASK_PATTERNS]


def classify_heuristic(text: str) -> TaskType | None:
    """Return TaskType.INTROSPECTIVE or TaskType.SCHEDULED if a heuristic match is found."""
    for pattern in _COMPILED_INTROSPECTIVE:
        if pattern.search(text):
            return TaskType.INTROSPECTIVE
    for pattern in _COMPILED_SCHEDULED:
        if pattern.search(text):
            return TaskType.SCHEDULED
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
  Questions that use "you" to refer to yourself: "what do you know/remember?",
  "what have we discussed?", "do you recall X?", "tell me what you learned".
  The test: an introspective message QUERIES what you already know. A message
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

IMPORTANT: When uncertain between FUNCTIONAL and INTROSPECTIVE, prefer FUNCTIONAL.
When uncertain between FUNCTIONAL and CORRECTION, look for explicit disagreement signals.

Also decide wants_search: true ONLY if answering well requires fetching external or
current information from the web. Statements that give information to remember are
wants_search=false. General-knowledge questions you can answer without looking
anything up are also wants_search=false.

Respond with ONLY valid JSON:
{"task_type": "functional"|"introspective"|"correction"|"search"|"scheduled",
 "wants_search": true|false}""",
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
        return ClassificationResult(task_type=task_type, wants_search=wants_search)
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
