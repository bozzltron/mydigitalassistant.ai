#!/usr/bin/env python3
"""Mechanical audit of a run's ledger.

Five of the seven ledger rules in ``docs/AGENTIC_WORKFLOW.md`` are decidable without a
model: a disposition exists, a citation points at something real, a deferral lands in a
register row, an escalation says why, and severity is one of four values. Those belong in a
script — it is free, instant, and unavoidable in a way a prompt instruction is not.

What is deliberately *not* here, because it needs reading:

- "nothing asserted in ``implementation.md`` is missing from the ledger" — semantic;
- "does the reject's falsifier actually address the claim" — semantic;
- "is this principle enforced by any test or lint" — semantic;
- "does this diff make a doc claim false" — semantic.

The verifier does those. This does the rest, so its context is spent on judgement.

**Empty runs.** The invariant is not "a run must find something" — that would punish a
clean change and invite an agent to invent a finding to pass. It is "every gate must be
accounted for". A ledger with no rows passes when the run's ``log.jsonl`` line records
which gates were dispatched; it fails when that record is missing, because "nothing ran"
and "nothing found" look identical otherwise, and on the first day of this harness nothing
had in fact run.

Usage:
    scripts/ledger-audit.py --run 2026-10-29-si03
    scripts/ledger-audit.py --ledger path/to/ledger.md
    scripts/ledger-audit.py                     # newest run (interactive use only)

Exit: 0 clean · 1 problems · 2 nothing to audit.

**Amendments — the resolution path.** A ledger row may not be edited (append-only), and two
rules meant such a row could never be resolved: the verifier opens its ``v-`` rows *after*
the only disposition pass, and ``L4`` forbids the writer changing a severity. On 2026-10-31
that deadlocked run ``2026-10-31-b03``: ``v-01`` was born undispositioned and no agent could
lawfully give it one, so the run was permanently ``PARTIAL`` with every requirement met.

The repair is a second, append-only table — never an edit to a written row:

    ## Amendments

    | id | date | by | ruling |
    |---|---|---|---|
    | r-01 | 2026-11-01 | human | sev: S3 → S2; disposition: escalate → B-23 |

A ruling is ``key: value`` pairs separated by ``;``. Two keys are understood:

- ``sev`` — one value, or ``Sx → Sy``; the **last** value is the effective one.
- ``disposition`` — the effective disposition, in the normal grammar.

An amendment that names a row which does not exist is a typo, and is reported as one.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / ".agents" / "runs"

SEVERITIES = {"S1", "S2", "S3", "S4"}
SOURCES = {"reviewer", "implementor", "design-guardian", "doc-gardener", "verifier",
           "planner",
           # Historical names: a ledger row keeps the prefix and source it was written
           # with, so old runs stay valid after the tester and observer were folded into
           # the implementor and step 3 of self-improve respectively.
           "implementer", "tester", "observer"}
DISPOSITIONS = ("fix", "reject", "defer", "escalate")
ROW = re.compile(r"^\|\s*([a-z]{1,2}-\d+)\s*\|")
AMENDMENT_HEADING = re.compile(r"^#{2,3}\s+amendment", re.IGNORECASE)
ANY_HEADING = re.compile(r"^#{1,6}\s+\S")
# S1 and S2 may not be deferred: "S1 — no, human only" and "S2 — no, fix or escalate".
NO_DEFER = {"S1", "S2"}


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


def _dispatch_record(log_path: Path, run_id: str) -> dict[str, dict] | None:
    """Return the run's ``gates`` object from ``log.jsonl``, or None when absent."""
    if not log_path.exists():
        return None
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("run_id") != run_id:
            continue
        gates = entry.get("gates")
        if isinstance(gates, dict) and any(
            isinstance(g, dict) and g.get("dispatched") for g in gates.values()
        ):
            return gates
    return None


