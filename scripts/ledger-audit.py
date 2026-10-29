#!/usr/bin/env python3
"""Mechanical audit of a run's ledger.

Five of the seven ledger rules in ``docs/AGENTIC_WORKFLOW.md`` are decidable without a
model: a disposition exists, a citation points at something real, a deferral lands in a
register row, an escalation says why, and severity is one of four values. Those belong in
a script — it is free, instant, and unavoidable in a way a prompt instruction is not.

What is deliberately *not* here, because it needs reading:

- "nothing asserted in ``implementation.md`` is missing from the ledger" — semantic;
- "does the reject's falsifier actually address the claim" — semantic;
- "is this principle enforced by any test or lint" — semantic (the script can only list
  the candidates);
- "does this diff make a doc claim false" — semantic.

The verifier does those. This does the rest, so its context is spent on judgement.

Usage:
    scripts/ledger-audit.py                     # newest run that has a ledger
    scripts/ledger-audit.py 2026-10-27-anim     # one run
    scripts/ledger-audit.py path/to/ledger.md   # an explicit file

Exit 1 on any problem, 0 when clean.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / ".agents" / "runs"
BACKLOG = ROOT / ".agents" / "backlog.md"

SEVERITIES = {"S1", "S2", "S3", "S4"}
SOURCES = {"reviewer", "implementer", "tester", "observer", "design-guardian",
           "doc-gardener", "verifier", "planner"}
DISPOSITIONS = ("fix", "reject", "defer", "escalate")
ROW = re.compile(r"^\|\s*([a-z]{1,2}-\d+)\s*\|")


def find_ledger(argv: list[str]) -> Path | None:
    if argv:
        candidate = Path(argv[0])
        if candidate.suffix == ".md" and candidate.exists():
            return candidate
        guess = RUNS / argv[0] / "ledger.md"
        return guess if guess.exists() else None
    ledgers = sorted(RUNS.glob("*/ledger.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return ledgers[0] if ledgers else None


def cells(line: str) -> list[str]:
    """Split a markdown table row into cells, honouring escaped ``\\|`` inside a cell.

    Escaping matters: agents write ``|| true`` and ``a | b`` inside a claim, and those
    are literal pipes, not column breaks. Splitting on every ``|`` silently shifts the
    columns and then reports a phantom problem in the disposition — which is precisely
    what happened the first time this script met a real ledger.
    """
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", body)]


def audit(path: Path) -> list[str]:
    problems: list[str] = []
    backlog = BACKLOG.read_text(encoding="utf-8") if BACKLOG.exists() else ""
    seen: set[str] = set()
    rows = 0

    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not ROW.match(line):
            continue
        rows += 1
        cells_ = cells(line)
        if len(cells_) < 8:
            problems.append(f"line {lineno}: {len(cells_)} columns, expected 8")
            continue
        rid, src, sev, claim, where, falsifier, disposition = cells_[:7]
        tag = f"{rid} (line {lineno})"

        if rid in seen:
            problems.append(f"{tag}: duplicate row id")
        seen.add(rid)

        if src not in SOURCES:
            problems.append(f"{tag}: unknown src {src!r} — see the prefixes in §The ledger")
        if sev not in SEVERITIES:
            problems.append(f"{tag}: sev {sev!r} is not one of S1–S4")
        if not claim:
            problems.append(f"{tag}: empty claim")
        if not where:
            problems.append(f"{tag}: empty `where` — a row must point at something")
        if not falsifier:
            problems.append(f"{tag}: no falsifier — write `preference` if you cannot name one")
        if not disposition:
            problems.append(f"{tag}: NO DISPOSITION — the run cannot pass with this row open")
            continue

        word = disposition.split()[0].strip("*`").lower()
        if word not in DISPOSITIONS:
            problems.append(f"{tag}: disposition {word!r} is not one of {', '.join(DISPOSITIONS)}")
        elif word == "fix":
            cites = re.findall(r"`([^`]+)`", disposition + " " + falsifier)
            real = [c for c in cites if (ROOT / c.split(":")[0]).exists()]
            if not cites:
                problems.append(f"{tag}: `fix` cites nothing (want a commit, test or file)")
            elif not real:
                problems.append(
                    f"{tag}: `fix` cites {cites} — none of those exist in the tree"
                )
        elif word == "defer":
            ids = re.findall(r"B-\d+", disposition) or re.findall(r"B-\d+", falsifier)
            if not ids:
                problems.append(f"{tag}: `defer` names no register row (B-<n>)")
            for register_id in set(ids):
                if f"| {register_id} |" not in backlog:
                    problems.append(f"{tag}: defers to {register_id}, absent from .agents/backlog.md")
        elif word == "escalate":
            if len(disposition.split()) < 4:
                problems.append(f"{tag}: `escalate` with no reason — say who and why")
        elif word == "reject":
            if not re.search(r"`[^`]+`|\btest\b|\.py:|\.md:|log", falsifier + disposition):
                problems.append(
                    f"{tag}: `reject` cites no check — name the test, log line or doc line"
                )

    if rows == 0:
        problems.append("no rows found — a ledger with no rows is a run with no findings, "
                        "which is legitimate but worth confirming")
    return problems, rows


def main(argv: list[str]) -> int:
    path = find_ledger(argv)
    if path is None:
        print("ledger-audit: no ledger found", file=sys.stderr)
        return 2
    problems, rows = audit(path)
    rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    if problems:
        print(f"ledger-audit: FAIL ({rel}) — {len(problems)} problem(s) across {rows} rows")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"ledger-audit: PASS ({rel}) — {rows} rows, all dispositioned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
