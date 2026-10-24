"""Local decision models (ollama's ``/v1/systemone``) for typed judgements.

A decision model answers named, typed questions about a piece of state — a
``choice``, a ``noul`` (yes/no), or a ``score`` — in one fast local call. This
module is the project's single place to ask one, so a decision that used to be a
scripted heuristic can become a model judgement.

Two judgements guard the tool loop (see ``streaming.stream_tool_loop``), the
model-first fix for a turn that *announces* an action and never performs it:

- :func:`needs_action` — does the request require the assistant to *do* something words
  cannot counterfeit: change something, or fetch current external information? Gates
  the first nudge. A read of the user's own stored content is **not** an action — an
  answer can satisfy it.
- :func:`outstanding` — given the tool calls that actually ran, is there an action
  the user asked for that they did **not** perform? Gates the second nudge, because
  a turn can call a *different* tool (a read) and still claim the write.

Measured in ``assistant/experiments/decision_model_routing/`` and
``.../decision_model_completion/``: ``tev1:0.8b`` is the best ``needs_action`` judge
(95% / 70 ms) and useless at ``outstanding`` (no separation), while ``nimble`` is
the reverse (93.8% with a 0.63 margin). Hence two models — a deliberate fleet cost.
The *scope* of ``needs_action`` is measured in ``.../decision_nudge_scope/`` — but against
the earlier two-way question. The three-way question now in the code owes a
re-measurement; see ``experiments/ship_tracker_efficacy/result.md``.
"""

from __future__ import annotations

import logging

import httpx

from assistant.backend.config import settings

logger = logging.getLogger(__name__)

_NEEDS_ACTION_QUESTION = (
    "Does this user request ask the assistant to (a) CHANGE something with a tool — "
    "create, edit, rename, delete, append or move a file or record, or run a task that "
    "changes state; (b) LOOK something up that is current and external — search the web, "
    "fetch a page; or (c) be ANSWERED from what the assistant already knows, including "
    "reading its own stored memory?"
)

_OUTSTANDING_QUESTION = (
    "Given the user's request and the tool calls the assistant actually made, is "
    "there an action the user asked for that the tool calls did NOT perform? Judge "
    "the tool calls, not anything the assistant said."
)


async def _ask(
    model: str, state: dict, name: str, question: str, criteria: dict | None
) -> dict | None:
    """One typed question about ``state``; the answer dict, or None on any failure.

    Best-effort by contract: a missing model, a transport error, or an unusable
    answer returns ``None``, and the caller must treat that as "unknown" rather
    than guessing.

    The client is per-call on purpose. A shared module-level client would be bound to
    the event loop that created it, which breaks any second loop (tests create one per
    test) — and on localhost the connect it saves is negligible.
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


async def needs_action(text: str) -> str | None:
    """**Which kind** of action ``text`` asks for — ``"change"``, ``"lookup"``, or ``None``.

    The kind, not a bool, because the completion check needs it: a request to change
    something is not performed by a lookup. Treating both as one "action" let a
    ``fetch_url`` satisfy a request to write a file — measured in
    ``experiments/ship_tracker_efficacy/`` run 2, where 5 of 10 trials fetched, never
    wrote, and were reported done.

    "Action" here is deliberately narrow — a **change** (create/edit/rename/delete/
    append/move) or an **external lookup** (search): the deeds whose result words can
    counterfeit. A read of the user's own stored content is *not* an action — if the
    model has the content, answering from it is the design; if it does not, it has
    nothing to answer with and reads anyway. So a question the assistant can answer
    from memory never fires the nudge. Measured in
    ``assistant/experiments/decision_nudge_scope/`` — against the earlier two-way
    question; the three-way question now in this function owes a re-measurement.

    The threshold is **biased toward firing** (``settings.decision_action_threshold``,
    default 0.4): a wrong ``answer`` skips the fix entirely, while a wrong ``change`` or
    ``lookup`` costs one nudge round.
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
            "change": (
                "the assistant must change something with a tool — create, edit, rename, "
                "delete, append or move a file or record, or run a task that changes state"
            ),
            "lookup": (
                "the assistant must fetch current external information — search the web or "
                "fetch a page"
            ),
            "answer": (
                "the assistant can satisfy the request by answering from what it already knows"
            ),
        },
    )
    if answer is None:
        return None

    # The kind with the highest probability, if it clears the (still biased-toward-firing)
    # threshold. `answer` wins ties by being absent from this set.
    probabilities = answer.get("probabilities") or {}
    scored = {
        kind: probabilities[kind]
        for kind in ("change", "lookup")
        if isinstance(probabilities.get(kind), (int, float))
    }
    if not scored:
        return None
    kind = max(scored, key=lambda k: scored[k])
    decided = scored[kind] >= settings.decision_action_threshold
    _log_decision("needs_action", model, answer, decided, positive_key=kind)
    return kind if decided else None


