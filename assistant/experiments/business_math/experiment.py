"""Can the warm chat model replace the dedicated math model for business math?

Pre-registered in plan.md. Drives the production compute path
(``OllamaClient.execute_python``): the model writes Python, the backend executes it,
stdout is parsed. No brain, no tools.

    python assistant/experiments/business_math/experiment.py

Writes ``result.json``. ``result.md`` only after ``verification.md``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import statistics
import time
from pathlib import Path

from assistant.backend.config import settings
from assistant.backend.pipeline.llm_client import OllamaClient

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
HERE = Path(__file__).resolve().parent
TRIALS = int(os.environ.get("EXP_TRIALS", "3"))

DEDICATED = os.environ.get("MATH_CANDIDATE", "sorc/qwen3.5-claude-4.6-opus-q4:9b")
MODELS: list[tuple[str, str]] = [
    ("dedicated math (incumbent)", DEDICATED),
    ("warm chat (qwen3.5:9b)", "qwen3.5:9b"),
    ("warm utility (qwen3.5:4b)", "qwen3.5:4b"),
]


def _reference() -> list[tuple[str, float, float]]:
    """(prompt, expected, tolerance) — the expected value is computed here, not typed."""
    subtotal = 4 * 12.50 + 7 * 3.25 + 2 * 19.99
    tax = round(subtotal * 0.0825, 2)
    total = round(subtotal + tax, 2)
    return [
        ("An invoice has three lines: 4 units at $12.50 each, 7 units at $3.25 each, "
         "and 2 units at $19.99 each. What is the invoice subtotal, in dollars?",
         subtotal, 0.02),
        ("An invoice subtotal is $112.73 and the sales tax rate is 8.25%. What is the "
         "sales tax amount, rounded to the nearest cent?",
         tax, 0.02),
        ("An invoice subtotal is $112.73 and the sales tax rate is 8.25%. What is the "
         "invoice total including tax, rounded to the nearest cent?",
         total, 0.02),
        ("A shop starts with 240 units of an item. It sells 37, receives a delivery of "
         "60, then sells 88 more. How many units remain?",
         240 - 37 + 60 - 88, 0),
        ("Inventory: 45 units at $8.40, 120 units at $2.15, and 30 units at $15.99. "
         "What is the total inventory value, in dollars?",
         45 * 8.40 + 120 * 2.15 + 30 * 15.99, 0.02),
        ("An item costs $18.40 and sells for $29.99. What is the gross margin "
         "percentage, to one decimal place?",
         (29.99 - 18.40) / 29.99 * 100, 0.05),
        ("A bill of $247.80 receives a 15% discount. What is the discounted total, "
         "rounded to the nearest cent?",
         round(247.80 * 0.85, 2), 0.02),
        ("A restaurant bill of $186.40 is split evenly among 4 people, after a 20% tip "
         "is added to the bill. What does each person pay, rounded to the nearest cent?",
         round(186.40 * 1.20 / 4, 2), 0.02),
        ("A supplier packs a product in cases of 24. Starting from 1,000 units, how "
         "many units are left over after packing as many full cases as possible?",
         1000 % 24, 0),
        ("A business borrows $25,000 at 5.5% annual interest, compounded monthly, for "
         "2 years. What is the total owed at the end, rounded to the nearest cent?",
         round(25000 * (1 + 0.055 / 12) ** 24, 2), 0.02),
    ]


PROBLEMS = _reference()

_NUMBER = re.compile(r"[-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?")


def _numbers(text: str) -> list[float]:
    out: list[float] = []
    for m in _NUMBER.finditer(text or ""):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            continue
    return out


def _matched(numbers: list[float], expected: float, tol: float) -> float | None:
    """Did the output produce the right value anywhere? (Models print extra detail.)"""
    for n in numbers:
        if abs(n - expected) <= tol:
            return n
    return None


async def run_arm(
    label: str,
    model: str,
    problems: list[tuple[str, float, float]] | None = None,
    trials: int | None = None,
) -> dict:
    problems = problems if problems is not None else PROBLEMS
    trials = trials if trials is not None else TRIALS
    llm = OllamaClient(
        base_url=OLLAMA,
        math_model=model,
        math_num_ctx=settings.math_num_ctx,
    )
    rows: list[dict] = []
    for trial in range(1, trials + 1):
        for prompt, expected, tol in problems:
            started = time.time()
            try:
                stdout = await llm.execute_python(prompt)
                error = ""
            except Exception as exc:  # no tool call, timeout, unsupported model
                stdout, error = "", f"{type(exc).__name__}: {exc}"
            wall = round(time.time() - started, 2)
            numbers = _numbers(stdout)
            matched = _matched(numbers, expected, tol)
            rows.append(
                {
                    "trial": trial,
                    "prompt": prompt[:58],
                    "expected": round(expected, 4),
                    "tol": tol,
                    "matched": matched,
                    "correct": matched is not None and not error,
                    "empty_stdout": not (stdout or "").strip(),
                    "error": error,
                    "wall_s": wall,
                }
            )
    n = len(rows)
    correct = sum(1 for r in rows if r["correct"])
    empty = sum(1 for r in rows if r["empty_stdout"])
    walls = [r["wall_s"] for r in rows]
    return {
        "label": label,
        "model": model,
        "correct": correct,
        "n": n,
        "reliability": round(correct / n, 3),
        "empty_stdout": empty,
        "empty_rate": round(empty / n, 3),
        "median_s": round(statistics.median(walls), 2),
        "rows": rows,
    }


def _hard() -> list[tuple[str, float, float]]:
    """Hard problems — symbolic / multi-step / finance. Expected values computed here."""
    import math

    m = [[2, -1, 3], [0, 4, 5], [1, 2, -2]]
    det = (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )

    a, b, c = 1.0, -5.0, 6.0
    disc = math.sqrt(b * b - 4 * a * c)
    root = max((-b + disc) / (2 * a), (-b - disc) / (2 * a))

    integral = math.pi**2 - 4  # integral of x^2 sin x over [0, pi]

    flows = [-10000, 3000, 4000, 5000, 3000]

    def npv(r: float) -> float:
        return sum(cf / (1 + r) ** t for t, cf in enumerate(flows))

    lo, hi = -0.9, 5.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            lo = mid
        else:
            hi = mid
    irr = (lo + hi) / 2 * 100

    p3 = math.comb(10, 3) * 0.25**3 * 0.75**7
    x = (12 + 3) / 5  # 2x + 3y = 12, x - y = 1

    return [
        ("Compute the determinant of the 3x3 matrix [[2, -1, 3], [0, 4, 5], "
         "[1, 2, -2]].", det, 0.01),
        ("What is the larger root of the equation x^2 - 5x + 6 = 0?", root, 0.01),
        ("Evaluate the definite integral of x^2 * sin(x) with respect to x, from 0 to "
         "pi. Give the numeric value to 4 decimal places.", integral, 0.001),
        ("A project has cash flows of -10000, 3000, 4000, 5000, and 3000 in years 0 to 4. "
         "What is the internal rate of return (IRR) as a percentage, to one decimal "
         "place?", irr, 0.05),
        ("In 10 independent trials, each with probability 0.25 of success, what is the "
         "probability of exactly 3 successes? Give the value to 4 decimal places.",
         p3, 0.0005),
        ("Solve the linear system 2x + 3y = 12 and x - y = 1. What is the value of x?",
         x, 0.01),
    ]


async def arm_hard() -> None:
    problems = _hard()
    results: dict[str, dict] = {}
    for label, model in MODELS[:2]:  # dedicated vs utility (H5)
        print(f"\n[{label}] {model}")
        res = await run_arm(label, model, problems=problems, trials=2)
        results[label] = res
        print(
            f"  {res['correct']}/{res['n']} = {res['reliability']:.2f}  "
            f"empty {res['empty_stdout']}  median {res['median_s']}s"
        )
    print("\n=== hard-math summary ===")
    for label, r in results.items():
        misses = [
            f"t{row['trial']}: {row['prompt'][:34]}" for row in r["rows"] if not row["correct"]
        ]
        print(f"  {label:32} {r['correct']:>2}/{r['n']:<2}  misses={misses}")
    (HERE / "result_hard.json").write_text(json.dumps(results, indent=2))
    print(f"\nwrote {HERE / 'result_hard.json'}")


async def arm_num_ctx() -> None:
    """Does changing the requested context reload the model? (H4)"""
    import httpx

    async def loaded() -> list[dict]:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{OLLAMA}/api/ps", timeout=10)
            return [
                {k: v for k, v in m.items() if k in ("name", "context_length", "size_vram")}
                for m in r.json().get("models", [])
            ]

    model = "qwen3.5:4b"
    plan = [4096, 4096, 16384, 16384, 4096]
    rows: list[dict] = []
    for ctx in plan:
        llm = OllamaClient(base_url=OLLAMA, math_model=model, math_num_ctx=ctx)
        started = time.time()
        try:
            await llm.execute_python("What is 12 * 34?")
            err = ""
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
        row = {
            "num_ctx": ctx,
            "wall_s": round(time.time() - started, 2),
            "loaded": await loaded(),
            "error": err,
        }
        rows.append(row)
        print(f"  num_ctx={ctx:6}  wall={row['wall_s']:6.2f}s  loaded={row['loaded']}")
    (HERE / "result_num_ctx.json").write_text(json.dumps({"model": model, "rows": rows}, indent=2))
    print(f"\nwrote {HERE / 'result_num_ctx.json'}")


async def main() -> None:
    arm = os.environ.get("EXP_ARM", "business")
    if arm == "num_ctx":
        await arm_num_ctx()
        return
    if arm == "hard":
        await arm_hard()
        return

    results: dict[str, dict] = {}
    for label, model in MODELS:
        print(f"\n[{label}] {model}")
        res = await run_arm(label, model)
        results[label] = res
        print(
            f"  reliability {res['correct']}/{res['n']} = {res['reliability']:.2f}   "
            f"empty-stdout {res['empty_stdout']}/{res['n']} = {res['empty_rate']:.2f}   "
            f"median {res['median_s']}s"
        )

    print("\n=== summary ===")
    print("  model                            correct   empty   median")
    for label, r in results.items():
        print(
            f"  {label:32} {r['correct']:>3}/{r['n']:<3} {r['empty_stdout']:>5}/{r['n']:<3} "
            f"{r['median_s']:>6}s"
        )

    out = HERE / "result.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
