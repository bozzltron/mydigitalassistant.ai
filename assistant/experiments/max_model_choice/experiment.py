"""Is the opus Q4 9B a better MAX model than the 27B? Pre-registered in plan.md.

Direct `OllamaClient.chat` with thinking on — the MAX tier's shape. No brain, no
tools, no network beyond local Ollama.

    python assistant/experiments/max_model_choice/experiment.py

Writes ``result.json``. ``result.md`` only after ``verification.md``.
"""

from __future__ import annotations

import asyncio
import json
import re
import statistics
import time
from pathlib import Path

from assistant.backend.config import settings
from assistant.backend.pipeline.llm_client import ChatMessage, OllamaClient

HERE = Path(__file__).resolve().parent

MODELS: list[tuple[str, str]] = [
    ("incumbent (qwen3.8:27b)", "qwen3.8:27b"),
    ("candidate (opus-q4:9b)", "sorc/qwen3.5-claude-4.6-opus-q4:9b"),
]

SYSTEM = (
    "Solve the problem carefully. Show your reasoning, then end with a final "
    "line exactly in the form: ANSWER: <number>"
)

# prompt -> exact numeric answer.
PROBLEMS: list[tuple[str, float]] = [
    ("How many trailing zeros are there in 100! (100 factorial)?", 24),
    (
        "What is the sum of all integers from 1 to 1000 that are divisible by 3 "
        "or by 5?",
        234168,
    ),
    (
        "A 3x3x3 cube is painted on all faces and then cut into 27 unit cubes. "
        "How many of the unit cubes have exactly two painted faces?",
        12,
    ),
    (
        "How many distinct ways are there to make change for $1.00 using "
        "quarters, dimes, nickels, and pennies?",
        242,
    ),
    (
        "A password has 4 digits (0-9), no digit repeats, and the first digit is "
        "odd. How many such passwords are there?",
        2520,
    ),
    (
        "Two trains are 200 km apart and approach each other at 60 km/h and "
        "40 km/h. A fly flies back and forth between them at 90 km/h until they "
        "meet. How far, in km, does the fly travel?",
        180,
    ),
    ("What is the smallest positive integer that has exactly 24 positive divisors?", 360),
    ("If x + 1/x = 5, what is x^2 + 1/x^2?", 23),
    (
        "How many times do the hour and minute hands of a clock overlap during a "
        "24-hour day?",
        22,
    ),
    (
        "A lily pad doubles in area every day. It covers the entire lake on day "
        "48. On which day was it half covered?",
        47,
    ),
]

_ANSWER = re.compile(r"ANSWER:\s*([-+]?\d[\d,]*\.?\d*)")


def parse_answer(text: str) -> float | None:
    """The value after the final ``ANSWER:`` marker, or None."""
    matches = _ANSWER.findall(text or "")
    if not matches:
        return None
    try:
        return float(matches[-1].replace(",", ""))
    except ValueError:
        return None


def _close(got: float, expected: float) -> bool:
    return abs(got - expected) <= max(0.01, abs(expected) * 0.001)


async def _run_arm(label: str, model: str) -> list[dict]:
    llm = OllamaClient(
        base_url=settings.ollama_url,
        chat_model=model,
        keep_alive=settings.ollama_keep_alive,
    )
    rows: list[dict] = []
    try:
        for prompt, expected in PROBLEMS:
            started = time.time()
            try:
                resp = await llm.chat(
                    [
                        ChatMessage(role="system", content=SYSTEM),
                        ChatMessage(role="user", content=prompt),
                    ],
                    model=model,
                    temperature=0.0,
                    think=True,
                    num_predict=4096,
                )
                text, error = resp.content or "", ""
            except Exception as exc:  # timeout, model missing, ...
                text, error = "", f"{type(exc).__name__}: {exc}"
            wall = round(time.time() - started, 2)
            got = parse_answer(text)
            correct = got is not None and _close(got, expected) and not error
            rows.append(
                {
                    "prompt": prompt[:70],
                    "expected": expected,
                    "got": got,
                    "correct": correct,
                    "wall_s": wall,
                    "error": error,
                    "answer_tail": (text or "")[-120:],
                }
            )
            print(
                f"  {'ok  ' if correct else 'FAIL'} exp={expected:>10} got={got} "
                f"wall={wall}s {error}"
            )
    finally:
        await llm.close()
    return rows


async def main() -> None:
    results: dict[str, list[dict]] = {}
    for label, model in MODELS:
        print(f"\n[{label}] {model}")
        results[label] = await _run_arm(label, model)

    inc = results[MODELS[0][0]]
    cand = results[MODELS[1][0]]
    inc_score = sum(1 for r in inc if r["correct"])
    cand_score = sum(1 for r in cand if r["correct"])
    inc_med = statistics.median([r["wall_s"] for r in inc])
    cand_med = statistics.median([r["wall_s"] for r in cand])
    speedup = round(inc_med / cand_med, 2) if cand_med else None

    print(f"\naccuracy: incumbent {inc_score}/{len(PROBLEMS)}  "
          f"candidate {cand_score}/{len(PROBLEMS)}")
    print(f"median wall: incumbent {inc_med}s  candidate {cand_med}s  "
          f"speedup {speedup}x")

    out = HERE / "result.json"
    out.write_text(
        json.dumps(
            {
                "experiment": "max_model_choice",
                "models": {label: model for label, model in MODELS},
                "incumbent_correct": inc_score,
                "candidate_correct": cand_score,
                "n_problems": len(PROBLEMS),
                "incumbent_median_wall_s": inc_med,
                "candidate_median_wall_s": cand_med,
                "speedup": speedup,
                "rows": results,
            },
            indent=2,
        )
    )
    print(f"\nwrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
