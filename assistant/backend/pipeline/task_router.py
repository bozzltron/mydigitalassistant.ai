import json
import re
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assistant.backend.pipeline.llm_client import OllamaClient


class TaskType(StrEnum):
    FUNCTIONAL = "functional"  # goal-directed: search, explain, remind, do X
    INTROSPECTIVE = "introspective"  # reflective: what do you know/remember/think about X
    CORRECTION = "correction"  # user is correcting a stored fact
    SEARCH = "search"  # explicit request to search the web


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

# Compiled regex patterns
_COMPILED_INTROSPECTIVE = [re.compile(p, re.IGNORECASE) for p in INTROSPECTIVE_PATTERNS]


def classify_heuristic(text: str) -> TaskType | None:
    """Return TaskType.INTROSPECTIVE if a heuristic match is found, else None."""
    for pattern in _COMPILED_INTROSPECTIVE:
        if pattern.search(text):
            return TaskType.INTROSPECTIVE
    return None


async def classify_with_llm(text: str, llm_client: "OllamaClient") -> TaskType:
    """LLM-based classifier. Uses utility_model + JSON format."""
    from assistant.backend.pipeline.llm_client import ChatMessage

    system = ChatMessage(
        role="system",
        content="""Analyze the user's message and classify its intent.

Classify into exactly one of these four types based on what the user is TRYING to do:

FUNCTIONAL: The user wants information, help with a task, an explanation, or an action.
  Any question that seeks facts, explanations, or help accomplishing something.
  Even casual questions like "what's the capital of France?" are functional.

INTROSPECTIVE: The user is asking about YOUR memory, knowledge, or past interactions.
  Questions that use "you" to refer to yourself: "what do you know/remember?",
  "what have we discussed?", "do you recall X?", "tell me what you learned".

CORRECTION: The user is asserting that something you said or stored is WRONG and needs correcting.
  Look for: disagreement words ("actually", "no", "wrong", "not right", "mistake"),
  specificity about what is wrong ("it's 12, not 6", "you said X but it's actually Y"),
  self-corrections ("I meant...", "let me rephrase"), or contradiction signals.

SEARCH: The user is explicitly asking you to search the web.
  Direct requests like "search for X", "look up Y", "find info about Z".
  Not: questions that COULD be answered by search, but actual requests to search.

IMPORTANT: When uncertain between FUNCTIONAL and INTROSPECTIVE, prefer FUNCTIONAL.
When uncertain between FUNCTIONAL and CORRECTION, look for explicit disagreement signals.

Respond with ONLY valid JSON:
{"task_type": "functional"|"introspective"|"correction"|"search"}""",
    )
    user = ChatMessage(role="user", content=text)
    try:
        response = await llm_client.chat(
            [system, user],
            model=llm_client.utility_model,
            format="json",
            temperature=0.0,
        )
        data = json.loads(response.content)
        tt = data.get("task_type", "functional").lower()
        try:
            return TaskType(tt)
        except ValueError:
            return TaskType.FUNCTIONAL
    except (json.JSONDecodeError, AttributeError):
        return TaskType.FUNCTIONAL
    except Exception:
        return TaskType.FUNCTIONAL


async def classify(
    text: str,
    llm_client: "OllamaClient",
    use_llm_fallback: bool = True,
) -> TaskType:
    """Classify a user query. Heuristic first; LLM fallback for ambiguous cases.

    Single classification -- no double blocking call. If heuristic matches,
    return immediately. Otherwise (and use_llm_fallback=True), call LLM once.
    """
    heuristic_result = classify_heuristic(text)
    if heuristic_result is not None:
        return heuristic_result
    if not use_llm_fallback:
        return TaskType.FUNCTIONAL
    return await classify_with_llm(text, llm_client)
