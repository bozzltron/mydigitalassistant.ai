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
        content="""You classify user queries into one of four categories:

- FUNCTIONAL: goal-directed queries seeking information, explanation, or task completion.
  Examples: "How does a guitar work?", "What's the weather?", "Explain quantum physics".

- INTROSPECTIVE: reflective queries about the assistant's own memory or prior interactions.
  Examples: "What do you remember about guitars?", "What have we discussed?",
  "Do you know about my dog?", "What did I tell you about X?".

- CORRECTION: the user is saying something stored in memory is wrong and wants to correct it.
  Examples: "Actually, that's wrong", "The guitar has 12 strings, not 6", "I meant to say...",
  "No, it's actually the other way around".

- SEARCH: the user is explicitly asking to search the web.
  Examples: "Search for X", "Look up Y", "Find information about Z", "Google it".

Respond with ONLY a JSON object:
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
        # Fallback to functional if LLM output is malformed.
        return TaskType.FUNCTIONAL
    except Exception:
        # Fallback to functional on LLM failure (e.g. connection error).
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