def _amendments(text: str) -> tuple[dict[str, dict], list[str]]:
    """Read every ``## Amendments`` table, keyed by the row id it rules on.

    The section exists so a written row is never edited: a human ruling, a severity
    correction, or a disposition the writer was forbidden to set lands as a new line
    here and the audit applies it. Without it, a row the verifier opens after the
    disposition pass has no lawful repair — which is how a run with every requirement
    met stayed ``PARTIAL`` forever on 2026-10-31.
    """
    amendments: dict[str, dict] = {}
    problems: list[str] = []
    in_section = False
    for line in text.splitlines():
        if ANY_HEADING.match(line):
            in_section = bool(AMENDMENT_HEADING.match(line))
            continue
        if not in_section or not ROW.match(line):
            continue
        cells_ = cells(line)
        if len(cells_) < 4:
            problems.append(
                f"amendment row has {len(cells_)} columns, expected 4 "
                "(id, date, by, ruling)"
            )
            continue
        rid, date, by, ruling = cells_[:4]
        entry = {"date": date, "by": by, "raw": ruling, "sev": None, "disposition": None}
        for part in ruling.split(";"):
            key, _, value = part.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "sev":
                found = re.findall(r"\bS[1-4]\b", value)
                if found:
                    entry["sev"] = found[-1]
            elif key == "disposition":
                entry["disposition"] = value
        if not date.strip() or not by.strip():
            problems.append(f"amendment {rid}: needs a date and a `by` (who ruled)")
        if entry["sev"] is None and entry["disposition"] is None:
            problems.append(
                f"amendment {rid}: ruling sets neither `sev:` nor `disposition:`"
            )
        if rid in amendments:
            problems.append(
                f"amendment {rid}: amended twice — the later ruling wins, say which you meant"
            )
        amendments[rid] = entry
    return amendments, problems


def audit(
    ledger_path: Path,
    *,
    backlog: Path,
    log: Path,
    root: Path,
) -> tuple[list[str], int]:
    """Audit one ledger. Returns (problems, row_count)."""
    problems: list[str] = []
    backlog_text = backlog.read_text(encoding="utf-8") if backlog.exists() else ""
    ledger_text = ledger_path.read_text(encoding="utf-8")
    amendments, amendment_problems = _amendments(ledger_text)
    problems.extend(amendment_problems)
    applied: list[str] = []
    seen: set[str] = set()
    rows = 0
    in_amendments = False

    for lineno, line in enumerate(ledger_text.splitlines(), 1):
        if ANY_HEADING.match(line):
            in_amendments = bool(AMENDMENT_HEADING.match(line))
            continue
        if in_amendments or not ROW.match(line):
            continue
        rows += 1
        cells_ = cells(line)
        if len(cells_) != 8:
            problems.append(
                f"line {lineno}: {len(cells_)} columns, expected 8 — an unescaped `|` "
                "inside a cell splits it; write `\\|` for a literal pipe"
            )
            continue
        rid, src, sev, claim, where, falsifier, disposition = cells_[:7]
        tag = f"{rid} (line {lineno})"
        amendment = amendments.get(rid)
        if amendment is not None:
            if amendment["sev"]:
                sev = amendment["sev"]
                applied.append(f"{rid}: sev {amendment['sev']} per {amendment['by']}")
            if amendment["disposition"]:
                disposition = amendment["disposition"]
                applied.append(f"{rid}: disposition per {amendment['by']}")

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
        else:
            if word == "defer" and sev in NO_DEFER:
                problems.append(
                    f"{tag}: `defer` on {sev} — {sev} is fix or escalate; a deferral of a "
                    "correctness or stability finding is the run deciding not to look"
                )
            if word == "fix":
                cites = re.findall(r"`([^`]+)`", disposition + " " + falsifier)
                real = [c for c in cites if (root / c.split(":")[0]).exists()]
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
                    if f"| {register_id} |" not in backlog_text:
                        problems.append(
                            f"{tag}: defers to {register_id}, absent from the register"
                        )
            elif word == "escalate":
                if len(disposition.split()) < 4:
                    problems.append(f"{tag}: `escalate` with no reason — say who and why")
            elif word == "reject":
                if not re.search(r"`[^`]+`|\btest\b|\.py:|\.md:|log", falsifier + disposition):
                    problems.append(
                        f"{tag}: `reject` cites no check — name the test, log line or doc"
                    )


    for rid in sorted(set(amendments) - seen):
        problems.append(
            f"amendment {rid}: rules on a row that is not in this ledger — a typo here "
            "would silently leave the real row unresolved"
        )
    if applied:
        print("ledger-audit: amendments applied — " + "; ".join(applied))

    if rows == 0:
        run_id = ledger_path.parent.name
        gates = _dispatch_record(log, run_id)
        if gates is None:
            problems.append(
                "EMPTY RUN with no dispatch record — this is not 'nothing found', it is "
                "'nothing accounted for'. Add the `gates` object to this run's log.jsonl "
                "line (dispatched/rows per gate), or record the findings the gates opened."
            )
        else:
            dispatched = sorted(
                k for k, v in gates.items() if isinstance(v, dict) and v.get("dispatched")
            )
            print(f"ledger-audit: empty run, gates accounted for: {', '.join(dispatched)}")
    return problems, rows


