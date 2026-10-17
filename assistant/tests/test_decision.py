"""The decision-model judgements that gate the tool loop's one-shot nudge.

``needs_action`` decides *which kind of deed the request needs* — a change
(create/edit/rename/delete/append/move) or an external lookup — and ``None`` when an answer
can satisfy it. A read of the user's own stored content is deliberately out of scope: an
answer can satisfy it, so a nudge there is an unrequested round trip. Scope measured in
``assistant/experiments/decision_nudge_scope/``.

It returns the **kind**, not a bool, because the completion check needs it: a request to
change something is not performed by a lookup.
"""

from __future__ import annotations

import logging

from assistant.backend.pipeline import decision


async def test_needs_action_scopes_out_reads(monkeypatch):
    """A request an answer can satisfy must not fire the nudge.

    Regression: the question counted reads ("list my open tasks") as actions, so the
    nudge could force an unrequested read round. The scope is now changes + external
    lookups, and ``None`` is what "no action" means.
    """
    captured: dict = {}

    async def fake_ask(model, state, name, question, criteria):
        captured["criteria"] = criteria
        captured["question"] = question
        return {
            "choice": "answer",
            "probabilities": {"change": 0.1, "lookup": 0.1, "answer": 0.8},
        }

    monkeypatch.setattr(decision, "_ask", fake_ask)

    assert await decision.needs_action("List my open tasks") is None
    assert set(captured["criteria"]) == {"change", "lookup", "answer"}
    assert "CHANGE" in captured["question"]
    assert "LOOK" in captured["question"]  # lookups stay in scope


async def test_needs_action_returns_the_kind(monkeypatch):
    """The kind, not a bool — and the two kinds are told apart.

    A request to change something is not performed by a lookup. Treating both as one
    "action" let a `fetch_url` satisfy a request to write a file: measured in
    `experiments/ship_tracker_efficacy/` run 2, where 5 of 10 trials fetched, never wrote,
    and the completion check reported the action done.
    """

    async def fake_ask(model, state, name, question, criteria):
        request = state["request"]
        if "Create" in request:
            return {
                "choice": "change",
                "probabilities": {"change": 0.9, "lookup": 0.05, "answer": 0.05},
            }
        return {
            "choice": "lookup",
            "probabilities": {"change": 0.05, "lookup": 0.9, "answer": 0.05},
        }

    monkeypatch.setattr(decision, "_ask", fake_ask)

    assert await decision.needs_action("Create a file called x.md") == "change"
    assert await decision.needs_action("Look up the latest Ollama release notes") == "lookup"


async def test_needs_action_is_unknown_when_the_model_is_silent(monkeypatch):
    """Best-effort: an unusable answer is None, and nothing nudges."""

    async def fake_ask(model, state, name, question, criteria):
        return None

    monkeypatch.setattr(decision, "_ask", fake_ask)
    assert await decision.needs_action("Create a file called x.md") is None

    # An empty request never reaches the model.
    assert await decision.needs_action("   ") is None


def test_the_action_threshold_is_biased_toward_firing():
    """A wrong ``answer`` skips the fix; a wrong ``do`` costs one round."""
    answer = {"choice": "do", "probabilities": {"do": 0.45, "answer": 0.55}}
    assert decision._yes(answer, 0.4, positive_key="do") is True
    below = {"choice": "answer", "probabilities": {"do": 0.35, "answer": 0.65}}
    assert decision._yes(below, 0.4, positive_key="do") is False


def test_completion_nudge_is_off_by_default():
    """The completion nudge is opt-in, and this pins the default.

    It needs a ~9.5 GB model (`nimble`) and fires only on a turn that already needed
    an action, ran a tool, and *still* left it undone (`nudge_bound`: the first nudge
    fired on 0 of 10 live write-class turns). If this default flips back, the second
    nudge silently costs a model load on every install.
    """
    from assistant.backend.config import Settings

    default = Settings.model_fields["decision_completion_model"].default
    assert default == "", "the completion nudge must stay opt-in"


def test_log_decision_reads_the_positive_key(caplog):
    """Regression: the log looked up a hardcoded ``action`` probability.

    After the scope change the positive key is ``do``, so a hardcoded lookup logged
    ``p=None`` and hid the score the threshold acts on.
    """
    with caplog.at_level(logging.INFO, logger="assistant.backend.pipeline.decision"):
        decision._log_decision(
            "needs_action",
            "tev1:0.8b",
            {"choice": "do", "probabilities": {"do": 0.62, "answer": 0.38}},
            True,
            positive_key="do",
        )
    assert "decision needs_action=True" in caplog.text
    assert "p=0.62" in caplog.text
