"""Can a decision model judge whether the action was done? Pre-registered in plan.md.

Calls ollama 0.35's `/v1/systemone`. No brain, no tools.

    python assistant/experiments/decision_model_completion/experiment.py

Writes ``result.json``. ``result.md`` only after ``verification.md``.
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

MODELS = ["nimble", "tev1", "tev1:0.8b"]

QUESTION = (
    "Given the user's request and the tool calls the assistant actually made, is "
    "there an action the user asked for that the tool calls did NOT perform? Judge "
    "the tool calls, not anything the assistant said."
)

# (label, request, tools_called). label: "outstanding" | "satisfied"
CASES: list[tuple[str, str, str]] = [
    (
        "outstanding",
        "No edits to Why Not Release. We want to create Friend Music Records "
        "Release Template.md",
        "read_file(path='Why Not Release.md')",
    ),
    ("outstanding", "Create notes.txt with today's plan", "read_file(path='notes.txt')"),
    ("outstanding", "Delete the old draft file", "read_file(path='old draft.md')"),
    ("outstanding", "Rename notes.txt to todo.txt", "read_file(path='notes.txt')"),
    (
        "outstanding",
        "Append this row to contacts.csv",
        "search_file(path='contacts.csv', query='ada')",
    ),
    (
        "outstanding",
        "Add a point to my map at -122.68, 45.523",
        "read_file(path='map.geojson')",
    ),
    ("outstanding", "We want to create a release template", "glob(pattern='**/*.md')"),
    ("outstanding", "Run my morning briefing now", "list_files()"),
    (
        "satisfied",
        "Create notes.txt with today's plan",
        "write_file(path='notes.txt', content='...')",
    ),
    (
        "satisfied",
        "No edits to Why Not Release. We want to create Friend Music Records "
        "Release Template.md",
        "write_file(path='Friend Music Records Release Template.md', content='...')",
    ),
    ("satisfied", "Delete the old draft file", "delete_file(path='old draft.md')"),
    (
        "satisfied",
        "Rename notes.txt to todo.txt",
        "rename_file(path='notes.txt', new_name='todo.txt')",
    ),
    (
        "satisfied",
        "Read Why Not Release.md",
        "read_file(path='Why Not Release.md')",
    ),
    ("satisfied", "What is my name?", ""),
    ("satisfied", "Explain how photosynthesis works", ""),
    ("satisfied", "Thanks, that's helpful", ""),
]


async def judge(client: httpx.AsyncClient, model: str, request: str, calls: str) -> dict:
    started = time.time()
    payload = {
        "model": model,
        "state": {"request": request, "tools_called": calls or "(none)"},
        "questions": {"outstanding": {"type": "noul", "instructions": QUESTION}},
    }
    try:
        r = await client.post(f"{OLLAMA}/v1/systemone", json=payload, timeout=60)
        answer = (r.json().get("answers") or {}).get("outstanding") or {}
        p = answer.get("noul")
    except Exception:
        p = None
    return {"p_outstanding": p, "wall_s": round(time.time() - started, 3)}


async def run_arm(model: str) -> dict:
    rows: list[dict] = []
    async with httpx.AsyncClient() as client:
        for expected, request, calls in CASES:
            got = await judge(client, model, request, calls)
            p = got["p_outstanding"]
            decided = None if p is None else ("outstanding" if p >= 0.5 else "satisfied")
            got.update(
                {
                    "expected": expected,
                    "request": request,
                    "tools_called": calls,
                    "decided": decided,
                    "correct": decided == expected,
                }
            )
            rows.append(got)
            mark = "ok  " if got["correct"] else "FAIL"
            print(
                f"  [{model:10}] {mark} want={expected:11} p={p if p is None else round(p, 3)} "
                f"({got['wall_s'] * 1000:.0f}ms)  {calls[:44]}"
            )

    correct = sum(1 for r in rows if r["correct"])
    out_p = [
        r["p_outstanding"]
        for r in rows
        if r["expected"] == "outstanding" and r["p_outstanding"] is not None
    ]
    sat_p = [
        r["p_outstanding"]
        for r in rows
        if r["expected"] == "satisfied" and r["p_outstanding"] is not None
    ]
    return {
        "model": model,
        "accuracy_pct": round(correct / len(rows) * 100, 1),
        "correct": correct,
        "n": len(rows),
        "mean_p_outstanding": round(statistics.fmean(out_p), 3) if out_p else None,
        "mean_p_satisfied": round(statistics.fmean(sat_p), 3) if sat_p else None,
        "margin": round(statistics.fmean(out_p) - statistics.fmean(sat_p), 3)
        if out_p and sat_p
        else None,
        "median_ms": round(statistics.median([r["wall_s"] for r in rows]) * 1000, 1),
        "rows": rows,
    }


async def main() -> None:
    results: dict[str, dict] = {}
    for model in MODELS:
        print(f"\n[decision] {model}")
        results[model] = await run_arm(model)

    print("\n=== summary ===")
    for model, res in results.items():
        print(
            f"  {model:10} accuracy {res['accuracy_pct']:5.1f}% ({res['correct']}/{res['n']})  "
            f"P(out) {res['mean_p_outstanding']} vs P(sat) {res['mean_p_satisfied']}  "
            f"margin {res['margin']}  {res['median_ms']:.0f}ms"
        )

    out = HERE / "result.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
