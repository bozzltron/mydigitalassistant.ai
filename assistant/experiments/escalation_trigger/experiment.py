"""Does the auto-escalation fire for the right tasks? Pre-registered in plan.md.

Pure heuristics: no model, no brain, no network.

    python assistant/experiments/escalation_trigger/experiment.py

Writes ``result.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

from assistant.backend.pipeline.reasoner import (
    MemorySufficiency,
    _has_explicit_think_intent,
    _should_use_max_intelligence,
)

HERE = Path(__file__).resolve().parent

# ("tool_task" | "hard_reasoning", query)
QUERIES: list[tuple[str, str]] = [
    # Mechanical: multiple tool steps, no reasoning difficulty. Should run on the
    # warm model — each one escalated is minutes of avoidable wait on the 27B.
    ("tool_task", "I've updated muso-soup-offers.txt. Let's read it and then "
                  "regenerate Muso Soup Priority.md"),
    ("tool_task", "Read the emails in the inbox folder and then write a summary to summary.md"),
    ("tool_task", "Find all the .eml files and then count how many there are"),
    ("tool_task", "Read contacts.csv and then append the new rows to it"),
    ("tool_task", "List the files in the sandbox and then rename the reports"),
    ("tool_task", "Search the mailbox dump for invoices and then save the matches to a file"),
    ("tool_task", "Read the notes and then update the todo list"),
    ("tool_task", "Open the spreadsheet and then add a column for the totals"),
    ("tool_task", "Go through the .eml files and then write out the senders"),
    ("tool_task", "Read the release notes and then draft the summary file"),
    # Genuinely hard: needs reasoning beyond following a file recipe.
    ("hard_reasoning", "Work through whether we should switch the max model, "
                       "and reason about the tradeoffs"),
    ("hard_reasoning", "Think through the failure modes of the current escalation logic"),
    ("hard_reasoning", "Step by step, derive the memory budget for running two 30B models at once"),
    ("hard_reasoning", "Reason through the tradeoffs of caching versus recomputing the embeddings"),
    ("hard_reasoning", "Prove that the confidence ladder is order-independent"),
    ("hard_reasoning", "Design a corroboration scheme across sources and justify the thresholds"),
    ("hard_reasoning", "Analyse whether a 9B can replace a 27B in the max role, and say why"),
    ("hard_reasoning", "Work through the root cause of a turn that runs for fifteen minutes"),
]


def evaluate(query: str) -> dict:
    explicit = _has_explicit_think_intent(query)
    # sufficiency is NONE here (empty memory); after the fix `escalate` is the
    # explicit cue alone, so `max_intelligence == explicit`.
    max_intel = _should_use_max_intelligence(
        query, "functional", MemorySufficiency.NONE, explicit
    )
    return {
        "query": query,
        "explicit_think": explicit,
        "escalate": explicit,
        "max_intelligence": max_intel,
    }


def main() -> None:
    rows: list[dict] = []
    for klass, query in QUERIES:
        row = evaluate(query)
        row["class"] = klass
        rows.append(row)
        flag = "MAX" if row["max_intelligence"] else " - "
        print(
            f"  [{klass:14}] {flag}  explicit={row['explicit_think']!s:5}  {query[:64]}"
        )

    tool = [r for r in rows if r["class"] == "tool_task"]
    hard = [r for r in rows if r["class"] == "hard_reasoning"]
    tool_fire = sum(1 for r in tool if r["max_intelligence"])
    hard_fire = sum(1 for r in hard if r["max_intelligence"])
    tool_rate = tool_fire / len(tool) * 100
    hard_rate = hard_fire / len(hard) * 100

    print(f"\ntool_task      escalation: {tool_rate:.0f}% ({tool_fire}/{len(tool)})")
    print(f"hard_reasoning escalation: {hard_rate:.0f}% ({hard_fire}/{len(hard)})")
    print("\ntool tasks wrongly escalated:")
    for r in tool:
        if r["max_intelligence"]:
            print(f"  - {r['query'][:70]}")

    out = HERE / "result.json"
    out.write_text(
        json.dumps(
            {
                "experiment": "escalation_trigger",
                "tool_task_escalation_pct": round(tool_rate, 1),
                "hard_reasoning_escalation_pct": round(hard_rate, 1),
                "rows": rows,
            },
            indent=2,
        )
    )
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
