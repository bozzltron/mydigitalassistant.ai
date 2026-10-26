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
    "doc-gardener", "spec-verifier",
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


def test_gate_scripts_are_executable():
    for name in ("agent-gates.sh", "agents-link.sh", "log-snapshot.sh"):
        path = ROOT / "scripts" / name
        assert path.is_file(), f"scripts/{name} is missing"
        assert os.access(path, os.X_OK), f"scripts/{name} is not executable"
