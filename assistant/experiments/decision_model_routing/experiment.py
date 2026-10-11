"""Can a decision model judge `needs_action`? Pre-registered in plan.md.

Calls ollama 0.35's `/v1/systemone` (decision models) and `/api/chat` (the 4B
baseline) against local Ollama. No brain, no tools.

    python assistant/experiments/decision_model_routing/experiment.py

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

DECISION_MODELS = ["nimble", "tev1", "tev1:0.8b"]
BASELINE = "qwen3.5:4b"

# (label, request). label: "action" | "answer"
REQUESTS: list[tuple[str, str]] = [
    ("action", "Write a file called notes.txt with today's plan"),
    ("action", "Create Friend Music Records Release Template.md as a generic template"),
    ("action", "We want to create Friend Music Records Release Template.md"),
    ("action", "No edits to Why Not Release. We want to create a release template"),
    ("action", "Read Why Not Release.md"),
    ("action", "Rename notes.txt to todo.txt"),
    ("action", "Delete the old draft file"),
    ("action", "Append this row to contacts.csv"),
    ("action", "Add a point to my survey map at -122.68, 45.523"),
    ("action", "Run my morning briefing now"),
    ("answer", "What is my name?"),
    ("answer", "Explain how photosynthesis works"),
    ("answer", "What did we decide about the launch?"),
    ("answer", "Thanks, that's helpful"),
    ("answer", "What's the capital of France?"),
    ("answer", "Summarize what you know about the Mozworth project"),
    ("answer", "Why is the sky blue?"),
    ("answer", "How many books are on my reading list?"),
    ("answer", "What did my morning briefing find yesterday?"),
    ("answer", "Is it a good idea to release on a Friday?"),
]

QUESTION = (
    "Does this user request require the assistant to PERFORM an action with a tool "
    "(create, read, edit, rename, or delete a file; run a task; search the web) — as "
    "opposed to just answering with words?"
)

QUESTIONS = {
    "needs_action": {
        "type": "choice",
        "instructions": QUESTION,
        "criteria": {
            "action": "the assistant must perform an action with a tool",
            "answer": "the assistant can just reply with words",
        },
    }
}


async def decision(client: httpx.AsyncClient, model: str, request: str) -> dict:
    started = time.time()
    try:
        r = await client.post(
            f"{OLLAMA}/v1/systemone",
            json={"model": model, "state": {"request": request}, "questions": QUESTIONS},
            timeout=60,
        )
        data = r.json()
        ans = (data.get("answers") or {}).get("needs_action") or {}
        choice = ans.get("choice")
        confidence = ans.get("confidence")
    except Exception as e:
        choice, confidence = None, f"{type(e).__name__}: {e}"
    return {"choice": choice, "confidence": confidence, "wall_s": round(time.time() - started, 3)}


async def baseline(client: httpx.AsyncClient, request: str) -> dict:
    started = time.time()
    try:
        r = await client.post(
            f"{OLLAMA}/api/chat",
            json={
                "model": BASELINE,
                "messages": [
                    {
                        "role": "system",
                        "content": 'Answer with JSON only: {"needs_action": true|false}',
                    },
                    {"role": "user", "content": f"{QUESTION}\n\nRequest: {request}"},
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
            },
            timeout=120,
        )
        content = (r.json().get("message") or {}).get("content", "")
        value = json.loads(content).get("needs_action")
        choice = "action" if value is True else ("answer" if value is False else None)
    except Exception as e:
        choice = None
        content = f"{type(e).__name__}: {e}"
    return {"choice": choice, "wall_s": round(time.time() - started, 3), "raw": content[:60]}


async def run_arm(label: str, fn) -> dict:
    rows: list[dict] = []
    async with httpx.AsyncClient() as client:
        for expected, request in REQUESTS:
            got = await fn(client, request)
            got.update({"expected": expected, "request": request})
            got["correct"] = got["choice"] == expected
            rows.append(got)
            mark = "ok  " if got["correct"] else "FAIL"
            print(
                f"  [{label:12}] {mark} want={expected:6} got={str(got['choice']):6} "
                f"{got['wall_s'] * 1000:6.0f}ms  {request[:52]}"
            )
    correct = sum(1 for r in rows if r["correct"])
    walls = [r["wall_s"] for r in rows]
    return {
        "label": label,
        "accuracy_pct": round(correct / len(rows) * 100, 1),
        "correct": correct,
        "n": len(rows),
        "median_ms": round(statistics.median(walls) * 1000, 1),
        "rows": rows,
    }


async def main() -> None:
    results: dict[str, dict] = {}
    for model in DECISION_MODELS:
        print(f"\n[decision] {model}")
        results[model] = await run_arm(model, lambda c, r, m=model: decision(c, m, r))
    print(f"\n[baseline] {BASELINE}")
    results[BASELINE] = await run_arm(BASELINE, baseline)

    print("\n=== summary ===")
    for label, res in results.items():
        print(
            f"  {label:14} accuracy {res['accuracy_pct']:5.1f}% "
            f"({res['correct']}/{res['n']})  median {res['median_ms']:6.1f}ms"
        )

    out = HERE / "result.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
