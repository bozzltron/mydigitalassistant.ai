"""Local decision models (ollama's ``/v1/systemone``) for typed judgements.

A decision model answers named, typed questions about a piece of state — a
``choice``, a ``noul`` (yes/no), or a ``score`` — in one fast local call. This
module is the project's single place to ask one, so a decision that used to be a
scripted heuristic can become a model judgement.

Two judgements guard the tool loop (see ``streaming.stream_tool_loop``), the
model-first fix for a turn that *announces* an action and never performs it:

- :func:`needs_action` — does the request require the assistant to *do* something
  with a tool, rather than just reply? Gates the first nudge.
- :func:`outstanding` — given the tool calls that actually ran, is there an action
  the user asked for that they did **not** perform? Gates the second nudge, because
  a turn can call a *different* tool (a read) and still claim the write.

Measured in ``assistant/experiments/decision_model_routing/`` and
``.../decision_model_completion/``: ``tev1:0.8b`` is the best ``needs_action`` judge
(95% / 70 ms) and useless at ``outstanding`` (no separation), while ``nimble`` is
the reverse (93.8% with a 0.63 margin). Hence two models — a deliberate fleet cost.
"""

from __future__ import annotations

import logging

import httpx

from assistant.backend.config import settings

logger = logging.getLogger(__name__)

_NEEDS_ACTION_QUESTION = (
    "Does this user request require the assistant to PERFORM an action with a tool "
    "(create, read, edit, rename, or delete a file; run a task; search the web) — as "
    "opposed to just answering with words?"
)

_OUTSTANDING_QUESTION = (
    "Given the user's request and the tool calls the assistant actually made, is "
    "there an action the user asked for that the tool calls did NOT perform? Judge "
    "the tool calls, not anything the assistant said."
)


async def _ask(model: str, state: dict, name: str, question: str, criteria: dict | None):
    """One typed question about ``state``; the answer dict, or None on any failure.

    Best-effort by contract: a missing model, a transport error, or an unusable
    answer returns ``None``, and the caller must treat that as "unknown" rather
    than guessing.
    """
    if not model:
        return None

    question_def: dict = {"type": "noul", "instructions": question}
    if criteria:
        question_def = {
            "type": "choice",
            "instructions": question,
            "criteria": criteria,
        }
    payload = {"model": model, "state": state, "questions": {name: question_def}}

    try:
        async with httpx.AsyncClient(timeout=settings.decision_timeout) as client:
            response = await client.post(
                f"{settings.ollama_url.rstrip('/')}/v1/systemone", json=payload
            )
            response.raise_for_status()
            return (response.json().get("answers") or {}).get(name) or {}
    except Exception as e:
        logger.debug("decision model unavailable (%s): %s", model, e)
        return None


async def needs_action(text: str) -> bool | None:
    """Whether ``text`` asks for a tool action. ``None`` when undecidable.

    The threshold is **biased toward ``action``**
    (``settings.decision_action_threshold``, default 0.4): a wrong ``answer`` skips
    the fix entirely, while a wrong ``action`` costs one nudge round.
    """
    if not text.strip():
        return None
    model = settings.decision_model
    answer = await _ask(
        model,
        {"request": text},
        "needs_action",
        _NEEDS_ACTION_QUESTION,
        {
            "action": "the assistant must perform an action with a tool",
            "answer": "the assistant can just reply with words",
        },
    )
    if answer is None:
        return None
    decided = _yes(answer, model, settings.decision_action_threshold, positive_key="action")
    _log_decision("needs_action", model, answer, decided)
    return decided


async def outstanding(request: str, tools_called: list[str]) -> bool | None:
    """Whether the tool calls left an action the user asked for undone.

    ``None`` when undecidable (no completion model, an error, an unusable answer).
    The threshold is biased toward ``outstanding`` (``settings.decision_outstanding_threshold``)
    for the same cost reason: a missed action is the bug, a spurious nudge is a round.
    """
    if not request.strip():
        return None
    model = settings.decision_completion_model
    answer = await _ask(
        model,
        {
            "request": request,
            "tools_called": ", ".join(tools_called) if tools_called else "(none)",
        },
        "outstanding",
        _OUTSTANDING_QUESTION,
        None,
    )
    if answer is None:
        return None
    decided = _yes(answer, model, settings.decision_outstanding_threshold)
    _log_decision("outstanding", model, answer, decided)
    return decided


def _log_decision(name: str, model: str, answer: dict, decided: bool | None) -> None:
    """One INFO line per judgement, so a nudge is never a mystery.

    The probability is included because the decision is a threshold on it: a
    ``noul`` at 0.44 vs a 0.40 threshold flips the behaviour, and without the
    number a user seeing an unexplained retry has no way to tell why.
    """
    probabilities = answer.get("probabilities") or {}
    p = probabilities.get("action") if "action" in probabilities else answer.get("noul")
    logger.info(
        "decision %s=%s (p=%s, model=%s)",
        name,
        decided,
        round(p, 3) if isinstance(p, (int, float)) else p,
        model,
    )


def _yes(answer: dict, model: str, threshold: float, positive_key: str = "noul") -> bool | None:
    """Turn a decision answer into a boolean, or None when unusable.

    For a ``choice`` the probability of ``positive_key`` is the score; for a
    ``noul`` the score is the ``noul`` field itself.
    """
    probabilities = answer.get("probabilities") or {}
    p = probabilities.get(positive_key) if positive_key in probabilities else answer.get("noul")
    if isinstance(p, (int, float)):
        return p >= threshold

    choice = answer.get("choice")
    if choice is None:
        return None
    return choice == positive_key
