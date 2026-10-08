"""Is the candidate the right MATH_MODEL? Pre-registered in ``plan.md``.

Drives the production compute path (``OllamaClient.execute_python``): the model
writes Python, the backend executes it, the stdout is parsed for the final number
and compared to ground truth. Pure model I/O plus local execution; no brain.

    python assistant/experiments/math_model_value/experiment.py

Writes ``result.json``. ``result.md`` only after ``verification.md``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

from assistant.backend.config import settings
from assistant.backend.pipeline.llm_client import OllamaClient

HERE = Path(__file__).resolve().parent

CANDIDATE = os.environ.get("CANDIDATE_MODEL", "sorc/qwen3.5-claude-4.6-opus:latest")
MODELS: list[tuple[str, str]] = [
    ("candidate", CANDIDATE),
    ("math baseline (qwen3.8:27b)", "qwen3.8:27b"),
    ("chat fallback (qwen3.5:9b)", "qwen3.5:9b"),
]

# prompt -> known numeric answer.
PROBLEMS: list[tuple[str, float]] = [
    ("What is 17% of 2,480?", 421.6),
    ("Compute the sum of the squares of the integers from 1 to 100.", 338350.0),
    (
        "A $10,000 loan at 6% annual interest, compounded monthly, for 3 years. "
        "What is the total amount owed at the end?",
        10000 * (1 + 0.06 / 12) ** 36,
    ),
    (
        "What is the net present value of $1,000 received at the end of each year "
        "for 5 years, at an 8% annual discount rate?",
        sum(1000 / 1.08**year for year in range(1, 6)),
    ),
    ("What is the mean of the numbers 12, 7, 19, 4, and 8?", 10.0),
    (
        "What is the population standard deviation of 2, 4, 4, 4, 5, 5, 7, and 9?",
        2.0,
    ),
    (
        "A train travels 240 km in 2.5 hours. At the same speed, how far does it "
        "travel in 4 hours?",
        384.0,
    ),
    ("What is 10 factorial?", 3628800.0),
]

_NUMBER = re.compile(r"[-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?")


def _numbers(text: str) -> list[float]:
    out: list[float] = []
    for m in _NUMBER.finditer(text or ""):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            continue
    return out


def _close(got: float, expected: float) -> bool:
    return abs(got - expected) <= max(0.01, abs(expected) * 0.01)


def _matched(numbers: list[float], expected: float) -> float | None:
    """The value in the output that matches ground truth, or None.

    The models print the answer *and* extra detail (an interest line, a
    year-by-year breakdown), so "the last number" graded correct answers wrong.
    The fair rule is: did the computation produce the right value anywhere.
    """
    for n in numbers:
        if _close(n, expected):
            return n
    return None


async def _run_arm(label: str, model: str) -> list[dict]:
    llm = OllamaClient(
        base_url=settings.ollama_url,
        math_model=model,
        math_num_ctx=settings.math_num_ctx,
    )
    rows: list[dict] = []
    try:
        for prompt, expected in PROBLEMS:
            import time

            started = time.time()
            try:
                stdout = await llm.execute_python(prompt)
                error = ""
            except Exception as exc:  # no tool call, timeout, ...
                stdout, error = "", f"{type(exc).__name__}: {exc}"
            wall = round(time.time() - started, 2)
            numbers = _numbers(stdout)
            matched = _matched(numbers, expected)
            correct = matched is not None and not error
            rows.append(
                {
                    "prompt": prompt[:60],
                    "expected": round(expected, 4),
                    "matched": matched,
                    "numbers_seen": [round(n, 4) for n in numbers][:8],
                    "correct": correct,
                    "wall_s": wall,
                    "error": error,
                    "stdout_head": (stdout or "")[:160],
                }
            )
            print(
                f"  {'ok ' if correct else 'FAIL'} exp={expected:>12.4f} "
                f"match={matched} wall={wall}s {error}"
            )
    finally:
        await llm.close()
    return rows


async def _main() -> None:
    results: dict[str, list[dict]] = {}
    for label, model in MODELS:
        print(f"\n[{label}] {model}")
        results[label] = await _run_arm(label, model)
        score = sum(1 for r in results[label] if r["correct"])
        print(f"  -> {score}/{len(PROBLEMS)} correct")
    out = Path(os.environ.get("RESULT_PATH", HERE / "result.json"))
    out.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    asyncio.run(_main())
