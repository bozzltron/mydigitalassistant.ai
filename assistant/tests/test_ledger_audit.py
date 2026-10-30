"""Tests for ``scripts/ledger-audit.py``.

The audit exists because a script cannot be forgotten, so it needs the thing it exists to
enforce: a test that would go red if it stopped working. Its first real run produced a
phantom failure — a claim containing escaped pipes (``\\|\\| true``) shifted the columns —
so case 10 is that regression, kept permanently.

Cases 2 and 3 pin the rule that replaced "fail on an empty ledger": the invariant is not
"a run must find something", it is "every gate must be accounted for". An empty run passes
when its log line records which gates ran, and fails when it does not — because on the first
day of the harness, "nothing found" and "nothing ran" were indistinguishable, and it was the
second one.
"""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ledger-audit.py"

_spec = importlib.util.spec_from_file_location("ledger_audit", SCRIPT)
ledger_audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ledger_audit)

HEADER = ("| id | src | sev | claim | where | falsifier | disposition | closed |\n"
          "|---|---|---|---|---|---|---|---|\n")


def row(rid="r-01", src="reviewer", sev="S2", claim="a defect", where="`README.md`",
        falsifier="wrong if the test passes", disposition="fix `README.md`"):
    return f"| {rid} | {src} | {sev} | {claim} | {where} | {falsifier} | {disposition} | |\n"


@pytest.fixture
def env(tmp_path):
    """A miniature repo: one ledger, a register, a log."""
    (tmp_path / ".agents" / "runs" / "2026-01-01-abcd").mkdir(parents=True)
    (tmp_path / "README.md").write_text("x")
    backlog = tmp_path / ".agents" / "backlog.md"
    backlog.write_text("| id | sev |\n|---|---|\n| B-01 | S2 |\n")
    log = tmp_path / ".agents" / "runs" / "log.jsonl"

    def build(rows: str, log_entries: list[dict] | None = None) -> Path:
        ledger = tmp_path / ".agents" / "runs" / "2026-01-01-abcd" / "ledger.md"
        ledger.write_text(HEADER + rows)
        if log_entries is not None:
            log.write_text("\n".join(json.dumps(e) for e in log_entries) + "\n")
        return ledger

    def run(ledger: Path):
        return ledger_audit.audit(ledger, backlog=backlog, log=log, root=tmp_path)

    return build, run


def test_a_clean_ledger_passes(env):
    build, run = env
    problems, rows = run(build(row()))
    assert (problems, rows) == ([], 1)


def test_an_empty_run_without_a_dispatch_record_fails(env):
    build, run = env
    problems, rows = run(build("", log_entries=[{"run_id": "2026-01-01-abcd", "verdict": "PASS"}]))
    assert rows == 0
    assert any("EMPTY RUN with no dispatch record" in p for p in problems)


def test_an_empty_run_with_a_dispatch_record_passes(env):
    build, run = env
    problems, rows = run(build("", log_entries=[{
        "run_id": "2026-01-01-abcd",
        "gates": {"reviewer": {"dispatched": True, "rows": 0},
                  "tester": {"dispatched": True, "rows": 0}},
    }]))
    assert (problems, rows) == ([], 0)


def test_an_undispositioned_row_fails(env):
    build, run = env
    problems, _ = run(build(row(disposition="")))
    assert any("NO DISPOSITION" in p for p in problems)


def test_a_fix_citing_a_missing_path_fails(env):
    build, run = env
    problems, _ = run(build(row(disposition="fix `ghost/path.py`")))
    assert any("none of those exist" in p for p in problems)


def test_a_defer_to_a_missing_register_row_fails(env):
    build, run = env
    problems, _ = run(build(row(disposition="defer → B-999")))
    assert any("absent from the register" in p for p in problems)


def test_an_escalate_with_no_reason_fails(env):
    build, run = env
    problems, _ = run(build(row(disposition="escalate")))
    assert any("no reason" in p for p in problems)


def test_a_reject_citing_no_check_fails(env):
    build, run = env
    problems, _ = run(build(row(disposition="reject", falsifier="it is fine")))
    assert any("cites no check" in p for p in problems)


def test_bad_severity_source_and_duplicate_id_are_all_reported(env):
    build, run = env
    rows = row(rid="r-01", sev="S9") + row(rid="r-01", src="ghost")
    problems, _ = run(build(rows))
    assert any("is not one of S1" in p for p in problems)
    assert any("unknown src" in p for p in problems)
    assert any("duplicate row id" in p for p in problems)


def test_escaped_pipes_in_a_claim_do_not_shift_columns(env):
    """The regression from the audit's first real run: `\\|\\| true` is literal text."""
    build, run = env
    problems, _ = run(build(row(claim="the guard is `x \\|\\| true` and never fails")))
    assert problems == [], problems


def test_an_unescaped_pipe_is_reported_as_a_malformed_row(env):
    """A shifted row must say so, not blame a field that moved under it."""
    build, run = env
    problems, rows = run(build(row(claim="a | b")))
    assert rows == 1
    assert any("expected 8" in p and "unescaped" in p for p in problems)


def test_no_ledger_is_not_a_failure(tmp_path):
    missing = tmp_path / ".agents" / "runs" / "nope" / "ledger.md"
    assert not missing.exists()
    assert ledger_audit.main(["--ledger", str(missing)]) == 2


def test_every_problem_is_reported_not_just_the_first(env):
    build, run = env
    rows = row(rid="r-01", sev="S9", disposition="") + row(rid="r-02", src="ghost", claim="")
    problems, _ = run(build(rows))
    assert len(problems) >= 4, problems


def test_run_id_selects_that_run_and_never_guesses(env, tmp_path):
    build, _ = env
    build(row())
    assert ledger_audit.main(["--run", "2026-01-01-abcd", "--root", str(tmp_path)]) == 0
    assert ledger_audit.main(["--run", "does-not-exist", "--root", str(tmp_path)]) == 2
