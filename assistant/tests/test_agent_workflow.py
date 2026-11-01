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
# Derived, not restated: the only writer is the implementor, so every other agent is
# read-only by construction. Two hand-maintained sets drifted before; one cannot.
PATH = ("planner", "implementor", "reviewer", "verifier")

# The run path. Everything else is an audit outside it (AGENTIC_WORKFLOW.md
# §Audits that are not runbook steps) — the distinction the harness exists to keep.
AUDITS = ("design-guardian", "doc-gardener", "memory-architect", "analyzer")

WRITERS = {"implementor"}

READ_ONLY = (set(PATH) | set(AUDITS)) - WRITERS

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
    assert set(PATH) <= found.keys(), f"missing a path agent: {set(PATH) - found.keys()}"
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


def test_the_run_path_stays_small():
    """The path is four agents. A fifth is a decision, not a convenience.

    Every agent added to the path is a dispatch per run, and a gate nobody judges for
    contribution accumulates (docs/AGENTIC_WORKFLOW.md §The ratchet). If this fails, the
    change is adding a gate: register it and say what it contributes, or make it an audit
    with its own trigger instead.

    **The roster is derived from disk, not restated.** An earlier version asserted
    `len(PATH) == 4` against a hardcoded tuple and a blocklist of four deleted names —
    so adding a fifth agent file passed the whole suite (reviewer row `r-02`,
    2026-10-31-evidence: 15/15 green with `zz-probe-agent.md` present). A test that cannot
    fail on the drift it exists to stop is decoration.
    """
    found = agents()
    declared = set(PATH) | set(AUDITS)
    assert set(found) == declared, (
        "the agent roster drifted from the declared sets: "
        f"on disk but undeclared = {sorted(set(found) - declared)}; "
        f"declared but missing = {sorted(declared - set(found))}. "
        "A new agent must be added to PATH (a dispatch every run) or AUDITS (own trigger, "
        "own run ID) — the declaration is the decision the ratchet needs."
    )
    assert len(PATH) == 4, f"the run path is {len(PATH)} agents: {PATH}"
    # Nothing deleted may come back half-referenced: an agent file whose name no runbook
    # uses is the old drift.
    for gone in ("implementer", "spec-verifier", "tester", "observer"):
        assert gone not in found, (
            f"{gone}.md is back. The roster is {sorted(found)} — {gone} was merged into "
            "another agent or folded into a runbook step; do not reintroduce the split."
        )


def _family(model_id: str) -> str:
    """`opencode-go/glm-5.3-flash` -> `glm`. The vendor part, not the snapshot.

    The rule is "a second opinion from the same model is a formality", and the unit that
    makes a second opinion a formality is the *family*. Comparing full ids would pass
    `glm-5.3` against `glm-5.3-flash`, which shares the same training lineage and the same
    blind spots — that swap is a price cut, not a second opinion.
    """
    name = model_id.split("/", 1)[1]
    return name.split("-", 1)[0].split(".", 1)[0]


def test_the_judges_run_a_different_model_from_the_implementor():
    """The spec's rule: a second opinion from the same model is a formality."""
    found = agents()
    implementor = found["implementor"]["model"]
    for judge in ("reviewer", "verifier"):
        assert _family(found[judge]["model"]) != _family(implementor), (
            f"{judge} runs the {_family(found[judge]['model'])} family, the same as the "
            f"implementor ({implementor}); same-family review is a formality, not a "
            "second opinion"
        )
    for audit in ("design-guardian", "doc-gardener", "memory-architect"):
        assert _family(found[audit]["model"]) != _family(implementor), (
            f"{audit} shares the implementor's model family"
        )


def test_same_family_is_not_a_second_opinion():
    """Pin the helper's semantics against the exact regression it exists to catch.

    Without this, `test_the_judges_run_a_different_model_from_the_implementor` could be
    "fixed" back to a substring compare and still pass, which is how the rule would rot.
    """
    assert _family("opencode-go/glm-5.3") == _family("opencode-go/glm-5.3-flash"), (
        "glm-5.3 and glm-5.3-flash are one family; a model swap within a family is a "
        "price cut, not a second opinion"
    )
    assert _family("opencode-go/deepseek-v4.1-flash") == _family("opencode-go/deepseek-v4-pro")
    assert _family("opencode-go/qwen3.8-max") == _family("opencode-go/qwen3.8-flash")
    assert _family("opencode-go/glm-5.3-flash") != _family("opencode-go/qwen3.8-max")
    assert _family("opencode-go/deepseek-v4.1-flash") != _family("opencode-go/glm-5.3-flash")


def test_the_implementor_stays_on_the_validated_model():
    """The implementor writes ~74% of the tokens for ~10% of the run's cost.

    Swapping it is therefore a quality bet with almost no money in it: the four cheaper
    catalog models save 0.5-2.0 cents a run, and this one is the model the user validated
    for implementation (docs/AGENTIC_WORKFLOW.md §Model choice). If this fails, the
    change is a bet on the highest-volume role — say what measurement it rests on, or put
    it back.
    """
    found = agents()
    assert found["implementor"]["model"] == "opencode-go/deepseek-v4.1-flash", (
        "the implementor moved off the validated model "
        f"({found['implementor']['model']}); it carries ~74% of the run's tokens"
    )



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


