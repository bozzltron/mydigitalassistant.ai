"""The agent workflow is part of the repo, so it gets checked like the repo.

Nothing here tests a model. It tests the **contract** the workflow depends on: every
agent has a model and a mode, the read-only agents cannot edit code, the runbooks
name agents that exist, the measurement log is valid JSONL, and the gate scripts are
executable. These are the mistakes that fail silently — an agent with no `mode`
becomes a primary agent nobody launches, and a runbook that names a missing agent
looks fine until a human tries to run it.
"""

import json
import os
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = ROOT / ".agents" / "agents"
SKILLS_DIR = ROOT / ".agents" / "skills"
RUNS_DIR = ROOT / ".agents" / "runs"

MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9-]*/[A-Za-z0-9._:-]+$")

# Agents that must not edit project files; they write their handoff file only.
READ_ONLY = {
    "observer", "planner", "reviewer", "design-guardian",
    "doc-gardener", "spec-verifier", "memory-architect",
}

REQUIRED = {"name", "description", "model", "mode"}


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert match, f"{path.name} has no YAML frontmatter"
    return yaml.safe_load(match.group(1))


def agents() -> dict[str, dict]:
    return {p.stem: frontmatter(p) for p in sorted(AGENTS_DIR.glob("*.md"))}


def test_every_agent_declares_its_contract():
    found = agents()
    assert len(found) >= 6, f"expected the core agents, found {sorted(found)}"
    for name, meta in found.items():
        missing = REQUIRED - meta.keys()
        assert not missing, f"{name}: missing {sorted(missing)}"
        assert meta["mode"] == "subagent", (
            f"{name}: mode is {meta['mode']!r}; without 'subagent' OpenCode treats a "
            "new agent as primary and no runbook can launch it"
        )
        assert MODEL_RE.match(meta["model"]), (
            f"{name}: model {meta['model']!r} is not a provider/model id"
        )


def test_the_judges_run_a_different_model_from_the_implementer():
    """The spec's rule: a second opinion from the same model is a formality."""
    found = agents()
    implementer = found["implementer"]["model"]
    assert found["reviewer"]["model"] != implementer
    assert found["spec-verifier"]["model"] != implementer
    assert found["design-guardian"]["model"] != implementer
    assert found["doc-gardener"]["model"] != implementer
    assert found["memory-architect"]["model"] != implementer


def test_read_only_agents_cannot_edit_the_project():
    """`permissions` is the enforcement the spec's "can edit? no" column describes."""
    found = agents()
    for name in READ_ONLY:
        rules = found[name].get("permissions") or []
        deny_all = any(
            r.get("action") == "edit" and r.get("resource") == "*" and r.get("effect") == "deny"
            for r in rules
        )
        allow_handoff = any(
            r.get("action") == "edit"
            and r.get("resource") == ".agents/runs/**"
            and r.get("effect") == "allow"
            for r in rules
        )
        assert deny_all, f"{name}: does not deny edit on '*'"
        assert allow_handoff, f"{name}: cannot write its own handoff file"


def test_the_tester_may_edit_tests_but_not_production_code():
    rules = agents()["tester"].get("permissions") or []
    assert any(
        r.get("action") == "edit" and r.get("resource") == "*" and r.get("effect") == "deny"
        for r in rules
    ), "tester does not deny edit on '*'"
    allowed = {
        r["resource"] for r in rules if r.get("action") == "edit" and r.get("effect") == "allow"
    }
    assert "assistant/tests/**" in allowed
    assert not any("assistant/backend" in resource for resource in allowed)


def test_runbooks_name_agents_that_exist():
    found = agents()
    skills = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    assert {p.parent.name for p in skills} >= {"ship-feature", "self-improve"}
    for skill in skills:
        text = skill.read_text(encoding="utf-8")
        mentioned = {n for n in found if f"**{n}**" in text}
        assert len(mentioned) >= 5, (
            f"{skill.parent.name} mentions only {sorted(mentioned)} — a runbook that "
            "skips a role silently drops its gate"
        )
        for name in mentioned:
            assert (AGENTS_DIR / f"{name}.md").is_file()


def test_the_measurement_log_is_valid_jsonl():
    log = RUNS_DIR / "log.jsonl"
    assert log.exists(), "the measurement log must exist (empty is fine)"
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        assert {"ts", "run_id", "runbook", "verdict", "met", "total", "tests"} <= entry.keys()
        assert entry["verdict"] in {"PASS", "PARTIAL", "FAIL"}


# --- the cut, and the ratchet that stops the set growing back -----------------
#
# Phase 3 of the agent-set plan demoted design-guardian and doc-gardener from run
# steps to periodic audits. The demotion is only safe if two things hold, and both
# are cheap to undo by accident:
#
#   1. the runbooks no longer dispatch them (a step left behind quietly re-adds a
#      gate nobody measures), and
#   2. each demoted agent still states its trigger, so "periodic" cannot decay into
#      "never" — which is what actually happened to the docs pass in 0/2 runs.
#
# The ratchet itself is a rule in the doc; it has no script behind it yet, so the
# doc line is the thing worth pinning.