# The tools that *perform* each kind of action. `needs_action` is already scoped to
# exactly two kinds — a change, or an external lookup — so "did a tool capable of it
# run?" is answerable structurally, without a second judgement.
_MUTATING_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "append_file",
        "delete_file",
        "rename_file",
        "upsert_slot",
        "upsert_association",
        "mark_essential",
    }
)
_LOOKUP_TOOLS = frozenset({"web_search", "fetch_url"})


def action_performed(tools_called: list[str], kind: str | None) -> bool:
    """Whether the calls that ran could have performed the action.

    **Structural on purpose.** This replaces a model judgement (`outstanding`, `nimble`)
    that asked a small model whether a `read_file` had performed a write. Measured live in
    ``experiments/ship_tracker_efficacy/``: 7 of 10 trials read a CSV, never called a write
    tool, and were judged *done* — the task failed 70% of the time and the check reported
    nothing wrong. This module had already measured `nimble` as "useless at ``outstanding``
    (no separation)"; that run reproduced it, on the task the judgement was written for.

    A tool name is not a guess about wording, and a request that needs a change is not
    satisfied by a read. Both facts are structural, so this is too.

    **The kind decides which set counts.** The first version of this checked both sets and
    accepted either — so a `fetch_url` satisfied a request to write a file, and run 2 of
    the experiment failed the same way run 1 had, one tool further along.
    """
    called = set(tools_called)
    if kind == "change":
        return bool(called & _MUTATING_TOOLS)
    if kind == "lookup":
        return bool(called & _LOOKUP_TOOLS)
    return False


async def outstanding(request: str, tools_called: list[str]) -> bool | None:
    """Whether the tool calls left an action the user asked for undone.

    **Superseded by `action_performed`, and no longer called by the loop.** It is *not* dead
    code, and the earlier note here — "delete it, and `decision_completion_model` /
    `decision_outstanding_threshold` with it, once H5 reads 0" — was wrong about what that
    would cost.

    Three experiments hold it as the thing under comparison: `nudge_bound` and
    `terminal_declaration` monkeypatch it to count whether the completion judgement was
    consulted at all (their `outstanding_consulted` column is the evidence that the old design
    could not fire for the case it was built for), and `decision_model_completion` measured it
    against labelled cases. `main.py` also reports `decision_completion` in `/health`.
    Deleting it would take those runs' apparatus with it, and they are the record of *why* the
    check is structural now — they have to stay runnable to remain evidence.

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
    decided = _yes(answer, settings.decision_outstanding_threshold)
    _log_decision("outstanding", model, answer, decided)
    return decided


def _probability(answer: dict, positive_key: str) -> float | None:
    """The score the threshold acts on.

    For a ``choice`` it is the probability of ``positive_key``; for a ``noul`` it is
    the ``noul`` field itself. ``None`` when the answer carries neither.
    """
    probabilities = answer.get("probabilities") or {}
    value = probabilities[positive_key] if positive_key in probabilities else answer.get("noul")
    return float(value) if isinstance(value, (int, float)) else None


def _log_decision(
    name: str, model: str, answer: dict, decided: bool | None, positive_key: str = "noul"
) -> None:
    """One INFO line per judgement, so a nudge is never a mystery.

    The probability is included because the decision is a threshold on it: a score at
    0.44 vs a 0.40 threshold flips the behaviour, and without the number a user seeing
    an unexplained retry has no way to tell why.
    """
    p = _probability(answer, positive_key)
    # For `needs_action` the positive key *is* the kind, and it was the one thing the
    # line did not say: "needs_action=True (p=0.541)" could not be told apart from a
    # request to write a file or a request to search the web, and those have opposite
    # consequences when the model then searches successfully and is nudged anyway.
    kind = f" kind={positive_key}" if positive_key in ("change", "lookup") else ""
    logger.info(
        "decision %s=%s%s (p=%s, model=%s)",
        name,
        decided,
        kind,
        round(p, 3) if p is not None else None,
        model,
    )


def _yes(answer: dict, threshold: float, positive_key: str = "noul") -> bool | None:
    """Turn a decision answer into a boolean, or None when unusable.

    A numeric score for ``positive_key`` wins; otherwise the raw ``choice`` is
    compared, so an answer that carries no probabilities still decides.
    """
    p = _probability(answer, positive_key)
    if p is not None:
        return p >= threshold

    choice = answer.get("choice")
    if choice is None:
        return None
    return choice == positive_key