def test_the_implementor_writes_the_tests():
    """The tester was merged into the implementor, and the price is paid in two rules.

    A separate tester could not weaken a test to make production code pass; the
    implementor can. Nothing restores that guarantee except the ordering rule and the
    verifier's test-diff audit, so both are pinned here.
    """
    text = (AGENTS_DIR / "implementor.md").read_text(encoding="utf-8")
    assert "`test-report.md`" in text, "the implementor no longer owns the test report"
    assert "failing test comes first" in text, (
        "the implementor no longer commits the failing regression test before the fix — "
        "that ordering is the only reason merging the tester in is safe"
    )
    assert "Never weaken" in text, "the never-weaken-a-test rule is gone"

    verifier = (AGENTS_DIR / "verifier.md").read_text(encoding="utf-8")
    assert "test diff" in verifier, (
        "the verifier no longer audits the test diff, so a weakened assertion ships"
    )


def test_the_verifier_covers_a_ticket_and_a_whole_split():
    """Two scopes, or the last ticket's verifier cannot see the whole project.

    A split ships as several runs, each verified alone. Without `project` scope nothing
    re-reads the original ticket after the last merge, and a requirement dropped between
    the smaller tickets is dropped silently — which is the failure the split rules exist
    to prevent (AGENTIC_WORKFLOW.md §Splitting).
    """
    text = (AGENTS_DIR / "verifier.md").read_text(encoding="utf-8")
    for scope in ("`ticket`", "`project`"):
        assert scope in text, f"the verifier no longer knows the {scope} scope"
    for artifact in ("verification.md", "project-verification.md"):
        assert artifact in text, f"the verifier no longer writes {artifact}"
    # A verdict with no evidence scale collapses back into an opinion.
    for verdict in ("met", "partial", "not met", "can't verify"):
        assert verdict in text, f"the verifier no longer grades a requirement {verdict!r}"
    assert "file:line" in text, (
        "the verifier no longer demands `file:line` evidence per requirement, so "
        "'met' becomes the implementor's word"
    )


def test_runbooks_name_agents_that_exist():
    found = agents()
    skills = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    assert {p.parent.name for p in skills} >= {"ship-feature", "self-improve"}
    for skill in skills:
        text = skill.read_text(encoding="utf-8")
        mentioned = {n for n in found if f"**{n}**" in text}
        assert len(mentioned) >= 4, (
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
        assert "**verifier**" in text
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


def test_the_small_change_lane_exists_and_keeps_the_gates():
    """A one-file fix must not pay for a planner dispatch; it must still be reviewed.

    Measured on `2026-10-31-b03`: a 38-line regression test cost $0.29 and six
    dispatch passes, with the planner at 50% of the bill for an 8.4 KB plan. The
    lane exists because an evaluator is worth its cost only when the task is beyond
    what the model does reliably alone — so the lane may drop the *planner*, never
    the reviewer or the verifier, and it needs a threshold a reader can apply.
    """
    for name in ("ship-feature", "self-improve"):
        text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
        assert "Small-change lane" in text, (
            f"{name} has no small-change lane: every one-file fix pays for a planner"
        )
        assert "under ~50 lines" in text, f"{name}: the lane states no size threshold"
        assert "one file" in text, f"{name}: the lane does not bound the file count"
        # The lane removes process cost, not judgement.
        assert "**reviewer**" in text and "**verifier**" in text


def test_the_planner_plans_outcomes_not_implementations():
    """The altitude rule is the fix for the most expensive gate in the run.

    Anthropic's harness post: a planner that specifies granular technical detail
    makes the implementor follow a spec that may be wrong, and the error cascades.
    Ours did exactly that (line-level instructions, named tests), and wrote 12,253
    output tokens for a 38-line change. This pins the correction so it cannot drift
    back into a file-level design document.
    """
    text = (AGENTS_DIR / "planner.md").read_text(encoding="utf-8")
    assert "Altitude" in text, "the planner's altitude rule is gone"
    assert "leave the path to the implementor" in text.lower(), (
        "the planner no longer forbids prescribing the implementation"
    )
    assert "Budget:" in text, (
        "the planner has no length budget, which is how a 38-line change got an "
        "8.4 KB plan"
    )
    for kept in ("falsifier", "Risk tier", "Weakest link"):
        assert kept in text, f"the planner dropped {kept}, which the runbooks rely on"


def test_the_analyzer_is_an_audit_over_the_log_only():
    """The OAV gap: nothing read `log.jsonl` back and asked what the flow cost.

    The Observer reads behaviour and the Verifier reads conformance; the Analyzer
    reads the instrument. It must stay an audit with a trigger (a per-run gate that
    finds nothing cannot lose its slot) and it must not read the code, or it becomes
    a second, worse verifier — and it runs on a free model because it is arithmetic
    over a table, not judgement.
    """
    assert "analyzer" in AUDITS and "analyzer" not in PATH
    text = (AGENTS_DIR / "analyzer.md").read_text(encoding="utf-8")
    for required in ("## When you run", "Trigger:", "run ID"):
        assert required in text, f"the analyzer has no {required!r}: 'periodic' would mean 'never'"
    assert ".agents/runs/log.jsonl" in text, "the analyzer does not read the log"
    assert "Not the code" in text or "not the code" in text, (
        "the analyzer may read the code, which makes it a second verifier"
    )
    assert "you propose" in text.lower() or "You propose" in text, (
        "the analyzer is not told that it proposes and a human disposes"
    )
    meta = frontmatter(AGENTS_DIR / "analyzer.md")
    assert meta["model"].startswith("opencode/"), (
        "the analyzer is not on a free model; it is arithmetic, and it is the one "
        "audit whose cost must never be the reason it does not run"
    )


def test_gate_scripts_are_executable():
    for name in ("agent-gates.sh", "agents-link.sh", "log-snapshot.sh"):
        path = ROOT / "scripts" / name
        assert path.is_file(), f"scripts/{name} is missing"
        assert os.access(path, os.X_OK), f"scripts/{name} is not executable"