DEMOTED = ("design-guardian", "doc-gardener")


def test_the_demoted_audits_are_not_runbook_steps():
    """Bold names are how a runbook marks a step role; a demoted audit must not be one."""
    for skill in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        text = skill.read_text(encoding="utf-8")
        for name in DEMOTED:
            assert f"**{name}**" not in text, (
                f"{skill.parent.name} still dispatches {name} as a step. A run step that no "
                "per-run measurement covers is the thing Phase 3 removed — put the check in "
                "the verifier's rubric, or trigger the audit outside the runbook."
            )
        # The verifier carries both checks that used to be separate dispatches.
        assert "**spec-verifier**" in text
        assert "design principles" in text
        assert "scoped docs" in text or "doc claim" in text


def test_the_demoted_audits_state_a_trigger():
    """A periodic audit with no trigger is an audit that never runs again."""
    # memory-architect was already outside the path; it is included because the doc now
    # claims every out-of-path audit has a condition trigger, and that claim was false for
    # it (v-03 on 2026-10-31-aset3) — an "ad hoc" agent is one nothing invokes.
    for name in DEMOTED + ("memory-architect",):
        text = (AGENTS_DIR / f"{name}.md").read_text(encoding="utf-8")
        assert "## When you run" in text, (
            f"{name} has no 'When you run' section: without one, 'periodic' means 'never'"
        )
        assert "Trigger:" in text, f"{name} states no trigger condition"
        assert "run ID" in text, (
            f"{name} does not say it gets its own run ID, so its output would be "
            "unciteable and unverifiable"
        )


def test_the_ratchet_is_documented():
    """The 6-run kill criterion is the mechanism; a mechanism in an agent's head is not."""
    doc = (ROOT / "docs" / "AGENTIC_WORKFLOW.md").read_text(encoding="utf-8")
    assert "6 consecutive dispatched runs" in doc, (
        "the ratchet's kill criterion is missing from docs/AGENTIC_WORKFLOW.md — a gate "
        "that cannot lose its job will accumulate"
    )
    assert "never `0 hits`" in doc, (
        "the doc must keep the distinction between a gate that was not dispatched and one "
        "that opened nothing real, or the ratchet deletes gates nobody invoked"
    )


GATE_FIELDS = {"dispatched": bool, "rows": int, "hits": int}


def check_gates(entry: dict) -> list[str]:
    """Return the problems with one log entry's `gates` object."""
    problems: list[str] = []
    gates = entry.get("gates")
    if gates is None:
        return problems
    if not isinstance(gates, dict) or not gates:
        return ["gates must be a non-empty object keyed by agent name"]
    for name, record in gates.items():
        if not isinstance(record, dict):
            problems.append(f"{name}: gate record is {type(record).__name__}, not an object")
            continue
        for field, kind in GATE_FIELDS.items():
            if not isinstance(record.get(field), kind):
                problems.append(f"{name}: {field} is missing or not {kind.__name__}")
        if record.get("dispatched") is False and (record.get("rows", 0) or record.get("hits", 0)):
            problems.append(
                f"{name}: not dispatched but reports rows or hits — a gate that did not run "
                "opened nothing, so these numbers describe some other gate"
            )
    return problems


def test_the_gates_record_carries_what_the_ratchet_needs():
    """A hits count without `dispatched` cannot tell an idle gate from a useless one."""
    good = {"gates": {"reviewer": {"dispatched": True, "msgs": 19, "rows": 6, "hits": 2}}}
    idle = {"gates": {"tester": {"dispatched": False, "rows": 0, "hits": 0}}}
    assert check_gates(good) == []
    assert check_gates(idle) == []
    # The failure this guards: a gate that never ran, scored as if it opened nothing.
    assert check_gates(
        {"gates": {"reviewer": {"dispatched": False, "rows": 0, "hits": 2}}}
    )
    assert check_gates(
        {"gates": {"reviewer": {"dispatched": False, "rows": 6, "hits": 0}}}
    )
    assert check_gates({"gates": {"reviewer": {"rows": 6, "hits": 2}}})
    assert check_gates({"gates": {}})

    log = RUNS_DIR / "log.jsonl"
    for line in log.read_text(encoding="utf-8").splitlines():
        if line.strip():
            assert check_gates(json.loads(line)) == []


def test_gate_scripts_are_executable():
    for name in ("agent-gates.sh", "agents-link.sh", "log-snapshot.sh"):
        path = ROOT / "scripts" / name
        assert path.is_file(), f"scripts/{name} is missing"
        assert os.access(path, os.X_OK), f"scripts/{name} is not executable"