def _resolve(args: argparse.Namespace) -> tuple[Path, Path, Path, Path] | None:
    root = Path(args.root).resolve() if args.root else ROOT
    backlog = Path(args.backlog) if args.backlog else root / ".agents" / "backlog.md"
    log = Path(args.log) if args.log else root / ".agents" / "runs" / "log.jsonl"
    if args.ledger:
        ledger = Path(args.ledger)
    elif args.run:
        ledger = root / ".agents" / "runs" / args.run / "ledger.md"
    else:
        ledgers = sorted((root / ".agents" / "runs").glob("*/ledger.md"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        ledger = ledgers[0] if ledgers else None
    if ledger is None or not ledger.exists():
        return None
    return ledger, backlog, log, root


def _count(ledger_path: Path) -> tuple[int, int]:
    """(rows, hits) derived from the ledger, not from anyone's self-report.

    `hits` is the number of rows that were **closed** and not rejected: a row only
    counts when someone other than its opener confirmed it (or the verifier closed
    it after the work landed). The verifier used to write this figure itself at
    logging time, which made every run look like precision 1.0 — `B-19`. The
    dispatcher now fills `gates.<gate>.hits` from here, at commit, so the number
    comes from ledger state the counting session did not author.
    """
    text = ledger_path.read_text(encoding="utf-8")
    amendments, _ = _amendments(text)
    rows = hits = 0
    in_amendments = False
    for line in text.splitlines():
        if ANY_HEADING.match(line):
            in_amendments = bool(AMENDMENT_HEADING.match(line))
            continue
        if in_amendments or not ROW.match(line):
            continue
        cells_ = cells(line)
        if len(cells_) != 8:
            continue
        rows += 1
        rid, disposition = cells_[0], cells_[6]
        amendment = amendments.get(rid) or {}
        disposition = amendment.get("disposition") or disposition
        closed = cells_[7].strip()
        if closed and not disposition.strip("*` ").lower().startswith("reject"):
            hits += 1
    return rows, hits


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Audit a run's ledger.")
    parser.add_argument("--run", help="run ID, e.g. 2026-10-29-si03")
    parser.add_argument("--ledger", help="path to a ledger.md")
    parser.add_argument("--root", help="repo root (tests/fixtures)")
    parser.add_argument("--backlog", help="path to the register")
    parser.add_argument("--log", help="path to log.jsonl")
    parser.add_argument(
        "--hits",
        action="store_true",
        help="print `rows=N hits=M` derived from the ledger's closed cells (for the "
             "log line, which the dispatcher fills at commit)",
    )
    args = parser.parse_args(argv)

    resolved = _resolve(args)
    if resolved is None:
        print("ledger-audit: nothing to audit (no ledger found)", file=sys.stderr)
        return 2
    ledger, backlog, log, root = resolved

    problems, rows = audit(ledger, backlog=backlog, log=log, root=root)
    if args.hits:
        derived_rows, derived_hits = _count(ledger)
        print(f"rows={derived_rows} hits={derived_hits}")
        return 0 if not problems else 1
    try:
        shown = ledger.relative_to(root)
    except ValueError:
        shown = ledger
    if problems:
        print(f"ledger-audit: FAIL ({shown}) — {len(problems)} problem(s) across {rows} rows")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"ledger-audit: PASS ({shown}) — {rows} rows, all dispositioned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
