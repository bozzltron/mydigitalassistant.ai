"""The decision-model judgements that gate the tool loop's one-shot nudge.

``needs_action`` decides *whether the request needs a deed words cannot counterfeit* —
a change (create/edit/rename/delete/append/move) or an external lookup. A read of the
user's own stored content is deliberately out of scope: an answer can satisfy it, so a
nudge there is an unrequested round trip. Scope measured in
``assistant/experiments/decision_nudge_scope/``.
"""

from __future__ import annotations

import logging

from assistant.backend.pipeline import decision


async def test_needs_action_scopes_out_reads(monkeypatch):
    """A request an answer can satisfy must not fire the nudge.

    Regression: the question counted reads ("list my open tasks") as actions, so the
    nudge could force an unrequested read round. The scope is now changes + external
    lookups, with the positive key ``do``.
    """
    captured: dict = {}

    async def fake_ask(model, state, name, question, criteria):
        captured["criteria"] = criteria
        captured["question"] = question
        return {"choice": "answer", "probabilities": {"do": 0.2, "answer": 0.8}}

    monkeypatch.setattr(decision, "_ask", fake_ask)

    assert await decision.needs_action("List my open tasks") is False
    assert set(captured["criteria"]) == {"do", "answer"}
    assert "changes something" in captured["question"]
    assert "search" in captured["question"]  # lookups stay in scope


async def test_needs_action_fires_on_a_change_and_a_lookup(monkeypatch):
    """Both counterfeit-able deeds are in scope: a change and an external lookup."""

    async def fake_ask(model, state, name, question, criteria):
        return {"choice": "do", "probabilities": {"do": 0.9, "answer": 0.1}}

    monkeypatch.setattr(decision, "_ask", fake_ask)

    assert await decision.needs_action("Create a file called x.md") is True
    assert await decision.needs_action("Look up the latest Ollama release notes") is True


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
    assert decision._yes(answer, "tev1:0.8b", 0.4, positive_key="do") is True
    below = {"choice": "answer", "probabilities": {"do": 0.35, "answer": 0.65}}
    assert decision._yes(below, "tev1:0.8b", 0.4, positive_key="do") is False


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
