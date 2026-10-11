"""Local decision models (ollama's ``/v1/systemone``) for typed judgements.

A decision model answers named, typed questions about a piece of state — a
``choice``, a ``noul`` (yes/no), or a ``score`` — in one fast local call. This
module is the project's single place to ask one, so a decision that used to be a
scripted heuristic can become a model judgement.

The first use: **``needs_action``** — does a request require the assistant to
*perform* something with a tool, rather than just reply? It gates the tool loop's
one-shot nudge (see ``streaming.stream_tool_loop``), the model-first fix for a turn
that announces an action ("I'll now create X") and never calls the tool.

Measured in ``assistant/experiments/decision_model_routing/``: ``tev1:0.8b`` judges
this at **95% / 70 ms** against a ``qwen3.5:4b`` chat call's 90% / 7,162 ms.
"""

from __future__ import annotations

import logging

import httpx

from assistant.backend.config import settings

logger = logging.getLogger(__name__)

_QUESTION = (
    "Does this user request require the assistant to PERFORM an action with a tool "
    "(create, read, edit, rename, or delete a file; run a task; search the web) — as "
    "opposed to just answering with words?"
)

_CRITERIA = {
    "action": "the assistant must perform an action with a tool",
    "answer": "the assistant can just reply with words",
}


async def needs_action(text: str) -> bool | None:
    """Whether ``text`` asks for a tool action. ``None`` when undecidable.

    Decided by a local decision model. ``None`` — no model configured, the model
    missing, a transport error, or an unusable answer — means "unknown", and the
    caller must treat that as "do not nudge" rather than guessing.

    The threshold is **biased toward ``action``** (``settings.decision_action_threshold``,
    default 0.4): a wrong ``answer`` skips the fix entirely, while a wrong ``action``
    costs one nudge round, so the asymmetry is worth paying for.
    """
    model = settings.decision_model
    if not model or not text.strip():
        return None

    payload = {
        "model": model,
        "state": {"request": text},
        "questions": {
            "needs_action": {
                "type": "choice",
                "instructions": _QUESTION,
                "criteria": _CRITERIA,
            }
        },
    }

    try:
        async with httpx.AsyncClient(timeout=settings.decision_timeout) as client:
            response = await client.post(
                f"{settings.ollama_url.rstrip('/')}/v1/systemone", json=payload
            )
            response.raise_for_status()
            answer = (response.json().get("answers") or {}).get("needs_action") or {}
    except Exception as e:
        # Best-effort: a missing decision model must not break the turn.
        logger.debug("decision model unavailable (%s): %s", model, e)
        return None

    probabilities = answer.get("probabilities") or {}
    p_action = probabilities.get("action")
    if isinstance(p_action, (int, float)):
        return p_action >= settings.decision_action_threshold

    choice = answer.get("choice")
    if choice == "action":
        return True
    if choice == "answer":
        return False
    return None
