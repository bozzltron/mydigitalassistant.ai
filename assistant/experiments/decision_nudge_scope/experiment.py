"""Should the `needs_action` nudge fire on read-class requests? Pre-registered in plan.md.

Calls ollama 0.35's ``/v1/systemone`` against local Ollama. No brain, no tools.

    python assistant/experiments/decision_nudge_scope/experiment.py

Compares the current ``needs_action`` question with a change-scoped ``needs_change``
question against a target: **fire iff the request requires a state change.** Writes
``result.json``. ``result.md`` only after ``verification.md``.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from pathlib import Path

import httpx

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
HERE = Path(__file__).resolve().parent

# Deployed config: tev1:0.8b for needs_action, action-biased threshold (decision.py).
THRESHOLD = 0.4
DEPLOYED = "tev1:0.8b"
MODELS = ["tev1:0.8b", "tev1", "nimble"]

# (class, request). class: "change" | "read" | "answer" | "search"
# Target: the nudge fires iff class == "change".
REQUESTS: list[tuple[str, str]] = [
    # change -- an answer cannot satisfy it; the nudge MUST fire.
    ("change", "Write a file called notes.txt with today's plan"),
    ("change", "Create Friend Music Records Release Template.md as a generic template"),
    ("change", "We want to create Friend Music Records Release Template.md"),
    ("change", "No edits to Why Not Release. We want to create a release template"),
    ("change", "Rename notes.txt to todo.txt"),
    ("change", "Delete the old draft file"),
    ("change", "Append this row to contacts.csv"),
    ("change", "Add a point to my survey map at -122.68, 45.523"),
    ("change", "Edit the summary line in ReadingList.txt to say 12 books"),
    ("change", "Move draft.md into the archive folder"),
    # read -- an answer (from memory) can satisfy it; the nudge should be silent.
    ("read", "Read Why Not Release.md"),
    ("read", "How many books are on my reading list?"),
    ("read", "What did my morning briefing find yesterday?"),
    ("read", "Summarize what's in the release notes"),
    ("read", "What's in my ReadingList.txt?"),
    ("read", "List my open tasks"),
    ("read", "Show me the contacts I've saved"),
    ("read", "What does the geojson plan say?"),
    # answer -- pure question; the nudge should be silent.
    ("answer", "What is my name?"),
    ("answer", "Explain how photosynthesis works"),
    ("answer", "What did we decide about the launch?"),
    ("answer", "Thanks, that's helpful"),
    ("answer", "What's the capital of France?"),
    ("answer", "Why is the sky blue?"),
    ("answer", "Is it a good idea to release on a Friday?"),
    # search -- lookup; reported separately (the router owns forcing search).
    ("search", "Look up the latest Ollama release notes"),
    ("search", "Search the web for the current price of a Raspberry Pi 5"),
    ("search", "Find out who won the 2026 World Cup"),
]

QUESTIONS: dict[str, dict] = {
    "needs_action": {
        "instructions": (
            "Does this user request require the assistant to PERFORM an action with a "
            "tool (create, read, edit, rename, or delete a file; run a task; search the "
            "web) — as opposed to just answering with words?"
        ),
        "criteria": {
            "action": "the assistant must perform an action with a tool",
            "answer": "the assistant can just reply with words",
        },
        "positive": "action",
        "target_classes": ("change",),
    },
    "needs_change": {
        "instructions": (
            "Does this user request ask the assistant to CHANGE something (create, edit, "
            "rename, delete, append, or move a file or record; run a task that changes "
            "state) — as opposed to satisfying the request by telling the user something "
            "(answering, explaining, or looking something up)?"
        ),
        "criteria": {
            "change": "the assistant must create, edit, rename, delete, append, or move something",
            "answer": "the assistant can satisfy the request by telling the user something",
        },
        "positive": "change",
        "target_classes": ("change",),
    },
    # Addendum: the union -- changes OR external lookups; a read of the user's own
    # stored content is excluded (an answer can satisfy it).
    "needs_change_or_lookup": {
        "instructions": (
            "Does this user request ask the assistant to PERFORM an action with a tool "
            "that changes something (create, edit, rename, delete, append, or move a file "
            "or record; run a task that changes state) or that fetches current external "
            "information (search the web) — as opposed to satisfying the request by "
            "answering from what it already knows (including reading its own stored "
            "memory)?"
        ),
        "criteria": {
            "do": "the assistant must change something or fetch current external information",
            "answer": "the assistant can satisfy the request by answering from what it already knows",
        },
        "positive": "do",
        "target_classes": ("change", "search"),
    },
}


async def ask(
    client: httpx.AsyncClient, model: str, name: str, spec: dict, request: str
) -> dict:
    """One typed question; returns choice, P(positive), confidence, wall-clock."""
    started = time.time()
    payload = {
        "model": model,
        "state": {"request": request},
        "questions": {
            name: {
                "type": "choice",
                "instructions": spec["instructions"],
                "criteria": spec["criteria"],
            }
        },
    }
    try:
        r = await client.post(f"{OLLAMA}/v1/systemone", json=payload, timeout=60)
        ans = (r.json().get("answers") or {}).get(name) or {}
        choice = ans.get("choice")
        p = (ans.get("probabilities") or {}).get(spec["positive"])
        confidence = ans.get("confidence")
    except Exception as e:
        choice, p, confidence = None, None, f"{type(e).__name__}: {e}"
    return {
        "choice": choice,
        "p": p,
        "confidence": confidence,
        "wall_s": round(time.time() - started, 3),
    }


def fires(row: dict, spec: dict) -> bool:
    """The deployed rule: P(positive) >= threshold, else the raw choice."""
    p = row["p"]
    if isinstance(p, (int, float)):
        return p >= THRESHOLD
    return row["choice"] == spec["positive"]


async def run_arm(question_name: str, model: str) -> dict:
    spec = QUESTIONS[question_name]
    rows: list[dict] = []
    async with httpx.AsyncClient() as client:
        for cls, request in REQUESTS:
            got = await ask(client, model, question_name, spec, request)
            got.update({"class": cls, "request": request})
            got["fire"] = fires(got, spec)
            got["target"] = cls in spec["target_classes"]  # should fire for this question
            got["match"] = got["fire"] == got["target"]
            rows.append(got)
            flag = "  " if got["match"] else "!!"
            p = f"{got['p']:.3f}" if isinstance(got["p"], (int, float)) else "  -  "
            print(
                f"  [{question_name:12} {model:10}] {flag} {cls:6} "
                f"fire={str(got['fire']):5} p={p}  {request[:48]}"
            )
    by_class: dict[str, list[dict]] = {}
    for r in rows:
        by_class.setdefault(r["class"], []).append(r)

    def fire_rate(cls: str) -> float | None:
        rs = by_class.get(cls, [])
        if not rs:
            return None
        return round(sum(1 for r in rs if r["fire"]) / len(rs), 3)

    def n_fire(cls: str) -> int:
        return sum(1 for r in by_class.get(cls, []) if r["fire"])

    return {
        "question": question_name,
        "model": model,
        "threshold": THRESHOLD,
        "change_recall": fire_rate("change"),
        "change_n": len(by_class.get("change", [])),
        "change_fired": n_fire("change"),
        "read_fire_rate": fire_rate("read"),
        "read_fired": n_fire("read"),
        "read_n": len(by_class.get("read", [])),
        "answer_fire_rate": fire_rate("answer"),
        "answer_fired": n_fire("answer"),
        "answer_n": len(by_class.get("answer", [])),
        "read_answer_fired": n_fire("read") + n_fire("answer"),
        "read_answer_n": len(by_class.get("read", [])) + len(by_class.get("answer", [])),
        "search_fired": n_fire("search"),
        "search_n": len(by_class.get("search", [])),
        "target_accuracy": round(sum(1 for r in rows if r["match"]) / len(rows), 3),
        "median_ms": round(statistics.median(r["wall_s"] for r in rows) * 1000, 1),
        "rows": rows,
    }


async def main() -> None:
    results: list[dict] = []
    for question_name in ("needs_action", "needs_change", "needs_change_or_lookup"):
        for model in MODELS:
            print(f"\n[{question_name}] {model}")
            results.append(await run_arm(question_name, model))

    print("\n=== summary ===")
    print(
        f"  {'question':22} {'model':10} {'change_recall':>13} "
        f"{'read+ans fired':>15} {'search fired':>13} {'target acc':>10} {'median':>8}"
    )
    for r in results:
        recall = f"{r['change_fired']}/{r['change_n']}"
        ra = f"{r['read_answer_fired']}/{r['read_answer_n']}"
        se = f"{r['search_fired']}/{r['search_n']}"
        print(
            f"  {r['question']:22} {r['model']:10} {recall:>13} {ra:>15} "
            f"{se:>13} {r['target_accuracy']:>10.3f} {r['median_ms']:>7.0f}ms"
        )

    # The deployed config decides H3: does needs_action fire on any read/answer?
    deployed = next(
        r for r in results if r["question"] == "needs_action" and r["model"] == DEPLOYED
    )
    print(
        f"\n[deployed {DEPLOYED}] needs_action fires on "
        f"{deployed['read_answer_fired']}/{deployed['read_answer_n']} read+answer cases"
    )

    out = HERE / "result.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
